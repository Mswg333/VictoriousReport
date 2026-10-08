"""Sta klijent vidi u svakom izvestaju.

Za svaki kanal (i za zbirni izvestaj) tim posebno bira koje brojke i koje
delove izvestaja klijent vidi. Izbor se cuva kao JSON u koloni
portal_clients.layout, u obliku:

    {"google_ads": {"m": ["spend", "clicks"], "s": ["chart"], "chart": "clicks"}, ...}

m = brojke, s = delovi izvestaja, chart = sta grafikon prikazuje.
Klijent bez sacuvanog izbora dobija podrazumevani, izveden iz starih
podesavanja (show_spend, show_clicks...), pa se postojeci izvestaji ne menjaju.
"""
import json

COMBINED = "ukupno"
SITE = "ga4"

# Brojke za kanale oglasavanja. Redosled je i redosled prikaza u izvestaju.
ADS_METRICS = [
    ("spend", "Potrošeno"),
    ("impressions", "Prikazi"),
    ("clicks", "Klikovi"),
    ("ctr", "CTR (procenat klikova)"),
    ("cpc", "Cena po kliku"),
    ("cpm", "Cena za 1.000 prikaza"),
    ("conversions", "Konverzije"),
    ("conv_rate", "Stopa konverzije"),
    ("cpa", "Cena po konverziji"),
]
ADS_SECTIONS = [
    ("chart", "Grafikon po danima"),
    ("campaigns", "Tabela kampanja"),
    ("terms", "Pojmovi pretrage"),
]
ADS_CHART = [("clicks", "Klikovi"), ("impressions", "Prikazi"), ("spend", "Potrošnja"), ("conversions", "Konverzije")]

COMBINED_SECTIONS = [("chart", "Grafikon po danima"), ("campaigns", "Tabela po kanalima")]

SITE_METRICS = [
    ("sessions", "Posete"),
    ("users", "Posetioci"),
    ("new_users", "Novi posetioci"),
    ("avg_time", "Prosečno trajanje posete"),
    ("engagement", "Stopa angažovanja"),
    ("page_views", "Pregledi strana"),
    ("key_events", "Konverzije iz Analytics-a"),
]
SITE_SECTIONS = [
    ("chart", "Grafikon po danima"),
    ("channels", "Odakle dolaze posete"),
    ("pages", "Najgledanije strane"),
]
SITE_CHART = [("sessions", "Posete"), ("users", "Posetioci"), ("key_events", "Konverzije")]


def options(key, has_terms=False):
    """(brojke, delovi, izbor grafikona) koji postoje za ovaj izvestaj."""
    if key == SITE:
        return SITE_METRICS, SITE_SECTIONS, SITE_CHART
    if key == COMBINED:
        return ADS_METRICS, COMBINED_SECTIONS, ADS_CHART
    sections = ADS_SECTIONS if has_terms else [s for s in ADS_SECTIONS if s[0] != "terms"]
    return ADS_METRICS, sections, ADS_CHART


def _stored(client):
    try:
        data = json.loads(client.get("layout") or "{}")
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def default(client, key):
    """Izbor za klijenta koji jos nije podesavan, prema starim prekidacima."""
    if key == SITE:
        return {"m": ["sessions", "users", "avg_time"], "s": ["chart", "channels", "pages"], "chart": "sessions"}
    spend, clicks, conv = client.get("show_spend", 1), client.get("show_clicks", 1), client.get("show_conv", 1)
    metrics = []
    if spend:
        metrics.append("spend")
    if clicks:
        metrics += ["impressions", "clicks"]
    if spend and clicks:
        metrics.append("cpc")
    if conv:
        metrics.append("conversions")
    if conv and spend:
        metrics.append("cpa")
    sections = []
    if clicks:
        sections.append("chart")
    if key == COMBINED or client.get("show_campaigns", 1):
        sections.append("campaigns")
    if key != COMBINED and client.get("show_terms", 0):
        sections.append("terms")
    return {"m": metrics, "s": sections, "chart": "clicks"}


def get(client, key):
    """Izbor za jedan izvestaj: {"m": set, "s": set, "chart": str}."""
    entry = _stored(client).get(key) or default(client, key)
    metrics, sections, charts = options(key, has_terms=True)
    allowed_charts = [c for c, _ in charts]
    chart = entry.get("chart") if entry.get("chart") in allowed_charts else allowed_charts[0]
    return {
        "m": {m for m in entry.get("m", []) if m in dict(metrics)},
        "s": {s for s in entry.get("s", []) if s in dict(sections)},
        "chart": chart,
    }


def from_form(client, form, keys):
    """Novi JSON za kolonu layout. Menja samo izvestaje cija su polja bila na strani."""
    data = _stored(client)
    for key in keys:
        if not form.get(f"lay_{key}"):
            continue  # izbor za ovaj kanal nije bio prikazan, pa ostaje kakav je bio
        metrics, sections, charts = options(key, has_terms=True)
        chart = form.get(f"chart_{key}", "")
        data[key] = {
            "m": [m for m, _ in metrics if m in form.getlist(f"m_{key}")],
            "s": [s for s, _ in sections if s in form.getlist(f"s_{key}")],
            "chart": chart if chart in dict(charts) else charts[0][0],
        }
    return json.dumps(data, ensure_ascii=False)
