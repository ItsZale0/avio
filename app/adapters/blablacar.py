"""BlaBlaCar - API ufficiale v3 (richiede chiave gratuita da dev.blablacar.com)."""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlencode

import httpx

from .base import Adapter, AdapterResult, Offer, SearchContext
from ..services.settings import get as get_setting

API_URL = "https://public-api.blablacar.com/api/v3/trips"


class BlaBlaCarAdapter(Adapter):
    id = "blablacar"
    label = "BlaBlaCar (API ufficiale)"
    description = "Passaggi in auto tramite API BlaBlaCar. Richiede una chiave gratuita."
    modes = ("rideshare",)
    requires = "blablacar_api_key"

    async def search(self, ctx: SearchContext) -> AdapterResult:
        key = get_setting("blablacar_api_key")
        if not key:
            return AdapterResult(
                hints=[
                    "BlaBlaCar: chiave API non configurata. "
                    "Usa il link di ricerca diretto oppure ottieni una chiave su dev.blablacar.com."
                ]
            )
        if None in (ctx.from_lat, ctx.from_lon, ctx.to_lat, ctx.to_lon):
            return AdapterResult(hints=["BlaBlaCar: coordinate mancanti."])

        start = ctx.depart_after or ctx.window_start
        params = {
            "from_coordinate": f"{ctx.from_lat},{ctx.from_lon}",
            "to_coordinate": f"{ctx.to_lat},{ctx.to_lon}",
            "start_date_local": start.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S"),
            "currency": "EUR",
            "locale": "it-IT",
            "count": "20",
        }

        try:
            async with httpx.AsyncClient(timeout=18) as client:
                response = await client.get(
                    API_URL,
                    params=params,
                    headers={"Authorization": f"Key {key}", "Accept": "application/json"},
                )
                if response.status_code == 401:
                    return AdapterResult(hints=["BlaBlaCar: chiave API non valida (401)."])
                if response.status_code == 429:
                    return AdapterResult(hints=["BlaBlaCar: limite richieste raggiunto, riprovo al prossimo giro."])
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            return AdapterResult(hints=[f"BlaBlaCar non raggiungibile: {type(exc).__name__}"])

        offers: list[Offer] = []
        for trip in payload.get("trips", []) or []:
            waypoints = trip.get("waypoints") or []
            if len(waypoints) >= 2:
                first, last = waypoints[0], waypoints[-1]
            else:
                first = trip.get("source") or {}
                last = trip.get("destination") or {}
                first = {"place": first, "date_time": first.get("date")}
                last = {"place": last, "date_time": last.get("date")}

            price_info = trip.get("price") or {}
            try:
                price = float(price_info.get("amount")) if price_info.get("amount") is not None else None
            except (TypeError, ValueError):
                price = None

            depart_at = _parse(first.get("date_time"))
            arrive_at = _parse(last.get("date_time"))
            if depart_at is None:
                continue

            duration = trip.get("duration_in_seconds")
            duration_min = int(duration / 60) if duration else None

            first_place = first.get("place") or {}
            last_place = last.get("place") or {}

            offers.append(
                Offer(
                    source=self.id,
                    external_id=str(trip.get("id") or trip.get("link") or f"{depart_at.isoformat()}"),
                    mode="rideshare",
                    operator="BlaBlaCar",
                    depart_at=depart_at,
                    arrive_at=arrive_at,
                    price=price,
                    pickup_name=first_place.get("address") or first_place.get("city") or ctx.from_place,
                    pickup_lat=first_place.get("latitude"),
                    pickup_lon=first_place.get("longitude"),
                    dropoff_name=last_place.get("address") or last_place.get("city") or ctx.to_place,
                    dropoff_lat=last_place.get("latitude"),
                    dropoff_lon=last_place.get("longitude"),
                    duration_min=duration_min,
                    seats=trip.get("seats_available"),
                    rating=(trip.get("driver") or {}).get("rating"),
                    deeplink=trip.get("link"),
                    details={
                        "veicolo": trip.get("vehicle", {}).get("model", "") if trip.get("vehicle") else "",
                        "fonte": "API BlaBlaCar",
                    },
                )
            )

        if not offers:
            hints.append("BlaBlaCar: nessun passaggio trovato nella finestra richiesta.")
        return AdapterResult(offers=offers, hints=hints)


def _parse(value):
    from ..timeutil import parse_dt

    try:
        return parse_dt(value)
    except Exception:
        return None
