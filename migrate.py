"""Prenos podataka iz ranije verzije portala (samo Google kanal).

Pokrece se pri svakom startu i ne radi nista ako nema sta da se prenese.
"""
import connections
import db
from platforms import base


def run():
    moved = _clients() | _google_connection()
    if moved:
        base.cache_clear()  # kljucevi kesa su promenili oblik


def _clients():
    if not db.columns("portal_clients_v1"):
        return False
    for row in db.query("SELECT * FROM portal_clients_v1"):
        db.execute(
            "INSERT INTO portal_clients (id, name, token, password_hash, access_key, link_active, "
            "show_spend, show_clicks, show_conv, show_campaigns, show_terms, show_combined, conv_kind, "
            "failed_attempts, locked_until, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
            (
                row["id"], row["name"], row["token"], row["password_hash"], row["access_key"],
                row["link_active"], row["show_spend"], row["show_clicks"], row["show_conv"],
                row["show_campaigns"], row["show_terms"], row["conv_kind"], row["failed_attempts"],
                row["locked_until"], row["created_at"],
            ),
        )
        db.execute(
            "INSERT INTO portal_sources (client_id, platform, account_id, account_name, currency, visible) "
            "VALUES (?, 'google_ads', ?, ?, ?, 1)",
            (row["id"], row["google_ads_id"], row["name"], row["currency"]),
        )
        if row["ga4_property"]:
            db.execute(
                "INSERT INTO portal_sources (client_id, platform, account_id, visible) VALUES (?, 'ga4', ?, ?)",
                (row["id"], row["ga4_property"], row["show_ga"]),
            )
    if db.IS_PG:
        # Redovi su upisani sa starim brojevima, pa brojac mora da nastavi posle najveceg.
        db.execute(
            "SELECT setval(pg_get_serial_sequence('portal_clients', 'id'), "
            "(SELECT COALESCE(MAX(id), 0) + 1 FROM portal_clients), false)"
        )
    db.execute("DROP TABLE portal_clients_v1")
    return True


def _google_connection():
    if not db.columns("portal_connection"):
        return False
    old = db.query("SELECT * FROM portal_connection WHERE id = 1", one=True)
    if old and not connections.get("google"):
        try:
            refresh_token = connections.decrypt_legacy(old["refresh_token_enc"])
        except Exception:
            refresh_token = None  # sifrovano drugim kljucem; veza se pravi ponovo
        if refresh_token:
            connections.save(
                "google", old["email"], {"refresh_token": refresh_token}, old["connected_by"], old["scopes"]
            )
    db.execute("DROP TABLE portal_connection")
    return True
