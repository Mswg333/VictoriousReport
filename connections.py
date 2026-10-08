"""Veze sa platformama (Google, Meta, Microsoft, TikTok).

Za svaku platformu cuva se najvise jedna veza: ko je povezan i sifrovan
token kojim portal cita podatke. Token je sifrovan kljucem izvedenim iz
SECRET_KEY-a; bez njega je sadrzaj baze neupotrebljiv.
"""
import base64
import hashlib
import json
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

import db
from platforms.base import NotConnected, SourceError

_fernet = None


def init(secret_key):
    global _fernet
    digest = hashlib.sha256(("portal-token:" + secret_key).encode()).digest()
    _fernet = Fernet(base64.urlsafe_b64encode(digest))


def get(platform):
    """Podaci o vezi bez tokena (za prikaz), ili None."""
    return db.query(
        "SELECT platform, label, scopes, connected_by, connected_at FROM portal_connections WHERE platform = ?",
        (platform,),
        one=True,
    )


def secret(platform):
    """Desifrovan token (recnik). Baca NotConnected ako veze nema."""
    row = db.query("SELECT secret_enc FROM portal_connections WHERE platform = ?", (platform,), one=True)
    if not row:
        raise NotConnected("Ovaj kanal još nije povezan.")
    try:
        return json.loads(_fernet.decrypt(row["secret_enc"].encode()).decode())
    except (InvalidToken, ValueError):
        raise SourceError(
            "Sačuvana veza ne može da se pročita (promenjen je SECRET_KEY). Povežite kanal ponovo."
        )


def save(platform, label, secret_data, connected_by, scopes=""):
    db.execute("DELETE FROM portal_connections WHERE platform = ?", (platform,))
    db.execute(
        "INSERT INTO portal_connections (platform, label, secret_enc, scopes, connected_by, connected_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            platform,
            label,
            _fernet.encrypt(json.dumps(secret_data).encode()).decode(),
            scopes,
            connected_by,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )


def update_secret(platform, secret_data):
    """Menja samo token (na primer kad platforma izda nov refresh token)."""
    db.execute(
        "UPDATE portal_connections SET secret_enc = ? WHERE platform = ?",
        (_fernet.encrypt(json.dumps(secret_data).encode()).decode(), platform),
    )


def remove(platform):
    db.execute("DELETE FROM portal_connections WHERE platform = ?", (platform,))


def decrypt_legacy(text):
    """Cita token iz starog formata (obican tekst umesto recnika)."""
    return _fernet.decrypt(text.encode()).decode()
