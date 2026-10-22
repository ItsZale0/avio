"""Trenitalia - endpoint interno usato dal sito/app (non documentato).

Copre treni regionali, Frecciarossa, Frecciargento, Intercity e Intercity Notte
con i prezzi reali di Trenitalia. Non e' un'API pubblica: se cambia, l'adapter
degrada con un hint senza bloccare le altre fonti.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

import httpx

from ..geo import CITIES, haversine_km
from ..timeutil import ROME, parse_dt
from .base import Adapter, AdapterResult, Offer, SearchContext, tappa

log = logging.getLogger("carpool.trenitalia")

LOCATIONS_URL = "https://app.lefrecce.it/Channels.Website.BFF.WEB/app/locations"
SOLUTIONS_URL = "https://app.lefrecce.it/Channels.Website.BFF.WEB/website/ticket/solutions"

HEADERS = {
    "user-agent": "Mozilla/5.0 (compatible; AVIO/1.0)",
    "content-type": "application/json",
    "accept": "application/json",
}

MAX_FALLBACK_KM = 60
# Quanti tentativi per la ricerca di una stazione: l'API ogni tanto rallenta
# o risponde con un errore transitorio, e un timeout non deve far fallire
# l'intera ricerca.
LOOKUP_ATTEMPTS = 3

_station_cache: dict[str, dict | None] = {}


def _candidates(place: str, lat: float | None, lon: float | None) -> list[tuple[str, float]]:
    names: list[tuple[str, float]] = [(place, 0.0)]
    if lat is None or lon is None:
        return names
    ranked = sorted(CITIES.items(), key=lambda item: haversine_km(lat, lon, item[1][0], item[1][1]))
    for name, coords in ranked:
        if name.lower() == place.strip().lower():
            continue
        distance = haversine_km(lat, lon, coords[0], coords[1])
        if distance > MAX_FALLBACK_KM:
            break
        names.append((name.title(), round(distance, 1)))
        if len(names) >= 3:
            break
    return names


# Stazioni secondarie: non servono per un viaggio in treno.
SECONDARY_STATIONS = (
    "aeroporto",
    " bus",
    "ospedale",
    "fiera",
    "castel",
    "policlinico",
    "deposito",
    "mobil",
)
# Nomi delle stazioni principali (le preferite).
MAIN_STATIONS = ("centrale", "porta nuova", "tutte le stazioni")


def _choose_station(
    items: list[dict], key: str, lat: float | None = None, lon: float | None = None
) -> dict | None:
    """Sceglie la stazione giusta fra quelle restituite dall'API.

    "Verona" non deve diventare "VERONA AEROPORTO" o "VERONA BUS" (i treni
    da li' sono radi e costano di piu'): meglio "Verona Porta Nuova". Fra le
    stazioni rimaste si sceglie la piu' vicina al centro della citta'.
    """

    def name_of(item: dict) -> str:
        return str(item.get("name") or "")

    if not items:
        return None
    exact = [i for i in items if name_of(i).lower() == key]
    if exact:
        return exact[0]

    pool = items
    if "aeroport" not in key:
        senza = [
            i
            for i in items
            if not any(mark in name_of(i).lower() for mark in SECONDARY_STATIONS)
        ]
        if senza:
            pool = senza

    # Le stazioni principali hanno la precedenza.
    principali = [
        i for i in pool if any(hint in name_of(i).lower() for hint in MAIN_STATIONS)
    ]
    if principali:
        pool = principali

    # Se non c'e' un nome chiaro, la stazione piu' vicina al centro citta'.
    if lat is not None and lon is not None:
        from ..geo import haversine_km

        def distanza(item: dict) -> float:
            coords = item.get("geographicCoordinates") or {}
            clat = coords.get("latitude")
            clon = coords.get("longitude")
            if clat is None or clon is None:
                return 1e9
            return haversine_km(lat, lon, float(clat), float(clon)) or 1e9

        return min(pool, key=distanza)
    return pool[0]


async def _lookup_station(
    client: httpx.AsyncClient, name: str, lat: float | None = None, lon: float | None = None
) -> dict | None:
    """Cerca la stazione omonima. None = non trovata (o API irraggiungibile).

    I risultati "non trovato" vengono messi in cache, i fallimenti di rete no:
    cosi' un timeout momentaneo non blocca le ricerche successive.
    """
    key = name.strip().lower()
    cache_key = f"{key}|{lat}|{lon}"
    if cache_key in _station_cache:
        return _station_cache[cache_key]
    result: dict | None = None
    confirmed = False
    for attempt in range(LOOKUP_ATTEMPTS):
        try:
            response = await client.get(LOCATIONS_URL, params={"name": name, "limit": "8"})
            response.raise_for_status()
            items = response.json()
            confirmed = True
            if isinstance(items, list):
                chosen = _choose_station(items, key, lat, lon)
                if chosen and chosen.get("locationId"):
                    coords = chosen.get("geographicCoordinates") or {}
                    result = {
                        "id": chosen["locationId"],
                        "name": chosen.get("name") or name,
                        # Le coordinate vere della stazione: senza queste il
                        # tempo a piedi viene calcolato sul centro citta'.
                        "lat": coords.get("latitude"),
                        "lon": coords.get("longitude"),
                    }
            break
        except Exception as exc:  # noqa: BLE001
            log.debug("lookup stazione '%s' fallito (%s): %s", name, type(exc).__name__, exc)
            if attempt + 1 < LOOKUP_ATTEMPTS:
                await asyncio.sleep(0.7 * (attempt + 1))
    if confirmed:
        _station_cache[cache_key] = result
    return result


async def _resolve(client: httpx.AsyncClient, place: str, lat: float | None, lon: float | None):
    for candidate, distance in _candidates(place, lat, lon):
        station = await _lookup_station(client, candidate)
        if station:
            shifted = candidate.strip().lower() != place.strip().lower()
            return station, (distance if shifted else 0.0), shifted
    return None, 0.0, False


def query_starts(ctx: SearchContext) -> list[datetime]:
    """Da quando interrogare l'API, per non perdere il giorno di arrivo.

    L'API restituisce circa dieci soluzioni ordinate per orario di partenza:
    se si interroga dal giorno prima, i treni del giorno di arrivo non
    arrivano MAI nella lista (e vengono scartati come "arrivati il giorno
    prima"). Per questo si interroga due volte: la sera prima (treni
    notturni e lunghi) e il giorno di arrivo.
    """
    giorno = ctx.arrival_day_start
    sera = giorno - timedelta(hours=6)
    starts: list[datetime] = []
    for candidate in (sera, giorno):
        start = max(candidate, ctx.window_start)
        if all(abs((start - other).total_seconds()) > 1800 for other in starts):
            starts.append(start)
    return starts or [ctx.window_start]


async def _post_solutions(
    client: httpx.AsyncClient, body: dict, attempts: int = 3
) -> dict:
    """Chiede le soluzioni, riproificando in caso di errore transitorio."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            response = await client.post(SOLUTIONS_URL, json=body)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.debug("solutions fallito (%s): %s", type(exc).__name__, exc)
            if attempt + 1 < attempts:
                await asyncio.sleep(0.7 * (attempt + 1))
    raise last if last else RuntimeError("soluzioni non disponibili")


class TrenitaliaAdapter(Adapter):
    id = "trenitalia"
    label = "Trenitalia (treni)"
    description = "Regionali, Frecciarossa, Intercity e notturni con i prezzi Trenitalia."
    modes = ("train",)

    async def search(self, ctx: SearchContext) -> AdapterResult:
        hints: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=25, headers=HEADERS) as client:
                from_st, from_km, from_shifted = await _resolve(
                    client, ctx.from_place, ctx.from_lat, ctx.from_lon
                )
                to_st, to_km, to_shifted = await _resolve(
                    client, ctx.to_place, ctx.to_lat, ctx.to_lon
                )
                if not from_st or not to_st:
                    return AdapterResult(
                        hints=[
                            f"Trenitalia: stazione non trovata o API non raggiungibile "
                            f"({ctx.from_place} / {ctx.to_place})."
                        ]
                    )
                if from_shifted:
                    hints.append(
                        f"Trenitalia: {ctx.from_place} non servita, ho cercato da {from_st['name']} (~{from_km:.0f} km)."
                    )
                if to_shifted:
                    hints.append(
                        f"Trenitalia: {ctx.to_place} non servita, ho cercato fino a {to_st['name']} (~{to_km:.0f} km)."
                    )

                # L'API dà solo una decina di soluzioni: si interroga due
                # volte per coprire sia la sera prima che il giorno di arrivo.
                payload: list[dict] = []
                seen_ids: set[str] = set()
                seen_times: set[tuple[str, str, float]] = set()
                for start in query_starts(ctx):
                    body = {
                        "departureLocationId": from_st["id"],
                        "arrivalLocationId": to_st["id"],
                        "departureTime": start.astimezone(ROME).strftime("%Y-%m-%dT%H:%M:%S.000"),
                        "adults": ctx.passengers,
                        "children": 0,
                        "criteria": {
                            "frecceOnly": False,
                            "regionalOnly": False,
                            "intercityOnly": False,
                            "noChanges": False,
                            "order": "DEPARTURE_DATE",
                            "offset": 0,
                            "limit": 15,
                        },
                        "advancedSearchRequest": {"bestFare": False, "bikeFilter": False},
                    }
                    data = await _post_solutions(client, body)
                    for entry in data.get("solutions") or []:
                        solution = entry.get("solution") if isinstance(entry, dict) else None
                        if not isinstance(solution, dict):
                            continue
                        key = str(solution.get("id") or "")
                        if key and key in seen_ids:
                            continue
                        try:
                            amount = float((solution.get("price") or {}).get("amount"))
                        except (TypeError, ValueError):
                            amount = 0.0
                        # Due interrogazioni possono ridare la stessa corsa
                        # con un codice diverso: si tiene una volta sola.
                        tempo = (
                            str(solution.get("departureTime") or ""),
                            str(solution.get("arrivalTime") or ""),
                            amount,
                        )
                        if tempo in seen_times:
                            continue
                        seen_times.add(tempo)
                        if key:
                            seen_ids.add(key)
                        payload.append(entry)
                data = {"solutions": payload}
        except Exception as exc:
            return AdapterResult(hints=[f"Trenitalia non raggiungibile: {type(exc).__name__}"])
        offers = self._parse(
            data,
            ctx,
            from_st,
            to_st,
            from_shifted=from_shifted,
            to_shifted=to_shifted,
            from_km=from_km,
            to_km=to_km,
        )
        if not offers:
            hints.append("Trenitalia: nessuna soluzione acquistabile per questa data.")
        return AdapterResult(offers=offers, hints=hints)

    def _parse(
        self,
        data: dict,
        ctx: SearchContext,
        from_st: dict,
        to_st: dict,
        *,
        from_shifted: bool,
        to_shifted: bool,
        from_km: float,
        to_km: float,
    ) -> list[Offer]:
        offers: list[Offer] = []
        for entry in data.get("solutions") or []:
            solution = entry.get("solution") if isinstance(entry, dict) else None
            if not isinstance(solution, dict):
                continue
            if str(solution.get("status")) != "SALEABLE":
                continue
            price_info = solution.get("price") or {}
            try:
                price = float(price_info.get("amount"))
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue

            depart_at = parse_dt(solution.get("departureTime"))
            arrive_at = parse_dt(solution.get("arrivalTime"))
            if not depart_at:
                continue

            duration_min = None
            if arrive_at:
                duration_min = max(
                    0, int((arrive_at - depart_at).total_seconds() // 60)
                )

            trains = solution.get("trains") or []
            categories = [str(t.get("trainCategory") or "").strip() for t in trains if t.get("trainCategory")]
            operator = _main_operator(categories)

            offers.append(
                Offer(
                    source=self.id,
                    external_id=str(solution.get("id") or depart_at.isoformat()),
                    mode="train",
                    operator=operator,
                    depart_at=depart_at,
                    arrive_at=arrive_at,
                    price=round(price, 2),
                    pickup_name=solution.get("origin") or from_st["name"],
                    pickup_lat=(
                        from_st.get("lat")
                        if from_st.get("lat") is not None
                        else ctx.from_lat
                    ),
                    pickup_lon=(
                        from_st.get("lon")
                        if from_st.get("lon") is not None
                        else ctx.from_lon
                    ),
                    dropoff_name=solution.get("destination") or to_st["name"],
                    dropoff_lat=(
                        to_st.get("lat") if to_st.get("lat") is not None else ctx.to_lat
                    ),
                    dropoff_lon=(
                        to_st.get("lon") if to_st.get("lon") is not None else ctx.to_lon
                    ),
                    seats=None,
                    duration_min=duration_min,
                    deeplink="https://www.trenitalia.com/it.html",
                    details={
                        "fonte": "Trenitalia",
                        "cambi": max(0, len(trains) - 1),
                        "treni": ", ".join(categories[:4]),
                        "tappe": _tappe_from_nodes(solution.get("nodes") or []),
                        "durata": solution.get("duration"),
                        "partenza_alternativa": from_st["name"] if from_shifted else "",
                        "arrivo_alternativo": to_st["name"] if to_shifted else "",
                        "distanza_partenza_km": from_km if from_shifted else 0,
                        "distanza_arrivo_km": to_km if to_shifted else 0,
                    },
                )
            )
        return offers


def _train_label(train: dict) -> str:
    acronym = str(train.get("acronym") or "").strip()
    name = str(train.get("name") or train.get("description") or "").strip()
    category = str(train.get("trainCategory") or "").strip()
    parts = [part for part in (acronym, name or category) if part]
    return " ".join(dict.fromkeys(parts))


def _tappe_from_nodes(nodes: list[dict]) -> list[dict]:
    """Tratte reali della soluzione: dove parte, dove cambia, dove arriva."""
    result: list[dict] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        result.append(
            tappa(
                node.get("origin"),
                node.get("destination"),
                node.get("departureTime"),
                node.get("arrivalTime"),
                _train_label(node.get("train") or {}),
            )
        )
    return result


def _main_operator(categories: list[str]) -> str:
    """Il nome dell'operatore e del tipo di treno, per la card."""
    if not categories:
        return "Trenitalia"
    high_speed = {"Frecciarossa", "Frecciargento", "Frecciabianca", "Italo"}
    for category in categories:
        if category in high_speed:
            return category
    for category in categories:
        if category.startswith("Intercity"):
            return category  # Intercity, Intercity Notte
        if category.startswith("EuroCity"):
            return category
        if category.startswith("Frecce Regional"):
            return "Frecce Regionale"
    return f"Trenitalia {categories[0]}"
