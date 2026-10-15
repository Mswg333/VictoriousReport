"""Tanak sloj za bazu.

Lokalno radi sa SQLite fajlom (portal.db), a na serveru sa Postgres bazom
(Supabase) kad je podesena promenljiva DATABASE_URL.

Upiti se pisu sa znakom ? za parametre; za Postgres se on automatski
pretvara u %s. Tabele imaju prefiks portal_ da mogu da stoje u istoj bazi
sa drugim aplikacijama.
"""
import os
import sqlite3

from flask import g

DATABASE_URL = os.environ.get("DATABASE_URL", "")
IS_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))
SQLITE_PATH = os.environ.get("SQLITE_PATH", "portal.db")

_ID = "SERIAL PRIMARY KEY" if IS_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"

SCHEMA = [
    # Clanovi tima. Prijavljuju se Google nalogom, pa lozinke ovde nema.
    f"""CREATE TABLE IF NOT EXISTS portal_team_users (
        id {_ID},
        email TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_login_at TEXT NOT NULL
    )""",
    f"""CREATE TABLE IF NOT EXISTS portal_clients (
        id {_ID},
        name TEXT NOT NULL,
        token TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL DEFAULT '',
        access_key TEXT NOT NULL DEFAULT '',
        link_active INTEGER NOT NULL DEFAULT 1,
        show_spend INTEGER NOT NULL DEFAULT 1,
        show_clicks INTEGER NOT NULL DEFAULT 1,
        show_conv INTEGER NOT NULL DEFAULT 1,
        show_campaigns INTEGER NOT NULL DEFAULT 1,
        show_terms INTEGER NOT NULL DEFAULT 0,
        show_combined INTEGER NOT NULL DEFAULT 1,
        conv_kind TEXT NOT NULL DEFAULT 'upit',
        range_start TEXT NOT NULL DEFAULT '',
        range_end TEXT NOT NULL DEFAULT '',
        layout TEXT NOT NULL DEFAULT '',
        logo_version TEXT NOT NULL DEFAULT '',
        failed_attempts INTEGER NOT NULL DEFAULT 0,
        locked_until INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    # Koje naloge klijent ima na kom kanalu (najvise jedan nalog po kanalu).
    # platform: google_ads, meta, microsoft, tiktok ili ga4.
    f"""CREATE TABLE IF NOT EXISTS portal_sources (
        id {_ID},
        client_id INTEGER NOT NULL,
        platform TEXT NOT NULL,
        account_id TEXT NOT NULL,
        account_name TEXT NOT NULL DEFAULT '',
        currency TEXT NOT NULL DEFAULT '',
        visible INTEGER NOT NULL DEFAULT 1,
        UNIQUE (client_id, platform)
    )""",
    # Veze sa platformama: po jedna za google, meta, microsoft i tiktok.
    # Token je sifrovan; bez SECRET_KEY-a je neupotrebljiv.
    """CREATE TABLE IF NOT EXISTS portal_connections (
        platform TEXT PRIMARY KEY,
        label TEXT NOT NULL,
        secret_enc TEXT NOT NULL,
        scopes TEXT NOT NULL DEFAULT '',
        connected_by TEXT NOT NULL,
        connected_at TEXT NOT NULL
    )""",
    # Logo klijenta. U bazi, a ne kao fajl, jer server brise fajlove pri svakom postavljanju.
    """CREATE TABLE IF NOT EXISTS portal_logos (
        client_id INTEGER PRIMARY KEY,
        mime TEXT NOT NULL,
        data TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    # Kes odgovora sa platformi, da se strane otvaraju brzo i da se ne trosi kvota.
    """CREATE TABLE IF NOT EXISTS portal_cache (
        cache_key TEXT PRIMARY KEY,
        payload TEXT NOT NULL,
        fetched_at INTEGER NOT NULL
    )""",
]


def _connect():
    if IS_PG:
        import psycopg2
        import psycopg2.extras

        return psycopg2.connect(
            DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor
        )
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_conn():
    if "db_conn" not in g:
        g.db_conn = _connect()
    return g.db_conn


def close_conn(_exc=None):
    conn = g.pop("db_conn", None)
    if conn is not None:
        conn.close()


def _sql(sql):
    return sql.replace("?", "%s") if IS_PG else sql


def query(sql, params=(), one=False):
    """SELECT: vraca listu redova (ili jedan red / None kad je one=True)."""
    cur = get_conn().cursor()
    cur.execute(_sql(sql), params)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql, params=()):
    """INSERT / UPDATE / DELETE, sa potvrdom (commit)."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(_sql(sql), params)
    conn.commit()
    cur.close()


def insert(sql, params=()):
    """INSERT koji vraca id novog reda. Upit mora da se zavrsi sa RETURNING id."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(_sql(sql), params)
    row = cur.fetchone()
    conn.commit()
    cur.close()
    return dict(row)["id"]


def _set_aside_demo_database():
    """Lokalna baza iz demo verzije ima drugaciju semu, pa se cuva pod drugim imenom."""
    if IS_PG or not os.path.exists(SQLITE_PATH):
        return
    conn = sqlite3.connect(SQLITE_PATH)
    columns = [row[1] for row in conn.execute("PRAGMA table_info(portal_team_users)")]
    conn.close()
    if "password_hash" in columns:
        backup = SQLITE_PATH + ".demo-backup"
        os.replace(SQLITE_PATH, backup)
        print(f"Stara demo baza je sacuvana kao {backup}; pravi se nova.")


def columns(table):
    """Imena kolona tabele (prazna lista ako tabela ne postoji)."""
    conn = _connect()
    cur = conn.cursor()
    if IS_PG:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s",
            (table,),
        )
        names = [dict(row)["column_name"] for row in cur.fetchall()]
    else:
        cur.execute(f"PRAGMA table_info({table})")
        names = [row[1] for row in cur.fetchall()]
    cur.close()
    conn.close()
    return names


def init_schema():
    _set_aside_demo_database()
    conn = _connect()
    cur = conn.cursor()
    # Verzija sa samo Google kanalom drzala je nalog u tabeli klijenata. Ta tabela
    # se sklanja pod drugo ime; podatke iz nje posle prenosi migrate.py.
    if "google_ads_id" in columns("portal_clients") and not columns("portal_clients_v1"):
        cur.execute("ALTER TABLE portal_clients RENAME TO portal_clients_v1")
    for statement in SCHEMA:
        cur.execute(statement)
    conn.commit()
    # Kolone dodate posle prvih verzija.
    existing = columns("portal_clients")
    for column in ("range_start", "range_end", "layout", "logo_version"):
        if column not in existing:
            cur.execute(f"ALTER TABLE portal_clients ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
    conn.commit()
    cur.close()
    conn.close()
