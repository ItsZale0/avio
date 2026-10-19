"""Importa un feed GTFS (zip) nel database locale.

Legge i file .txt standard (stops, routes, trips, stop_times, calendar,
calendar_dates, agency) e li carica in tabelle dedicate indicizzate.
Roba non presente nel feed viene semplicemente ignorata.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from . import db as gtfsdb
from .feeds import FEED_BY_ID

BATCH = 8000


def _name_map(zf: zipfile.ZipFile) -> dict[str, str]:
    """mappa 'stops.txt' -> percorso reale dentro lo zip (anche in sottocartelle)."""
    mapping: dict[str, str] = {}
    for name in zf.namelist():
        base = name.rsplit("/", 1)[-1].lower()
        if base.endswith(".txt"):
            mapping.setdefault(base, name)
    return mapping


def _reader(zf: zipfile.ZipFile, names: dict[str, str], filename: str) -> Iterator[dict]:
    real = names.get(filename.lower())
    if not real:
        return
    with zf.open(real) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        for row in csv.DictReader(text):
            yield row


def _secs(value: str | None) -> int | None:
    """'HH:MM:SS' -> secondi dalla mezzanotte (puo' superare 24 ore)."""
    if not value:
        return None
    parts = value.strip().split(":")
    try:
        nums = [int(part) for part in parts]
    except ValueError:
        return None
    if len(nums) == 3:
        hours, minutes, seconds = nums
    elif len(nums) == 2:
        hours, minutes = nums
        seconds = 0
    else:
        return None
    return hours * 3600 + minutes * 60 + seconds


def _int(value: str | None, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _float(value: str | None) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _chunks(rows: Iterable[tuple]) -> Iterator[list[tuple]]:
    batch: list[tuple] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= BATCH:
            yield batch
            batch = []
    if batch:
        yield batch


def import_feed(feed_id: str, zip_path: Path) -> dict:
    """Carica il feed nel database; sostituisce i dati precedenti dello stesso feed."""
    meta = FEED_BY_ID.get(feed_id, {"id": feed_id, "label": feed_id})
    gtfsdb.init()
    counts = {"stops": 0, "routes": 0, "trips": 0, "stop_times": 0}

    with zipfile.ZipFile(zip_path) as zf:
        names = _name_map(zf)
        with gtfsdb.db() as conn:
            for table in (
                "gtfs_agency",
                "gtfs_stops",
                "gtfs_routes",
                "gtfs_trips",
                "gtfs_stop_times",
                "gtfs_calendar",
                "gtfs_calendar_dates",
            ):
                conn.execute(f"DELETE FROM {table} WHERE feed = ?", (feed_id,))

            # agency
            for batch in _chunks(
                (
                    (
                        feed_id,
                        _clean(row.get("agency_id")) or "default",
                        _clean(row.get("agency_name")),
                        _clean(row.get("agency_url")),
                        _clean(row.get("agency_timezone")),
                    )
                    for row in _reader(zf, names, "agency.txt")
                )
            ):
                conn.executemany(
                    "INSERT OR REPLACE INTO gtfs_agency(feed, agency_id, name, url, timezone) "
                    "VALUES (?,?,?,?,?)",
                    batch,
                )

            # stops
            for batch in _chunks(
                (
                    (
                        feed_id,
                        _clean(row.get("stop_id")),
                        _clean(row.get("stop_name")),
                        _clean(row.get("stop_code")),
                        _float(row.get("stop_lat")),
                        _float(row.get("stop_lon")),
                    )
                    for row in _reader(zf, names, "stops.txt")
                    if _clean(row.get("stop_id"))
                )
            ):
                conn.executemany(
                    "INSERT OR REPLACE INTO gtfs_stops(feed, stop_id, name, code, lat, lon) "
                    "VALUES (?,?,?,?,?,?)",
                    batch,
                )
                counts["stops"] += len(batch)

            # routes
            for batch in _chunks(
                (
                    (
                        feed_id,
                        _clean(row.get("route_id")),
                        _clean(row.get("route_short_name")),
                        _clean(row.get("route_long_name")),
                        _int(row.get("route_type"), -1),
                        _clean(row.get("agency_id")),
                        _clean(row.get("route_color")),
                    )
                    for row in _reader(zf, names, "routes.txt")
                    if _clean(row.get("route_id"))
                )
            ):
                conn.executemany(
                    "INSERT OR REPLACE INTO gtfs_routes"
                    "(feed, route_id, short_name, long_name, route_type, agency_id, color) "
                    "VALUES (?,?,?,?,?,?,?)",
                    batch,
                )
                counts["routes"] += len(batch)

            # trips
            for batch in _chunks(
                (
                    (
                        feed_id,
                        _clean(row.get("trip_id")),
                        _clean(row.get("route_id")),
                        _clean(row.get("service_id")),
                        _clean(row.get("trip_headsign")),
                    )
                    for row in _reader(zf, names, "trips.txt")
                    if _clean(row.get("trip_id"))
                )
            ):
                conn.executemany(
                    "INSERT OR REPLACE INTO gtfs_trips(feed, trip_id, route_id, service_id, headsign) "
                    "VALUES (?,?,?,?,?)",
                    batch,
                )
                counts["trips"] += len(batch)

            # stop_times
            for batch in _chunks(
                (
                    (
                        feed_id,
                        _clean(row.get("trip_id")),
                        _clean(row.get("stop_id")),
                        _int(row.get("stop_sequence"), 0),
                        _secs(row.get("arrival_time")),
                        _secs(row.get("departure_time")),
                    )
                    for row in _reader(zf, names, "stop_times.txt")
                    if _clean(row.get("trip_id")) and _clean(row.get("stop_id"))
                )
            ):
                conn.executemany(
                    "INSERT OR REPLACE INTO gtfs_stop_times"
                    "(feed, trip_id, stop_id, seq, arrival, departure) VALUES (?,?,?,?,?,?)",
                    batch,
                )
                counts["stop_times"] += len(batch)

            # calendar
            for batch in _chunks(
                (
                    (
                        feed_id,
                        _clean(row.get("service_id")),
                        _clean(row.get("start_date")),
                        _clean(row.get("end_date")),
                        _int(row.get("monday")),
                        _int(row.get("tuesday")),
                        _int(row.get("wednesday")),
                        _int(row.get("thursday")),
                        _int(row.get("friday")),
                        _int(row.get("saturday")),
                        _int(row.get("sunday")),
                    )
                    for row in _reader(zf, names, "calendar.txt")
                    if _clean(row.get("service_id"))
                )
            ):
                conn.executemany(
                    "INSERT OR REPLACE INTO gtfs_calendar"
                    "(feed, service_id, start_date, end_date, mon, tue, wed, thu, fri, sat, sun) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    batch,
                )

            # calendar_dates
            for batch in _chunks(
                (
                    (
                        feed_id,
                        _clean(row.get("service_id")),
                        _clean(row.get("date")),
                        _int(row.get("exception_type")),
                    )
                    for row in _reader(zf, names, "calendar_dates.txt")
                    if _clean(row.get("service_id")) and _clean(row.get("date"))
                )
            ):
                conn.executemany(
                    "INSERT OR REPLACE INTO gtfs_calendar_dates"
                    "(feed, service_id, date, exception) VALUES (?,?,?,?)",
                    batch,
                )

            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO gtfs_feeds(id, label, region, url, license, site, downloaded_at, "
                "imported_at, stops, trips, bytes) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET label=excluded.label, region=excluded.region, "
                "url=excluded.url, license=excluded.license, site=excluded.site, "
                "imported_at=excluded.imported_at, stops=excluded.stops, trips=excluded.trips, "
                "bytes=excluded.bytes, downloaded_at=excluded.downloaded_at",
                (
                    feed_id,
                    meta.get("label"),
                    meta.get("region"),
                    meta.get("url"),
                    meta.get("license"),
                    meta.get("site"),
                    now,
                    now,
                    counts["stops"],
                    counts["trips"],
                    zip_path.stat().st_size if zip_path.exists() else 0,
                ),
            )

    return {"feed": feed_id, **counts}
