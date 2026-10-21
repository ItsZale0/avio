"""Percorsi a piedi veri, dalle mappe OpenStreetMap.

Il tempo a piedi non si stima piu' con la linea d'aria: si chiede il percorso
pedonale al servizio di routing FOSSGIS (dati OpenStreetMap) e si usano la
distanza e la durata che restituisce. Se il servizio non risponde il dato
manca: meglio niente che un numero inventato.

I percorsi si calcolano solo quando l'utente ha indicato un **punto esatto**
(una stazione, un indirizzo, un pin sulla mappa). Se ha scritto solo il nome
di una citta' non sappiamo da dove parte davvero: aggiungere il cammino dal
centro citta' alla stazione sarebbe un numero inventato, quindi non si fa.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .adapters.base import Offer, SearchContext
from .geo import haversine_km

log = logging.getLogger("carpool.routing")

# FOSSGIS: servizio pubblico di routing su dati OpenStreetMap, profilo a piedi.
ROUTER_URL = "https://routing.openstreetmap.de/routed-foot/route/v1/foot"
USER_AGENT = "CarpoolHunter/1.0 (pianificatore personale)"

# Sotto 50 m si e' gia' li': non serve chiedere un percorso.
MIN_KM = 0.05
# Oltre 10 km in linea d'aria il cammino non e' un'opzione: non si calcola.
MAX_KM = 10.0

_SEM = asyncio.Semaphore(3)
_CACHE: dict[tuple[int, int, int, int], dict[str, float] | None] = {}


def _chiave(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[int, int, int, int]:
    return (
        round(lat1 * 100_000),
        round(lon1 * 100_000),
        round(lat2 * 100_000),
        round(lon2 * 100_000),
    )


async def _fetch(url: str) -> dict[str, Any] | None:
    """Chiama il servizio di routing. Separata per poterla sostituire nei test."""
    async with _SEM:
        async with httpx.AsyncClient(
            timeout=10, headers={"User-Agent": USER_AGENT}
        ) as client:
            response = await client.get(
                url, params={"overview": "false", "steps": "false"}
            )
            response.raise_for_status()
            return response.json()


async def percorso_a_piedi(
    lat1: float | None, lon1: float | None, lat2: float | None, lon2: float | None
) -> dict[str, float] | None:
    """Distanza (km) e minuti del percorso pedonale reale.

    Restituisce ``{"km": ..., "minuti": ...}`` oppure ``None`` se il percorso
    non e' calcolabile (punti mancanti, servizio non raggiungibile).
    """
    if None in (lat1, lon1, lat2, lon2):
        return None
    dritta = haversine_km(lat1, lon1, lat2, lon2)  # type: ignore[arg-type]
    if dritta is None:
        return None
    if dritta < MIN_KM:
        return {"km": 0.0, "minuti": 0}
    if dritta > MAX_KM:
        return None

    key = _chiave(lat1, lon1, lat2, lon2)  # type: ignore[arg-type]
    if key in _CACHE:
        return _CACHE[key]

    url = (
        f"{ROUTER_URL}/{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}"
    )
    try:
        payload = await _fetch(url)
    except Exception as exc:  # noqa: BLE001
        log.debug("percorso a piedi non disponibile: %s", exc)
        return None
    routes = (payload or {}).get("routes") or []
    if not routes:
        return None
    try:
        metri = float(routes[0].get("distance") or 0)
        secondi = float(routes[0].get("duration") or 0)
    except (TypeError, ValueError):
        return None
    if metri <= 0 or secondi <= 0:
        return None
    risultato = {
        "km": round(metri / 1000, 2),
        "minuti": max(1, int(round(secondi / 60))),
    }
    _CACHE[key] = risultato
    return risultato


async def annota_offerte(offers: list[Offer], ctx: SearchContext) -> None:
    """Calcola i percorsi a piedi reali per le offerte di questa ricerca.

    Il cammino c'e' solo verso un punto che l'utente ha scelto davvero:

    - andata: dal punto di partenza esatto al punto di ritrovo dell'offerta;
    - ritorno: dal punto di arrivo dell'offerta al punto di destinazione esatto.

    Se l'utente ha indicato solo la citta' non si annota nulla: il centro
    citta' non e' la sua porta di casa.
    """
    partenza = (
        (ctx.from_point_lat, ctx.from_point_lon)
        if ctx.from_point_lat is not None and ctx.from_point_lon is not None
        else None
    )
    arrivo = (
        (ctx.to_point_lat, ctx.to_point_lon)
        if ctx.to_point_lat is not None and ctx.to_point_lon is not None
        else None
    )
    if partenza is None and arrivo is None:
        return

    richieste: list[tuple[Offer, str, tuple[float, float, float, float]]] = []
    for offer in offers:
        if partenza and offer.pickup_lat is not None and offer.pickup_lon is not None:
            richieste.append(
                (
                    offer,
                    "piedi_pickup",
                    (partenza[0], partenza[1], offer.pickup_lat, offer.pickup_lon),
                )
            )
        if arrivo and offer.dropoff_lat is not None and offer.dropoff_lon is not None:
            richieste.append(
                (
                    offer,
                    "piedi_dropoff",
                    (offer.dropoff_lat, offer.dropoff_lon, arrivo[0], arrivo[1]),
                )
            )

    async def _una(offer: Offer, chiave: str, punti: tuple[float, float, float, float]) -> None:
        percorso = await percorso_a_piedi(*punti)
        if percorso is None:
            return
        details = dict(offer.details or {})
        details[chiave] = percorso
        offer.details = details

    await asyncio.gather(*(_una(offer, chiave, punti) for offer, chiave, punti in richieste))
