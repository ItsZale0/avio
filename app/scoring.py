"""Punteggio e badge delle offerte.

L'obiettivo non e' solo "il piu' economico": un bravo travel agent guarda
il quadro intero — prezzo, **durata del viaggio**, numero di cambi, quanto
si arriva prima della scadenza, distanza dai punti richiesti e affidabilita'.

Il costo effettivo include una stima per raggiungere il punto di ritrovo.

I pesi cambiano se l'utente ha detto cosa preferisce (prefs["prefer_fast"]):
con "preferisco arrivare prima" il tempo pesa piu' del prezzo.
"""

from __future__ import annotations

import math
from datetime import datetime

from .adapters.base import Offer, SearchContext
from .geo import walking_km, walking_minutes

# Pesi di base: il prezzo guida, ma tempo e comodita' contano davvero.
BASE_WEIGHTS = {
    "price": 0.40,
    "duration": 0.16,
    "changes": 0.10,
    "arrival": 0.14,
    "proximity": 0.10,
    "reliability": 0.10,
}

# Se l'utente preferisce arrivare prima, il tempo pesa quanto il prezzo.
FAST_WEIGHTS = {
    "price": 0.28,
    "duration": 0.30,
    "changes": 0.12,
    "arrival": 0.14,
    "proximity": 0.08,
    "reliability": 0.08,
}


def _weights(prefs: dict) -> dict[str, float]:
    if prefs.get("prefer_fast"):
        return dict(FAST_WEIGHTS)
    return dict(BASE_WEIGHTS)


def _duration_score(offer: Offer, fastest_min: int | None) -> float:
    """Quanto e' rapido il viaggio, rispetto al piu' rapido trovato.

    Non e' una punizione assoluta: un viaggio 30 minuti piu' lento del
    migliore e' praticamente uguale, uno 5 ore piu' lungo no.
    """
    if not offer.duration_min or not fastest_min:
        return 62.0
    if offer.duration_min <= fastest_min:
        return 100.0
    slower = offer.duration_min - fastest_min
    if slower <= 20:
        return 96.0
    if slower <= 60:
        return 88.0
    if slower <= 120:
        return 76.0
    if slower <= 240:
        return 60.0
    if slower <= 420:
        return 42.0
    return 25.0


def _changes_count(offer: Offer) -> int:
    """Quanti cambi servono per questo viaggio.

    Se la fonte li dichiara si usa il dato, altrimenti si deduce dalle tappe
    (ogni tappa in piu' della prima e' un cambio).
    """
    changes = offer.details.get("cambi")
    if changes is None:
        tappe = offer.details.get("tappe") or []
        changes = max(0, len(tappe) - 1) if tappe else 0
    try:
        return int(changes)
    except (TypeError, ValueError):
        return 0


def _changes_score(offer: Offer) -> float:
    """Comodita': quanti cambi e quante tratte.

    Un viaggio diretto e' sempre preferibile a uno con un cambio, se il
    prezzo non e' molto diverso.
    """
    changes = _changes_count(offer)
    if changes <= 0:
        return 100.0
    if changes == 1:
        return 82.0
    if changes == 2:
        return 62.0
    if changes == 3:
        return 45.0
    return 30.0


def _time_score(offer: Offer, ctx: SearchContext) -> float:
    """Quanto si arriva prima della scadenza (cuscinetto)."""
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


def _cost_of(offer: Offer) -> float | None:
    """Il costo che l'utente paga davvero (0 se coperto da un abbonamento)."""
    if offer.net_cost is not None:
        return offer.net_cost
    return offer.price


def score_offers(offers: list[Offer], ctx: SearchContext) -> dict[str, dict]:
    """Restituisce {fingerprint: {score, badges, details}} per le offerte attive."""
    priced = [o for o in offers if _cost_of(o) is not None]
    cheapest = min((_cost_of(o) for o in priced if not o.price_is_estimate), default=None)
    priciest = max((_cost_of(o) for o in priced if not o.price_is_estimate), default=None)
    durations = [o.duration_min for o in offers if o.duration_min]
    # La durata di riferimento e' quella del viaggio COMPLETO piu' rapido:
    # confrontare un treno di 3 ore con un accesso in auto di 12 minuti
    # non ha senso. Se non ci sono viaggi completi, si usa il minimo totale.
    limit = max(ctx.radius_km * 1.4, ctx.radius_km + 2.5)
    journey_offers = [
        o
        for o in offers
        if o.duration_min
        and (
            o.details.get("arrivo_alternativo")
            or o.dropoff_distance_km(ctx) is None
            or (o.dropoff_distance_km(ctx) or 0) <= limit
        )
    ]
    journey_durations = [o.duration_min for o in journey_offers]
    fastest = min(journey_durations) if journey_durations else (
        min(durations) if durations else None
    )
    pickup_distances = [
        d for d in (o.pickup_distance_km(ctx) for o in offers) if d is not None
    ]
    closest = min(pickup_distances) if pickup_distances else None

    weights = _weights(ctx.prefs or {})
    result: dict[str, dict] = {}
    best_score = -1.0
    best_fp: str | None = None

    for offer in offers:
        score: float | None = None
        costo = _cost_of(offer)
        if costo is None:
            price_score = None
        elif cheapest is not None and priciest is not None and priciest > cheapest:
            price_score = 100.0 * cheapest / max(0.01, costo)
            price_score = min(100.0, price_score)
            if offer.price_is_estimate:
                price_score *= 0.85  # una stima vale un po' meno di un prezzo certo
        else:
            price_score = 90.0 if not offer.price_is_estimate else 76.0

        duration_score = _duration_score(offer, fastest)
        changes = _changes_score(offer)
        arrival_score = _time_score(offer, ctx)
        proximity = _proximity_score(offer, ctx)
        reliability = _reliability_score(offer)

        access = _access_cost(offer, ctx)
        # Le offerte con prezzo si punteggiano normalmente. Gli orari GTFS
        # (prezzo non presente nel feed) vengono punteggiati su tempo/vicinanza/
        # affidabilita' rinormalizzati; le altre offerte senza prezzo restano
        # fuori classifica e non compaiono tra le "migliori".
        has_schedule = bool(offer.details.get("orario_gtfs"))
        if price_score is not None:
            score = (
                weights["price"] * price_score
                + weights["duration"] * duration_score
                + weights["changes"] * changes
                + weights["arrival"] * arrival_score
                + weights["proximity"] * proximity
                + weights["reliability"] * reliability
            )
            score = round(score, 1)
            if score > best_score:
                best_score = score
                best_fp = offer.fingerprint
        elif has_schedule:
            weight_sum = (
                weights["duration"]
                + weights["changes"]
                + weights["arrival"]
                + weights["proximity"]
                + weights["reliability"]
            )
            score = round(
                (
                    weights["duration"] * duration_score
                    + weights["changes"] * changes
                    + weights["arrival"] * arrival_score
                    + weights["proximity"] * proximity
                    + weights["reliability"] * reliability
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
                # I minuti a piedi calcolati dal backend (percorso reale,
                # non linea d'aria): il frontend non deve ricalcolarli.
                "minuti_pickup_a_piedi": _minuti_a_piedi(
                    offer.pickup_distance_km(ctx)
                ),
                "minuti_dropoff_a_piedi": _minuti_a_piedi(
                    offer.dropoff_distance_km(ctx)
                ),
                # I km realmente percorsi a piedi (non la retta): cosi' i
                # minuti e i km nella card dicono la stessa cosa.
                "km_pickup_a_piedi": _round(
                    walking_km(offer.pickup_distance_km(ctx))
                ),
                "km_dropoff_a_piedi": _round(
                    walking_km(offer.dropoff_distance_km(ctx))
                ),
                "costo_accesso_stimato": access,
                "costo_effettivo": round(costo + access, 2) if costo is not None else None,
                "durata_min": offer.duration_min,
                "cambi": _changes_count(offer),
            },
        }

    if offers:
        if cheapest is not None:
            target = next(
                (
                    o
                    for o in offers
                    if _cost_of(o) == cheapest and not o.price_is_estimate
                ),
                None,
            )
            if target is not None:
                result[target.fingerprint]["badges"].append("cheapest")
        if fastest is not None:
            same = [o for o in offers if o.duration_min == fastest]
            # Preferisce un viaggio completo rispetto a una tratta di accesso.
            journey_fps = {o.fingerprint for o in journey_offers}
            target = next((o for o in same if o.fingerprint in journey_fps), same[0])
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
            and _cost_of(offer) is not None
            and _cost_of(offer) <= ctx.max_price * 0.85
            and not offer.price_is_estimate
        ):
            info["badges"].append("under_budget")
        if offer.net_cost is not None:
            # Il biglietto e' gia' coperto dall'abbonamento: costa 0.
            info["badges"].append("coperto")
        if _changes_count(offer) == 0 and offer.mode in ("train", "bus", "flight"):
            # Nessun cambio: la comodita' massima, se il prezzo non e' assurdo.
            info["badges"].append("diretto")

    return result


def _minuti_a_piedi(distanza_km: float | None) -> int | None:
    """Minuti a piedi per una distanza in linea d'aria (percorso reale)."""
    return walking_minutes(walking_km(distanza_km))


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
