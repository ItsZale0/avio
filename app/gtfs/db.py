"""Orari pubblici in formato GTFS (database dedicato, separato da carpool.db)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from ..config import DATA_DIR

GTFS_DB_PATH = DATA_DIR / "gtfs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS gtfs_feeds (
    id            TEXT PRIMARY KEY,
    label         TEXT,
    region        TEXT,
    url           TEXT,
    license       TEXT,
    site          TEXT,
    downloaded_at TEXT,
    imported_at   TEXT,
    stops         INTEGER NOT NULL DEFAULT 0,
    trips         INTEGER NOT NULL DEFAULT 0,
    bytes         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS gtfs_agency (
    feed      TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    name      TEXT,
    url       TEXT,
    timezone  TEXT,
    PRIMARY KEY (feed, agency_id)
);

CREATE TABLE IF NOT EXISTS gtfs_stops (
    feed    TEXT NOT NULL,
    stop_id TEXT NOT NULL,
    name    TEXT,
    code    TEXT,
    lat     REAL,
    lon     REAL,
    PRIMARY KEY (feed, stop_id)
);

CREATE TABLE IF NOT EXISTS gtfs_routes (
    feed       TEXT NOT NULL,
    route_id   TEXT NOT NULL,
    short_name TEXT,
    long_name  TEXT,
    route_type INTEGER,
    agency_id  TEXT,
    color      TEXT,
    PRIMARY KEY (feed, route_id)
);

CREATE TABLE IF NOT EXISTS gtfs_trips (
    feed       TEXT NOT NULL,
    trip_id    TEXT NOT NULL,
    route_id   TEXT,
    service_id TEXT,
    headsign   TEXT,
    PRIMARY KEY (feed, trip_id)
);

CREATE TABLE IF NOT EXISTS gtfs_stop_times (
    feed      TEXT NOT NULL,
    trip_id   TEXT NOT NULL,
    stop_id   TEXT NOT NULL,
    seq       INTEGER NOT NULL,
    arrival   INTEGER,
    departure INTEGER,
    PRIMARY KEY (feed, trip_id, seq)
);

CREATE TABLE IF NOT EXISTS gtfs_calendar (
    feed       TEXT NOT NULL,
    service_id TEXT NOT NULL,
    start_date TEXT,
    end_date   TEXT,
    mon INTEGER, tue INTEGER, wed INTEGER, thu INTEGER,
    fri INTEGER, sat INTEGER, sun INTEGER,
    PRIMARY KEY (feed, service_id)
);

CREATE TABLE IF NOT EXISTS gtfs_calendar_dates (
    feed       TEXT NOT NULL,
    service_id TEXT NOT NULL,
    date       TEXT NOT NULL,
    exception  INTEGER,
    PRIMARY KEY (feed, service_id, date)
);

CREATE INDEX IF NOT EXISTS idx_gtfs_stops_geo  ON gtfs_stops(feed, lat, lon);
CREATE INDEX IF NOT EXISTS idx_gtfs_stt_stop   ON gtfs_stop_times(feed, stop_id);
CREATE INDEX IF NOT EXISTS idx_gtfs_stt_trip   ON gtfs_stop_times(feed, trip_id, seq);
CREATE INDEX IF NOT EXISTS idx_gtfs_trips_svc  ON gtfs_trips(feed, service_id);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(GTFS_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
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


def init() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)


def imported_feeds() -> list[dict]:
    init()
    with db() as conn:
        rows = conn.execute("SELECT * FROM gtfs_feeds ORDER BY region, label").fetchall()
    return [dict(row) for row in rows]


def imported_feed_ids() -> list[str]:
    return [row["id"] for row in imported_feeds()]


def has_data() -> bool:
    try:
        with db() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM gtfs_stop_times").fetchone()
        return bool(row and row["n"])
    except sqlite3.OperationalError:
        return False
