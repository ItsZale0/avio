"""Pianificatore deterministico: costruisce le combinazioni candidate.

L'AI non inventa nulla: puo' solo scegliere tra cio' che costruiamo qui con
dati reali. Una combinazione puo' avere 1 o 2 tratte (es. carsharing fino
alla stazione + autobus). I totali sono la somma dei prezzi noti.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..adapters.base import Offer, SearchContext
from ..geo import haversine_km

# Modalita' che possono fare da "prima tratta" (per raggiungere una stazione).
ACCESS_MODES = {"carsharing", "rideshare", "transit", "bus"}
# Modalita' che possono fare da "tratta principale".
MAIN_MODES = {"bus", "train", "rideshare"}

MAX_COMBOS = 40
MAX_SINGLES = 60


@dataclass
class Candidate:
    id: str
    kind: str
    legs: list[Offer]
    total_price: float
    total_duration_min: int | None
    depart_at: datetime | None
    arrive_at: datetime | None
    estimated: bool
    access_cost: float = 0.0
    transfer_km: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def effective_cost(self) -> float:
        return round(self.total_price + self.access_cost, 2)

    def label(self) -> str:
        names = []
        for leg in self.legs:
            name = leg.operator or leg.source
            if leg.source == "demo":
                name = f"{name} (demo)"
            names.append(name)
        return " + ".join(names)

    def as_dict(self, *, ranked: dict[str, dict] | None = None) -> dict:
        steps = []
        for index, leg in enumerate(self.legs, start=1):
            info = (ranked or {}).get(leg.fingerprint, {}).get("details", {})
            steps.append(
                {
                    "leg": index,
                    "offer_id": leg.fingerprint,
                    "source": leg.source,
                    "mode": leg.mode,
                    "operator": leg.operator,
                    "depart_at": leg.depart_at.isoformat() if leg.depart_at else None,
                    "arrive_at": leg.arrive_at.isoformat() if leg.arrive_at else None,
                    "price": round(leg.price, 2) if leg.price is not None else None,
                    "price_is_estimate": leg.price_is_estimate,
                    "pickup_name": leg.pickup_name,
                    "dropoff_name": leg.dropoff_name,
                    "deeplink": leg.deeplink,
                    "access_cost": info.get("costo_accesso_stimato"),
                }
            )
        return {
            "candidate_id": self.id,
            "kind": self.kind,
            "title": self.label(),
            "total_price": round(self.total_price, 2),
            "access_cost": round(self.access_cost, 2),
            "effective_cost": self.effective_cost,
            "total_duration_min": self.total_duration_min,
            "depart_at": self.depart_at.isoformat() if self.depart_at else None,
            "arrive_at": self.arrive_at.isoformat() if self.arrive_at else None,
            "estimated": self.estimated,
            "transfer_km": round(self.transfer_km, 2) if self.transfer_km is not None else None,
            "notes": list(self.notes),
            "steps": steps,
        }


def _access_cost(offer: Offer, ctx: SearchContext) -> float:
    distance = offer.pickup_distance_km(ctx)
    if distance is None or distance <= 1.2:
        return 0.0
    return round(min(2.0, distance * 0.9) if distance <= 5 else max(6.0, distance * 1.1), 2)


def _transfer_km(leg1: Offer, leg2: Offer) -> float | None:
    if None in (leg1.dropoff_lat, leg1.dropoff_lon, leg2.pickup_lat, leg2.pickup_lon):
        return None
    return haversine_km(
        leg1.dropoff_lat, leg1.dropoff_lon, leg2.pickup_lat, leg2.pickup_lon  # type: ignore[arg-type]
    )


def _reaches_destination(offer: Offer, ctx: SearchContext) -> bool:
    """True se la tratta arriva (piu' o meno) dove l'utente deve andare."""
    if offer.details.get("arrivo_alternativo"):
        return True
    distance = offer.dropoff_distance_km(ctx)
    if distance is None:
        return True
    limit = max(ctx.radius_km * 1.4, ctx.radius_km + 2.5)
    return distance <= limit


def _duration(depart: datetime | None, arrive: datetime | None) -> int | None:
    if not depart or not arrive:
        return None
    return max(0, int((arrive - depart).total_seconds() // 60))


def build_candidates(
    offers: list[Offer],
    ctx: SearchContext,
    *,
    ranked: dict[str, dict] | None = None,
    flexible_minutes: int = 0,
    max_transfer_km: float = 3.0,
    transfer_min: int = 15,
    max_wait_min: int = 360,
) -> list[Candidate]:
    """Restituisce le combinazioni valide ordinate per costo totale."""
    # Le offerte "demo" sono simulate: mai in un piano, servono solo alla vetrina.
    priced = [o for o in offers if o.price is not None and o.source != "demo"]
    if not priced:
        return []

    latest = ctx.arrive_by + timedelta(minutes=flexible_minutes)
    candidates: dict[tuple, Candidate] = {}

    singles = sorted(priced, key=lambda o: (o.price or 0) + _access_cost(o, ctx))[:MAX_SINGLES]
    for offer in singles:
        if offer.arrive_at and offer.arrive_at > latest:
            continue
        if not _reaches_destination(offer, ctx):
            continue
        candidates[("single", offer.fingerprint)] = Candidate(
            id="",
            kind="single",
            legs=[offer],
            total_price=round(offer.price, 2),
            total_duration_min=offer.duration_min
            or _duration(offer.depart_at, offer.arrive_at),
            depart_at=offer.depart_at,
            arrive_at=offer.arrive_at,
            estimated=offer.price_is_estimate,
            access_cost=_access_cost(offer, ctx),
        )

    # Combinazioni a due tratte: una "access" per raggiungere la tratta principale.
    combos: list[Candidate] = []
    for leg1 in priced:
        if leg1.mode not in ACCESS_MODES or not leg1.arrive_at:
            continue
        for leg2 in priced:
            if leg2 is leg1 or leg2.mode not in MAIN_MODES or not leg2.depart_at:
                continue
            if leg1.source == leg2.source and leg1.external_id == leg2.external_id:
                continue
            if leg2.price is None or leg1.price is None:
                continue
            if leg2.arrive_at and leg2.arrive_at > latest:
                continue
            if not _reaches_destination(leg2, ctx):
                continue

            transfer_km = _transfer_km(leg1, leg2)
            if transfer_km is not None and transfer_km > max_transfer_km + ctx.radius_km:
                continue

            earliest = leg1.arrive_at + timedelta(minutes=transfer_min)
            if leg2.depart_at < earliest:
                continue
            if leg2.depart_at > leg1.arrive_at + timedelta(minutes=max_wait_min):
                continue

            total = round(leg1.price + leg2.price, 2)
            candidates[("combo", leg1.fingerprint, leg2.fingerprint)] = Candidate(
                id="",
                kind="combo",
                legs=[leg1, leg2],
                total_price=total,
                total_duration_min=_duration(leg1.depart_at, leg2.arrive_at),
                depart_at=leg1.depart_at,
                arrive_at=leg2.arrive_at,
                estimated=leg1.price_is_estimate or leg2.price_is_estimate,
                access_cost=_access_cost(leg1, ctx),
                transfer_km=transfer_km,
                notes=[f"cambio a {leg1.dropoff_name} (~{transfer_km:.1f} km)"]
                if transfer_km is not None
                else [],
            )

    combos = [c for c in candidates.values() if c.kind == "combo"]
    combos.sort(key=lambda c: c.total_price)
    singles_list = [c for c in candidates.values() if c.kind == "single"]

    ordered = singles_list + combos[:MAX_COMBOS]
    # Il "piu' economico" e' il costo effettivo: prezzo + costo per raggiungere il ritrovo.
    ordered.sort(key=lambda c: (c.effective_cost, c.total_duration_min or 10**6))
    for index, candidate in enumerate(ordered, start=1):
        candidate.id = f"c{index}"
    return ordered
