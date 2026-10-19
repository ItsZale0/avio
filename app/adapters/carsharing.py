"""Carsharing: stima del costo di un noleggio a uso singolo e zone disponibili.

Le tariffe sono indicative e configurabili in `data/carsharing.json`.
La disponibilita' reale del veicolo va sempre verificata nell'app del provider:
qui stimiamo solo il costo e la fattibilita' del viaggio.
"""

from __future__ import annotations

import json
from datetime import timedelta

from ..config import DATA_DIR
from ..geo import haversine_km, nearest_city
from .base import Adapter, AdapterResult, Offer, SearchContext

DEFAULT_PROVIDERS = [
    {
        "id": "enjoy",
        "name": "Enjoy",
        "cities": ["milano", "roma", "torino", "firenze", "bologna"],
        "unlock": 1.0,
        "per_min": 0.29,
        "per_km": 0.49,
        "day_cap": 59.0,
        "intercity_one_way": False,
        "url": "https://enjoy.eni.com/it/",
        "note": "Free floating, parcheggio in zone Enjoy",
    },
    {
        "id": "free2move",
        "name": "Free2Move / Share Now",
        "cities": ["milano", "roma", "torino", "firenze"],
        "unlock": 1.0,
        "per_min": 0.39,
        "per_km": 0.45,
        "day_cap": 69.0,
        "intercity_one_way": False,
        "url": "https://www.free2move.com/it/it/",
        "note": "Free floating, area di rilascio in app",
    },
    {
        "id": "ubeeqo",
        "name": "Ubeeqo",
        "cities": ["milano", "roma", "torino", "bologna", "firenze"],
        "unlock": 0.0,
        "per_min": 0.35,
        "per_km": 0.40,
        "day_cap": 55.0,
        "intercity_one_way": False,
        "url": "https://www.ubeeqo.it/",
        "note": "Stazioni dedicatE, prenotazione anticipata",
    },
    {
        "id": "evai",
        "name": "E-Vai",
        "cities": ["milano", "bergamo", "brescia", "como", "varese", "monza", "pavia"],
        "unlock": 0.0,
        "per_min": 0.25,
        "per_km": 0.35,
        "day_cap": 49.0,
        "intercity_one_way": True,
        "url": "https://www.e-vai.com/",
        "note": "Elettrico, possibile one-way tra stazioni E-Vai",
    },
]


def load_providers() -> list[dict]:
    path = DATA_DIR / "carsharing.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass
    return DEFAULT_PROVIDERS


def _rate_text(provider: dict) -> str:
    return (
        f"{provider.get('unlock', 0):.2f}€ sblocco + "
        f"{provider.get('per_min', 0):.2f}€/min + "
        f"{provider.get('per_km', 0):.2f}€/km"
    )


class CarsharingAdapter(Adapter):
    id = "carsharing"
    label = "Carsharing (stima costo)"
    description = "Stima il costo di un'auto condivisa e verifica le città coperte."
    modes = ("carsharing",)

    async def search(self, ctx: SearchContext) -> AdapterResult:
        if None in (ctx.from_lat, ctx.from_lon, ctx.to_lat, ctx.to_lon):
            return AdapterResult(hints=["Coordinate mancanti: impossibile stimare il carsharing."])

        from_city = nearest_city(ctx.from_lat, ctx.from_lon, 25)  # type: ignore[arg-type]
        to_city = nearest_city(ctx.to_lat, ctx.to_lon, 25)  # type: ignore[arg-type]
        if not from_city or not to_city:
            return AdapterResult()

        direct = haversine_km(ctx.from_lat, ctx.from_lon, ctx.to_lat, ctx.to_lon)  # type: ignore[arg-type]
        road_km = direct * 1.28
        duration_min = max(12, int(road_km / 26 * 60))
        departure = ctx.depart_after or (ctx.arrive_by - timedelta(minutes=duration_min + 30))
        arrival = departure + timedelta(minutes=duration_min)

        offers: list[Offer] = []
        for provider in load_providers():
            cities = {str(c).lower() for c in provider.get("cities", [])}
            if from_city[0] not in cities or to_city[0] not in cities:
                continue

            intercity = from_city[0] != to_city[0]
            if intercity and not provider.get("intercity_one_way"):
                continue
            if road_km > 420:
                continue

            cost = (
                float(provider.get("unlock", 0))
                + duration_min * float(provider.get("per_min", 0))
                + road_km * float(provider.get("per_km", 0))
            )
            cost = min(cost, float(provider.get("day_cap", 999)))
            cost = round(cost, 2)

            offers.append(
                Offer(
                    source=self.id,
                    external_id=f"{provider['id']}|{from_city[0]}|{to_city[0]}|{ctx.arrive_by.date()}",
                    mode="carsharing",
                    operator=provider.get("name", provider["id"]),
                    depart_at=departure,
                    arrive_at=arrival,
                    price=cost,
                    pickup_name=f"Auto in zona {from_city[0].title()}",
                    pickup_lat=ctx.from_lat,
                    pickup_lon=ctx.from_lon,
                    dropoff_name=f"Rilascio in zona {to_city[0].title()}",
                    dropoff_lat=ctx.to_lat,
                    dropoff_lon=ctx.to_lon,
                    duration_min=duration_min,
                    seats=4,
                    deeplink=provider.get("url"),
                    price_is_estimate=True,
                    details={
                        "stima": True,
                        "distanza_km": round(road_km, 1),
                        "tariffa": _rate_text(provider),
                        "nota": provider.get("note", ""),
                        "disponibilita": "Verificare l'auto nell'app del provider",
                        "intercity": intercity,
                    },
                )
            )

        hints = []
        if not offers:
            hints.append(
                "Nessun carsharing one-way stimato per questa tratta: "
                "prova una tratta urbana o combinata con il treno."
            )
        return AdapterResult(offers=offers, hints=hints)
