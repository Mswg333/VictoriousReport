"""Microsoft Ads (Bing): citanje preko Microsoft Advertising API-ja (v13, REST).

Potrebni su: aplikacija registrovana u Microsoft Entra (Azure) sa
MICROSOFT_ADS_CLIENT_ID i MICROSOFT_ADS_CLIENT_SECRET, i developer token
iz Microsoft Ads naloga (MICROSOFT_ADS_DEVELOPER_TOKEN).

Izvestaji se kod Microsoft-a prave u pozadini: portal posalje zahtev,
saceka da izvestaj bude gotov, pa ga preuzme kao CSV u zip arhivi.
Salju se samo zahtevi za citanje i izvestaje.
"""
import csv
import io
import os
import threading
import time
import zipfile
from urllib.parse import urlencode

import connections
from platforms.base import NotConnected, SourceError, body, call

KEY = "microsoft"
NAME = "Microsoft Ads (Bing)"
CONNECTION = "microsoft"
HAS_SEARCH_TERMS = False

CLIENT_ID = os.environ.get("MICROSOFT_ADS_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("MICROSOFT_ADS_CLIENT_SECRET", "").strip()
DEVELOPER_TOKEN = os.environ.get("MICROSOFT_ADS_DEVELOPER_TOKEN", "").strip()
TENANT = os.environ.get("MICROSOFT_ADS_TENANT", "common").strip() or "common"
# Vremenska zona u kojoj se racunaju dani u izvestaju.
TIMEZONE = os.environ.get("MICROSOFT_ADS_TIMEZONE", "BelgradeBratislavaBudapestLjubljanaPrague").strip()

AUTH_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
SCOPE = "https://ads.microsoft.com/msads.manage offline_access"
CUSTOMER_BASE = "https://clientcenter.api.bingads.microsoft.com/CustomerManagement/v13"
REPORT_BASE = "https://reporting.api.bingads.microsoft.com/Reporting/v13"

POLL_SECONDS = 2
POLL_LIMIT = 60          # koliko se najduze ceka da izvestaj bude gotov
_sleep = time.sleep      # zamenjuje se u testovima

_token_lock = threading.Lock()
_token_cache = {"for": None, "token": None, "expires": 0}


def format_id(value):
    """Nalog se cuva kao "customerId:accountId"; prikazuje se samo broj naloga."""
    return str(value or "").split(":")[-1]


def configured():
    return bool(CLIENT_ID and CLIENT_SECRET and DEVELOPER_TOKEN)


def accounts_scope():
    return ""


# ---------------------------------------------------------------- OAuth

def auth_url(redirect_uri, state):
    return AUTH_URL + "?" + urlencode(
        {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "response_mode": "query",
            "scope": SCOPE,
            "state": state,
            "prompt": "select_account",
        }
    )


def _token_request(fields):
    resp = call("POST", TOKEN_URL, data={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "scope": SCOPE, **fields})
    data = body(resp)
    if resp.status_code != 200 or "access_token" not in data:
        if data.get("error") == "invalid_grant":
            raise SourceError("Dozvola za Microsoft Ads je istekla ili je opozvana. Povežite nalog ponovo.")
        raise SourceError(
            "Microsoft nije izdao pristup: "
            + str(data.get("error_description") or data.get("error") or resp.status_code).splitlines()[0]
        )
    return data


def exchange_code(code, redirect_uri):
    data = _token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri})
    if not data.get("refresh_token"):
        raise SourceError("Microsoft nije izdao trajnu dozvolu. Pokušajte povezivanje ponovo.")
    return data


def prepare():
    """Vraca kratkotrajni token. Microsoft pri obnavljanju izdaje nov refresh token, pa se on cuva."""
    try:
        stored = connections.secret(CONNECTION)
    except NotConnected:
        raise NotConnected("Microsoft Ads nalog još nije povezan.")
    with _token_lock:
        if _token_cache["for"] == stored["refresh_token"] and _token_cache["expires"] > time.time() + 60:
            return _token_cache["token"]
    data = _token_request({"grant_type": "refresh_token", "refresh_token": stored["refresh_token"]})
    current = data.get("refresh_token") or stored["refresh_token"]
    if current != stored["refresh_token"]:
        connections.update_secret(CONNECTION, {"refresh_token": current})
    with _token_lock:
        _token_cache.update(
            {"for": current, "token": data["access_token"], "expires": time.time() + int(data.get("expires_in", 3600))}
        )
    return data["access_token"]


def forget_token():
    with _token_lock:
        _token_cache.update({"for": None, "token": None, "expires": 0})


# ---------------------------------------------------------------- API

def _first_message(data):
    """Microsoft vraca greske u vise oblika; uzima se prva poruka koja se nadje."""
    if isinstance(data, dict):
        for key in ("Message", "message", "error_description"):
            if isinstance(data.get(key), str) and data[key]:
                return data[key]
        for value in data.values():
            found = _first_message(value)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _first_message(item)
            if found:
                return found
    return None


def _post(url, token, payload, account_id=None):
    headers = {"Authorization": "Bearer " + token, "DeveloperToken": DEVELOPER_TOKEN}
    if account_id:
        customer_id, _, account = str(account_id).partition(":")
        headers["CustomerId"] = customer_id
        headers["CustomerAccountId"] = account
    resp = call("POST", url, headers=headers, json=payload)
    data = body(resp)
    if resp.status_code != 200:
        raise SourceError(_first_message(data) or f"HTTP {resp.status_code}")
    return data


def whoami(token):
    user = _post(f"{CUSTOMER_BASE}/User/Query", token, {"UserId": None}).get("User", {})
    return user.get("Id"), user.get("UserName") or "Microsoft Ads nalog"


def list_accounts(token):
    user_id, _ = whoami(token)
    data = _post(
        f"{CUSTOMER_BASE}/Accounts/Search",
        token,
        {
            "Predicates": [{"Field": "UserId", "Operator": "Equals", "Value": str(user_id)}],
            "Ordering": [],
            "PageInfo": {"Index": 0, "Size": 1000},
        },
    )
    accounts = []
    for account in data.get("Accounts", []):
        if account.get("AccountLifeCycleStatus", "Active") != "Active":
            continue
        accounts.append(
            {
                "id": f"{account.get('ParentCustomerId')}:{account.get('Id')}",
                "name": account.get("Name") or str(account.get("Id")),
                "currency": account.get("CurrencyCode") or "USD",
            }
        )
    return sorted(accounts, key=lambda a: a["name"].lower())


def _number(text):
    try:
        return float(str(text or "0").replace(",", "").replace("%", "") or 0)
    except ValueError:
        return 0.0


def _download(url):
    """Preuzima zip sa izvestajem i vraca redove iz CSV fajla u njemu."""
    if not url.startswith("https://"):
        raise SourceError("Microsoft je vratio neispravnu adresu izveštaja.")
    resp = call("GET", url)
    if resp.status_code != 200:
        raise SourceError(f"Izveštaj nije preuzet (HTTP {resp.status_code}).")
    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
            text = archive.read(archive.namelist()[0]).decode("utf-8-sig")
    except (zipfile.BadZipFile, IndexError, UnicodeDecodeError):
        raise SourceError("Izveštaj iz Microsoft Ads-a ne može da se pročita.")
    return list(csv.DictReader(io.StringIO(text)))


def fetch_daily(token, account_id, start, end):
    """Brojke po kampanji i danu, iz izvestaja Campaign Performance."""
    account = int(format_id(account_id))

    def day(d):
        return {"Day": d.day, "Month": d.month, "Year": d.year}

    submitted = _post(
        f"{REPORT_BASE}/GenerateReport/Submit",
        token,
        {
            "ReportRequest": {
                "Type": "CampaignPerformanceReportRequest",
                "ReportName": "Portal dnevni izvestaj",
                "Format": "Csv",
                "FormatVersion": "2.0",
                "ExcludeColumnHeaders": False,
                "ExcludeReportHeader": True,
                "ExcludeReportFooter": True,
                "ReturnOnlyCompleteData": False,
                "Aggregation": "Daily",
                "Columns": ["TimePeriod", "CampaignId", "CampaignName", "Spend", "Impressions", "Clicks", "Conversions"],
                "Scope": {"AccountIds": [account]},
                "Time": {
                    "CustomDateRangeStart": day(start),
                    "CustomDateRangeEnd": day(end),
                    "ReportTimeZone": TIMEZONE,
                },
            }
        },
        account_id,
    )
    request_id = submitted.get("ReportRequestId")
    if not request_id:
        raise SourceError("Microsoft Ads nije prihvatio zahtev za izveštaj.")

    waited, status = 0, {}
    while True:
        status = _post(
            f"{REPORT_BASE}/GenerateReport/Poll", token, {"ReportRequestId": request_id}, account_id
        ).get("ReportRequestStatus", {})
        if status.get("Status") == "Success":
            break
        if status.get("Status") == "Error":
            raise SourceError("Microsoft Ads nije uspeo da napravi izveštaj.")
        if waited >= POLL_LIMIT:
            raise SourceError("Microsoft Ads izveštaj se pravi predugo. Pokušajte ponovo za koji minut.")
        _sleep(POLL_SECONDS)
        waited += POLL_SECONDS

    out = []
    # Kad u periodu nema podataka, izvestaj uspe ali adrese za preuzimanje nema.
    if status.get("ReportDownloadUrl"):
        for row in _download(status["ReportDownloadUrl"]):
            day_text = str(row.get("TimePeriod", ""))[:10]
            if len(day_text) != 10 or day_text[4] != "-":
                continue
            out.append(
                [
                    day_text,
                    str(row.get("CampaignId", "")),
                    row.get("CampaignName", ""),
                    _number(row.get("Spend")),
                    int(_number(row.get("Impressions"))),
                    int(_number(row.get("Clicks"))),
                    _number(row.get("Conversions")),
                ]
            )
    return {"start": start.isoformat(), "end": end.isoformat(), "rows": out}


def connected():
    return connections.get(CONNECTION) is not None
