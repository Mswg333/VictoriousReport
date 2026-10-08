"""Sastavljanje izvestaja koje vidi klijent.

Klijent ima po jedan izvestaj za svaki kanal koji mu je ukljucen, zbirni
izvestaj preko svih kanala oglasavanja (ako je ukljucen) i izvestaj o
posetama sajtu iz Analytics-a.

Ovde se samo racuna i priprema tekst; izgled je u templates/report.html.
"""
from datetime import timedelta

import fmt
import platforms
from platforms import base, ga4
from platforms.base import SourceError

COMBINED = "ukupno"


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
        return _site(sources[ga4.KEY], start, end)
    return _single(client, platforms.get(tab), sources[tab], start, end, data_range)


def _phrase(start, end):
    return fmt.period_phrase(start, end, base.yesterday())


# ---------------------------------------------------------------- delovi izvestaja

def _chart(series, forms, title_noun):
    """Stubici po danima, a za periode duze od dva meseca po nedeljama."""
    if len(series) < 2:
        return None  # za jedan dan grafikon nema smisla
    weekly = len(series) > 62
    first_day = fmt.day_label(series[0]["date"]) if series else ""
    last_day = fmt.day_label(series[-1]["date"]) if series else ""
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
            "tip": f"{fmt.day_label(p['date'])}: {fmt.num(p['value'])} {fmt.plural(p['value'], forms)}",
            "peak": p is peak,
        }
        for p in series
    ]
    if not peak:
        note = f"U ovom periodu nije bilo {forms[2]}."
    elif weekly:
        note = (
            f"Najjača nedelja počela je {fmt.day_label(peak['date'])}: "
            f"{fmt.num(peak['value'])} {fmt.plural(peak['value'], forms)}. Označena je roze bojom."
        )
    else:
        note = (
            f"Najviše je bilo {fmt.day_label(peak['date'])}: "
            f"{fmt.num(peak['value'])} {fmt.plural(peak['value'], forms)}. Taj dan je označen roze bojom."
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


def _ads_report(client, scope, totals, spend_by_currency, series, start, end, table_title, first_column, rows):
    """Zajednicki oblik izvestaja za jedan kanal i za zbir svih kanala.

    rows: [{"name", "spend", "currency", "clicks", "conversions"}]
    """
    kind = fmt.CONV_KINDS.get(client["conv_kind"], fmt.CONV_KINDS["upit"])
    conversions, clicks = totals["conversions"], totals["clicks"]
    one_currency = next(iter(spend_by_currency)) if len(spend_by_currency) == 1 else None
    spend_total = sum(spend_by_currency.values())
    cpa = fmt.money(spend_total / conversions, one_currency, 2) if one_currency and conversions else None
    cpc = fmt.money(spend_total / clicks, one_currency, 2) if one_currency and clicks else None
    active = bool(totals["impressions"] or clicks or spend_total)
    when = _phrase(start, end)

    if not active:
        hero = f"{when} {scope} nisu bili aktivni."
    elif client["show_conv"] and client["show_spend"] and cpa:
        hero = (
            f"{when} {scope} su vam doneli {fmt.conv(conversions)} "
            f"{fmt.plural(conversions, kind['forms'])}, po prosečnoj ceni od {cpa} {kind['per']}."
        )
    elif client["show_conv"]:
        hero = (
            f"{when} {scope} su vam doneli {fmt.conv(conversions)} "
            f"{fmt.plural(conversions, kind['forms'])}."
        )
    else:
        hero = (
            f"{when} {scope} su vam doneli {fmt.num(clicks)} "
            f"{fmt.plural(clicks, fmt.CLICK_FORMS)} ka sajtu."
        )

    figures = []
    if active and client["show_spend"]:
        figures.append({"value": fmt.money_sum(spend_by_currency), "label": "Potrošeno na oglase"})
    if active and client["show_clicks"]:
        figures.append({"value": fmt.num(totals["impressions"]), "label": "Puta je oglas prikazan"})
        figures.append({"value": fmt.num(clicks), "label": "Klikova na oglas"})
    if active and client["show_spend"] and client["show_clicks"] and cpc:
        figures.append({"value": cpc, "label": "Prosečna cena klika"})

    table = None
    if active and rows:
        columns = [first_column]
        if client["show_spend"]:
            columns.append("Potrošeno")
        if client["show_clicks"]:
            columns.append("Klikovi")
        if client["show_conv"]:
            columns.append(kind["column"])
        if client["show_conv"] and client["show_spend"]:
            columns.append(kind["cost"])

        def cells(name, spend_text, row_clicks, row_conv, row_cpa):
            out = [name]
            if client["show_spend"]:
                out.append(spend_text)
            if client["show_clicks"]:
                out.append(fmt.num(row_clicks))
            if client["show_conv"]:
                out.append(fmt.conv(row_conv))
            if client["show_conv"] and client["show_spend"]:
                out.append(row_cpa)
            return out

        table = {
            "title": table_title,
            "columns": columns,
            "rows": [
                cells(
                    r["name"],
                    fmt.money(r["spend"], r["currency"]),
                    r["clicks"],
                    r["conversions"],
                    fmt.money(r["spend"] / r["conversions"], r["currency"], 2) if r["conversions"] else kind["none"],
                )
                for r in rows
            ],
            "total": cells("Ukupno", fmt.money_sum(spend_by_currency), clicks, conversions, cpa or ("" if conversions else kind["none"])),
        }

    return {
        "kind": "ads",
        "hero": hero,
        "figures": figures,
        "chart": _chart(series, fmt.CLICK_FORMS, "Klikovi") if active and client["show_clicks"] else None,
        "table": table,
        "range_text": _range_text(start, end),
        "last_label": fmt.day_label(end),
    }


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
    report = _ads_report(
        client,
        f"{mod.NAME} oglasi",
        summary["totals"],
        {currency: summary["totals"]["spend"]},
        summary["series"],
        summary["start"],
        summary["end"],
        "Rezultati po kampanjama",
        "Kampanja",
        [{**c, "currency": currency} for c in summary["campaigns"]] if client["show_campaigns"] else [],
    )

    if getattr(mod, "HAS_SEARCH_TERMS", False) and client["show_terms"]:
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
        for point in summary["series"]:
            per_day[point["date"]] = per_day.get(point["date"], 0) + point["value"]
        rows.append(
            {
                "name": mod.NAME,
                "spend": summary["totals"]["spend"],
                "currency": currency,
                "clicks": summary["totals"]["clicks"],
                "conversions": summary["totals"]["conversions"],
            }
        )
    totals["conversions"] = round(totals["conversions"], 2)
    if len(spend_by_currency) > 1:
        notes.append("Kanali su u različitim valutama, pa se potrošnja prikazuje odvojeno i bez zbirne cene.")

    report = _ads_report(
        client,
        "oglasi na svim kanalima",
        totals,
        spend_by_currency,
        [{"date": day, "value": per_day[day]} for day in sorted(per_day)],
        start,
        end,
        "Rezultati po kanalima",
        "Kanal",
        rows,
    )
    return report, notes


# ---------------------------------------------------------------- sajt (Analytics)

def _site(source, start, end):
    days = (end - start).days + 1
    try:
        data = ga4.report(source["account_id"], start, end)
    except SourceError as exc:
        return None, [f"Analytics nije vratio podatke: {exc}"]

    sessions = data["sessions"]
    series = []
    for i in range(days):
        day = start + timedelta(days=i)
        series.append({"date": day, "value": data["by_day"].get(day.strftime("%Y%m%d"), 0)})
    if sessions:
        hero = (
            f"{_phrase(start, end)} vaš sajt je imao {fmt.num(sessions)} "
            f"{fmt.plural(sessions, fmt.VISIT_FORMS_OBJECT)}."
        )
    else:
        hero = f"{_phrase(start, end)} Analytics nije zabeležio posete sajtu."
    return {
        "kind": "site",
        "hero": hero,
        "figures": [
            {"value": fmt.num(sessions), "label": "Poseta sajtu"},
            {"value": fmt.num(data["users"]), "label": "Posetilaca"},
            {"value": fmt.duration(data["avg_seconds"]), "label": "Prosečno trajanje posete"},
        ]
        if sessions
        else [],
        "chart": _chart(series, fmt.VISIT_FORMS, "Posete") if sessions else None,
        "channels": [
            {
                "name": ga4.CHANNELS.get(c["name"], c["name"]),
                "value": f"{fmt.num(c['sessions'])} {fmt.plural(c['sessions'], fmt.VISIT_FORMS)}",
            }
            for c in data["channels"]
        ],
        "pages": [{"name": p["path"], "value": f"{fmt.num(p['views'])} pregleda"} for p in data["pages"]],
        "range_text": _range_text(start, end),
        "last_label": fmt.day_label(end),
    }, []
