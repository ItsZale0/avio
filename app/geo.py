"""Geocoding (OpenStreetMap/Nominatim) con cache e fallback offline."""

from __future__ import annotations

import asyncio
import math
import time
import unicodedata
from datetime import timedelta

import httpx

from . import db
from .config import USER_AGENT
from .timeutil import now_utc

# Coordinate approssimative dei principali centri italiani: servono come
# fallback quando Nominatim non risponde e per capire in quali citta' esiste
# un servizio di carsharing.
CITIES: dict[str, tuple[float, float]] = {
    "milano": (45.4642, 9.1900),
    "roma": (41.9028, 12.4964),
    "torino": (45.0703, 7.6869),
    "napoli": (40.8518, 14.2681),
    "bologna": (44.4949, 11.3426),
    "firenze": (43.7696, 11.2558),
    "venezia": (45.4408, 12.3155),
    "mestre": (45.4917, 12.2424),
    "verona": (45.4384, 10.9916),
    "padova": (45.4064, 11.8768),
    "brescia": (45.5416, 10.2118),
    "bergamo": (45.6983, 9.6773),
    "monza": (45.5845, 9.2744),
    "como": (45.8081, 9.0852),
    "varese": (45.8206, 8.8251),
    "pavia": (45.1847, 9.1582),
    "piacenza": (45.0526, 9.6930),
    "parma": (44.8015, 10.3279),
    "modena": (44.6471, 10.9252),
    "reggio emilia": (44.6989, 10.6297),
    "rimini": (44.0678, 12.5695),
    "ravenna": (44.4184, 12.2035),
    "ferrara": (44.8381, 11.6198),
    "genova": (44.4056, 8.9463),
    "la spezia": (44.1024, 9.8241),
    "savona": (44.3091, 8.4772),
    "sanremo": (43.8159, 7.7761),
    "imperia": (43.8897, 8.0395),
    "cuneo": (44.3845, 7.5427),
    "aosta": (45.7370, 7.3201),
    "novara": (45.4451, 8.6218),
    "alessandria": (44.9133, 8.6150),
    "asti": (44.9009, 8.2064),
    "busto arsizio": (45.6120, 8.8524),
    "gallarate": (45.6602, 8.7916),
    "saronno": (45.6269, 9.0353),
    "treviso": (45.6669, 12.2420),
    "vicenza": (45.5455, 11.5354),
    "trento": (46.0748, 11.1217),
    "bolzano": (46.4983, 11.3548),
    "udine": (46.0711, 13.2346),
    "trieste": (45.6495, 13.7768),
    "perugia": (43.1107, 12.3908),
    "ancona": (43.6158, 13.5189),
    "pescara": (42.4618, 14.2161),
    "l'aquila": (42.3499, 13.3995),
    "bari": (41.1171, 16.8719),
    "taranto": (40.4644, 17.2470),
    "lecce": (40.3515, 18.1750),
    "brindisi": (40.6327, 17.9418),
    "reggio calabria": (38.1113, 15.6473),
    "catanzaro": (38.9098, 16.5877),
    "palermo": (38.1157, 13.3615),
    "catania": (37.5079, 15.0830),
    "messina": (38.1938, 15.5540),
    "cagliari": (39.2238, 9.1217),
    "olbia": (40.9236, 9.4964),
    "sassari": (40.7259, 8.5557),
    "salerno": (40.6824, 14.7681),
    "caserta": (41.0723, 14.3329),
    "foggia": (41.4622, 15.5446),
    "siena": (43.3188, 11.3308),
    "pisa": (43.7228, 10.4017),
    "livorno": (43.5485, 10.3106),
    "lucca": (43.8430, 10.5059),
    "arezzo": (43.4633, 11.8796),
    "cortina d'ampezzo": (46.5405, 12.1357),
    "cortina": (46.5405, 12.1357),
    # Alto Adige: i centri della provincia serviti dai pass altoadigemobilita.
    "bressanone": (46.7084, 11.6556),
    "merano": (46.6727, 11.1590),
    "brunico": (46.7975, 12.0839),
    "silandro": (46.6283, 10.7672),
    "egna": (46.3140, 11.4050),
    "vipiteno": (46.9404, 11.2881),
    "laives": (46.3667, 11.3750),
    "appiano": (46.4603, 11.3319),
    "san candido": (46.5836, 12.6567),
    "ponte gardena": (46.5858, 11.8106),
}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().replace("-", " ").split())


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def nearest_city(lat: float, lon: float, max_km: float = 40) -> tuple[str, float] | None:
    best: tuple[str, float] | None = None
    for name, (clat, clon) in CITIES.items():
        dist = haversine_km(lat, lon, clat, clon)
        if dist <= max_km and (best is None or dist < best[1]):
            best = (name, dist)
    return best


# Velocita' di camminata: passo spedito di una persona media (4,5 km/h).
# Il tempo a piedi conta: 20 minuti a piedi spostano un treno dalle 9:40
# alle 10:00, e possono cambiare la scelta.
WALK_KMH = 4.5


def walking_minutes(km: float | None) -> int | None:
    """Minuti di cammino per una distanza in linea d'aria.

    E' una stima prudenziale: il percorso pedonale reale e' un po' piu'
    lungo dello stretto (strade, deviatoi) e in citta' i semafori allungano.
    """
    if km is None or km < 0:
        return None
    if km < 0.05:
        return 0
    return int(round(km / WALK_KMH * 60))


def walking_text(km: float | None) -> str:
    """Il tempo a piedi in parole brevi."""
    minutes = walking_minutes(km)
    if minutes is None:
        return ""
    if minutes <= 2:
        return "a piedi"
    if minutes < 60:
        return f"{minutes} min a piedi"
    hours, rest = divmod(minutes, 60)
    if rest:
        return f"{hours}h {rest:02d} a piedi"
    return f"{hours}h a piedi"


def offline_lookup(query: str) -> tuple[float, float, str] | None:
    q = normalize(query)
    if not q:
        return None
    # match esatto o contenimento (es. "Milano Centrale" -> milano)
    for name in sorted(CITIES, key=len, reverse=True):
        if name in q:
            lat, lon = CITIES[name]
            return lat, lon, name.title()
    return None


class _RateLimiter:
    def __init__(self, min_interval: float = 1.1):
        self.min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            delta = time.monotonic() - self._last
            if delta < self.min_interval:
                await asyncio.sleep(self.min_interval - delta)
            self._last = time.monotonic()


_nominatim = _RateLimiter()


def _cache_get(query: str) -> tuple[float, float, str] | None:
    key = normalize(query)
    with db.db() as conn:
        row = conn.execute("SELECT * FROM geocache WHERE query = ?", (key,)).fetchone()
    if not row:
        return None
    updated = row["updated_at"]
    try:
        from .timeutil import parse_dt

        if parse_dt(updated) and now_utc() - parse_dt(updated) > timedelta(days=90):  # type: ignore[operator]
            return None
    except Exception:
        pass
    return float(row["lat"]), float(row["lon"]), row["display_name"] or query


def _cache_put(query: str, lat: float, lon: float, display_name: str) -> None:
    with db.db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO geocache(query, lat, lon, display_name, updated_at) VALUES (?,?,?,?,?)",
            (normalize(query), lat, lon, display_name, now_utc().isoformat()),
        )


async def geocode(query: str) -> tuple[float, float, str] | None:
    """Restituisce (lat, lon, nome) per una localita' italiana."""
    query = (query or "").strip()
    if not query:
        return None

    cached = _cache_get(query)
    if cached:
        return cached

    fallback = offline_lookup(query)

    await _nominatim.wait()
    try:
        async with httpx.AsyncClient(timeout=12, headers={"User-Agent": USER_AGENT}) as client:
            response = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": query,
                    "format": "jsonv2",
                    "limit": 1,
                    "countrycodes": "it",
                    "accept-language": "it",
                },
            )
            response.raise_for_status()
            data = response.json()
        if data:
            item = data[0]
            lat, lon = float(item["lat"]), float(item["lon"])
            label = item.get("display_name") or query
            _cache_put(query, lat, lon, label)
            return lat, lon, label
    except Exception:
        pass

    if fallback:
        _cache_put(query, fallback[0], fallback[1], fallback[2])
        return fallback
    return None


async def resolve_place(
    name: str, lat: float | None = None, lon: float | None = None
) -> tuple[float | None, float | None, str]:
    """Usa le coordinate fornite, altrimenti geocodifica il nome."""
    if lat is not None and lon is not None:
        return lat, lon, name
    found = await geocode(name)
    if not found:
        return None, None, name
    return found[0], found[1], found[2]
