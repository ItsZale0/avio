"""Motore di coincidenze GTFS.

Collega fermate reali trovando:
  * corse **dirette** (stessa corsa da una fermata di partenza a una di arrivo);
  * corse con **un cambio** (partenza -> fermata X -> arrivo), rispettando un
    tempo minimo di coincidenza.

Gli orari sono in secondi dalla mezzanotte del giorno di servizio (possono
superare le 24 ore, es. 25:10). Il fuso e' quello dell'azienda (Europe/Rome).
"""

from __future__ import annotations

import math
from datetime import date as Date
from typing import Any, Iterable, Iterator

from ..geo import haversine_km
from . import db as gtfsdb

WEEKDAY_COLS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MIN_TRANSFER_SEC = 300
MAX_TRANSFER_WAIT_SEC = 3 * 3600
WALK_KM = 0.4
WALK_SPEED_KMH = 4.5
DEFAULT_RADIUS_KM = 8.0
MAX_STOPS_PER_SIDE = 16
EXTRA_BUSY_STOPS = 4
MAX_LEG_ROWS = 40000
MAX_X_STOPS = 900
MAX_WALK_ORIGINS = 250
MAX_WALK_NEIGHBORS = 3
MAX_BOARD_STOPS = 3000
EARTH_KM = 6371.0


def _chunks(items: list, size: int = 800) -> Iterator[list]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _xy(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    x = math.radians(lon - lon0) * EARTH_KM * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * EARTH_KM
    return x, y


def _point_segment_km(plat: float, plon: float, alat: float, alon: float, blat: float, blon: float) -> float:
    lat0 = (alat + blat) / 2
    lon0 = (alon + blon) / 2
    px, py = _xy(plat, plon, lat0, lon0)
    ax, ay = _xy(alat, alon, lat0, lon0)
    bx, by = _xy(blat, blon, lat0, lon0)
    dx, dy = bx - ax, by - ay
    base = dx * dx + dy * dy
    if base == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / base))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _departure_counts(conn, feed: str, stop_ids: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for chunk in _chunks(stop_ids):
        placeholders = ",".join("?" * len(chunk))
        for row in conn.execute(
            f"SELECT stop_id, COUNT(*) AS n FROM gtfs_stop_times "
            f"WHERE feed = ? AND stop_id IN ({placeholders}) GROUP BY stop_id",
            [feed, *chunk],
        ):
            counts[row["stop_id"]] = row["n"]
    return counts


def nearby_stops(conn, feeds: list[str], lat: float, lon: float, radius_km: float, limit: int = MAX_STOPS_PER_SIDE) -> list[dict]:
    if not feeds:
        return []
    pad = radius_km / 111.0 + 0.03
    placeholders = ",".join("?" * len(feeds))
    rows = conn.execute(
        f"SELECT feed, stop_id, name, code, lat, lon FROM gtfs_stops "
        f"WHERE feed IN ({placeholders}) AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
        [*feeds, lat - pad, lat + pad, lon - pad, lon + pad],
    ).fetchall()
    scored: list[tuple[float, dict]] = []
    for row in rows:
        if row["lat"] is None or row["lon"] is None:
            continue
        distance = haversine_km(lat, lon, row["lat"], row["lon"])
        if distance <= radius_km:
            scored.append((distance, dict(row)))
    scored.sort(key=lambda item: item[0])
    seen: set[tuple] = set()
    unique: list[dict] = []
    for distance, row in scored:
        key = (round(row["lat"], 4), round(row["lon"], 4), (row["name"] or "").lower()[:24])
        if key in seen:
            continue
        seen.add(key)
        row["distance_km"] = round(distance, 2)
        unique.append(row)

    # Le fermate piu' vicine non bastano sempre: una stazione importante (es. la
    # ferrovia Trento-Male') puo' essere poco piu' lontana ma servire molte piu'
    # corse. Aggiungiamo le piu' trafficate nel raggio.
    chosen = unique[:limit]
    remainder = unique[limit:]
    if remainder:
        by_feed: dict[str, list[dict]] = {}
        for row in remainder:
            by_feed.setdefault(row["feed"], []).append(row)
        for feed, candidates in by_feed.items():
            counts = _departure_counts(conn, feed, [row["stop_id"] for row in candidates])
            candidates.sort(key=lambda row: counts.get(row["stop_id"], 0), reverse=True)
            chosen.extend(row for row in candidates[:EXTRA_BUSY_STOPS] if counts.get(row["stop_id"], 0) > 0)
    return chosen


def active_services(conn, feed: str, day: Date) -> set[str]:
    stamp = day.strftime("%Y%m%d")
    column = WEEKDAY_COLS[day.weekday()]
    active: set[str] = set()
    for row in conn.execute(
        f"SELECT service_id, start_date, end_date, {column} AS dow FROM gtfs_calendar WHERE feed = ?",
        (feed,),
    ):
        if (row["start_date"] or "00000000") <= stamp <= (row["end_date"] or "99999999") and row["dow"]:
            active.add(row["service_id"])
    for row in conn.execute(
        "SELECT service_id, exception FROM gtfs_calendar_dates WHERE feed = ? AND date = ?",
        (feed, stamp),
    ):
        if row["exception"] == 1:
            active.add(row["service_id"])
        elif row["exception"] == 2:
            active.discard(row["service_id"])
    return active


def _stops_by_id(conn, feed: str, ids: Iterable[str]) -> dict[str, dict]:
    unique = [value for value in dict.fromkeys(ids) if value]
    out: dict[str, dict] = {}
    for chunk in _chunks(unique):
        placeholders = ",".join("?" * len(chunk))
        for row in conn.execute(
            f"SELECT stop_id, name, lat, lon FROM gtfs_stops WHERE feed = ? AND stop_id IN ({placeholders})",
            [feed, *chunk],
        ):
            out[row["stop_id"]] = dict(row)
    return out


def _enrich(conn, feed: str, trip_ids: Iterable[str]) -> tuple[dict, dict, dict]:
    unique = [value for value in dict.fromkeys(trip_ids) if value]
    trips: dict[str, dict] = {}
    for chunk in _chunks(unique):
        placeholders = ",".join("?" * len(chunk))
        for row in conn.execute(
            f"SELECT trip_id, route_id, service_id, headsign FROM gtfs_trips "
            f"WHERE feed = ? AND trip_id IN ({placeholders})",
            [feed, *chunk],
        ):
            trips[row["trip_id"]] = dict(row)

    route_ids = [t["route_id"] for t in trips.values() if t.get("route_id")]
    routes: dict[str, dict] = {}
    for chunk in _chunks([value for value in dict.fromkeys(route_ids)]):
        placeholders = ",".join("?" * len(chunk))
        for row in conn.execute(
            f"SELECT route_id, short_name, long_name, route_type, agency_id FROM gtfs_routes "
            f"WHERE feed = ? AND route_id IN ({placeholders})",
            [feed, *chunk],
        ):
            routes[row["route_id"]] = dict(row)

    agency_ids = [r.get("agency_id") for r in routes.values() if r.get("agency_id")]
    agencies: dict[str, dict] = {}
    for chunk in _chunks([value for value in dict.fromkeys(agency_ids)]):
        placeholders = ",".join("?" * len(chunk))
        for row in conn.execute(
            f"SELECT agency_id, name, url FROM gtfs_agency WHERE feed = ? AND agency_id IN ({placeholders})",
            [feed, *chunk],
        ):
            agencies[row["agency_id"]] = dict(row)
    return trips, routes, agencies


def _line_label(route: dict | None) -> str:
    if not route:
        return ""
    short = (route.get("short_name") or "").strip()
    long = (route.get("long_name") or "").strip()
    if short and long and long.lower() != short.lower():
        return f"{short} · {long}"
    return short or long


def _leg(trip_id, dep_stop, arr_stop, dep_sec, arr_sec, stops, trips, routes, agencies) -> dict | None:
    trip = trips.get(trip_id)
    if not trip:
        return None
    route = routes.get(trip.get("route_id") or "")
    agency_id = (route or {}).get("agency_id")
    agency = agencies.get(agency_id or "") or {}
    dep = stops.get(dep_stop) or {}
    arr = stops.get(arr_stop) or {}
    return {
        "trip_id": trip_id,
        "route_id": trip.get("route_id"),
        "line": _line_label(route),
        "headsign": (trip.get("headsign") or "").strip(),
        "route_type": (route or {}).get("route_type"),
        "agency": (agency.get("name") or "").strip() or "Trasporto pubblico",
        "agency_url": (agency.get("url") or "").strip() or None,
        "dep_stop": dep_stop,
        "dep_name": (dep.get("name") or "").strip(),
        "dep_lat": dep.get("lat"),
        "dep_lon": dep.get("lon"),
        "dep_sec": dep_sec,
        "arr_stop": arr_stop,
        "arr_name": (arr.get("name") or "").strip(),
        "arr_lat": arr.get("lat"),
        "arr_lon": arr.get("lon"),
        "arr_sec": arr_sec,
    }


def _direct(conn, feed, origin_ids, dest_ids, stops, dep_min, dep_max, active) -> list[dict]:
    ph_from = ",".join("?" * len(origin_ids))
    ph_to = ",".join("?" * len(dest_ids))
    rows = conn.execute(
        f"""
        SELECT a.trip_id AS trip_id, a.stop_id AS dep_stop,
               COALESCE(a.departure, a.arrival) AS dep_sec,
               b.stop_id AS arr_stop,
               COALESCE(b.arrival, b.departure) AS arr_sec
        FROM gtfs_stop_times a
        JOIN gtfs_stop_times b
          ON b.feed = a.feed AND b.trip_id = a.trip_id AND b.seq > a.seq
        WHERE a.feed = ? AND a.stop_id IN ({ph_from}) AND b.stop_id IN ({ph_to})
          AND COALESCE(a.departure, a.arrival) BETWEEN ? AND ?
        LIMIT ?
        """,
        [feed, *origin_ids, *dest_ids, dep_min, dep_max, MAX_LEG_ROWS],
    ).fetchall()

    trips, routes, agencies = _enrich(conn, feed, (r["trip_id"] for r in rows))
    best: dict[str, dict] = {}
    for row in rows:
        trip = trips.get(row["trip_id"])
        if not trip or trip.get("service_id") not in active:
            continue
        if row["dep_sec"] is None or row["arr_sec"] is None or row["arr_sec"] <= row["dep_sec"]:
            continue
        leg = _leg(
            row["trip_id"], row["dep_stop"], row["arr_stop"],
            row["dep_sec"], row["arr_sec"], stops, trips, routes, agencies,
        )
        if not leg:
            continue
        key = row["trip_id"]
        current = best.get(key)
        if current is None or row["arr_sec"] < current["arr_sec"]:
            best[key] = leg
    return [
        {"transfers": 0, "legs": [leg]}
        for leg in best.values()
    ]


def _corridor_ids(conn, feed, from_lat, from_lon, to_lat, to_lon, radius_km) -> dict[str, tuple[float, float]]:
    band = max(radius_km * 3.0, 30.0)
    lat0 = (from_lat + to_lat) / 2
    pad_lat = band / 111.0 + 0.05
    pad_lon = band / max(0.2, 111.0 * math.cos(math.radians(lat0))) + 0.05
    rows = conn.execute(
        "SELECT stop_id, lat, lon FROM gtfs_stops WHERE feed = ? AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
        (feed, min(from_lat, to_lat) - pad_lat, max(from_lat, to_lat) + pad_lat,
         min(from_lon, to_lon) - pad_lon, max(from_lon, to_lon) + pad_lon),
    ).fetchall()
    ids: dict[str, tuple[float, float]] = {}
    for row in rows:
        if row["lat"] is None or row["lon"] is None:
            continue
        if _point_segment_km(row["lat"], row["lon"], from_lat, from_lon, to_lat, to_lon) <= band:
            ids[row["stop_id"]] = (row["lat"], row["lon"])
    return ids


def _walk_candidates(corridor: dict[str, tuple[float, float]], origin: str, *, limit: int, walk_km: float = WALK_KM) -> list[tuple[str, float, int]]:
    """Fermate del corridoio a piedi dall'origine: (stop_id, km, secondi)."""
    coords = corridor.get(origin)
    if not coords:
        return []
    lat, lon = coords
    found: list[tuple[float, str]] = []
    for stop_id, (other_lat, other_lon) in corridor.items():
        if stop_id == origin:
            continue
        if abs(other_lat - lat) > 0.02 or abs(other_lon - lon) > 0.03:
            continue
        distance = haversine_km(lat, lon, other_lat, other_lon)
        if distance <= walk_km:
            found.append((distance, stop_id))
    found.sort()
    return [
        (stop_id, round(distance, 3), int(distance / WALK_SPEED_KMH * 3600) + 60)
        for distance, stop_id in found[:limit]
    ]


class _WalkIndex:
    """Indice spaziale delle fermate, per trovare i punti di cambio a piedi."""

    def __init__(self, corridor: dict[str, tuple[float, float]]) -> None:
        self.coords = corridor
        self.cell = WALK_KM / 111.0
        self.grid: dict[tuple[int, int], list[str]] = {}
        for stop_id, (lat, lon) in corridor.items():
            self.grid.setdefault(self._key(lat, lon), []).append(stop_id)

    def _key(self, lat: float, lon: float) -> tuple[int, int]:
        return (int(math.floor(lat / self.cell)), int(math.floor(lon / self.cell)))

    def near(self, stop_id: str, *, limit: int, walk_km: float = WALK_KM) -> list[tuple[str, float, int]]:
        coords = self.coords.get(stop_id)
        if not coords:
            return []
        lat, lon = coords
        kx, ky = self._key(lat, lon)
        found: list[tuple[float, str]] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other in self.grid.get((kx + dx, ky + dy), ()):
                    if other == stop_id:
                        continue
                    other_lat, other_lon = self.coords[other]
                    distance = haversine_km(lat, lon, other_lat, other_lon)
                    if distance <= walk_km:
                        found.append((distance, other))
        found.sort()
        return [
            (other, round(distance, 3), int(distance / WALK_SPEED_KMH * 3600) + 60)
            for distance, other in found[:limit]
        ]


def _one_transfer(conn, feed, origin_ids, dest_ids, stops, from_lat, from_lon, to_lat, to_lon, radius_km, dep_min, dep_max, active) -> list[dict]:
    corridor = _corridor_ids(conn, feed, from_lat, from_lon, to_lat, to_lon, radius_km)
    if not corridor:
        return []
    dest_set = set(dest_ids)
    ph_from = ",".join("?" * len(origin_ids))
    leg1 = conn.execute(
        f"""
        SELECT a.trip_id AS t1, a.stop_id AS dep_stop,
               COALESCE(a.departure, a.arrival) AS dep_sec,
               b.stop_id AS x_stop,
               COALESCE(b.arrival, b.departure) AS arr1_sec,
               sx.lat AS x_lat, sx.lon AS x_lon
        FROM gtfs_stop_times a
        JOIN gtfs_stop_times b
          ON b.feed = a.feed AND b.trip_id = a.trip_id AND b.seq > a.seq
        JOIN gtfs_stops sx
          ON sx.feed = b.feed AND sx.stop_id = b.stop_id
        WHERE a.feed = ? AND a.stop_id IN ({ph_from})
          AND COALESCE(a.departure, a.arrival) BETWEEN ? AND ?
        LIMIT ?
        """,
        [feed, *origin_ids, dep_min, dep_max, MAX_LEG_ROWS],
    ).fetchall()

    best_x: dict[str, Any] = {}
    leg1_trips, _, _ = _enrich(conn, feed, (row["t1"] for row in leg1))
    for row in leg1:
        if row["x_stop"] not in corridor or row["x_stop"] in dest_set:
            continue
        if row["dep_sec"] is None or row["arr1_sec"] is None:
            continue
        trip = leg1_trips.get(row["t1"])
        if not trip or trip.get("service_id") not in active:
            continue
        current = best_x.get(row["x_stop"])
        if current is None or row["arr1_sec"] < current["arr1_sec"]:
            best_x[row["x_stop"]] = row
    if not best_x:
        return []

    def _to_dest(row: Any) -> float:
        if row["x_lat"] is None or row["x_lon"] is None:
            return 1e9
        return haversine_km(row["x_lat"], row["x_lon"], to_lat, to_lon)

    # Priorita' ai punti di cambio piu' vicini alla destinazione: sono i piu'
    # probabili per l'ultima tratta.
    ranked_x = sorted(best_x.items(), key=lambda item: _to_dest(item[1]))

    # board_map: fermata di salita della seconda corsa -> [(fermata di arrivo
    # della prima corsa, secondi a piedi)]. Lo stesso punto di cambio spesso ha
    # due fermate distinte a pochi metri (es. "Mezzana. Ftm" e "Mezzana. Staz
    # Ftm"): senza questo si perdono molte coincidenze reali.
    board_map: dict[str, list[tuple[str, float, int]]] = {}
    for stop_id, _ in ranked_x[:MAX_X_STOPS]:
        board_map.setdefault(stop_id, []).append((stop_id, 0.0, 0))
    walk_index = _WalkIndex(corridor)
    for stop_id, _ in ranked_x[:MAX_X_STOPS]:
        for other, distance, seconds in walk_index.near(stop_id, limit=MAX_WALK_NEIGHBORS):
            if other in dest_set:
                continue
            entries = board_map.setdefault(other, [])
            if len(entries) >= MAX_WALK_NEIGHBORS:
                continue
            entries.append((stop_id, distance, seconds))

    board_ids = list(board_map.keys())[:MAX_BOARD_STOPS]
    if not board_ids:
        return []
    ph_x = ",".join("?" * len(board_ids))
    ph_to = ",".join("?" * len(dest_ids))
    leg2 = conn.execute(
        f"""
        SELECT a.trip_id AS t2, a.stop_id AS x_stop,
               COALESCE(a.departure, a.arrival) AS dep2_sec,
               b.stop_id AS arr_stop,
               COALESCE(b.arrival, b.departure) AS arr2_sec
        FROM gtfs_stop_times a
        JOIN gtfs_stop_times b
          ON b.feed = a.feed AND b.trip_id = a.trip_id AND b.seq > a.seq
        WHERE a.feed = ? AND a.stop_id IN ({ph_x}) AND b.stop_id IN ({ph_to})
          AND COALESCE(a.departure, a.arrival) BETWEEN ? AND ?
        LIMIT ?
        """,
        [feed, *board_ids, *dest_ids, dep_min, dep_max, MAX_LEG_ROWS],
    ).fetchall()

    pairs: dict[tuple, dict] = {}
    for row in leg2:
        if row["dep2_sec"] is None or row["arr2_sec"] is None or row["arr2_sec"] <= row["dep2_sec"]:
            continue
        for origin_x, walk_km, walk_sec in board_map.get(row["x_stop"], ()):
            first = best_x.get(origin_x)
            if first is None:
                continue
            if row["dep2_sec"] < first["arr1_sec"] + MIN_TRANSFER_SEC + walk_sec:
                continue
            if row["dep2_sec"] - first["arr1_sec"] > MAX_TRANSFER_WAIT_SEC:
                continue
            key = (first["t1"], row["t2"])
            current = pairs.get(key)
            if current is None or row["arr2_sec"] < current["second"]["arr2_sec"]:
                pairs[key] = {"first": first, "second": row, "walk_km": walk_km, "walk_sec": walk_sec}

    if not pairs:
        return []

    trip_ids = [p["first"]["t1"] for p in pairs.values()] + [p["second"]["t2"] for p in pairs.values()]
    trips, routes, agencies = _enrich(conn, feed, trip_ids)
    x_stops = _stops_by_id(
        conn, feed,
        [value for pair in pairs.values() for value in (pair["first"]["x_stop"], pair["second"]["x_stop"])],
    )
    lookup = {**stops, **x_stops}

    journeys: list[dict] = []
    for pair in pairs.values():
        first, second = pair["first"], pair["second"]
        trip1 = trips.get(first["t1"])
        trip2 = trips.get(second["t2"])
        if not trip1 or not trip2:
            continue
        if trip1.get("service_id") not in active or trip2.get("service_id") not in active:
            continue
        leg_a = _leg(first["t1"], first["dep_stop"], first["x_stop"], first["dep_sec"], first["arr1_sec"], lookup, trips, routes, agencies)
        leg_b = _leg(second["t2"], second["x_stop"], second["arr_stop"], second["dep2_sec"], second["arr2_sec"], lookup, trips, routes, agencies)
        if not (leg_a and leg_b):
            continue
        if pair["walk_km"]:
            leg_b["walk_from_name"] = leg_a["arr_name"]
            leg_b["walk_from_lat"] = leg_a["arr_lat"]
            leg_b["walk_from_lon"] = leg_a["arr_lon"]
            leg_b["walk_km"] = round(pair["walk_km"], 2)
            leg_b["walk_sec"] = pair["walk_sec"]
        journeys.append({"transfers": 1, "legs": [leg_a, leg_b]})
    return journeys


def find_connections(
    *,
    feeds: list[str],
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
    day: Date,
    dep_min_sec: int = 0,
    dep_max_sec: int = 30 * 3600,
    radius_km: float = DEFAULT_RADIUS_KM,
    max_results: int = 20,
    max_transfers: int = 1,
) -> list[dict]:
    journeys: list[dict] = []
    with gtfsdb.db() as conn:
        for feed in feeds:
            origin = nearby_stops(conn, [feed], from_lat, from_lon, radius_km)
            if not origin:
                continue
            dest = nearby_stops(conn, [feed], to_lat, to_lon, radius_km)
            if not dest:
                continue
            active = active_services(conn, feed, day)
            if not active:
                continue
            origin_ids = [stop["stop_id"] for stop in origin]
            dest_ids = [stop["stop_id"] for stop in dest]
            stops = {stop["stop_id"]: stop for stop in (*origin, *dest)}

            for journey in _direct(conn, feed, origin_ids, dest_ids, stops, dep_min_sec, dep_max_sec, active):
                journey["feed"] = feed
                journey["date"] = day.isoformat()
                journeys.append(journey)
            if max_transfers >= 1:
                for journey in _one_transfer(
                    conn, feed, origin_ids, dest_ids, stops,
                    from_lat, from_lon, to_lat, to_lon, radius_km,
                    dep_min_sec, dep_max_sec, active,
                ):
                    journey["feed"] = feed
                    journey["date"] = day.isoformat()
                    journeys.append(journey)

    journeys.sort(key=lambda j: (j["legs"][0]["dep_sec"], j["legs"][-1]["arr_sec"], j["transfers"]))
    journeys = _pareto(journeys)
    result: list[dict] = []
    seen: set[tuple] = set()
    for journey in journeys:
        key = (journey["feed"], journey["legs"][0]["trip_id"], journey["legs"][-1]["trip_id"])
        if key in seen:
            continue
        seen.add(key)
        result.append(journey)
        if len(result) >= max_results:
            break
    return result


def _pareto(journeys: list[dict]) -> list[dict]:
    """Tiene solo le soluzioni non dominate.

    Un collegamento e' inutile se ne esiste un altro che parte piu' tardi e
    arriva prima (o alla stessa ora): lo scartiamo. Elimina anche le catene di
    cambi con attese lunghissime verso la stessa destinazione.
    """
    ordered = sorted(
        journeys,
        key=lambda j: (-j["legs"][0]["dep_sec"], j["legs"][-1]["arr_sec"], j["transfers"]),
    )
    kept: list[dict] = []
    best_arrival = None
    for journey in ordered:
        arrival = journey["legs"][-1]["arr_sec"]
        if best_arrival is None or arrival < best_arrival:
            kept.append(journey)
            best_arrival = arrival
    kept.sort(key=lambda j: (j["legs"][0]["dep_sec"], j["legs"][-1]["arr_sec"], j["transfers"]))
    return kept
