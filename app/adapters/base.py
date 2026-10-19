"""Contratto comune a tutte le fonti di ricerca."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..geo import haversine_km


class AdapterError(RuntimeError):
    pass


@dataclass
class SearchContext:
    """Tutto cio' che serve a una fonte per rispondere a una ricerca."""

    watch_id: int
    from_place: str
    to_place: str
    from_lat: float | None
    from_lon: float | None
    to_lat: float | None
    to_lon: float | None
    depart_after: datetime | None
    arrive_by: datetime
    radius_km: float
    passengers: int = 1
    max_price: float | None = None

    @property
    def distance_km(self) -> float | None:
        if None in (self.from_lat, self.from_lon, self.to_lat, self.to_lon):
            return None
        return haversine_km(self.from_lat, self.from_lon, self.to_lat, self.to_lon)  # type: ignore[arg-type]

    @property
    def window_start(self) -> datetime:
        from datetime import timedelta

        if self.depart_after:
            return self.depart_after
        return self.arrive_by - timedelta(hours=10)


@dataclass
class Offer:
    source: str
    external_id: str
    mode: str
    operator: str
    depart_at: datetime | None
    arrive_at: datetime | None
    price: float | None
    pickup_name: str
    dropoff_name: str
    pickup_lat: float | None = None
    pickup_lon: float | None = None
    dropoff_lat: float | None = None
    dropoff_lon: float | None = None
    duration_min: int | None = None
    seats: int | None = None
    rating: float | None = None
    deeplink: str | None = None
    currency: str = "EUR"
    price_is_estimate: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        # Volutamente senza prezzo: lo stesso passaggio puo' cambiare prezzo.
        dep = self.depart_at.isoformat() if self.depart_at else "-"
        return f"{self.source}:{self.external_id}:{dep}"

    def pickup_distance_km(self, ctx: SearchContext) -> float | None:
        if None in (self.pickup_lat, self.pickup_lon, ctx.from_lat, ctx.from_lon):
            return None
        return haversine_km(self.pickup_lat, self.pickup_lon, ctx.from_lat, ctx.from_lon)  # type: ignore[arg-type]

    def dropoff_distance_km(self, ctx: SearchContext) -> float | None:
        if None in (self.dropoff_lat, self.dropoff_lon, ctx.to_lat, ctx.to_lon):
            return None
        return haversine_km(self.dropoff_lat, self.dropoff_lon, ctx.to_lat, ctx.to_lon)  # type: ignore[arg-type]


def iso_time(value: Any) -> str | None:
    """Normalizza un orario di una tappa in ISO (o None se illeggibile)."""
    if value is None:
        return None
    from ..timeutil import parse_dt

    parsed = parse_dt(value)
    if parsed is not None:
        return parsed.isoformat()
    text = str(value).strip()
    return text or None


def tappa(da: str | None, a: str | None, partenza: Any = None, arrivo: Any = None, mezzo: str | None = None) -> dict[str, Any]:
    """Una tratta/fermata: da dove a dove, con orari e mezzo."""
    return {
        "da": (da or "").strip(),
        "a": (a or "").strip(),
        "partenza": iso_time(partenza),
        "arrivo": iso_time(arrivo),
        "mezzo": (mezzo or "").strip(),
    }


@dataclass
class AdapterResult:
    offers: list[Offer] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)


class Adapter:
    id: str = "base"
    label: str = "Base"
    description: str = ""
    modes: tuple[str, ...] = ()
    requires: str | None = None

    async def search(self, ctx: SearchContext) -> AdapterResult:  # pragma: no cover
        raise NotImplementedError

    def meta(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "modes": list(self.modes),
            "requires": self.requires,
        }
