"""Sastavljanje izvestaja koje vidi klijent.

Klijent ima po jedan izvestaj za svaki kanal koji mu je ukljucen, zbirni
izvestaj preko svih kanala oglasavanja (ako je ukljucen) i izvestaj o
posetama sajtu iz Analytics-a.

Ovde se samo racuna i priprema tekst; izgled je u templates/report.html.
"""
from datetime import timedelta

import fmt
import layout
import platforms
from platforms import base, ga4
from platforms.base import SourceError

COMBINED = layout.COMBINED


def tabs_for(client, sources):
    """Kartice izvestaja: [(oznaka, naziv)]. Redosled je i redosled prikaza."""
    ads = visible_ads(sources)
    tabs = []
    if client["show_combined"] and len(ads) >= 2:
        tabs.append((COMBINED, "Ukupno"))
    tabs += [(mod.KEY, mod.NAME) for mod, _ in ads]
    site = sources.get(ga4.KEY)
    if site and site["visible"]:
        tabs.append((ga4.KEY, ga4.NAME))
    return tabs


def visible_ads(sources):
    return [(mod, sources[mod.KEY]) for mod in platforms.ADS if mod.KEY in sources and sources[mod.KEY]["visible"]]


def build(client, sources, tab, start, end, data_range):
    """Vraca (izvestaj, napomene_za_tim). Izvestaj je None ako podataka nema.

    start, end: period koji klijent gleda.
    data_range: period koji se cita sa platformi (ceo period koji je tim
    odobrio), da promena perioda u izvestaju ne trazi nove pozive.
    """
    if tab == COMBINED:
        return _combined(client, sources, start, end, data_range)
    if tab == ga4.KEY:
        return _site(client, sources[ga4.KEY], start, end)
    return _single(client, platforms.get(tab), sources[tab], start, end, data_range)


def _phrase(start, end):
    return fmt.period_phrase(start, end, base.yesterday())


# ---------------------------------------------------------------- delovi izvestaja

def _chart(series, value_text, title_noun, none_text):
    """Stubici po danima, a za periode duze od dva meseca po nedeljama.

    series: [{"date", "value"}]; value_text(v) ispisuje vrednost ("42 klika").
    """
    if len(series) < 2:
        return None  # za jedan dan grafikon nema smisla
    weekly = len(series) > 62
    first_day = fmt.day_label(series[0]["date"])
    last_day = fmt.day_label(series[-1]["date"])
    if weekly:
        series = [
            {"date": chunk[0]["date"], "value": sum(p["value"] for p in chunk)}
            for chunk in (series[i:i + 7] for i in range(0, len(series), 7))
        ]
    top = max((p["value"] for p in series), default=0)
    peak = max(series, key=lambda p: p["value"]) if top else None
    bars = [
        {
            "height": max(4, round(p["value"] / top * 160)) if top else 4,
            "tip": f"{fmt.day_label(p['date'])}: {value_text(p['value'])}",
            "peak": p is peak,
        }
        for p in series
    ]
    if not peak:
        note = f"U ovom periodu nije bilo {none_text}."
    elif weekly:
        note = (
            f"Najjača nedelja počela je {fmt.day_label(peak['date'])}: "
            f"{value_text(peak['value'])}. Označena je roze bojom."
        )
    else:
        note = (
            f"Najviše je bilo {fmt.day_label(peak['date'])}: "
            f"{value_text(peak['value'])}. Taj dan je označen roze bojom."
        )
    return {
        "title": f"{title_noun} po nedeljama" if weekly else f"{title_noun} po danima",
        "note": note,
        "bars": bars,
        "first": first_day,
        "last": last_day,
    }


def _range_text(start, end):
    return f"Od {fmt.day_label(start)} do {fmt.day_label(end)} {end.year}."


# ---------------------------------------------------------------- brojke oglasa

def _ads_values(kind, spend_by_currency, impressions, clicks, conversions):
    """Tekst za svaku brojku. None znaci da se brojka ne moze izracunati
    (na primer cena po kliku kad su kanali u razlicitim valutama)."""
    currency = next(iter(spend_by_currency)) if len(spend_by_currency) == 1 else None
    spend = sum(spend_by_currency.values())
    return {
        "spend": fmt.money_sum(spend_by_currency) if spend_by_currency else None,
        "impressions": fmt.num(impressions),
        "clicks": fmt.num(clicks),
        "ctr": fmt.percent(clicks / impressions, 2) if impressions else None,
        "cpc": fmt.money(spend / clicks, currency, 2) if currency and clicks else None,
        "cpm": fmt.money(spend / impressions * 1000, currency, 2) if currency and impressions else None,
        "conversions": fmt.conv(conversions),
        "conv_rate": fmt.percent(conversions / clicks) if clicks else None,
        "cpa": (fmt.money(spend / conversions, currency, 2) if conversions else kind["none"]) if currency else None,
    }


def _ads_labels(kind):
    """(oznaka u velikom broju, naslov kolone u tabeli) za svaku brojku."""
    return {
        "spend": ("Potrošeno na oglase", "Potrošeno"),
        "impressions": ("Puta je oglas prikazan", "Prikazi"),
        "clicks": ("Klikova na oglas", "Klikovi"),
        "ctr": ("Prikaza koji su doneli klik (CTR)", "CTR"),
        "cpc": ("Prosečna cena klika", "Cena klika"),
        "cpm": ("Cena za 1.000 prikaza", "Cena 1.000 prikaza"),
        "conversions": (kind["column"], kind["column"]),
        "conv_rate": (kind["rate"], "Stopa konverzije"),
        "cpa": (kind["cost"], kind["cost"]),
    }


def _ads_report(client, key, label, scope, totals, spend_by_currency, daily, start, end, table_title, first_column, rows):
    """Zajednicki oblik izvestaja za jedan kanal i za zbir svih kanala.

    rows: [{"name", "spend", "currency", "impressions", "clicks", "conversions"}]
    daily: [{"date", "spend", "impressions", "clicks", "conversions"}]
    """
    lay = layout.get(client, key)
    shown = [m for m, _ in layout.ADS_METRICS if m in lay["m"]]
    kind = fmt.CONV_KINDS.get(client["conv_kind"], fmt.CONV_KINDS["upit"])
    labels = _ads_labels(kind)
    conversions, clicks, impressions = totals["conversions"], totals["clicks"], totals["impressions"]
    values = _ads_values(kind, spend_by_currency, impressions, clicks, conversions)
    active = bool(impressions or clicks or sum(spend_by_currency.values()))
    when = _phrase(start, end)
    notes = []

    told = set()  # brojke koje vec pise u glavnoj recenici, pa se ne ponavljaju ispod
    if not active:
        hero = f"{when} {scope} nisu bili aktivni."
    elif "conversions" in shown:
        hero = f"{when} {scope} su vam doneli {fmt.conv(conversions)} {fmt.plural(conversions, kind['forms'])}"
        told.add("conversions")
        if "cpa" in shown and conversions and values["cpa"]:
            hero += f", po prosečnoj ceni od {values['cpa']} {kind['per']}"
            told.add("cpa")
        hero += "."
    elif "clicks" in shown:
        hero = f"{when} {scope} su vam doneli {fmt.num(clicks)} {fmt.plural(clicks, fmt.CLICK_FORMS)} ka sajtu."
        told.add("clicks")
    elif "impressions" in shown:
        hero = f"{when} {scope} su prikazani {fmt.num(impressions)} {fmt.plural(impressions, fmt.TIMES_FORMS)}."
        told.add("impressions")
    elif "spend" in shown:
        hero = f"{when} ulaganje u oglase ({label}) iznosilo je {values['spend']}."
        told.add("spend")
    else:
        hero = f"{when} {scope} su bili aktivni."

    figures = []
    if active:
        for metric in shown:
            if metric not in told and values[metric] is not None:
                figures.append({"value": values[metric], "label": labels[metric][0]})

    chart = None
    if active and "chart" in lay["s"]:
        metric = lay["chart"]
        if metric == "spend" and len(spend_by_currency) != 1:
            notes.append("Grafikon potrošnje se ne prikazuje, jer su kanali u različitim valutama.")
        else:
            currency = next(iter(spend_by_currency), "")
            text, title, none = {
                "clicks": (lambda v: f"{fmt.num(v)} {fmt.plural(v, fmt.CLICK_FORMS)}", "Klikovi", "klikova"),
                "impressions": (lambda v: f"{fmt.num(v)} {fmt.plural(v, fmt.IMPRESSION_FORMS)}", "Prikazi", "prikaza"),
                "spend": (lambda v: fmt.money(v, currency), "Potrošnja", "potrošnje"),
                "conversions": (
                    lambda v: f"{fmt.conv(v)} {fmt.plural(v, kind['forms_nom'])}",
                    kind["column"],
                    kind["forms"][2],
                ),
            }[metric]
            chart = _chart([{"date": d["date"], "value": d[metric]} for d in daily], text, title, none)

    table = None
    if active and rows and "campaigns" in lay["s"] and shown:
        def cells(name, row_values):
            return [name] + [row_values[m] if row_values[m] is not None else "–" for m in shown]

        table = {
            "title": table_title,
            "columns": [first_column] + [labels[m][1] for m in shown],
            "rows": [
                cells(
                    r["name"],
                    _ads_values(kind, {r["currency"]: r["spend"]}, r["impressions"], r["clicks"], r["conversions"]),
                )
                for r in rows
            ],
            "total": cells("Ukupno", values),
        }

    return {
        "kind": "ads",
        "hero": hero,
        "figures": figures,
        "chart": chart,
        "table": table,
        "range_text": _range_text(start, end),
        "last_label": fmt.day_label(end),
    }, notes


# ---------------------------------------------------------------- jedan kanal

def _single(client, mod, source, start, end, data_range):
    notes = []
    payload, error = platforms.daily(mod.KEY, source["account_id"], *data_range)
    if payload is None:
        return None, [f"{mod.NAME} nije vratio podatke: {error}"]
    if error:
        notes.append(f"{mod.NAME}: prikazani su ranije sačuvani podaci, jer servis trenutno javlja: {error}")

    summary = platforms.summarize(payload, start, end, client["conv_kind"])
    currency = source["currency"] or "EUR"
    report, more = _ads_report(
        client,
        mod.KEY,
        mod.NAME,
        f"{mod.NAME} oglasi",
        summary["totals"],
        {currency: summary["totals"]["spend"]},
        summary["daily"],
        summary["start"],
        summary["end"],
        "Rezultati po kampanjama",
        "Kampanja",
        [{**c, "currency": currency} for c in summary["campaigns"]],
    )
    notes += more

    if getattr(mod, "HAS_SEARCH_TERMS", False) and "terms" in layout.get(client, mod.KEY)["s"]:
        try:
            terms = platforms.search_terms(source["account_id"], summary["start"], summary["end"])
            report["terms"] = [
                {"term": t["term"], "clicks": fmt.num(t["clicks"]), "word": fmt.plural(t["clicks"], fmt.CLICK_FORMS)}
                for t in terms
            ]
        except SourceError as exc:
            notes.append(f"Pojmovi pretrage nisu učitani: {exc}")
    return report, notes


# ---------------------------------------------------------------- svi kanali zajedno

def _combined(client, sources, start, end, data_range):
    notes, loaded = [], []
    ads = visible_ads(sources)
    errors = platforms.warm([(mod.KEY, src["account_id"], *data_range) for mod, src in ads])
    for mod, src in ads:
        payload, _ = platforms.daily(mod.KEY, src["account_id"], *data_range, fetch=False)
        error = errors.get((mod.KEY, src["account_id"]))
        if payload is None:
            notes.append(f"{mod.NAME} nije u zbiru, jer nije vratio podatke: {error}")
            continue
        if error:
            notes.append(f"{mod.NAME}: u zbiru su ranije sačuvani podaci, jer servis trenutno javlja: {error}")
        loaded.append((mod, src, payload))
    if not loaded:
        return None, notes

    totals = {"impressions": 0, "clicks": 0, "conversions": 0.0}
    spend_by_currency, per_day, rows = {}, {}, []
    for mod, src, payload in loaded:
        summary = platforms.summarize(payload, start, end, client["conv_kind"])
        currency = src["currency"] or "EUR"
        for key in totals:
            totals[key] += summary["totals"][key]
        spend_by_currency[currency] = spend_by_currency.get(currency, 0) + summary["totals"]["spend"]
        for point in summary["daily"]:
            day = per_day.setdefault(
                point["date"], {"date": point["date"], "spend": 0.0, "impressions": 0, "clicks": 0, "conversions": 0.0}
            )
            for metric in ("spend", "impressions", "clicks", "conversions"):
                day[metric] += point[metric]
        rows.append(
            {
                "name": mod.NAME,
                "spend": summary["totals"]["spend"],
                "currency": currency,
                "impressions": summary["totals"]["impressions"],
                "clicks": summary["totals"]["clicks"],
                "conversions": summary["totals"]["conversions"],
            }
        )
    totals["conversions"] = round(totals["conversions"], 2)
    if len(spend_by_currency) > 1:
        notes.append("Kanali su u različitim valutama, pa se potrošnja prikazuje odvojeno i bez zbirne cene.")

    report, more = _ads_report(
        client,
        COMBINED,
        "svi kanali",
        "oglasi na svim kanalima",
        totals,
        spend_by_currency,
        [per_day[day] for day in sorted(per_day)],
        start,
        end,
        "Rezultati po kanalima",
        "Kanal",
        rows,
    )
    return report, notes + more


# ---------------------------------------------------------------- sajt (Analytics)

SITE_LABELS = {
    "sessions": "Poseta sajtu",
    "users": "Posetilaca",
    "new_users": "Novih posetilaca",
    "avg_time": "Prosečno trajanje posete",
    "engagement": "Angažovanih poseta",
    "page_views": "Pregleda strana",
    "key_events": "Konverzija na sajtu",
}


def _site(client, source, start, end):
    lay = layout.get(client, ga4.KEY)
    shown = [m for m, _ in layout.SITE_METRICS if m in lay["m"]]
    days = (end - start).days + 1
    try:
        data = ga4.report(source["account_id"], start, end)
    except SourceError as exc:
        return None, [f"Analytics nije vratio podatke: {exc}"]

    sessions = data["sessions"]
    when = _phrase(start, end)
    values = {
        "sessions": fmt.num(sessions),
        "users": fmt.num(data["users"]),
        "new_users": fmt.num(data["new_users"]),
        "avg_time": fmt.duration(data["avg_seconds"]),
        "engagement": fmt.percent(data["engagement"]),
        "page_views": fmt.num(data["page_views"]),
        "key_events": fmt.conv(data["key_events"]),
    }
    told = set()
    if not sessions:
        hero = f"{when} Analytics nije zabeležio posete sajtu."
    elif "sessions" in shown:
        hero = f"{when} vaš sajt je imao {values['sessions']} {fmt.plural(sessions, fmt.VISIT_FORMS_OBJECT)}."
        told.add("sessions")
    elif "users" in shown:
        hero = f"{when} vaš sajt je posetilo {values['users']} {fmt.plural(data['users'], fmt.USER_FORMS)}."
        told.add("users")
    elif "key_events" in shown:
        hero = (
            f"{when} Analytics je na vašem sajtu zabeležio {values['key_events']} "
            f"{fmt.plural(data['key_events'], fmt.KEY_EVENT_FORMS_OBJECT)}."
        )
        told.add("key_events")
    else:
        hero = f"{when} ovako su izgledale posete vašem sajtu."

    chart = None
    if sessions and "chart" in lay["s"]:
        metric = lay["chart"]
        text, title, none = {
            "sessions": (lambda v: f"{fmt.num(v)} {fmt.plural(v, fmt.VISIT_FORMS)}", "Posete", "poseta"),
            "users": (lambda v: f"{fmt.num(v)} {fmt.plural(v, fmt.USER_FORMS)}", "Posetioci", "posetilaca"),
            "key_events": (lambda v: f"{fmt.conv(v)} {fmt.plural(v, fmt.KEY_EVENT_FORMS)}", "Konverzije", "konverzija"),
        }[metric]
        series = []
        for i in range(days):
            day = start + timedelta(days=i)
            series.append({"date": day, "value": data["by_day"].get(day.strftime("%Y%m%d"), {}).get(metric, 0)})
        chart = _chart(series, text, title, none)

    return {
        "kind": "site",
        "hero": hero,
        "figures": [{"value": values[m], "label": SITE_LABELS[m]} for m in shown if m not in told] if sessions else [],
        "chart": chart,
        "channels": [
            {
                "name": ga4.CHANNELS.get(c["name"], c["name"]),
                "value": f"{fmt.num(c['sessions'])} {fmt.plural(c['sessions'], fmt.VISIT_FORMS)}",
            }
            for c in data["channels"]
        ]
        if "channels" in lay["s"]
        else [],
        "pages": [{"name": p["path"], "value": f"{fmt.num(p['views'])} pregleda"} for p in data["pages"]]
        if "pages" in lay["s"]
        else [],
        "range_text": _range_text(start, end),
        "last_label": fmt.day_label(end),
    }, []
