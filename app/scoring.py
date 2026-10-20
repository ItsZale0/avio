"""Punteggio e badge delle offerte.

L'obiettivo non e' solo "il piu' economico": pesiamo prezzo, rispetto della
finestra oraria, distanza dai punti richiesti e affidabilita' dichiarata.
Il costo effettivo include una stima per raggiungere il punto di ritrovo.
"""

from __future__ import annotations

import math
from datetime import datetime

from .adapters.base import Offer, SearchContext

MODEL_WEIGHTS = {"price": 0.52, "time": 0.22, "proximity": 0.16, "reliability": 0.10}


def _time_score(offer: Offer, ctx: SearchContext) -> float:
    if not offer.arrive_at:
        return 55.0
    buffer_min = (ctx.arrive_by - offer.arrive_at).total_seconds() / 60
    if buffer_min < -1:
        return 0.0
    if buffer_min <= 20:
        return 80.0
    if buffer_min <= 180:
        return 100.0
    if buffer_min <= 360:
        return 82.0
    if buffer_min <= 720:
        return 62.0
    return 45.0


def _proximity_score(offer: Offer, ctx: SearchContext) -> float:
    distances = [d for d in (offer.pickup_distance_km(ctx), offer.dropoff_distance_km(ctx)) if d is not None]
    if not distances:
        return 62.0
    worst = max(distances)
    # 0 km -> 100, raggio pieno -> ~37, oltre -> penalita' forte
    return max(5.0, 100.0 * math.exp(-1.0 * worst / max(0.8, ctx.radius_km)))


def _reliability_score(offer: Offer) -> float:
    if offer.rating:
        return max(30.0, min(100.0, offer.rating / 5 * 100))
    return 68.0


def _access_cost(offer: Offer, ctx: SearchContext) -> float:
    """Stima € per raggiungere il punto di ritrovo (a piedi sotto 1.2 km)."""
    distance = offer.pickup_distance_km(ctx)
    if distance is None or distance <= 1.2:
        return 0.0
    # mezzi pubblici ~2€, altrimenti taxi ~1,1€/km con minimo 6€
    return round(min(2.0, distance * 0.9) if distance <= 5 else max(6.0, distance * 1.1), 2)


def score_offers(offers: list[Offer], ctx: SearchContext) -> dict[str, dict]:
    """Restituisce {fingerprint: {score, badges, details}} per le offerte attive."""
    priced = [o for o in offers if o.price is not None]
    cheapest = min((o.price for o in priced if not o.price_is_estimate), default=None)
    priciest = max((o.price for o in priced if not o.price_is_estimate), default=None)
    durations = [o.duration_min for o in offers if o.duration_min]
    fastest = min(durations) if durations else None
    pickup_distances = [
        d for d in (o.pickup_distance_km(ctx) for o in offers) if d is not None
    ]
    closest = min(pickup_distances) if pickup_distances else None

    result: dict[str, dict] = {}
    best_score = -1.0
    best_fp: str | None = None

    for offer in offers:
        score: float | None = None
        if offer.price is None:
            price_score = None
        elif cheapest and priciest and priciest > cheapest:
            price_score = 100.0 * cheapest / max(0.01, offer.price)
            price_score = min(100.0, price_score)
            if offer.price_is_estimate:
                price_score *= 0.85  # una stima vale un po' meno di un prezzo certo
        else:
            price_score = 90.0 if not offer.price_is_estimate else 76.0

        access = _access_cost(offer, ctx)
        # Le offerte con prezzo si punteggiano normalmente. Gli orari GTFS
        # (prezzo non presente nel feed) vengono punteggiati su tempo/vicinanza/
        # affidabilita' rinormalizzati; le altre offerte senza prezzo restano
        # fuori classifica e non compaiono tra le "migliori".
        has_schedule = bool(offer.details.get("orario_gtfs"))
        if price_score is not None:
            score = (
                MODEL_WEIGHTS["price"] * price_score
                + MODEL_WEIGHTS["time"] * _time_score(offer, ctx)
                + MODEL_WEIGHTS["proximity"] * _proximity_score(offer, ctx)
                + MODEL_WEIGHTS["reliability"] * _reliability_score(offer)
            )
            score = round(score, 1)
            if score > best_score:
                best_score = score
                best_fp = offer.fingerprint
        elif has_schedule:
            weight_sum = (
                MODEL_WEIGHTS["time"] + MODEL_WEIGHTS["proximity"] + MODEL_WEIGHTS["reliability"]
            )
            score = round(
                (
                    MODEL_WEIGHTS["time"] * _time_score(offer, ctx)
                    + MODEL_WEIGHTS["proximity"] * _proximity_score(offer, ctx)
                    + MODEL_WEIGHTS["reliability"] * _reliability_score(offer)
                )
                / weight_sum,
                1,
            )
            # Gli orari senza prezzo non concorrono al badge "miglior valore":
            # non conosciamo il costo, quindi non possiamo dire che sia il migliore.

        result[offer.fingerprint] = {
            "score": score,
            "badges": [],
            "details": {
                "distanza_pickup_km": _round(offer.pickup_distance_km(ctx)),
                "distanza_dropoff_km": _round(offer.dropoff_distance_km(ctx)),
                "costo_accesso_stimato": access,
                "costo_effettivo": round(offer.price + access, 2) if offer.price is not None else None,
            },
        }

    if offers:
        if cheapest is not None:
            target = next(o for o in offers if o.price == cheapest and not o.price_is_estimate)
            result[target.fingerprint]["badges"].append("cheapest")
        if fastest is not None:
            target = next(o for o in offers if o.duration_min == fastest)
            result[target.fingerprint]["badges"].append("fastest")
        if closest is not None:
            target = next(o for o in offers if _round(o.pickup_distance_km(ctx)) == _round(closest))
            result[target.fingerprint]["badges"].append("closest")
        if best_fp:
            result[best_fp]["badges"].append("best_value")

    for offer in offers:
        info = result[offer.fingerprint]
        if offer.price_is_estimate:
            info["badges"].append("estimate")
        if offer.details.get("orario_gtfs"):
            info["badges"].append("orario")
        if offer.details.get("spezzatura"):
            # Prezzo ottenuto comprando due biglietti invece di uno.
            info["badges"].append("spezzato")
        elif (
            ctx.max_price is not None
            and offer.price is not None
            and offer.price <= ctx.max_price * 0.85
            and not offer.price_is_estimate
        ):
            info["badges"].append("under_budget")

    return result


def _round(value: float | None) -> float | None:
    return round(value, 1) if value is not None else None


def human_duration(minutes: int | None) -> str:
    if not minutes:
        return "-"
    hours, mins = divmod(int(minutes), 60)
    return f"{hours}h {mins:02d}m" if hours else f"{mins}m"


def parse_iso_datetime(value: str | None) -> datetime | None:
    from .timeutil import parse_dt

    return parse_dt(value) if value else None
