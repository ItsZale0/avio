"""Orari reali di treni e bus da feed GTFS aperti + motore di coincidenze.

E' l'evoluzione del vecchio adapter "transit": invece di limitarsi a elencare le
fermate vicine (OpenStreetMap), usa gli orari ufficiali (GTFS) e calcola
collegamenti reali, anche con un cambio. Il prezzo non e' nel GTFS, quindi non
viene inventato: la card rimanda alla tariffa ufficiale dell'operatore.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from ..gtfs import db as gtfs_db
from ..gtfs import engine
from ..gtfs.feeds import FEED_BY_ID
from ..timeutil import ROME
from .base import Adapter, AdapterResult, Offer, SearchContext, tappa

TRAIN_TYPES = {0, 1, 2, 5, 6, 7, 12}
BUS_TYPES = {3, 11}
MAX_OFFERS = 14
MAX_TRANSFERS = 1


def _mode(route_type: Any, line: str | None) -> str:
    if route_type in TRAIN_TYPES:
        return "train"
    text = (line or "").lower()
    if "tram" in text or "metrotram" in text:
        return "tram"
    if route_type in BUS_TYPES:
        return "bus"
    if any(token in text for token in ("tren", "regionale", " frecc", "rv ", "re ", "r ", "s ")):
        return "train"
    return "bus"


def _local_dt(day: str, seconds: int) -> datetime:
    base = datetime.fromisoformat(day)
    return (base + timedelta(seconds=int(seconds))).replace(tzinfo=ROME)


class GtfsAdapter(Adapter):
    id = "gtfs"
    label = "Orari GTFS (treni e bus)"
    description = (
        "Orari ufficiali da feed GTFS aperti (Trentino, Lombardia, Veneto) e "
        "coincidenze reali tra fermate, anche con un cambio."
    )
    modes = ("train", "bus")

    async def search(self, ctx: SearchContext) -> AdapterResult:
        from ..services import settings as settings_service

        if settings_service.get("gtfs_enabled", "1") != "1":
            return AdapterResult(hints=["GTFS: disattivato nelle Impostazioni."])
        if None in (ctx.from_lat, ctx.from_lon, ctx.to_lat, ctx.to_lon):
            return AdapterResult(hints=["GTFS: servono le coordinate di partenza e arrivo."])
        if not gtfs_db.has_data():
            return AdapterResult(
                hints=["GTFS: nessun orario importato. Impostazioni → Orari GTFS → “Aggiorna orari”."]
            )

        journeys = await asyncio.to_thread(self._collect, ctx)
        if not journeys:
            return AdapterResult(hints=["GTFS: nessun collegamento con orario tra queste fermate."])

        offers: list[Offer] = []
        seen: set[str] = set()
        for journey in journeys:
            offer = self._to_offer(journey, ctx)
            if offer is None or offer.fingerprint in seen:
                continue
            seen.add(offer.fingerprint)
            offers.append(offer)
            if len(offers) >= MAX_OFFERS:
                break
        return AdapterResult(offers=offers)

    def _collect(self, ctx: SearchContext) -> list[dict]:
        feeds = gtfs_db.imported_feed_ids()
        if not feeds:
            return []
        start_local = ctx.window_start.astimezone(ROME)
        end_local = ctx.arrive_by.astimezone(ROME)
        radius = max(3.0, min(ctx.radius_km, 15.0))
        journeys: list[dict] = []
        day = start_local.date()
        last = end_local.date()
        guard = 0
        while day <= last and guard < 4:
            midnight = datetime.combine(day, datetime.min.time(), ROME)
            dep_min = max(0, int((start_local - midnight).total_seconds()))
            dep_max = min(30 * 3600, int((end_local - midnight).total_seconds()))
            if dep_max > dep_min:
                journeys.extend(
                    engine.find_connections(
                        feeds=feeds,
                        from_lat=ctx.from_lat,
                        from_lon=ctx.from_lon,
                        to_lat=ctx.to_lat,
                        to_lon=ctx.to_lon,
                        day=day,
                        dep_min_sec=dep_min,
                        dep_max_sec=dep_max,
                        radius_km=radius,
                        max_results=24,
                        max_transfers=MAX_TRANSFERS,
                    )
                )
            day += timedelta(days=1)
            guard += 1
        return journeys

    def _to_offer(self, journey: dict, ctx: SearchContext) -> Offer | None:
        legs = journey.get("legs") or []
        if not legs:
            return None
        first, last = legs[0], legs[-1]
        day = journey["date"]
        dep_at = _local_dt(day, first["dep_sec"])
        arr_at = _local_dt(day, last["arr_sec"])
        if arr_at <= dep_at:
            return None
        if dep_at < ctx.window_start - timedelta(minutes=1):
            return None
        if arr_at > ctx.arrive_by + timedelta(minutes=10):
            return None

        feed = FEED_BY_ID.get(journey["feed"], {})
        line = first.get("line") or ""
        tappe = []
        for leg in legs:
            walk_km = float(leg.get("walk_km") or 0)
            if walk_km >= 0.05:
                start = _local_dt(day, leg["dep_sec"] - int(leg.get("walk_sec") or 0))
                tappe.append(
                    tappa(
                        leg.get("walk_from_name"),
                        leg.get("dep_name"),
                        start.isoformat(),
                        _local_dt(day, leg["dep_sec"]).isoformat(),
                        f"a piedi · {max(50, int(round(walk_km * 1000 / 50) * 50))} m",
                    )
                )
            tappe.append(
                tappa(
                    leg.get("dep_name"),
                    leg.get("arr_name"),
                    _local_dt(day, leg["dep_sec"]).isoformat(),
                    _local_dt(day, leg["arr_sec"]).isoformat(),
                    leg.get("line") or leg.get("agency") or "",
                )
            )
        details = {
            "fonte": f"GTFS · {feed.get('label', journey['feed'])}",
            "feed": journey["feed"],
            "cambi": len(legs) - 1,
            "tappe": tappe,
            "linea": line,
            "headsign": first.get("headsign") or "",
            "tariffa": "prezzo secondo la tariffa ufficiale dell'operatore",
            "orario_gtfs": True,
            "licenza": feed.get("license"),
        }
        return Offer(
            source=self.id,
            external_id="|".join(leg["trip_id"] for leg in legs),
            mode=_mode(first.get("route_type"), line),
            operator=first.get("agency") or "Trasporto pubblico",
            depart_at=dep_at,
            arrive_at=arr_at,
            price=None,
            pickup_name=first.get("dep_name") or ctx.from_place,
            pickup_lat=first.get("dep_lat"),
            pickup_lon=first.get("dep_lon"),
            dropoff_name=last.get("arr_name") or ctx.to_place,
            dropoff_lat=last.get("arr_lat"),
            dropoff_lon=last.get("arr_lon"),
            duration_min=int((arr_at - dep_at).total_seconds() // 60),
            deeplink=first.get("agency_url") or feed.get("site"),
            details=details,
        )
