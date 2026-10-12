"""Meta Ads (Facebook i Instagram): citanje preko Marketing API-ja.

Portal koristi token sistemskog korisnika iz Business Manager-a, sa
dozvolom ads_read. Token se upisuje na strani "Izvor podataka" i cuva
sifrovan. Salju se samo zahtevi za citanje.
"""
import json
import os

import connections
from platforms.base import SourceError, body, call

KEY = "meta"
NAME = "Meta Ads"
CONNECTION = "meta"
HAS_SEARCH_TERMS = False

API_VERSION = os.environ.get("META_API_VERSION", "v25.0")
HOST = "https://graph.facebook.com/"
BASE = HOST + API_VERSION
MAX_PAGES = 200


def format_id(value):
    return str(value or "")


def configured():
    return True  # nema podesavanja u okruzenju; dovoljan je token


def accounts_scope():
    return ""


def prepare():
    return connections.secret(CONNECTION)["access_token"]


def _get(url, token, params=None):
    resp = call("GET", url, headers={"Authorization": "Bearer " + token}, params=params)
    data = body(resp)
    if resp.status_code != 200 or "error" in data:
        error = data.get("error", {})
        raise SourceError(error.get("error_user_msg") or error.get("message") or f"HTTP {resp.status_code}")
    return data


def _paged(url, token, params):
    """Skuplja sve strane odgovora. Sledeca strana se prati samo ka Meta serveru."""
    rows = []
    for _ in range(MAX_PAGES):
        data = _get(url, token, params)
        rows.extend(data.get("data", []))
        url, params = data.get("paging", {}).get("next"), None
        if not url or not url.startswith(HOST):
            break
    return rows


def whoami(token):
    """Ime naloga kome token pripada (sluzi i kao provera da token radi)."""
    return _get(f"{BASE}/me", token, {"fields": "name"}).get("name", "Meta nalog")


def list_accounts(token):
    rows = _paged(
        f"{BASE}/me/adaccounts",
        token,
        {"fields": "account_id,name,currency,account_status", "limit": 200},
    )
    accounts = [
        {
            "id": str(row.get("account_id", "")),
            "name": row.get("name") or str(row.get("account_id", "")),
            "currency": row.get("currency") or "EUR",
        }
        for row in rows
        if row.get("account_id")
    ]
    return sorted(accounts, key=lambda a: a["name"].lower())


def _action(actions, *types):
    """Vrednost prve pronadjene vrste akcije (redom po prioritetu)."""
    for wanted in types:
        for action in actions:
            if action.get("action_type") == wanted:
                try:
                    return float(action.get("value", 0))
                except (TypeError, ValueError):
                    return 0.0
    return 0.0


PURCHASE_TYPES = ("omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase")
LEAD_TYPES = ("lead", "onsite_conversion.lead_grouped", "offsite_conversion.fb_pixel_lead")
LINK_CLICK = "link_click"  # rezultat se racuna iz inline_link_clicks

# "Rezultat" kao u Ads Manager-u: zavisi od cilja kampanje.
# cilj kampanje -> (vrsta rezultata, vrste akcija koje se broje)
RESULTS = {
    "leads": (("OUTCOME_LEADS", "LEAD_GENERATION"), LEAD_TYPES),
    "purchases": (("OUTCOME_SALES", "CONVERSIONS", "PRODUCT_CATALOG_SALES"), PURCHASE_TYPES),
    "link_clicks": (("OUTCOME_TRAFFIC", "LINK_CLICKS"), (LINK_CLICK,)),
    "engagement": (("OUTCOME_ENGAGEMENT", "POST_ENGAGEMENT", "PAGE_LIKES", "EVENT_RESPONSES"), ("post_engagement",)),
    "messages": (("MESSAGES",), ("onsite_conversion.messaging_conversation_started_7d",)),
    "app_installs": (("OUTCOME_APP_PROMOTION", "APP_INSTALLS"), ("mobile_app_install", "omni_app_install")),
    "video_views": (("VIDEO_VIEWS",), ("video_view",)),
}
# Kako se rezultat zove u izvestaju (1, 2-4, 5+).
RESULT_FORMS = {
    "leads": ("upit", "upita", "upita"),
    "purchases": ("kupovina", "kupovine", "kupovina"),
    "link_clicks": ("klik na link", "klika na link", "klikova na link"),
    "engagement": ("interakcija", "interakcije", "interakcija"),
    "messages": ("započet razgovor", "započeta razgovora", "započetih razgovora"),
    "app_installs": ("instalacija", "instalacije", "instalacija"),
    "video_views": ("pregled videa", "pregleda videa", "pregleda videa"),
}


def result_kind(objective):
    """Vrsta rezultata za cilj kampanje, ili None (npr. svest o brendu, gde je rezultat doseg)."""
    for kind, (objectives, _) in RESULTS.items():
        if objective in objectives:
            return kind
    return None


def _budget(value):
    """Meta salje budzet u najmanjoj jedinici valute (centi)."""
    try:
        return float(value) / 100 if value not in (None, "", "0") else None
    except (TypeError, ValueError):
        return None


def fetch_campaigns(token, account_id):
    """Cilj i trenutni budzet svake kampanje: {id: {"result", "daily", "lifetime"}}."""
    rows = _paged(
        f"{BASE}/act_{format_id(account_id)}/campaigns",
        token,
        {"fields": "id,objective,daily_budget,lifetime_budget", "limit": 500},
    )
    return {
        str(row.get("id", "")): {
            "result": result_kind(row.get("objective")),
            "daily": _budget(row.get("daily_budget")),
            "lifetime": _budget(row.get("lifetime_budget")),
        }
        for row in rows
    }


def fetch_daily(token, account_id, start, end):
    """Brojke po kampanji i danu. Klikovi su klikovi na link (ne svi klikovi na objavu)."""
    campaigns = fetch_campaigns(token, account_id)
    rows = _paged(
        f"{BASE}/act_{format_id(account_id)}/insights",
        token,
        {
            "level": "campaign",
            "time_increment": 1,
            "time_range": json.dumps({"since": start.isoformat(), "until": end.isoformat()}),
            "fields": "campaign_id,campaign_name,spend,impressions,inline_link_clicks,clicks,actions,action_values",
            "limit": 500,
        },
    )
    out = []
    for row in rows:
        day = row.get("date_start")
        if not day:
            continue
        actions = row.get("actions") or []
        campaign_id = str(row.get("campaign_id", ""))
        link_clicks = int(float(row.get("inline_link_clicks") or 0))
        kind = campaigns.get(campaign_id, {}).get("result")
        if kind == "link_clicks":
            results = float(link_clicks)
        elif kind:
            results = _action(actions, *RESULTS[kind][1])
        else:
            results = 0.0
        out.append(
            [
                day,
                campaign_id,
                row.get("campaign_name", ""),
                float(row.get("spend") or 0),
                int(float(row.get("impressions") or 0)),
                link_clicks,
                # Meta nema jedan broj "konverzija": cuvamo i upite i kupovine,
                # a izvestaj bira prema podesavanju klijenta.
                {
                    "upit": _action(actions, *LEAD_TYPES),
                    "kupovina": _action(actions, *PURCHASE_TYPES),
                },
                # Vrednost kupovina (Meta je prijavljuje samo za kupovine).
                _action(row.get("action_values") or [], *PURCHASE_TYPES),
                # Dodatne brojke koje postoje samo kod Mete.
                {
                    "results": results,
                    "clicks_all": int(float(row.get("clicks") or 0)),
                    "landing_views": _action(actions, "landing_page_view", "omni_landing_page_view"),
                },
            ]
        )
    return {"start": start.isoformat(), "end": end.isoformat(), "rows": out, "campaigns": campaigns}


RANKINGS = {
    "ABOVE_AVERAGE": "Iznad proseka",
    "AVERAGE": "Prosečno",
    "BELOW_AVERAGE_35": "Ispod proseka (donjih 35 %)",
    "BELOW_AVERAGE_20": "Ispod proseka (donjih 20 %)",
    "BELOW_AVERAGE_10": "Ispod proseka (donjih 10 %)",
}


def fetch_rankings(token, account_id, start, end, limit=10):
    """Ocene oglasa (kvalitet, angazovanje, konverzije) za oglase sa najvise prikaza.

    Meta ih daje samo na nivou oglasa i samo za oglase sa dovoljno prikaza;
    ostali dobijaju UNKNOWN i ne prikazuju se.
    """
    rows = _paged(
        f"{BASE}/act_{format_id(account_id)}/insights",
        token,
        {
            "level": "ad",
            "time_range": json.dumps({"since": start.isoformat(), "until": end.isoformat()}),
            "fields": "ad_id,ad_name,impressions,quality_ranking,engagement_rate_ranking,conversion_rate_ranking",
            "limit": 500,
        },
    )
    out = []
    for row in sorted(rows, key=lambda r: int(float(r.get("impressions") or 0)), reverse=True):
        ranks = [row.get(f) for f in ("quality_ranking", "engagement_rate_ranking", "conversion_rate_ranking")]
        if not any(r in RANKINGS for r in ranks):
            continue
        out.append({"name": row.get("ad_name") or row.get("ad_id", ""), "ranks": [RANKINGS.get(r, "Nema ocene") for r in ranks]})
        if len(out) >= limit:
            break
    return out


def fetch_reach(token, account_id, start, end):
    """Doseg (broj razlicitih ljudi) i frekvencija za ceo period.

    Doseg se ne sabira po danima ni po kampanjama (isti covek moze da vidi vise
    kampanja), pa se za svaki period trazi posebno: za nalog i za kampanje.
    """
    url = f"{BASE}/act_{format_id(account_id)}/insights"
    time_range = json.dumps({"since": start.isoformat(), "until": end.isoformat()})
    account = _paged(url, token, {"level": "account", "time_range": time_range, "fields": "reach,frequency"})
    campaigns = _paged(
        url,
        token,
        {"level": "campaign", "time_range": time_range, "fields": "campaign_id,reach,frequency", "limit": 500},
    )
    total = account[0] if account else {}
    return {
        "reach": int(float(total.get("reach") or 0)),
        "frequency": float(total.get("frequency") or 0),
        "campaigns": {
            str(c.get("campaign_id", "")): [int(float(c.get("reach") or 0)), float(c.get("frequency") or 0)]
            for c in campaigns
        },
    }


def connected():
    return connections.get(CONNECTION) is not None
