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


def fetch_daily(token, account_id, start, end):
    """Brojke po kampanji i danu. Klikovi su klikovi na link (ne svi klikovi na objavu)."""
    rows = _paged(
        f"{BASE}/act_{format_id(account_id)}/insights",
        token,
        {
            "level": "campaign",
            "time_increment": 1,
            "time_range": json.dumps({"since": start.isoformat(), "until": end.isoformat()}),
            "fields": "campaign_id,campaign_name,spend,impressions,inline_link_clicks,actions,action_values",
            "limit": 500,
        },
    )
    out = []
    for row in rows:
        day = row.get("date_start")
        if not day:
            continue
        actions = row.get("actions") or []
        out.append(
            [
                day,
                str(row.get("campaign_id", "")),
                row.get("campaign_name", ""),
                float(row.get("spend") or 0),
                int(float(row.get("impressions") or 0)),
                int(float(row.get("inline_link_clicks") or 0)),
                # Meta nema jedan broj "konverzija": cuvamo i upite i kupovine,
                # a izvestaj bira prema podesavanju klijenta.
                {
                    "upit": _action(actions, "lead", "onsite_conversion.lead_grouped", "offsite_conversion.fb_pixel_lead"),
                    "kupovina": _action(actions, *PURCHASE_TYPES),
                },
                # Vrednost kupovina (Meta je prijavljuje samo za kupovine).
                _action(row.get("action_values") or [], *PURCHASE_TYPES),
            ]
        )
    return {"start": start.isoformat(), "end": end.isoformat(), "rows": out}


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
