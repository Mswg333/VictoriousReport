"""Zajednicki delovi za sve kanale: greske, mrezni pozivi i kes u bazi."""
import json
import time
from datetime import date, timedelta

import requests

import db

TIMEOUT = 45
HISTORY_DAYS = 90            # podrazumevani period: poslednjih 90 dana
MAX_RANGE_DAYS = 366         # najduzi period koji se cita odjednom
DATA_TTL = 6 * 3600          # koliko dugo vazi kes za brojke
CLOSED_TTL = 24 * 3600       # kes za period koji se zavrsio pre vise od 3 dana
LIST_TTL = 3600              # koliko dugo vazi kes za liste naloga
ERROR_TTL = 300              # posle greske, toliko se ceka pre novog pokusaja


class SourceError(Exception):
    """Greska koju je bezbedno prikazati clanu tima."""


class NotConnected(SourceError):
    """Kanal jos nije povezan na strani "Izvor podataka"."""


def yesterday():
    return date.today() - timedelta(days=1)


def data_ttl(end):
    """Skorasnje brojke se jos menjaju (konverzije stizu naknadno); starije ne."""
    return CLOSED_TTL if (yesterday() - end).days > 3 else DATA_TTL


# ---------------------------------------------------------------- mreza

def call(method, url, **kwargs):
    """Jedan HTTP poziv. Mrezne greske pretvara u poruku za tim."""
    kwargs.setdefault("timeout", TIMEOUT)
    try:
        return requests.request(method, url, **kwargs)
    except requests.RequestException:
        raise SourceError("Servis trenutno nije dostupan. Pokušajte ponovo za koji minut.")


def body(resp):
    """JSON telo odgovora kao recnik (prazan ako nije JSON)."""
    try:
        data = resp.json()
    except ValueError:
        return {}
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------- kes

def cache_get(key):
    row = db.query("SELECT payload, fetched_at FROM portal_cache WHERE cache_key = ?", (key,), one=True)
    if not row:
        return None, 0
    return json.loads(row["payload"]), row["fetched_at"]


def cache_put(key, payload):
    db.execute(
        "INSERT INTO portal_cache (cache_key, payload, fetched_at) VALUES (?, ?, ?) "
        "ON CONFLICT (cache_key) DO UPDATE SET payload = excluded.payload, fetched_at = excluded.fetched_at",
        (key, json.dumps(payload), int(time.time())),
    )
    # Stari unosi (na primer za prethodne dane) vise ne trebaju.
    db.execute("DELETE FROM portal_cache WHERE fetched_at < ?", (int(time.time()) - 3 * 86400,))


def cache_delete(key):
    db.execute("DELETE FROM portal_cache WHERE cache_key = ?", (key,))


def cache_delete_prefix(prefix):
    db.execute("DELETE FROM portal_cache WHERE cache_key LIKE ?", (prefix.replace("%", "") + "%",))


def cache_clear():
    db.execute("DELETE FROM portal_cache")


def cached(key, ttl, fetch):
    """Vraca svez kes ili zove fetch(). Ako servis ne radi, vraca stari kes."""
    payload, fetched_at = cache_get(key)
    if payload is not None and time.time() - fetched_at < ttl:
        return payload
    try:
        fresh = fetch()
    except SourceError:
        if payload is not None:
            return payload
        raise
    cache_put(key, fresh)
    return fresh
