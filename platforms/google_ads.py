"""Google Ads: citanje preko Google Ads API-ja (REST). Samo upiti za citanje."""
import os
import re

import google_auth
from platforms.base import SourceError, body, call

KEY = "google_ads"
NAME = "Google Ads"
CONNECTION = "google"       # koju vezu koristi (isti Google nalog kao Analytics)
HAS_SEARCH_TERMS = True

API_VERSION = os.environ.get("GOOGLE_ADS_API_VERSION", "v25")
BASE = f"https://googleads.googleapis.com/{API_VERSION}"

# ID menadzerskog (MCC) naloga ispod kog su klijenti. Crtice nisu bitne.
MCC_ID = re.sub(r"\D", "", os.environ.get("GOOGLE_ADS_MCC_ID", ""))

# Ako je navedeno, portal cita ISKLJUCIVO ove Google Ads naloge (odvojeni
# zarezom) i ne trazi spisak naloga iz MCC-a. Korisno za probu na jednom nalogu.
ONLY_IDS = [
    digits
    for digits in (re.sub(r"\D", "", part) for part in os.environ.get("GOOGLE_ADS_ACCOUNT_IDS", "").split(","))
    if digits
]


def digits(value):
    return re.sub(r"\D", "", value or "")


def format_id(value):
    """6543737887 -> 654-373-7887"""
    d = digits(value)
    return f"{d[:3]}-{d[3:6]}-{d[6:]}" if len(d) == 10 else d


def configured():
    return google_auth.configured() and bool(ONLY_IDS or MCC_ID)


def accounts_scope():
    """Deo kljuca za kes liste naloga: lista zavisi od podesavanja."""
    return ",".join(ONLY_IDS) + ":" + MCC_ID


def prepare():
    if not google_auth.has_scope(google_auth.SCOPE_ADS):
        # get_access_token javlja jasniju poruku ako veze uopste nema
        google_auth.get_access_token()
        raise SourceError("Povezani Google nalog nije dao dozvolu za Google Ads. Povežite ga ponovo.")
    return google_auth.get_access_token()


def _error_text(resp):
    err = body(resp).get("error", {})
    for detail in err.get("details", []):
        for item in detail.get("errors", []):
            if item.get("message"):
                return item["message"]
    return err.get("message") or f"HTTP {resp.status_code}"


def search(token, customer_id, query):
    """Izvrsava jedan GAQL upit i vraca sve redove."""
    if ONLY_IDS and digits(customer_id) not in ONLY_IDS:
        raise SourceError(
            f"Nalog {format_id(customer_id)} nije među nalozima koje portal sme da čita "
            "(GOOGLE_ADS_ACCOUNT_IDS)."
        )
    headers = {"Authorization": "Bearer " + token}
    if MCC_ID:
        headers["login-customer-id"] = MCC_ID
    url = f"{BASE}/customers/{digits(customer_id)}/googleAds:search"
    rows, page_token = [], None
    while True:
        payload = {"query": query}
        if page_token:
            payload["pageToken"] = page_token
        resp = call("POST", url, headers=headers, json=payload)
        if resp.status_code != 200:
            raise SourceError(_error_text(resp))
        data = body(resp)
        rows.extend(data.get("results", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            return rows


def list_accounts(token):
    """Nalozi koje portal sme da ponudi: [{"id", "name", "currency"}].

    Ako je podesen GOOGLE_ADS_ACCOUNT_IDS, to su samo ti nalozi i MCC se ne
    pretrazuje. Inace su to svi aktivni nalozi ispod MCC-a.
    """
    if ONLY_IDS:
        accounts = []
        for account_id in ONLY_IDS:
            try:
                rows = search(
                    token,
                    account_id,
                    "SELECT customer.id, customer.descriptive_name, customer.currency_code "
                    "FROM customer LIMIT 1",
                )
            except SourceError as exc:
                raise SourceError(f"Nalog {format_id(account_id)}: {exc}")
            customer = rows[0].get("customer", {}) if rows else {}
            accounts.append(
                {
                    "id": account_id,
                    "name": customer.get("descriptiveName") or format_id(account_id),
                    "currency": customer.get("currencyCode") or "EUR",
                }
            )
        return sorted(accounts, key=lambda a: a["name"].lower())

    if not MCC_ID:
        raise SourceError(
            "Nije podešeno koje Google Ads naloge portal čita. Upišite GOOGLE_ADS_ACCOUNT_IDS "
            "(pojedinačni nalozi) ili GOOGLE_ADS_MCC_ID (svi nalozi ispod MCC-a)."
        )
    rows = search(
        token,
        MCC_ID,
        "SELECT customer_client.id, customer_client.descriptive_name, "
        "customer_client.currency_code "
        "FROM customer_client "
        "WHERE customer_client.manager = FALSE AND customer_client.status = 'ENABLED'",
    )
    accounts = {}
    for row in rows:
        c = row.get("customerClient", {})
        account_id = str(c.get("id", ""))
        if account_id:
            accounts[account_id] = {
                "id": account_id,
                "name": c.get("descriptiveName") or format_id(account_id),
                "currency": c.get("currencyCode") or "EUR",
            }
    return sorted(accounts.values(), key=lambda a: a["name"].lower())


def fetch_daily(token, account_id, start, end):
    """Brojke po kampanji i danu, od start do end (ukljucivo)."""
    rows = search(
        token,
        account_id,
        "SELECT campaign.id, campaign.name, segments.date, metrics.cost_micros, "
        "metrics.impressions, metrics.clicks, metrics.conversions "
        "FROM campaign "
        f"WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'",
    )
    out = []
    for row in rows:
        campaign, metrics = row.get("campaign", {}), row.get("metrics", {})
        day = row.get("segments", {}).get("date")
        if not day:
            continue
        out.append(
            [
                day,
                str(campaign.get("id", "")),
                campaign.get("name", ""),
                int(metrics.get("costMicros", 0)) / 1_000_000,
                int(metrics.get("impressions", 0)),
                int(metrics.get("clicks", 0)),
                float(metrics.get("conversions", 0)),
            ]
        )
    return {"start": start.isoformat(), "end": end.isoformat(), "rows": out}


def fetch_search_terms(token, account_id, start, end, limit=10):
    """Pojmovi pretrage sa najvise klikova u periodu."""
    rows = search(
        token,
        account_id,
        "SELECT search_term_view.search_term, metrics.clicks "
        "FROM search_term_view "
        f"WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}' "
        "AND metrics.clicks > 0 "
        "ORDER BY metrics.clicks DESC LIMIT 500",
    )
    # Isti pojam se javlja u vise grupa oglasa, pa se sabira.
    clicks = {}
    for row in rows:
        term = row.get("searchTermView", {}).get("searchTerm")
        if term:
            clicks[term] = clicks.get(term, 0) + int(row.get("metrics", {}).get("clicks", 0))
    top = sorted(clicks.items(), key=lambda item: item[1], reverse=True)[:limit]
    return [{"term": term, "clicks": count} for term, count in top]


def connected():
    return google_auth.has_scope(google_auth.SCOPE_ADS)
