"""Spezzatura dei biglietti: pagare meno con due biglietti invece di uno.

Su molte tratte italiane il biglietto unico costa piu' di due biglietti
partenziali: un regionale fino a una citta' intermedia e un secondo
biglietto da li' a destinazione. E' una pratica diffusa, e per trovarla
serve chiedere all'API i prezzi dei singoli segmenti.

Regole:
- si valutano solo i viaggi ferroviari con tappe note (l'itinerario reale);
- per ogni punto di spezzatura si chiedono i prezzi dei due segmenti;
- la spezzatura entra nel confronto **solo se e' piu' economica** del
  biglietto diretto: mai un piano con prezzi inventati o gonfiati.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ..adapters.base import Offer, SearchContext, tappa
from ..adapters.trenitalia import HEADERS, SOLUTIONS_URL, _lookup_station
from ..timeutil import ROME, parse_dt

log = logging.getLogger("carpool.split")

# Un segmento deve partire almeno dopo che il precedente e' arrivato.
MIN_TRANSFER_MIN = 25
# Quanti punti di spezzatura si provano per viaggio.
MAX_SPLIT_POINTS = 4
# Quanti viaggi si provano a spezzare (i piu' economici diretti).
MAX_SPLIT_OFFERS = 1

# I nodi ferroviari italiani, in ordine di importanza: e' li' che la
# spezzatura conviene, perche' offrono regionali economici che il treno
# diretto non usa.
RAIL_HUBS = [
    "bologna", "firenze", "verona", "padova", "roma", "milano", "napoli",
    "torino", "venezia", "genova", "ancona", "parma", "modena", "livorno",
    "pisa", "perugia", "pescara", "rimini", "ferrara", "arezzo", "salerno",
    "bari", "lecce", "reggio calabria", "taranto", "potenza", "ravenna",
    "piacenza", "la spezia", "genova piazza principe", "mestre", "venice",
]


async def _segment_price(
    client: httpx.AsyncClient,
    from_id: str,
    to_id: str,
    when,
) -> list[dict[str, Any]]:
    """Le soluzioni piu' economiche per un singolo segmento.

    L'API restituisce anche corse che partono prima dell'ora richiesta:
    qui vengono scartate, altrimenti il "cambio" sarebbe impossibile.
    """
    body = {
        "departureLocationId": from_id,
        "arrivalLocationId": to_id,
        "departureTime": when.astimezone(ROME).strftime("%Y-%m-%dT%H:%M:%S.000"),
        "adults": 1,
        "children": 0,
        "criteria": {
            "frecceOnly": False,
            "regionalOnly": False,
            "intercityOnly": False,
            "noChanges": False,
            "order": "DEPARTURE_DATE",
            "offset": 0,
            "limit": 12,
        },
        "advancedSearchRequest": {"bestFare": False, "bikeFilter": False},
    }
    try:
        response = await client.post(SOLUTIONS_URL, json=body)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        log.debug("segmento non disponibile: %s", exc)
        return []

    found: list[dict[str, Any]] = []
    for entry in payload.get("solutions") or []:
        solution = entry.get("solution") if isinstance(entry, dict) else None
        if not isinstance(solution, dict):
            continue
        if str(solution.get("status")) != "SALEABLE":
            continue
        try:
            price = float((solution.get("price") or {}).get("amount"))
        except (TypeError, ValueError):
            continue
        depart_at = parse_dt(solution.get("departureTime"))
        arrive_at = parse_dt(solution.get("arrivalTime"))
        if price <= 0 or not depart_at or not arrive_at:
            continue
        if depart_at < when:
            continue  # parte prima di quando richiesto
        found.append(
            {
                "price": round(price, 2),
                "depart_at": depart_at,
                "arrive_at": arrive_at,
                "origin": solution.get("origin") or "",
                "destination": solution.get("destination") or "",
                "id": str(solution.get("id") or ""),
            }
        )
    return sorted(found, key=lambda s: s["price"])


def _split_points(offer: Offer, ctx: SearchContext) -> list[str]:
    """Le citta' dove provare a spezzare il biglietto, in ordine di promessa.

    Prima i **nodi ferroviari in linea** sul percorso (Bologna, Firenze,
    Verona...): e' li' che la spezzatura conviene, perche' offrono
    regionali economici che il treno diretto non usa. Poi le fermate reali
    dell'itinerario, che sono un buon piano B.

    "In linea" vuol dire che la deviazione e' minima:
    dist(partenza, X) + dist(X, arrivo) - dist(partenza, arrivo) < 70 km.
    """
    points: list[str] = []

    if None not in (ctx.from_lat, ctx.from_lon, ctx.to_lat, ctx.to_lon):
        from ..geo import CITIES, haversine_km

        def _is_endpoint(hub: str) -> bool:
            """True se l'hub coincide con la partenza o l'arrivo."""
            for place in (ctx.from_place or "", ctx.to_place or ""):
                place = place.strip().lower()
                if not place:
                    continue
                if hub in place or place in hub:
                    return True
            return False

        direct = haversine_km(ctx.from_lat, ctx.from_lon, ctx.to_lat, ctx.to_lon)  # type: ignore[arg-type]
        if direct:
            on_the_way: list[tuple[int, str]] = []
            for priority, hub in enumerate(RAIL_HUBS):
                coords = CITIES.get(hub)
                if not coords or _is_endpoint(hub):
                    continue
                first = haversine_km(ctx.from_lat, ctx.from_lon, coords[0], coords[1])  # type: ignore[arg-type]
                second = haversine_km(coords[0], coords[1], ctx.to_lat, ctx.to_lon)  # type: ignore[arg-type]
                if first is None or second is None:
                    continue
                if first + second - direct < 70:
                    on_the_way.append((priority, hub))
            on_the_way.sort()
            for _, hub in on_the_way:
                label = hub.title()
                if label not in points:
                    points.append(label)

    # Poi le fermate reali del treno.
    tappe = offer.details.get("tappe") or []
    for step in tappe:
        if not isinstance(step, dict):
            continue
        for key in ("da", "a"):
            name = str(step.get(key) or "").strip()
            if name and name not in points:
                points.append(name)

    # Escludi partenza e arrivo.
    start = str(offer.pickup_name or "").strip().lower()
    end = str(offer.dropoff_name or "").strip().lower()
    return [p for p in points if p.lower() not in (start, end)]


async def find_splits(offers: list[Offer], ctx: SearchContext) -> list[Offer]:
    """Cerca biglietti spezzati piu' economici del viaggio diretto.

    Restituisce le offerte "spezzate" (una per ogni spezzatura che batte
    il prezzo diretto).
    """
    candidates = [
        offer
        for offer in offers
        if offer.mode == "train" and offer.price and (offer.details.get("tappe") or [])
    ]
    if not candidates:
        return []

    # Il viaggio da battere e' il piu' economico in assoluto: e' quello su
    # cui la spezzatura ha piu' margine.
    candidates.sort(key=lambda o: o.price or 0)
    candidates = candidates[:MAX_SPLIT_OFFERS]

    results: list[Offer] = []
    try:
        async with httpx.AsyncClient(timeout=25, headers=HEADERS) as client:
            for offer in candidates:
                results.extend(await _split_one(client, offer, ctx))
    except Exception as exc:  # noqa: BLE001
        log.debug("spezzatura non disponibile: %s", exc)
    # Tiene solo la spezzatura piu' vantaggiosa per ogni viaggio.
    best: dict[str, Offer] = {}
    for split in results:
        key = f"{split.pickup_name}->{split.dropoff_name}"
        if key not in best or (split.price or 0) < (best[key].price or 0):
            best[key] = split
    return list(best.values())


async def _split_one(
    client: httpx.AsyncClient, offer: Offer, ctx: SearchContext
) -> list[Offer]:
    """Prova a spezzare un viaggio e tieni solo se conviene."""
    if not offer.depart_at or not offer.arrive_at:
        return []

    origin = await _lookup_station(client, offer.pickup_name or ctx.from_place)
    destination = await _lookup_station(client, offer.dropoff_name or ctx.to_place)
    if not origin or not destination:
        return []

    out: list[Offer] = []
    for point in _split_points(offer, ctx)[:MAX_SPLIT_POINTS]:
        middle = await _lookup_station(client, point)
        if not middle:
            continue
        if middle["id"] in (origin["id"], destination["id"]):
            continue

        # Il primo biglietto parte dall'inizio della finestra di ricerca,
        # non dall'ora del treno diretto: altrimenti si perdono i regionali
        # mattutini, che sono quelli economici (e' il trucco).
        first = await _segment_price(
            client, origin["id"], middle["id"], ctx.window_start
        )
        if not first:
            continue
        leg1 = first[0]

        # Il secondo biglietto parte dopo l'arrivo del primo (cambio
        # fattibile: almeno MIN_TRANSFER_MIN di attesa).
        from datetime import timedelta

        when2 = leg1["arrive_at"] + timedelta(minutes=MIN_TRANSFER_MIN)
        second = await _segment_price(client, middle["id"], destination["id"], when2)
        if not second:
            continue
        leg2 = second[0]

        total = round(leg1["price"] + leg2["price"], 2)
        log.debug(
            "spezzatura %s: %.2f + %.2f = %.2f (diretto %.2f) arrivo %s",
            middle["name"], leg1["price"], leg2["price"], total, offer.price or 0,
            leg2["arrive_at"],
        )
        if total >= (offer.price or 0):
            continue  # non conviene: il diretto resta migliore
        if leg2["arrive_at"] > ctx.arrive_by + timedelta(minutes=10):
            log.debug("  scartata: arriva dopo il limite")
            continue
        if leg2["arrive_at"] < ctx.arrival_day_start:
            log.debug("  scartata: arriva il giorno prima")
            continue

        out.append(
            Offer(
                source="spezzatura",
                external_id=f"split:{leg1['id']}+{leg2['id']}",
                mode="train",
                operator="Trenitalia (2 biglietti)",
                depart_at=leg1["depart_at"],
                arrive_at=leg2["arrive_at"],
                price=total,
                pickup_name=offer.pickup_name,
                dropoff_name=offer.dropoff_name,
                pickup_lat=offer.pickup_lat,
                pickup_lon=offer.pickup_lon,
                dropoff_lat=offer.dropoff_lat,
                dropoff_lon=offer.dropoff_lon,
                duration_min=int((leg2["arrive_at"] - leg1["depart_at"]).total_seconds() // 60),
                deeplink="https://www.trenitalia.com/it.html",
                details={
                    "fonte": "Trenitalia",
                    "spezzatura": True,
                    "prezzo_diretto": offer.price,
                    "risparmio": round((offer.price or 0) - total, 2),
                    "biglietti": 2,
                    "cambi": 0,
                    "treni": "2 biglietti regionali",
                    "tappe": [
                        tappa(
                            leg1["origin"] or origin["name"],
                            leg1["destination"] or middle["name"],
                            leg1["depart_at"],
                            leg1["arrive_at"],
                            "Trenitalia",
                        ),
                        tappa(
                            leg2["origin"] or middle["name"],
                            leg2["destination"] or destination["name"],
                            leg2["depart_at"],
                            leg2["arrive_at"],
                            "Trenitalia",
                        ),
                    ],
                    "durata": f"{int((leg2['arrive_at'] - leg1['depart_at']).total_seconds() // 60)} min",
                },
            )
        )
    return out
