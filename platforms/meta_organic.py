"""Organski rezultati Facebook stranice i Instagram profila (Meta Business Suite > Insights).

Koristi iste Meta veze kao Meta Ads (licni tokeni clanova tima ili token
sistemskog korisnika), ali cita stranice i Instagram profile, a ne oglasne
naloge. Za to Meta veza treba da ima i dozvole pages_show_list,
pages_read_engagement, read_insights, instagram_basic i
instagram_manage_insights.

Tokeni stranica se nikad ne upisuju u bazu ni u kes; drze se samo u memoriji.
"""
import hashlib
import threading
import time
from datetime import timedelta

from platforms import meta_ads
from platforms.base import LIST_TTL, SourceError, cached, data_ttl

FB_KEY = "fb_page"
IG_KEY = "instagram"
FB_NAME = "Facebook stranica"
IG_NAME = "Instagram"
KEYS = (FB_KEY, IG_KEY)
NAMES = {FB_KEY: FB_NAME, IG_KEY: IG_NAME}

PAGE_FIELDS = "id,name,access_token,followers_count,instagram_business_account{id,username,followers_count}"
IG_CHUNK_DAYS = 30   # Instagram daje zbir najvise za 30 dana po zahtevu
FB_CHUNK_DAYS = 90   # Facebook najvise oko 3 meseca po zahtevu

_lock = threading.Lock()
_memory = {"at": 0, "pages": [], "tokens": {}, "ig_page": {}, "sig": ""}


def connected():
    return meta_ads.connected()


def _load(force=False):
    """Stranice koje povezani ljudi vide, sa tokenima stranica (samo u memoriji, 30 min).

    Kad se neko poveze ili prekine vezu, menja se skup tokena, pa se lista ucitava ponovo.
    """
    people = meta_ads._as_list(meta_ads.prepare())
    sig = hashlib.sha256("|".join(sorted(p["token"] for p in people)).encode()).hexdigest()
    with _lock:
        if not force and _memory["pages"] and _memory["sig"] == sig and time.time() - _memory["at"] < 1800:
            return _memory
    pages, tokens, ig_page, errors = {}, {}, {}, []
    for person in people:
        try:
            rows = meta_ads._paged(f"{meta_ads.BASE}/me/accounts", person["token"], {"fields": PAGE_FIELDS, "limit": 100})
        except SourceError as exc:
            errors.append(str(exc))
            continue
        for row in rows:
            page_id = str(row.get("id", ""))
            if not page_id or page_id in pages:
                continue
            ig = row.get("instagram_business_account") or {}
            pages[page_id] = {
                "id": page_id,
                "name": row.get("name") or page_id,
                "ig_id": str(ig.get("id", "")),
                "ig_name": ig.get("username") or "",
            }
            if row.get("access_token"):
                tokens[page_id] = row["access_token"]
            if ig.get("id"):
                ig_page[str(ig["id"])] = page_id
    if errors and not pages:
        raise SourceError("; ".join(errors))
    with _lock:
        _memory.update(
            {"at": time.time(), "pages": list(pages.values()), "tokens": tokens, "ig_page": ig_page, "sig": sig}
        )
        return _memory


def forget():
    with _lock:
        _memory.update({"at": 0, "pages": [], "tokens": {}, "ig_page": {}, "sig": ""})


def list_accounts(kind):
    """Stranice (FB_KEY) ili Instagram profili (IG_KEY): [{"id", "name"}]."""
    pages = _load()["pages"]
    if kind == FB_KEY:
        out = [{"id": p["id"], "name": p["name"]} for p in pages]
    else:
        out = [{"id": p["ig_id"], "name": f"@{p['ig_name']} ({p['name']})"} for p in pages if p["ig_id"]]
    return sorted(out, key=lambda a: a["name"].lower())


def accounts(kind):
    """Lista za podesavanja, zapamcena sat vremena (bez tokena)."""
    return cached(f"accounts:{kind}", LIST_TTL, lambda: list_accounts(kind))


def _page_token(page_id):
    memory = _load()
    if page_id not in memory["tokens"]:
        memory = _load(force=True)
    token = memory["tokens"].get(page_id)
    if not token:
        raise SourceError("Nijedan povezani Facebook nalog nema pristup ovoj stranici (ili nedostaje dozvola za stranice).")
    return token


def _ig_token(ig_id):
    memory = _load()
    if ig_id not in memory["ig_page"]:
        memory = _load(force=True)
    page_id = memory["ig_page"].get(ig_id)
    if not page_id:
        raise SourceError("Nijedan povezani Facebook nalog nema pristup ovom Instagram profilu.")
    return _page_token(page_id)


def _chunks(start, end, days):
    while start <= end:
        stop = min(end, start + timedelta(days=days - 1))
        yield start, stop
        start = stop + timedelta(days=1)


def _ts(day, end_of_day=False):
    """Unix vreme pocetka (ili kraja) dana, kako ga Meta ocekuje za since/until."""
    seconds = int(time.mktime(day.timetuple()))
    return seconds + 86400 if end_of_day else seconds


# ---------------------------------------------------------------- Facebook stranica

FB_METRICS = {
    "views": "page_media_view",                    # Views (zamena za page_impressions)
    "viewers": "page_total_media_view_unique",     # Viewers (zamena za page_impressions_unique)
    "interactions": "page_post_engagements",       # Content interactions
    "profile_visits": "page_views_total",          # posete stranice (Meta nije objavila zamenu; moze da ne radi)
    "new_follows": "page_daily_follows_unique",    # novi pratioci po danu
}


def facebook(page_id, start, end):
    """Pregledi, gledaoci, interakcije (zbir i po danima) i broj pratilaca stranice."""

    def fetch():
        token = _page_token(page_id)
        out = {"by_day": {}, "totals": {}, "errors": {}}
        for key, metric in FB_METRICS.items():
            try:
                if key == "viewers":
                    # jedinstveni gledaoci se ne sabiraju po danima: trazi se zbir za ceo period
                    data = meta_ads._get(
                        f"{meta_ads.BASE}/{page_id}/insights", token,
                        {"metric": metric, "period": "total_over_range",
                         "since": _ts(start), "until": _ts(end, True)},
                    )
                    values = (data.get("data") or [{}])[0].get("values") or [{}]
                    out["totals"][key] = float(values[-1].get("value") or 0)
                    continue
                total = 0.0
                for a, b in _chunks(start, end, FB_CHUNK_DAYS):
                    data = meta_ads._get(
                        f"{meta_ads.BASE}/{page_id}/insights", token,
                        {"metric": metric, "period": "day", "since": _ts(a), "until": _ts(b, True)},
                    )
                    for item in (data.get("data") or [{}])[0].get("values", []):
                        day = str(item.get("end_time", ""))[:10]
                        value = float(item.get("value") or 0) if not isinstance(item.get("value"), dict) else 0.0
                        if day:
                            out["by_day"].setdefault(day, {})[key] = value
                            total += value
                out["totals"][key] = total
            except SourceError as exc:
                out["errors"][key] = str(exc)
        try:
            info = meta_ads._get(f"{meta_ads.BASE}/{page_id}", token, {"fields": "followers_count,name"})
            out["totals"]["followers"] = float(info.get("followers_count") or 0)
        except SourceError as exc:
            out["errors"]["followers"] = str(exc)
        if len(out["errors"]) == len(FB_METRICS) + 1:
            raise SourceError(next(iter(out["errors"].values())))
        return out

    return cached(f"organic:{FB_KEY}:{page_id}:{start.isoformat()}:{end.isoformat()}", data_ttl(end), fetch)


# ---------------------------------------------------------------- Instagram

IG_METRICS = {
    "views": "views",
    "reach": "reach",                    # jedinstveni nalozi; ne sabira se preko vise delova
    "interactions": "total_interactions",
    "link_taps": "profile_links_taps",   # klikovi na linkove u profilu
    "profile_visits": "profile_views",
    "new_follows": "follows_and_unfollows",  # sa podelom follow_type: FOLLOWER = novi pratioci
}


def _ig_value(item, key):
    """Zbir iz odgovora; kod pratilaca samo deo FOLLOWER (bez onih koji su otpratili)."""
    value = item.get("total_value") or {}
    if key == "new_follows":
        for breakdown in value.get("breakdowns") or []:
            for result in breakdown.get("results") or []:
                if "FOLLOWER" in (result.get("dimension_values") or []):
                    return float(result.get("value") or 0)
    return float(value.get("value") or 0)


def instagram(ig_id, start, end):
    """Pregledi, doseg, interakcije, klikovi na link u profilu i broj pratilaca."""

    def fetch():
        token = _ig_token(ig_id)
        out = {"totals": {}, "errors": {}, "reach_partial": False}
        parts = list(_chunks(start, end, IG_CHUNK_DAYS))
        for key, metric in IG_METRICS.items():
            try:
                total = 0.0
                for a, b in parts:
                    params = {"metric": metric, "period": "day", "metric_type": "total_value",
                              "since": _ts(a), "until": _ts(b, True)}
                    if key == "new_follows":
                        params["breakdown"] = "follow_type"
                    data = meta_ads._get(f"{meta_ads.BASE}/{ig_id}/insights", token, params)
                    item = (data.get("data") or [{}])[0]
                    total += _ig_value(item, key)
                out["totals"][key] = total
                if key == "reach" and len(parts) > 1:
                    out["reach_partial"] = True
            except SourceError as exc:
                out["errors"][key] = str(exc)
        try:
            info = meta_ads._get(f"{meta_ads.BASE}/{ig_id}", token, {"fields": "followers_count,username"})
            out["totals"]["followers"] = float(info.get("followers_count") or 0)
        except SourceError as exc:
            out["errors"]["followers"] = str(exc)
        if len(out["errors"]) == len(IG_METRICS) + 1:
            raise SourceError(next(iter(out["errors"].values())))
        return out

    return cached(f"organic:{IG_KEY}:{ig_id}:{start.isoformat()}:{end.isoformat()}", data_ttl(end), fetch)
