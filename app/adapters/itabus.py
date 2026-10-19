"""Itabus - endpoint pubblico usato dal sito (non documentato).

Itabus e' un operatore italiano di autobus a prezzi bassi. Le ricerche usano i
codici stazione interni (es. MIL_T, TRE_T): li ricaviamo dall'elenco stazioni
ufficiale, con fallback sulle citta' vicine.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from datetime import timedelta

import httpx

from ..geo import CITIES, haversine_km
from ..timeutil import parse_dt
from .base import Adapter, AdapterResult, Offer, SearchContext, tappa

STATIONS_URL = "https://www.itabus.it/on/demandware.store/Sites-ITABUS-Site/en/Api-Stations"
TRAVELS_URL = "https://www.itabus.it/on/demandware.store/Sites-ITABUS-Site/en/Api-Travels"

HEADERS = {
    "user-agent": "Mozilla/5.0 (compatible; CarpoolHunter/0.1)",
    "accept": "application/json",
    "referer": "https://www.itabus.it/en/search",
}

MAX_FALLBACK_KM = 60

_stations: list[dict] | None = None


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _duration_minutes(text: str | None) -> int | None:
    if not text:
        return None
    match = re.match(r"\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?", str(text))
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    total = hours * 60 + minutes
    return total or None


def _cheapest_rate(rates) -> float | None:
    """Estrae il prezzo piu' basso da un albero di tariffe Itabus."""
    prices: list[float] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            price = node.get("price")
            if isinstance(price, (int, float)) and price > 0:
                prices.append(float(price))
            for key, value in node.items():
                if key == "price":
                    continue
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(rates)
    return min(prices) if prices else None


async def _load_stations(client: httpx.AsyncClient) -> list[dict]:
    global _stations
    if _stations is not None:
        return _stations
    try:
        response = await client.get(STATIONS_URL, params={"name": "x"})
        response.raise_for_status()
        data = response.json().get("data")
        _stations = data if isinstance(data, list) else []
    except Exception:
        _stations = []
    return _stations


def _match_station(stations: list[dict], place: str) -> dict | None:
    target = _normalize(place)
    if not target:
        return None

    def score(station: dict) -> tuple:
        name = _normalize(station.get("name") or "")
        synonyms = [_normalize(n) for n in (station.get("synonyms") or []) if _normalize(n)]
        name_match = name == target
        any_match = name_match or any(
            target == s or target in s or s in target for s in synonyms
        ) or (name and (target in name or name in target))
        is_main = str(station.get("code", "")).endswith("_T")
        return (name_match, is_main, any_match, -int(station.get("sequence_number") or 100))

    ranked = sorted(stations, key=score, reverse=True)
    best = ranked[0] if ranked else None
    if not best:
        return None
    if not score(best)[2]:
        return None
    return best


async def _resolve(client: httpx.AsyncClient, place: str, lat: float | None, lon: float | None):
    stations = await _load_stations(client)
    for candidate, distance in _candidates(place, lat, lon):
        station = _match_station(stations, candidate)
        if station and station.get("code"):
            shifted = candidate.strip().lower() != place.strip().lower()
            return station, (distance if shifted else 0.0), shifted
    return None, 0.0, False


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


def _tappe_from_legs(legs: list[dict]) -> list[dict]:
    """Tratte della corsa: dalla fermata di partenza a quella di arrivo."""
    result: list[dict] = []
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        result.append(
            tappa(
                leg.get("origin"),
                leg.get("destination"),
                leg.get("departure_timestamp"),
                leg.get("arrival_timestamp"),
                "bus",
            )
        )
    return result


class ItabusAdapter(Adapter):
    id = "itabus"
    label = "Itabus (autobus)"
    description = "Autobus italiani a basso costo con i prezzi reali di Itabus."
    modes = ("bus",)

    async def search(self, ctx: SearchContext) -> AdapterResult:
        hints: list[str] = []
        days = self._days(ctx)
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
                        hints=[f"Itabus: fermata non riconosciuta ({ctx.from_place} / {ctx.to_place})."]
                    )
                if from_shifted:
                    hints.append(
                        f"Itabus: {ctx.from_place} non servita, ho cercato da {from_st['name']} (~{from_km:.0f} km)."
                    )
                if to_shifted:
                    hints.append(
                        f"Itabus: {ctx.to_place} non servita, ho cercato fino a {to_st['name']} (~{to_km:.0f} km)."
                    )

                offers: list[Offer] = []
                for day in days:
                    routes: list[dict] = []
                    for attempt in range(2):
                        response = await client.get(
                            TRAVELS_URL,
                            params={
                                "origin": from_st["code"],
                                "destination": to_st["code"],
                                "datestart": day,
                                "adults": ctx.passengers,
                                "children": 0,
                                "membership": "false",
                            },
                        )
                        if response.status_code >= 300:
                            break
                        payload = response.json() or {}
                        routes = ((payload.get("data") or {}).get("outbound") or {}).get("routes") or []
                        if routes or attempt == 1:
                            break
                        await asyncio.sleep(1.2)
                    offers.extend(
                        self._parse(
                            routes,
                            ctx,
                            from_st,
                            to_st,
                            from_shifted=from_shifted,
                            to_shifted=to_shifted,
                            from_km=from_km,
                            to_km=to_km,
                        )
                    )
        except Exception as exc:
            return AdapterResult(hints=[f"Itabus non raggiungibile: {type(exc).__name__}"])

        if not offers:
            hints.append("Itabus: nessuna corsa trovata per questa data.")
        return AdapterResult(offers=offers, hints=hints)

    def _days(self, ctx: SearchContext) -> list[str]:
        start = ctx.window_start
        end = min(ctx.arrive_by, start + timedelta(days=2))
        days: list[str] = []
        current = start
        while current.date() <= end.date() and len(days) < 3:
            days.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)
        return days or [start.strftime("%Y-%m-%d")]

    def _parse(
        self,
        routes: list[dict],
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
        for route in routes:
            if not isinstance(route, dict) or not route.get("available", True):
                continue
            price = _cheapest_rate(route.get("rates"))
            if price is None:
                continue
            depart_at = parse_dt(route.get("departure_timestamp"))
            if not depart_at:
                continue
            arrive_at = parse_dt(route.get("arrival_timestamp"))
            origin = route.get("origin") or {}
            destination = route.get("destination") or {}
            services = route.get("services") or []

            offers.append(
                Offer(
                    source=self.id,
                    external_id=str(route.get("id") or route.get("service_identifier") or depart_at.isoformat()),
                    mode="bus",
                    operator="Itabus",
                    depart_at=depart_at,
                    arrive_at=arrive_at,
                    price=round(price, 2),
                    pickup_name=origin.get("name") or from_st["name"],
                    pickup_lat=ctx.from_lat,
                    pickup_lon=ctx.from_lon,
                    dropoff_name=destination.get("name") or to_st["name"],
                    dropoff_lat=ctx.to_lat,
                    dropoff_lon=ctx.to_lon,
                    duration_min=_duration_minutes(route.get("travel_duration")),
                    seats=None,
                    deeplink="https://www.itabus.it/en/search",
                    details={
                        "fonte": "Itabus",
                        "cambi": int(route.get("transferNumber") or 0),
                        "tappe": _tappe_from_legs(route.get("legs") or []),
                        "servizio": route.get("service_name"),
                        "servizi": ", ".join(str(s.get("code")) for s in services[:6] if isinstance(s, dict)),
                        "partenza_alternativa": from_st["name"] if from_shifted else "",
                        "arrivo_alternativo": to_st["name"] if to_shifted else "",
                        "distanza_partenza_km": from_km if from_shifted else 0,
                        "distanza_arrivo_km": to_km if to_shifted else 0,
                    },
                )
            )
        return offers
