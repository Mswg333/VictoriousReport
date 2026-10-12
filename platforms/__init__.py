"""Kanali oglasavanja i zajednicke radnje nad njima.

Svaki kanal je jedan modul sa istim skupom funkcija:

    KEY, NAME            oznaka i ime za prikaz
    configured()         da li su upisana podesavanja u okruzenju
    connected()          da li je kanal povezan na strani "Izvor podataka"
    prepare()            vraca token za pozive (jedino mesto koje cita bazu)
    list_accounts(token) nalozi koje povezani korisnik vidi
    fetch_daily(token, account_id, start, end)
                         brojke po kampanji i danu, u obliku
                         {"start": ..., "end": ..., "rows": [[dan, id, ime,
                           potroseno, prikazi, klikovi, konverzije,
                           vrednost konverzija], ...]}

Ovde su radnje koje su iste za sve kanale: kes, osvezavanje vise naloga
odjednom i sabiranje brojki za izabrani period.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from platforms import base, ga4, google_ads, meta_ads, microsoft_ads, tiktok_ads
from platforms.base import ERROR_TTL, LIST_TTL, NotConnected, SourceError

ADS = [google_ads, meta_ads, microsoft_ads, tiktok_ads]
BY_KEY = {mod.KEY: mod for mod in ADS}

__all__ = ["ADS", "BY_KEY", "ga4", "SourceError", "NotConnected"]


def get(key):
    return BY_KEY[key]


def accounts(mod):
    """Lista naloga za kanal, zapamcena na sat vremena."""
    return base.cached(
        f"accounts:{mod.KEY}:{mod.accounts_scope()}", LIST_TTL, lambda: mod.list_accounts(mod.prepare())
    )


# ---------------------------------------------------------------- dnevne brojke

def default_range():
    """Podrazumevani period: poslednjih HISTORY_DAYS dana, zakljucno sa jucerasnjim."""
    end = base.yesterday()
    return end - timedelta(days=base.HISTORY_DAYS - 1), end


def _keys(platform, account_id, start, end):
    # "v2": od kad redovi imaju i vrednost konverzija; stariji zapamceni odgovori se ne koriste.
    suffix = f"{platform}:{account_id}:v3:{start.isoformat()}:{end.isoformat()}"
    return f"daily:{suffix}", f"daily_error:{suffix}"


def _recent_error(error_key):
    """Poruka o nedavnoj gresci, da se servis ne zove na svako otvaranje strane."""
    payload, failed_at = base.cache_get(error_key)
    if payload is not None and time.time() - failed_at < ERROR_TTL:
        return payload["error"]
    return None


def _remember_error(error_key, exc):
    # Ako kanal nije povezan, nema sta da se pamti: cim se poveze, treba pokusati odmah.
    if not isinstance(exc, NotConnected):
        base.cache_put(error_key, {"error": str(exc)})


def _fresh(data_key, end):
    payload, fetched_at = base.cache_get(data_key)
    return payload, payload is not None and time.time() - fetched_at < base.data_ttl(end)


def _store(data_key, error_key, payload):
    base.cache_put(data_key, payload)
    base.cache_delete(error_key)


def daily(platform, account_id, start, end, fetch=True):
    """Brojke po kampanji i danu za period. Vraca (podaci, greska).

    Podaci mogu biti i stari kes ako servis trenutno ne radi.
    """
    data_key, error_key = _keys(platform, account_id, start, end)
    payload, fresh = _fresh(data_key, end)
    if fresh:
        return payload, None
    recent = _recent_error(error_key)
    if recent or not fetch:
        return payload, recent
    mod = get(platform)
    try:
        result = mod.fetch_daily(mod.prepare(), account_id, start, end)
    except SourceError as exc:
        _remember_error(error_key, exc)
        return payload, str(exc)
    _store(data_key, error_key, result)
    return result, None


def warm(items):
    """Osvezava kes za vise naloga odjednom.

    items: [(kanal, nalog, od, do)]. Vraca {(kanal, nalog): greska}.
    """
    errors, stale = {}, {}
    for platform, account_id, start, end in dict.fromkeys(items):
        data_key, error_key = _keys(platform, account_id, start, end)
        if _fresh(data_key, end)[1]:
            continue
        recent = _recent_error(error_key)
        if recent:
            errors[(platform, account_id)] = recent
        else:
            stale.setdefault(platform, []).append((account_id, start, end))

    # Tokeni se pripremaju ovde, u glavnoj niti, jer jedino ona sme da cita bazu.
    jobs = []
    for platform, wanted in stale.items():
        mod = get(platform)
        try:
            token = mod.prepare()
        except SourceError as exc:
            for account_id, _, _ in wanted:
                errors[(platform, account_id)] = str(exc)
            continue
        jobs.extend((mod, token, account_id, start, end) for account_id, start, end in wanted)
    if not jobs:
        return errors

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [
            (mod.KEY, account_id, start, end, pool.submit(mod.fetch_daily, token, account_id, start, end))
            for mod, token, account_id, start, end in jobs
        ]
    for platform, account_id, start, end, future in futures:
        data_key, error_key = _keys(platform, account_id, start, end)
        try:
            _store(data_key, error_key, future.result())
        except SourceError as exc:
            _remember_error(error_key, exc)
            errors[(platform, account_id)] = str(exc)
    return errors


def summarize(payload, start, end, conv_kind):
    """Iz dnevnih redova racuna zbir, kampanje i niz po danima za period od start do end."""
    days = (end - start).days + 1
    totals = {"spend": 0.0, "impressions": 0, "clicks": 0, "conversions": 0.0, "value": 0.0}
    per_day = {
        start + timedelta(days=i): {"spend": 0.0, "impressions": 0, "clicks": 0, "conversions": 0.0, "value": 0.0}
        for i in range(days)
    }
    totals["extra"] = {}
    info = payload.get("campaigns", {})  # podaci o kampanjama (cilj, budzet), ako ih kanal daje
    campaigns = {}
    for row in payload["rows"]:
        day_text, campaign_id, name, spend, impressions, clicks, conversions = row[:7]
        value = row[7] if len(row) > 7 else 0.0
        extra = row[8] if len(row) > 8 else {}  # brojke koje ima samo neki kanal (npr. Meta rezultati)
        day = date.fromisoformat(day_text)
        if day < start or day > end:
            continue
        if isinstance(conversions, dict):  # kanal razlikuje upite i kupovine
            conversions = conversions.get(conv_kind, 0)
        c = campaigns.setdefault(
            campaign_id,
            {
                "id": campaign_id, "name": name, "spend": 0.0, "impressions": 0, "clicks": 0,
                "conversions": 0.0, "value": 0.0, "extra": {},
                "info": info.get(campaign_id, {}),
            },
        )
        for target in (c, totals, per_day[day]):
            target["spend"] += spend
            target["impressions"] += impressions
            target["clicks"] += clicks
            target["conversions"] += conversions
            target["value"] += value
        for target in (c["extra"], totals["extra"]):
            for key, amount in extra.items():
                target[key] = target.get(key, 0) + amount
    for target in [totals, *campaigns.values()]:
        target["spend"] = round(target["spend"], 2)
        target["conversions"] = round(target["conversions"], 2)
        target["value"] = round(target["value"], 2)
    # Vrste rezultata kampanja koje su trosile novac. Zbir rezultata ima smisla
    # samo kad su sve iste (kao u Ads Manager-u).
    totals["result_kinds"] = sorted(
        {str(c["info"].get("result")) for c in campaigns.values() if c["spend"] or c["impressions"]}
    )
    return {
        "start": start,
        "end": end,
        "totals": totals,
        "campaigns": sorted(campaigns.values(), key=lambda c: c["spend"], reverse=True),
        # Dan po dan, sa svim brojkama: [{"date", "spend", "impressions", "clicks", "conversions"}]
        "daily": [{"date": day, **values} for day, values in per_day.items()],
    }


def search_terms(account_id, start, end):
    """Pojmovi pretrage (samo Google Ads), zapamceni kao i ostale brojke."""
    return base.cached(
        f"terms:{google_ads.KEY}:{account_id}:{start.isoformat()}:{end.isoformat()}",
        base.data_ttl(end),
        lambda: google_ads.fetch_search_terms(google_ads.prepare(), account_id, start, end),
    )


def reach(account_id, start, end):
    """Doseg i frekvencija (samo Meta Ads), zapamceni kao i ostale brojke."""
    return base.cached(
        f"reach:{meta_ads.KEY}:{account_id}:{start.isoformat()}:{end.isoformat()}",
        base.data_ttl(end),
        lambda: meta_ads.fetch_reach(meta_ads.prepare(), account_id, start, end),
    )


def ad_rankings(account_id, start, end):
    """Ocene oglasa (samo Meta Ads), zapamcene kao i ostale brojke."""
    return base.cached(
        f"rankings:{meta_ads.KEY}:{account_id}:{start.isoformat()}:{end.isoformat()}",
        base.data_ttl(end),
        lambda: meta_ads.fetch_rankings(meta_ads.prepare(), account_id, start, end),
    )


def clear(platform, account_id):
    """Brise zapamcene brojke za jedan nalog, za sve periode."""
    for prefix in ("daily", "daily_error", "terms", "reach", "rankings"):
        base.cache_delete_prefix(f"{prefix}:{platform}:{account_id}:")
    if platform == ga4.KEY:
        base.cache_delete_prefix(f"ga4:{account_id}:")
