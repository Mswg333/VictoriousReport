"""TikTok Ads: citanje preko TikTok API for Business (v1.3).

Potrebna je TikTok developerska aplikacija (TIKTOK_APP_ID i
TIKTOK_APP_SECRET). Oglasivac zatim na TikTok-u odobri pristup, a portal
dobijeni kod menja za token koji cuva sifrovan. Salju se samo zahtevi za
citanje.
"""
import json
import os
from datetime import timedelta
from urllib.parse import urlencode

import connections
from platforms.base import SourceError, body, call

KEY = "tiktok"
NAME = "TikTok Ads"
CONNECTION = "tiktok"
HAS_SEARCH_TERMS = False

APP_ID = os.environ.get("TIKTOK_APP_ID", "").strip()
APP_SECRET = os.environ.get("TIKTOK_APP_SECRET", "").strip()

AUTH_URL = "https://business-api.tiktok.com/portal/auth"
BASE = "https://business-api.tiktok.com/open_api/v1.3"
CHUNK_DAYS = 30   # dnevni izvestaj dozvoljava najvise 30 dana po zahtevu
PAGE_SIZE = 1000


def format_id(value):
    return str(value or "")


def configured():
    return bool(APP_ID and APP_SECRET)


def accounts_scope():
    return ""


def auth_url(redirect_uri, state):
    return AUTH_URL + "?" + urlencode({"app_id": APP_ID, "state": state, "redirect_uri": redirect_uri})


def _check(resp):
    data = body(resp)
    if resp.status_code != 200 or data.get("code") != 0:
        raise SourceError(str(data.get("message") or f"HTTP {resp.status_code}"))
    return data.get("data") or {}


def exchange(auth_code):
    """Menja kod dobijen posle odobrenja za token."""
    data = _check(
        call(
            "POST",
            f"{BASE}/oauth2/access_token/",
            json={"app_id": APP_ID, "secret": APP_SECRET, "auth_code": auth_code},
        )
    )
    if not data.get("access_token"):
        raise SourceError("TikTok nije vratio token.")
    return data


def prepare():
    return connections.secret(CONNECTION)["access_token"]


def _get(path, token, params):
    return _check(call("GET", BASE + path, headers={"Access-Token": token}, params=params))


def list_accounts(token):
    listed = _get("/oauth2/advertiser/get/", token, {"app_id": APP_ID, "secret": APP_SECRET}).get("list", [])
    names = {str(a.get("advertiser_id")): a.get("advertiser_name") or "" for a in listed if a.get("advertiser_id")}
    ids = list(names)
    currencies = {}
    for i in range(0, len(ids), 100):
        info = _get(
            "/advertiser/info/",
            token,
            {
                "advertiser_ids": json.dumps(ids[i:i + 100]),
                "fields": json.dumps(["advertiser_id", "name", "currency"]),
            },
        ).get("list", [])
        for item in info:
            currencies[str(item.get("advertiser_id"))] = item.get("currency")
    accounts = [
        {"id": aid, "name": names[aid] or aid, "currency": currencies.get(aid) or "USD"} for aid in ids
    ]
    return sorted(accounts, key=lambda a: a["name"].lower())


def fetch_daily(token, account_id, start, end):
    """Brojke po kampanji i danu, u delovima od po 30 dana."""
    out = []
    chunk_end = end
    first = start
    while chunk_end >= first:
        chunk_start = max(first, chunk_end - timedelta(days=CHUNK_DAYS - 1))
        page = 1
        while True:
            data = _get(
                "/report/integrated/get/",
                token,
                {
                    "advertiser_id": format_id(account_id),
                    "report_type": "BASIC",
                    "data_level": "AUCTION_CAMPAIGN",
                    "dimensions": json.dumps(["campaign_id", "stat_time_day"]),
                    "metrics": json.dumps(
                        ["campaign_name", "spend", "impressions", "clicks", "conversion", "complete_payment_roas"]
                    ),
                    "start_date": chunk_start.isoformat(),
                    "end_date": chunk_end.isoformat(),
                    "page": page,
                    "page_size": PAGE_SIZE,
                },
            )
            for row in data.get("list", []):
                dims, metrics = row.get("dimensions", {}), row.get("metrics", {})
                day = str(dims.get("stat_time_day", ""))[:10]
                if len(day) != 10:
                    continue
                out.append(
                    [
                        day,
                        str(dims.get("campaign_id", "")),
                        metrics.get("campaign_name", ""),
                        float(metrics.get("spend") or 0),
                        int(float(metrics.get("impressions") or 0)),
                        int(float(metrics.get("clicks") or 0)),
                        float(metrics.get("conversion") or 0),
                        # TikTok daje ROAS kupovina, a vrednost je ROAS puta potrosnja.
                        float(metrics.get("complete_payment_roas") or 0) * float(metrics.get("spend") or 0),
                    ]
                )
            total_pages = int(data.get("page_info", {}).get("total_page") or 1)
            if page >= total_pages or page >= 50:
                break
            page += 1
        chunk_end = chunk_start - timedelta(days=1)
    return {"start": start.isoformat(), "end": end.isoformat(), "rows": out}


def connected():
    return connections.get(CONNECTION) is not None
