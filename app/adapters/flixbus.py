"""FlixBus - endpoint pubblico usato dal sito (non documentato, puo' cambiare).

In caso di errore l'adapter restituisce un hint con il link di ricerca diretto,
cosi' la ricerca resta utilizzabile.
"""

from __future__ import annotations

from datetime import timedelta

import httpx

from ..geo import CITIES, haversine_km
from ..timeutil import parse_dt
from .base import Adapter, AdapterResult, Offer, SearchContext

AUTOCOMPLETE = "https://global.api.flixbus.com/search/autocomplete/cities"
SEARCH = "https://global.api.flixbus.com/search/service/v4/search"
_city_cache: dict[str, str | None] = {}

MAX_FALLBACK_KM = 60


def _candidates(place: str, lat: float | None, lon: float | None) -> list[str]:
    """Nome inserito dall'utente + le citta' vicine servite di solito."""
    names = [place]
    if lat is None or lon is None:
        return names
    ranked = sorted(
        CITIES.items(), key=lambda item: haversine_km(lat, lon, item[1][0], item[1][1])
    )
    for name, coords in ranked:
        if name.lower() == place.strip().lower():
            continue
        if haversine_km(lat, lon, coords[0], coords[1]) > MAX_FALLBACK_KM:
            break
        names.append(name.title())
        if len(names) >= 3:
            break
    return names


def _city_distance(name: str, lat: float | None, lon: float | None) -> float:
    if lat is None or lon is None:
        return 0.0
    coords = CITIES.get(name.strip().lower())
    if not coords:
        return 0.0
    return round(haversine_km(lat, lon, coords[0], coords[1]), 1)


def _search_dates(ctx: SearchContext) -> list[str]:
    """Giorni da interrogare (max 3) per coprire la finestra richiesta."""
    start = ctx.window_start
    end = min(ctx.arrive_by, start + timedelta(days=2))
    days: list[str] = []
    current = start
    while current.date() <= end.date() and len(days) < 3:
        days.append(current.strftime("%d.%m.%Y"))
        current += timedelta(days=1)
    return days or [start.strftime("%d.%m.%Y")]


def _iter_rides(payload: dict):
    """L'API raggruppa le corse per giorno: `trips[].results` e' un dizionario."""
    for trip in payload.get("trips") or []:
        results = trip.get("results")
        if isinstance(results, dict):
            for ride in results.values():
                if isinstance(ride, dict):
                    yield ride
        elif isinstance(trip, dict):
            yield trip


def _operator_name(value: str | None) -> str:
    text = (value or "FlixBus").strip()
    return "FlixBus" if text.lower() == "flixbus" else text.title()


def _deeplink(ctx: SearchContext, date: str) -> str:
    from urllib.parse import quote

    return (
        "https://shop.flixbus.it/search?"
        f"departureCity={quote(ctx.from_place)}&arrivalCity={quote(ctx.to_place)}"
        f"&rideDate={quote(date)}&adult={ctx.passengers}"
    )


class FlixBusAdapter(Adapter):
    id = "flixbus"
    label = "FlixBus / autobus"
    description = "Collegamenti in autobus a prezzi spesso competitivi (endpoint pubblico non ufficiale)."
    modes = ("bus",)

    async def _city_id(self, client: httpx.AsyncClient, name: str) -> str | None:
        key = name.strip().lower()
        if key in _city_cache:
            return _city_cache[key]
        try:
            response = await client.get(
                AUTOCOMPLETE, params={"q": name, "lang": "it", "country": "IT"}
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list) and data:
                city = data[0]
                value = str(city.get("id") or city.get("legacy_id") or "")
                _city_cache[key] = value or None
                return value or None
        except Exception:
            pass
        _city_cache[key] = None
        return None

    async def _resolve_city(
        self, client: httpx.AsyncClient, place: str, lat: float | None, lon: float | None
    ) -> tuple[str | None, str, float]:
        """Trova la citta' servita piu' vicina a quella richiesta."""
        candidates = _candidates(place, lat, lon)
        for candidate in candidates:
            city_id = await self._city_id(client, candidate)
            if city_id:
                return city_id, candidate, _city_distance(candidate, lat, lon)
        return None, place, 0.0

    async def search(self, ctx: SearchContext) -> AdapterResult:
        days = _search_dates(ctx)
        try:
            async with httpx.AsyncClient(
                timeout=20,
                headers={"User-Agent": "Mozilla/5.0 (compatible; CarpoolHunter/0.1)"},
            ) as client:
                from_id, from_label, from_km = await self._resolve_city(
                    client, ctx.from_place, ctx.from_lat, ctx.from_lon
                )
                to_id, to_label, to_km = await self._resolve_city(
                    client, ctx.to_place, ctx.to_lat, ctx.to_lon
                )
                if not from_id or not to_id:
                    return AdapterResult(
                        hints=[f"FlixBus: citta' non riconosciuta ({ctx.from_place} / {ctx.to_place})."]
                    )

                payloads = []
                for day in days:
                    response = await client.get(
                        SEARCH,
                        params={
                            "from_city_id": from_id,
                            "to_city_id": to_id,
                            "departure_date": day,
                            "products": '{"adult":1}',
                            "currency": "EUR",
                            "locale": "it",
                            "search_by": "cities",
                            "include_after_midnight_rides": "1",
                        },
                    )
                    response.raise_for_status()
                    payloads.append(response.json())
        except Exception as exc:
            return AdapterResult(hints=[f"FlixBus non raggiungibile: {type(exc).__name__}"])

        stations: dict = {}
        for payload in payloads:
            stations.update(payload.get("stations") or {})

        offers: list[Offer] = []
        hints: list[str] = []
        shifted_from = from_label.strip().lower() != ctx.from_place.strip().lower()
        shifted_to = to_label.strip().lower() != ctx.to_place.strip().lower()
        if shifted_from:
            hints.append(
                f"FlixBus: {ctx.from_place} non e' servita, ho cercato da {from_label} (~{from_km:.0f} km)."
            )
        if shifted_to:
            hints.append(
                f"FlixBus: {ctx.to_place} non e' servita, ho cercato fino a {to_label} (~{to_km:.0f} km)."
            )

        for payload in payloads:
            for ride in _iter_rides(payload):
                try:
                    departure = ride.get("departure") or {}
                    arrival = ride.get("arrival") or {}
                    depart_at = parse_dt(departure.get("date"))
                    arrive_at = parse_dt(arrival.get("date"))
                    if not depart_at:
                        continue

                    duration = ride.get("duration") or {}
                    duration_min = None
                    if duration:
                        duration_min = int(duration.get("hours", 0)) * 60 + int(duration.get("minutes", 0))

                    price_info = ride.get("price") or {}
                    price = None
                    try:
                        price = float(price_info.get("total"))
                    except (TypeError, ValueError):
                        pass

                    station = stations.get(departure.get("station_id")) or {}
                    arrival_station = stations.get(arrival.get("station_id")) or {}
                    station_location = station.get("location") or {}
                    arrival_location = arrival_station.get("location") or {}
                    seats = (ride.get("available") or {}).get("seats")
                    uid = ride.get("uid") or f"{depart_at.isoformat()}-{ride.get('provider', 'flixbus')}"

                    offers.append(
                        Offer(
                            source=self.id,
                            external_id=str(uid),
                            mode="bus",
                            operator=_operator_name(ride.get("provider")),
                            depart_at=depart_at,
                            arrive_at=arrive_at,
                            price=price,
                            pickup_name=station.get("name") or from_label,
                            pickup_lat=station_location.get("lat", ctx.from_lat),
                            pickup_lon=station_location.get("lon", ctx.from_lon),
                            dropoff_name=arrival_station.get("name") or to_label,
                            dropoff_lat=arrival_location.get("lat", ctx.to_lat),
                            dropoff_lon=arrival_location.get("lon", ctx.to_lon),
                            duration_min=duration_min,
                            seats=seats,
                            deeplink=_deeplink(ctx, days[0]),
                            details={
                                "tratte": ride.get("transfer_type", ""),
                                "stato": ride.get("status", ""),
                                "fonte": "FlixBus",
                                "partenza_alternativa": from_label if shifted_from else "",
                                "arrivo_alternativo": to_label if shifted_to else "",
                                "distanza_partenza_km": from_km if shifted_from else 0,
                                "distanza_arrivo_km": to_km if shifted_to else 0,
                            },
                        )
                    )
                except Exception:
                    continue

        if not offers:
            hints.append("FlixBus: nessuna corsa trovata per questa data.")
        return AdapterResult(offers=offers, hints=hints)
