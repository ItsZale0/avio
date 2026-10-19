"""Accesso SQLite: connessione per operazione, WAL, schema idempotente."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from .config import DATA_DIR

DB_PATH = DATA_DIR / "carpool.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT,
    from_place    TEXT NOT NULL,
    from_lat      REAL,
    from_lon      REAL,
    to_place      TEXT NOT NULL,
    to_lat        REAL,
    to_lon        REAL,
    depart_after  TEXT,
    arrive_by     TEXT NOT NULL,
    max_price     REAL,
    passengers    INTEGER NOT NULL DEFAULT 1,
    radius_km     REAL NOT NULL DEFAULT 5,
    sources       TEXT NOT NULL DEFAULT '[]',
    notify        TEXT NOT NULL DEFAULT '["web"]',
    interval_min  INTEGER NOT NULL DEFAULT 60,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    last_run_at   TEXT,
    next_run_at   TEXT
);

CREATE TABLE IF NOT EXISTS offers (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id       INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
    fingerprint    TEXT NOT NULL,
    source         TEXT NOT NULL,
    mode           TEXT NOT NULL,
    operator       TEXT,
    depart_at      TEXT,
    arrive_at      TEXT,
    price          REAL,
    currency       TEXT NOT NULL DEFAULT 'EUR',
    price_is_estimate INTEGER NOT NULL DEFAULT 0,
    pickup_name    TEXT,
    pickup_lat     REAL,
    pickup_lon     REAL,
    dropoff_name   TEXT,
    dropoff_lat    REAL,
    dropoff_lon    REAL,
    duration_min   INTEGER,
    seats          INTEGER,
    rating         REAL,
    deeplink       TEXT,
    score          REAL,
    badges         TEXT NOT NULL DEFAULT '[]',
    details        TEXT NOT NULL DEFAULT '{}',
    first_seen     TEXT NOT NULL,
    last_seen      TEXT NOT NULL,
    prev_price     REAL,
    active         INTEGER NOT NULL DEFAULT 1,
    UNIQUE (watch_id, fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_offers_watch ON offers(watch_id, active, score);
CREATE INDEX IF NOT EXISTS idx_offers_depart ON offers(depart_at);

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id     INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
    trigger      TEXT NOT NULL DEFAULT 'schedule',
    status       TEXT NOT NULL DEFAULT 'running',
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    offers_found INTEGER NOT NULL DEFAULT 0,
    alerts_sent  INTEGER NOT NULL DEFAULT 0,
    errors       TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id   INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
    offer_id   INTEGER REFERENCES offers(id) ON DELETE SET NULL,
    run_id     INTEGER,
    kind       TEXT NOT NULL,
    channel    TEXT NOT NULL DEFAULT 'web',
    title      TEXT NOT NULL,
    message    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at DESC);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS geocache (
    query        TEXT PRIMARY KEY,
    lat          REAL,
    lon          REAL,
    display_name TEXT,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS questions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id       INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
    run_id         INTEGER,
    key            TEXT NOT NULL,
    text           TEXT NOT NULL,
    options        TEXT NOT NULL DEFAULT '[]',
    allow_free_text INTEGER NOT NULL DEFAULT 1,
    status         TEXT NOT NULL DEFAULT 'pending',
    answer         TEXT,
    answer_source  TEXT,
    created_at     TEXT NOT NULL,
    answered_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_questions_watch ON questions(watch_id, status);

CREATE TABLE IF NOT EXISTS plans (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id          INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
    run_id            INTEGER,
    kind              TEXT NOT NULL DEFAULT 'cheapest',
    title             TEXT NOT NULL,
    total_price       REAL,
    total_duration_min INTEGER,
    steps             TEXT NOT NULL DEFAULT '[]',
    explanation       TEXT,
    watchouts         TEXT NOT NULL DEFAULT '[]',
    confidence        REAL,
    fingerprint       TEXT,
    created_at        TEXT NOT NULL,
    is_current        INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_plans_watch ON plans(watch_id, is_current);

CREATE TABLE IF NOT EXISTS ai_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id   INTEGER,
    run_id     INTEGER,
    task       TEXT NOT NULL,
    model      TEXT,
    tokens     INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    ok         INTEGER NOT NULL DEFAULT 1,
    error      TEXT,
    created_at TEXT NOT NULL
);
"""

# Colonne aggiunte nel tempo: create con ALTER se mancano (migrazione idempotente).
MIGRATIONS = [
    ("runs", "notes", "TEXT NOT NULL DEFAULT '[]'"),
    ("runs", "ai_used", "INTEGER NOT NULL DEFAULT 0"),
    ("runs", "ai_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("runs", "plan_price", "REAL"),
    ("watches", "prefs", "TEXT NOT NULL DEFAULT '{}'"),
    ("watches", "ai_summary", "TEXT"),
    ("watches", "ai_plan_fingerprint", "TEXT"),
    ("watches", "ai_best_price", "REAL"),
    ("watches", "ai_updated_at", "TEXT"),
]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        for table, column, definition in MIGRATIONS:
            _ensure_column(conn, table, column, definition)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def rows_to_dicts(rows) -> list[dict[str, Any]]:
    return [row_to_dict(r) for r in rows]  # type: ignore[misc]


def load_json(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def dump_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
