"""Fermate e stazioni vicine a partenza/arrivo (OpenStreetMap Overpass).

Non e' un orario: serve a capire quali mezzi passano vicino ai punti scelti,
cosi' da valutare combinazioni "a piedi + mezzo pubblico".

Overpass e' un servizio pubblico gratuito e a volte lento/indisponibile:
proviamo piu' mirror in parallelo, con timeout breve e cache. Se nessuno
risponde, la ricerca continua senza questa fonte.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from ..config import USER_AGENT
from ..geo import haversine_km
from .base import Adapter, AdapterResult, Offer, SearchContext

log = logging.getLogger("carpool.transit")

MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

TIMEOUT = 9.0
CACHE_TTL = 6 * 3600
FAIL_COOLDOWN = 15 * 60

_cache: dict[str, tuple[float, list[dict]]] = {}
_fail_until = 0.0


def _query(lat: float, lon: float, radius: int = 800) -> str:
    return (
        "[out:json][timeout:8];"
        f"(node(around:{radius},{lat},{lon})[highway=bus_stop];"
        f"node(around:{radius + 800},{lat},{lon})[railway=station];"
        f"node(around:{radius + 500},{lat},{lon})[railway=halt];"
        ");out body 10;"
    )


def _to_stops(elements: list[dict], lat: float, lon: float) -> list[dict]:
    stops = []
    for element in elements:
        tags = element.get("tags", {}) or {}
        name = tags.get("name")
        slat, slon = element.get("lat"), element.get("lon")
        if not name or slat is None or slon is None:
            continue
        stops.append(
            {
                "id": element.get("id"),
                "name": name,
                "kind": "stazione" if tags.get("railway") else "fermata",
                "lat": slat,
                "lon": slon,
                "distance_km": haversine_km(lat, lon, slat, slon),
            }
        )
    stops.sort(key=lambda s: s["distance_km"])
    return stops[:5]


def _gtfs_stops(lat: float, lon: float, radius_km: float = 1.6, limit: int = 5) -> list[dict]:
    """Fermate vicine dal GTFS gia' scaricato, senza passare da Overpass.

    Overpass e' un servizio pubblico e a volte e' irraggiungibile (o lento):
    i feed GTFS locali hanno le stesse fermate con le coordinate, quindi la
    funzione non si perde. Se non c'e' nessun feed importato, lista vuota.
    """
    try:
        from ..gtfs import db as gtfs_db
        from ..gtfs.engine import nearby_stops

        if not gtfs_db.has_data():
            return []
        feeds = gtfs_db.imported_feed_ids()
        if not feeds:
            return []
        with gtfs_db.db() as conn:
            righe = nearby_stops(conn, feeds, lat, lon, radius_km, limit=limit)
    except Exception as exc:  # noqa: BLE001
        log.debug("fermate GTFS non disponibili: %s", exc)
        return []

    fermate: list[dict] = []
    for row in righe:
        stop_lat, stop_lon = row.get("lat"), row.get("lon")
        if stop_lat is None or stop_lon is None:
            continue
        fermate.append(
            {
                "id": f"gtfs:{row.get('feed')}:{row.get('stop_id')}",
                "name": row.get("name") or str(row.get("stop_id")),
                "kind": "fermata",
                "lat": stop_lat,
                "lon": stop_lon,
                "distance_km": row.get("distance_km")
                if row.get("distance_km") is not None
                else haversine_km(lat, lon, stop_lat, stop_lon),
            }
        )
    fermate.sort(key=lambda f: f["distance_km"])
    return fermate[:limit]


async def _nearby_stops(lat: float, lon: float) -> tuple[bool, list[dict]]:
    """Restituisce (servizio_raggiungibile, fermate)."""
    global _fail_until

    key = f"{lat:.4f},{lon:.4f}"
    cached = _cache.get(key)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return True, cached[1]

    if time.time() < _fail_until:
        # Overpass e' gia' caduto da poco: si va direttamente al GTFS locale.
        locali = _gtfs_stops(lat, lon)
        return bool(locali), locali

    query = _query(lat, lon)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    async def attempt(url: str):
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=headers) as client:
            response = await client.post(url, data={"data": query})
            response.raise_for_status()
            return response.json().get("elements", [])

    tasks = [asyncio.create_task(attempt(url)) for url in MIRRORS]
    try:
        for completed in asyncio.as_completed(tasks, timeout=TIMEOUT + 1):
            try:
                elements = await completed
            except Exception:
                continue
            stops = _to_stops(elements or [], lat, lon)
            _cache[key] = (time.time(), stops)
            return True, stops
    except asyncio.TimeoutError:
        pass
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()

    _fail_until = time.time() + FAIL_COOLDOWN
    # Ripiego locale: le fermate dei feed GTFS hanno le stesse coordinate.
    locali = _gtfs_stops(lat, lon)
    if locali:
        _cache[key] = (time.time(), locali)
        return True, locali
    return False, []


class TransitAdapter(Adapter):
    id = "transit"
    label = "Mezzi pubblici vicini"
    description = "Trova stazioni e fermate entro pochi minuti a piedi da partenza e arrivo."
    modes = ("transit",)

    async def search(self, ctx: SearchContext) -> AdapterResult:
        if None in (ctx.from_lat, ctx.from_lon, ctx.to_lat, ctx.to_lon):
            return AdapterResult()

        (ok_a, origin_stops), (ok_b, dest_stops) = await asyncio.gather(
            _nearby_stops(ctx.from_lat, ctx.from_lon),  # type: ignore[arg-type]
            _nearby_stops(ctx.to_lat, ctx.to_lon),  # type: ignore[arg-type]
        )
        if (not ok_a and not ok_b) or not origin_stops or not dest_stops:
            if not ok_a and not ok_b:
                return AdapterResult(
                    hints=[
                        "Fermate vicine: OpenStreetMap non raggiungibile e nessuna "
                        "fermata nei feed GTFS per queste coordinate."
                    ]
                )
            return AdapterResult()

        maps_url = (
            "https://www.google.com/maps/dir/?api=1"
            f"&origin={ctx.from_lat},{ctx.from_lon}"
            f"&destination={ctx.to_lat},{ctx.to_lon}"
            "&travelmode=transit"
        )

        offers: list[Offer] = []
        for origin in origin_stops[:2]:
            for destination in dest_stops[:2]:
                offers.append(
                    Offer(
                        source=self.id,
                        external_id=f"{origin['id']}|{destination['id']}|{ctx.arrive_by.date()}",
                        mode="transit",
                        operator="Mezzi pubblici",
                        depart_at=None,
                        arrive_at=None,
                        price=None,
                        pickup_name=f"{origin['name']} ({origin['distance_km']:.1f} km a piedi)",
                        pickup_lat=origin["lat"],
                        pickup_lon=origin["lon"],
                        dropoff_name=f"{destination['name']} ({destination['distance_km']:.1f} km a piedi)",
                        dropoff_lat=destination["lat"],
                        dropoff_lon=destination["lon"],
                        deeplink=maps_url,
                        details={
                            "tipo_tratta": "combinazione a piedi + mezzo pubblico",
                            "nota": "Orari e prezzi da verificare su Google Maps / app del gestore",
                            "origine": origin["name"],
                            "destinazione": destination["name"],
                        },
                    )
                )
        return AdapterResult(offers=offers)
