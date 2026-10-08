"""Provera prenosa podataka iz ranije verzije (samo Google kanal).

Pravi bazu u starom obliku, pokrece aplikaciju nad njom i proverava da su
klijent, njegov nalog, lozinka i Google veza preneti. Pokretanje:

    python tests/test_migration.py
"""
import base64
import hashlib
import os
import sqlite3
import sys
import tempfile

from cryptography.fernet import Fernet

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

path = os.path.join(tempfile.mkdtemp(), "old.db")
os.environ.update(SQLITE_PATH=path, SECRET_KEY="stari-kljuc", DATABASE_URL="")

fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"portal-token:stari-kljuc").digest()))
old = sqlite3.connect(path)
old.executescript(
    """
    CREATE TABLE portal_team_users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL, created_at TEXT NOT NULL, last_login_at TEXT NOT NULL);
    CREATE TABLE portal_clients (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        google_ads_id TEXT UNIQUE NOT NULL, currency TEXT NOT NULL DEFAULT 'EUR', ga4_property TEXT NOT NULL DEFAULT '',
        token TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL DEFAULT '', access_key TEXT NOT NULL DEFAULT '',
        link_active INTEGER NOT NULL DEFAULT 1, show_spend INTEGER NOT NULL DEFAULT 1, show_clicks INTEGER NOT NULL DEFAULT 1,
        show_conv INTEGER NOT NULL DEFAULT 1, show_campaigns INTEGER NOT NULL DEFAULT 1, show_terms INTEGER NOT NULL DEFAULT 0,
        show_ga INTEGER NOT NULL DEFAULT 1, conv_kind TEXT NOT NULL DEFAULT 'upit', failed_attempts INTEGER NOT NULL DEFAULT 0,
        locked_until INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
    CREATE TABLE portal_connection (id INTEGER PRIMARY KEY, email TEXT NOT NULL, refresh_token_enc TEXT NOT NULL,
        scopes TEXT NOT NULL, connected_by TEXT NOT NULL, connected_at TEXT NOT NULL);
    CREATE TABLE portal_cache (cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched_at INTEGER NOT NULL);
    """
)
old.execute(
    "INSERT INTO portal_clients (id, name, google_ads_id, currency, ga4_property, token, password_hash, access_key, "
    "show_terms, show_ga, conv_kind, created_at) VALUES (7, 'Hrast', '1112045501', 'RSD', '555000111', 'tok', 'hes', 'kljuc', 1, 0, 'kupovina', 'x')"
)
old.execute(
    "INSERT INTO portal_clients (id, name, google_ads_id, token, created_at) VALUES (9, 'Vita', '2223187742', 'tok2', 'x')"
)
old.execute(
    "INSERT INTO portal_connection VALUES (1, 'nenad@victorious.rs', ?, 'openid adwords', 'nenad@victorious.rs', '2026-10-07')",
    (fernet.encrypt(b"stari-refresh-token").decode(),),
)
old.execute("INSERT INTO portal_cache VALUES ('daily:1112045501', '{}', 1)")
old.commit()
old.close()

import app as portal  # noqa: E402
import connections  # noqa: E402
import db  # noqa: E402

with portal.app.app_context():
    clients = {c["id"]: c for c in db.query("SELECT * FROM portal_clients")}
    assert set(clients) == {7, 9}
    assert clients[7]["token"] == "tok" and clients[7]["password_hash"] == "hes" and clients[7]["access_key"] == "kljuc"
    assert clients[7]["conv_kind"] == "kupovina" and clients[7]["show_terms"] == 1 and clients[7]["show_combined"] == 1
    assert "google_ads_id" not in clients[7]

    sources = {(s["client_id"], s["platform"]): s for s in db.query("SELECT * FROM portal_sources")}
    assert set(sources) == {(7, "google_ads"), (7, "ga4"), (9, "google_ads")}
    assert sources[(7, "google_ads")]["account_id"] == "1112045501" and sources[(7, "google_ads")]["currency"] == "RSD"
    assert sources[(7, "ga4")]["account_id"] == "555000111" and sources[(7, "ga4")]["visible"] == 0

    assert connections.secret("google") == {"refresh_token": "stari-refresh-token"}
    assert connections.get("google")["label"] == "nenad@victorious.rs"
    assert db.columns("portal_clients_v1") == [] and db.columns("portal_connection") == []
    assert db.query("SELECT * FROM portal_cache") == []

    # Novi klijent dobija broj posle postojecih, a ponovni start ne dira nista.
    new_id = db.insert("INSERT INTO portal_clients (name, token, created_at) VALUES ('Novi', 'tok3', 'x') RETURNING id")
    assert new_id == 10, new_id
    db.init_schema()
    import migrate
    migrate.run()
    assert len(db.query("SELECT * FROM portal_clients")) == 3 and len(db.query("SELECT * FROM portal_sources")) == 3

print("PRENOS PODATAKA RADI")
