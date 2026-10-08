"""Google prijava i pristup Google podacima.

Dve odvojene stvari idu kroz isti Google OAuth klijent:

1. Prijava clana tima. Trazi se samo ime i email, a ulaz je dozvoljen
   iskljucivo nalozima sa domena agencije.
2. Povezivanje podataka. Jedan clan tima daje portalu dozvolu da cita
   Google Ads i Google Analytics u njegovo ime. Portal cuva sifrovan
   "refresh token" i njime cita podatke i kad taj clan nije prijavljen
   (na primer kad klijent otvori svoj izvestaj).

Portal dobija iskljucivo pravo citanja iz Analytics-a. Google Ads nema
poseban "samo citanje" opseg, ali kod salje samo upite za citanje.
"""
import os
import threading
import time
from urllib.parse import urlencode

import connections
from platforms.base import NotConnected, SourceError, body, call

PLATFORM = "google"

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

SCOPE_ADS = "https://www.googleapis.com/auth/adwords"
SCOPE_ANALYTICS = "https://www.googleapis.com/auth/analytics.readonly"
LOGIN_SCOPES = ["openid", "email", "profile"]
DATA_SCOPES = LOGIN_SCOPES + [SCOPE_ADS, SCOPE_ANALYTICS]

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
ALLOWED_DOMAIN = os.environ.get("ALLOWED_DOMAIN", "victorious.rs").strip().lower()
# Opciono: ako je navedeno, ulaze samo ovi emailovi (odvojeni zarezom).
ALLOWED_EMAILS = {
    e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()
}

_token_lock = threading.Lock()
_token_cache = {"for": None, "token": None, "expires": 0}


def configured():
    return bool(CLIENT_ID and CLIENT_SECRET)


# ---------------------------------------------------------------- OAuth tok

def build_auth_url(redirect_uri, state, purpose, login_hint=None):
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
        "hd": ALLOWED_DOMAIN,
    }
    if purpose == "connect":
        params["scope"] = " ".join(DATA_SCOPES)
        params["access_type"] = "offline"   # trazimo refresh token
        params["prompt"] = "consent"        # bez ovoga ga Google ne izdaje ponovo
        if login_hint:
            params["login_hint"] = login_hint
    else:
        params["scope"] = " ".join(LOGIN_SCOPES)
        params["prompt"] = "select_account"
    return AUTH_URL + "?" + urlencode(params)


def exchange_code(code, redirect_uri):
    resp = call(
        "POST",
        TOKEN_URL,
        data={
            "code": code,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    data = body(resp)
    if resp.status_code != 200 or "access_token" not in data:
        raise SourceError(
            "Google nije prihvatio prijavu: "
            + str(data.get("error_description") or data.get("error") or resp.status_code)
        )
    return data


def fetch_userinfo(access_token):
    resp = call("GET", USERINFO_URL, headers={"Authorization": "Bearer " + access_token})
    data = body(resp)
    if resp.status_code != 200 or not data.get("email"):
        raise SourceError("Google nije vratio podatke o nalogu.")
    return data


def check_allowed(userinfo):
    """Dozvoljeni su samo potvrdjeni nalozi sa domena agencije."""
    email = userinfo.get("email", "").lower()
    if not userinfo.get("email_verified"):
        raise SourceError("Ovaj Google nalog nema potvrđen email.")
    # "hd" postoji samo kod Google Workspace naloga i govori kom domenu nalog pripada.
    if userinfo.get("hd", "").lower() != ALLOWED_DOMAIN or not email.endswith("@" + ALLOWED_DOMAIN):
        raise SourceError(f"Prijava je dozvoljena samo nalozima sa domena {ALLOWED_DOMAIN}.")
    if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:
        raise SourceError("Ovaj nalog nema pristup portalu. Javite se administratoru portala.")
    return email


# ---------------------------------------------------------------- veza za podatke

def has_scope(scope):
    connection = connections.get(PLATFORM)
    return bool(connection) and scope in connection["scopes"].split()


def save_connection(email, refresh_token, scopes, connected_by):
    connections.save(PLATFORM, email, {"refresh_token": refresh_token}, connected_by, scopes)
    _forget_token()


def remove_connection():
    """Brise vezu i, koliko je moguce, opoziva dozvolu kod Google-a."""
    try:
        token = connections.secret(PLATFORM)["refresh_token"]
        call("POST", REVOKE_URL, data={"token": token})
    except SourceError:
        pass  # veza se u svakom slucaju brise iz portala
    connections.remove(PLATFORM)
    _forget_token()


def _forget_token():
    with _token_lock:
        _token_cache.update({"for": None, "token": None, "expires": 0})


def get_access_token():
    """Kratkotrajni token za pozive ka Google-u. Pamti se dok ne istekne."""
    try:
        refresh_token = connections.secret(PLATFORM)["refresh_token"]
    except NotConnected:
        raise NotConnected("Google nalog još nije povezan.")
    with _token_lock:
        if _token_cache["for"] == refresh_token and _token_cache["expires"] > time.time() + 60:
            return _token_cache["token"]
    resp = call(
        "POST",
        TOKEN_URL,
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    data = body(resp)
    if resp.status_code != 200 or "access_token" not in data:
        if data.get("error") == "invalid_grant":
            raise SourceError(
                "Dozvola za čitanje podataka je istekla ili je opozvana. Povežite Google nalog ponovo."
            )
        raise SourceError(
            "Google nije izdao pristup: "
            + str(data.get("error_description") or data.get("error") or resp.status_code)
        )
    with _token_lock:
        _token_cache.update(
            {
                "for": refresh_token,
                "token": data["access_token"],
                "expires": time.time() + int(data.get("expires_in", 3600)),
            }
        )
    return data["access_token"]
