"""Adapter DEMO: genera passaggi realistici per provare l'app senza fonti esterne.

I dati sono deterministici per tratta + giorno (gli ID restano stabili tra un
giro e l'altro), mentre il prezzo varia a fasce di 6 ore per simulare le
oscillazioni reali e far scattare gli avvisi di ribasso.
"""

from __future__ import annotations

import random
from datetime import timedelta

from .base import Adapter, AdapterResult, Offer, SearchContext

CARPOOL_DRIVERS = [
    ("BlaBlaCar", "Luca", 4.9),
    ("BlaBlaCar", "Giulia", 4.8),
    ("BlaBlaCar", "Marco", 4.7),
    ("BlaBlaCar", "Sara", 5.0),
    ("BlaBlaCar", "Andrea", 4.6),
]

PICKUP_NOTES = ["", "Stazione Centrale", "uscita autostrada", "fermata metro", "parcheggio scambiatore"]
DROPOFF_NOTES = ["", "centro", "stazione", "zona universitaria"]


class DemoAdapter(Adapter):
    id = "demo"
    label = "Demo (dati simulati)"
    description = "Genera offerte realistiche per provare l'interfaccia e gli avvisi."
    modes = ("rideshare", "bus", "train")
    is_demo = True

    async def search(self, ctx: SearchContext) -> AdapterResult:
        distance = ctx.distance_km or 320.0
        distance = max(25.0, min(distance, 1200.0))

        day = ctx.arrive_by.date().isoformat()
        route_seed = f"{ctx.from_place.lower()}|{ctx.to_place.lower()}|{day}|{ctx.passengers}"
        rng = random.Random(route_seed)

        bucket = int(ctx.arrive_by.timestamp() // (6 * 3600))
        price_rng = random.Random(f"{route_seed}|{bucket}")

        window_start = ctx.window_start
        window_end = ctx.arrive_by
        if window_start >= window_end:
            window_start = window_end - timedelta(hours=6)

        base_minutes = int(distance / 78 * 60) + 15
        offers: list[Offer] = []

        def offset_coord(lat: float | None, lon: float | None, km: float):
            if lat is None or lon is None:
                return None, None
            # spostamento approssimativo: 1 grado lat ~ 111 km
            r = random.Random(f"{route_seed}|{lat}|{lon}|{km}")
            return lat + r.uniform(-km, km) / 111.0, lon + r.uniform(-km, km) / 75.0

        specs = [
            ("rideshare", "BlaBlaCar", 1.00),
            ("rideshare", "BlaBlaCar", 0.86),
            ("rideshare", "BlaBlaCar", 1.12),
            ("rideshare", "BlaBlaCar", 0.72),
            ("bus", "FlixBus", 0.78),
            ("bus", "Itabus", 0.65),
            ("train", "Italo", 0.95),
            ("train", "Trenitalia", 1.05),
        ]

        for index, (mode, operator, factor) in enumerate(specs):
            if mode == "rideshare":
                driver = CARPOOL_DRIVERS[index % len(CARPOOL_DRIVERS)][1]
                label = f"{operator} · {driver}"
                rating = CARPOOL_DRIVERS[index % len(CARPOOL_DRIVERS)][2]
                base_rate = 0.062
                pickup_shift = 0.0 if index % 2 == 0 else max(0.4, ctx.radius_km * 0.45)
            elif mode == "bus":
                label = operator
                rating = 4.2
                base_rate = 0.048
                pickup_shift = 0.2
            else:
                label = operator
                rating = 4.4
                base_rate = 0.088
                pickup_shift = 0.3

            jitter = 0.88 + price_rng.random() * 0.28
            price = round(max(3.0, distance * base_rate * factor * jitter), 2)
            if ctx.max_price is not None and price > ctx.max_price * 1.6:
                price = round(ctx.max_price * (0.75 + price_rng.random() * 0.2), 2)

            duration = int(base_minutes * (0.9 + rng.random() * 0.35))
            if mode == "bus":
                duration += int(rng.random() * 35)

            span = max(1, int((window_end - window_start).total_seconds() // 60) - duration - 10)
            depart_offset = int((span / len(specs)) * index + rng.random() * max(1, span / len(specs) - 5))
            depart = window_start + timedelta(minutes=depart_offset)
            arrive = depart + timedelta(minutes=duration)

            if arrive > ctx.arrive_by:
                arrive = ctx.arrive_by
                depart = arrive - timedelta(minutes=duration)
            if ctx.depart_after and depart < ctx.depart_after:
                depart = ctx.depart_after
                arrive = depart + timedelta(minutes=duration)
            if arrive > ctx.arrive_by:
                continue

            plat, plon = offset_coord(ctx.from_lat, ctx.from_lon, pickup_shift)
            dlat, dlon = offset_coord(ctx.to_lat, ctx.to_lon, pickup_shift * 0.8)

            note = PICKUP_NOTES[index % len(PICKUP_NOTES)]
            pickup_name = ctx.from_place + (f" · {note}" if note else "")
            dropoff_name = ctx.to_place + (f" · {DROPOFF_NOTES[index % len(DROPOFF_NOTES)]}" if index % 2 else "")

            offers.append(
                Offer(
                    source=self.id,
                    external_id=f"{ctx.from_place}|{ctx.to_place}|{day}|{index}",
                    mode=mode,
                    operator=label,
                    depart_at=depart,
                    arrive_at=arrive,
                    price=price,
                    pickup_name=pickup_name,
                    pickup_lat=plat,
                    pickup_lon=plon,
                    dropoff_name=dropoff_name,
                    dropoff_lat=dlat,
                    dropoff_lon=dlon,
                    duration_min=duration,
                    seats=rng.choice([1, 1, 2, 2, 3]),
                    rating=rating,
                    deeplink="https://www.blablacar.it/" if mode == "rideshare" else "https://www.flixbus.it/",
                    details={
                        "simulato": True,
                        "bagaglio": rng.choice(["zaino", "bagaglio a mano", "1 valigia"]),
                        "note": "Offerta dimostrativa generata localmente",
                    },
                )
            )

        return AdapterResult(offers=offers, hints=[])
