"""Google Analytics (GA4): posete sajtu. Koristi isti Google nalog kao Google Ads."""
import re

import google_auth
from platforms.base import LIST_TTL, SourceError, body, cached, call, data_ttl

KEY = "ga4"
NAME = "Sajt (Analytics)"

DATA_BASE = "https://analyticsdata.googleapis.com/v1beta"
ADMIN_BASE = "https://analyticsadmin.googleapis.com/v1beta"

# Google-ovi nazivi grupa kanala, prevedeni za klijenta.
CHANNELS = {
    "Paid Search": "Plaćena pretraga",
    "Organic Search": "Organska pretraga",
    "Paid Social": "Plaćene društvene mreže",
    "Organic Social": "Društvene mreže",
    "Direct": "Direktne posete",
    "Referral": "Linkovi sa drugih sajtova",
    "Email": "Email",
    "Display": "Baneri (Display)",
    "Paid Video": "Plaćeni video",
    "Organic Video": "Video",
    "Paid Shopping": "Plaćeni Shopping",
    "Organic Shopping": "Shopping",
    "Cross-network": "Više mreža (Performance Max)",
    "Paid Other": "Ostalo plaćeno",
    "Affiliates": "Partneri",
    "SMS": "SMS",
    "Audio": "Audio",
    "Mobile Push Notifications": "Obaveštenja",
    "Unassigned": "Nerazvrstano",
}


def normalize_id(value):
    """Prihvata "123456789" ili "properties/123456789"; vraca samo cifre."""
    return re.sub(r"\D", "", value or "")


def connected():
    return google_auth.has_scope(google_auth.SCOPE_ANALYTICS)


def _token():
    token = google_auth.get_access_token()
    if not connected():
        raise SourceError("Povezani Google nalog nije dao dozvolu za Analytics. Povežite ga ponovo.")
    return token


def _request(method, url, token, **kwargs):
    resp = call(method, url, headers={"Authorization": "Bearer " + token}, **kwargs)
    if resp.status_code != 200:
        raise SourceError(body(resp).get("error", {}).get("message") or f"HTTP {resp.status_code}")
    return body(resp)


def report(property_id, start, end):
    """Posete u periodu: ukupno, po danima, po kanalu i najgledanije strane."""
    property_id = normalize_id(property_id)
    date_range = [{"startDate": start.isoformat(), "endDate": end.isoformat()}]

    def fetch():
        data = _request(
            "POST",
            f"{DATA_BASE}/properties/{property_id}:batchRunReports",
            _token(),
            json={
                "requests": [
                    {
                        "dateRanges": date_range,
                        "metrics": [
                            {"name": "sessions"},
                            {"name": "totalUsers"},
                            {"name": "averageSessionDuration"},
                            {"name": "newUsers"},
                            {"name": "engagementRate"},
                            {"name": "screenPageViews"},
                            {"name": "keyEvents"},
                        ],
                    },
                    {
                        "dateRanges": date_range,
                        "dimensions": [{"name": "date"}],
                        "metrics": [{"name": "sessions"}, {"name": "totalUsers"}, {"name": "keyEvents"}],
                        "limit": 400,
                    },
                    {
                        "dateRanges": date_range,
                        "dimensions": [{"name": "sessionDefaultChannelGroup"}],
                        "metrics": [{"name": "sessions"}],
                        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
                        "limit": 8,
                    },
                    {
                        "dateRanges": date_range,
                        "dimensions": [{"name": "pagePath"}],
                        "metrics": [{"name": "screenPageViews"}],
                        "orderBys": [{"metric": {"metricName": "screenPageViews"}, "desc": True}],
                        "limit": 8,
                    },
                ]
            },
        )
        reports = data.get("reports", []) + [{}, {}, {}, {}]

        def rows(index):
            return reports[index].get("rows", [])

        def number(row, index=0):
            values = row.get("metricValues", [])
            return float(values[index].get("value", 0)) if len(values) > index else 0.0

        def label(row):
            values = row.get("dimensionValues", [])
            return values[0].get("value", "") if values else ""

        totals = rows(0)[0] if rows(0) else {}
        return {
            "sessions": int(number(totals, 0)),
            "users": int(number(totals, 1)),
            "avg_seconds": round(number(totals, 2)),
            "new_users": int(number(totals, 3)),
            "engagement": number(totals, 4),  # udeo od 0 do 1
            "page_views": int(number(totals, 5)),
            "key_events": number(totals, 6),
            # GA4 vraca datum kao 20261006
            "by_day": {
                label(r): {"sessions": int(number(r, 0)), "users": int(number(r, 1)), "key_events": number(r, 2)}
                for r in rows(1)
                if len(label(r)) == 8
            },
            "channels": [{"name": label(r), "sessions": int(number(r))} for r in rows(2)],
            "pages": [{"path": label(r), "views": int(number(r))} for r in rows(3)],
        }

    # "v2" u kljucu: zapamceni odgovori starijeg oblika (bez novih brojki) se ne koriste.
    return cached(f"ga4:{property_id}:v2:{start.isoformat()}:{end.isoformat()}", data_ttl(end), fetch)


# Koraci puta do kupovine (standardni GA4 e-commerce dogadjaji), redom.
FUNNEL = [
    ("view_item", "Pogledali proizvod"),
    ("add_to_cart", "Dodali u korpu"),
    ("begin_checkout", "Započeli kupovinu"),
    ("add_shipping_info", "Uneli podatke za dostavu"),
    ("add_payment_info", "Uneli podatke za plaćanje"),
    ("purchase", "Kupili"),
]

# Dogadjaji koje GA4 belezi sam za svaku posetu; ne govore na sta ljudi klikcu.
ROUTINE_EVENTS = {"page_view", "session_start", "first_visit", "user_engagement"}

# GA4 nazivi dogadjaja, prevedeni za klijenta. Nepoznati (sopstveni) ostaju kakvi jesu.
EVENTS = {
    "click": "Klik na link ka drugom sajtu",
    "scroll": "Pomerili stranu skoro do kraja",
    "file_download": "Preuzeli fajl",
    "form_start": "Počeli da popunjavaju formu",
    "form_submit": "Poslali formu",
    "view_search_results": "Pretraživali sajt",
    "video_start": "Pokrenuli video",
    "video_progress": "Gledali video",
    "video_complete": "Odgledali video do kraja",
    "view_item_list": "Pregledali listu proizvoda",
    "select_item": "Kliknuli na proizvod u listi",
    "view_item": "Pogledali proizvod",
    "add_to_cart": "Dodali u korpu",
    "remove_from_cart": "Uklonili iz korpe",
    "view_cart": "Pogledali korpu",
    "add_to_wishlist": "Dodali u listu želja",
    "begin_checkout": "Započeli kupovinu",
    "add_shipping_info": "Uneli podatke za dostavu",
    "add_payment_info": "Uneli podatke za plaćanje",
    "purchase": "Kupili",
    "refund": "Vraćen novac",
    "view_promotion": "Videli promociju",
    "select_promotion": "Kliknuli na promociju",
    "generate_lead": "Ostavili upit",
    "sign_up": "Registrovali se",
    "login": "Prijavili se",
    "share": "Podelili sadržaj",
    "search": "Pretraživali sajt",
}


def behavior(property_id, start, end):
    """Ponasanje posetilaca: put do kupovine, proizvodi, dogadjaji i ulazne strane."""
    property_id = normalize_id(property_id)
    date_range = [{"startDate": start.isoformat(), "endDate": end.isoformat()}]

    def fetch():
        data = _request(
            "POST",
            f"{DATA_BASE}/properties/{property_id}:batchRunReports",
            _token(),
            json={
                "requests": [
                    {
                        "dateRanges": date_range,
                        "dimensions": [{"name": "eventName"}],
                        "metrics": [{"name": "totalUsers"}],
                        "dimensionFilter": {
                            "filter": {
                                "fieldName": "eventName",
                                "inListFilter": {"values": [name for name, _ in FUNNEL]},
                            }
                        },
                    },
                    {
                        "dateRanges": date_range,
                        "dimensions": [{"name": "itemName"}],
                        "metrics": [{"name": "itemsViewed"}, {"name": "itemsAddedToCart"}, {"name": "itemsPurchased"}],
                        "orderBys": [{"metric": {"metricName": "itemsViewed"}, "desc": True}],
                        "limit": 10,
                    },
                    {
                        "dateRanges": date_range,
                        "dimensions": [{"name": "eventName"}],
                        "metrics": [{"name": "eventCount"}, {"name": "totalUsers"}],
                        "orderBys": [{"metric": {"metricName": "eventCount"}, "desc": True}],
                        "limit": 30,
                    },
                    {
                        "dateRanges": date_range,
                        "dimensions": [{"name": "landingPage"}],
                        "metrics": [{"name": "sessions"}, {"name": "bounceRate"}],
                        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
                        "limit": 8,
                    },
                ]
            },
        )
        reports = data.get("reports", []) + [{}, {}, {}, {}]

        def rows(index):
            return reports[index].get("rows", [])

        def number(row, index=0):
            values = row.get("metricValues", [])
            return float(values[index].get("value", 0)) if len(values) > index else 0.0

        def label(row):
            values = row.get("dimensionValues", [])
            return values[0].get("value", "") if values else ""

        return {
            "funnel": {label(r): int(number(r)) for r in rows(0)},
            "products": [
                {"name": label(r), "views": int(number(r, 0)), "carts": int(number(r, 1)), "purchases": int(number(r, 2))}
                for r in rows(1)
                if label(r) and label(r) != "(not set)"
            ],
            "events": [
                {"name": label(r), "count": int(number(r, 0)), "users": int(number(r, 1))}
                for r in rows(2)
                if label(r) not in ROUTINE_EVENTS
            ],
            "landing": [
                {"path": label(r), "sessions": int(number(r, 0)), "bounce": number(r, 1)}
                for r in rows(3)
                if label(r) and label(r) != "(not set)"
            ],
        }

    return cached(f"ga4:{property_id}:behavior:{start.isoformat()}:{end.isoformat()}", data_ttl(end), fetch)


def list_properties():
    """GA4 property-ji koje povezani nalog vidi: [{"id", "name"}].

    Radi samo ako je u Google Cloud projektu ukljucen Google Analytics Admin API.
    """

    def fetch():
        token = _token()
        properties, page_token = [], None
        while True:
            params = {"pageSize": 200}
            if page_token:
                params["pageToken"] = page_token
            data = _request("GET", f"{ADMIN_BASE}/accountSummaries", token, params=params)
            for account in data.get("accountSummaries", []):
                for prop in account.get("propertySummaries", []):
                    properties.append(
                        {
                            "id": normalize_id(prop.get("property")),
                            "name": f"{account.get('displayName', '')} / {prop.get('displayName', '')}",
                        }
                    )
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return sorted(properties, key=lambda p: p["name"].lower())

    return cached("ga4_properties", LIST_TTL, fetch)
