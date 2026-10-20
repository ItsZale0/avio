"""Pianificatore deterministico: costruisce le combinazioni candidate.

L'AI non inventa nulla: puo' solo scegliere tra cio' che costruiamo qui con
dati reali. Una combinazione puo' avere 1, 2 o 3 tratte: per esempio
carsharing fino alla stazione + treno + autobus per l'ultimo miglio. Il totale
e' la somma dei prezzi noti: se una tratta non ha prezzo (es. orari GTFS) il
piano e' "parziale" e va verificato sulla tariffa ufficiale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..adapters.base import Offer, SearchContext
from ..geo import haversine_km
from ..scoring import _changes_count as _changes_of
from ..services.passes import has_urban_pass, normalize_passes

# Modalita' che possono fare da "prima tratta" (per raggiungere una stazione).
ACCESS_MODES = {"carsharing", "rideshare", "transit", "bus", "tram"}
# Modalita' che possono fare da "tratta principale".
MAIN_MODES = {"bus", "train", "rideshare", "flight"}
# Modalita' che possono coprire l'"ultimo miglio", dalla stazione a destinazione.
LAST_MODES = {"carsharing", "rideshare", "transit", "bus", "tram"}
# Prima e seconda tratta di un viaggio a due tratte (es. treno + bus, oppure
# carsharing + treno).
FIRST_MODES = ACCESS_MODES | MAIN_MODES
SECOND_MODES = MAIN_MODES | LAST_MODES

MAX_COMBOS = 40
MAX_TRIPLES = 20
MAX_SINGLES = 60
# Quante tratte "veloci" tenere in aggiunta quando l'utente preferisce
# arrivare prima (il trade-off prezzo/tempo va mostrato).
MAX_FAST_SINGLES = 15
# Quante tratte tenere per lato quando si cercano i viaggi a tre tratte.
MAX_SIDE_LEGS = 40


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
    # Tratte senza prezzo (es. orari GTFS): il totale e' parziale, va verificato.
    unpriced_legs: int = 0
    # Costo reale dei biglietti quando un abbonamento copre la tratta: 0.
    # None = nessun abbonamento, il costo e' il prezzo normale.
    net_total_price: float | None = None
    # Pass che coprono il viaggio (nomi).
    passes: list[str] = field(default_factory=list)
    # Tempo porta a porta: a piedi + mezzo + a piedi.
    walk_out_min: int = 0
    walk_in_min: int = 0
    walk_out_km: float | None = None
    walk_in_km: float | None = None

    @property
    def partial(self) -> bool:
        return self.unpriced_legs > 0

    @property
    def net_total(self) -> float:
        """Costo dei biglietti con gli abbonamenti (uguale al prezzo se non coperti)."""
        if self.net_total_price is None:
            return self.total_price
        return self.net_total_price

    @property
    def effective_cost(self) -> float:
        """Quanto si spende davvero: biglietti (con abbonamenti) + spostamento."""
        return round(self.net_total + self.access_cost, 2)

    @property
    def door_to_door_min(self) -> int | None:
        """Il tempo totale reale: a piedi, poi il viaggio, poi a piedi."""
        if self.total_duration_min is None:
            return None
        return self.total_duration_min + self.walk_out_min + self.walk_in_min

    @property
    def covered(self) -> bool:
        if not self.passes:
            return False
        # Un pass a tariffa chilometrica (altoadigemobilita Flex) non
        # azzera: si paga il km percorso.
        for leg in self.legs:
            if (getattr(leg, "details", None) or {}).get("pass_tariffa"):
                return False
        return self.net_total < self.total_price - 0.01

    @property
    def changes(self) -> int:
        """Cambi totali del viaggio (quelli dentro le tratte + i cambi di mezzo)."""
        internal = sum(_changes_of(leg) for leg in self.legs)
        # Ogni tratta dopo la prima e' un cambio di mezzo: si conta gia' il
        # passaggio fra una tratta e l'altra, quindi non si aggiunge.
        return internal

    def label(self) -> str:
        names = []
        for leg in self.legs:
            names.append(leg.operator or leg.source)
        return " + ".join(names)

    def as_dict(self, *, ranked: dict[str, dict] | None = None) -> dict:
        steps = []
        for index, leg in enumerate(self.legs, start=1):
            info = (ranked or {}).get(leg.fingerprint, {}).get("details", {})
            step = {
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
            if leg.details.get("spezzatura"):
                # Spezzatura: un passo che contiene due biglietti.
                step["spezzatura"] = True
                step["risparmio"] = leg.details.get("risparmio")
                step["prezzo_diretto"] = leg.details.get("prezzo_diretto")
                step["biglietti"] = [
                    {
                        "da": t.get("da"),
                        "a": t.get("a"),
                        "partenza": t.get("partenza"),
                        "arrivo": t.get("arrivo"),
                    }
                    for t in (leg.details.get("tappe") or [])
                    if isinstance(t, dict)
                ]
            steps.append(step)
        return {
            "candidate_id": self.id,
            "kind": self.kind,
            "title": self.label(),
            "total_price": round(self.total_price, 2),
            "net_total_price": round(self.net_total, 2),
            "covered": self.covered,
            "passes": list(self.passes),
            "access_cost": round(self.access_cost, 2),
            "effective_cost": self.effective_cost,
            "total_duration_min": self.total_duration_min,
            "door_to_door_min": self.door_to_door_min,
            "walk_out_min": self.walk_out_min,
            "walk_in_min": self.walk_in_min,
            "walk_out_km": round(self.walk_out_km, 1) if self.walk_out_km is not None else None,
            "walk_in_km": round(self.walk_in_km, 1) if self.walk_in_km is not None else None,
            "changes": self.changes,
            "depart_at": self.depart_at.isoformat() if self.depart_at else None,
            # Quando uscire di casa per arrivare al punto di ritrovo in tempo.
            "leave_home_at": (
                (self.depart_at - timedelta(minutes=self.walk_out_min)).isoformat()
                if self.depart_at and self.walk_out_min
                else None
            ),
            "arrive_at": self.arrive_at.isoformat() if self.arrive_at else None,
            "estimated": self.estimated,
            "partial": self.partial,
            "unpriced_legs": self.unpriced_legs,
            "transfer_km": round(self.transfer_km, 2) if self.transfer_km is not None else None,
            "notes": list(self.notes),
            "steps": steps,
        }


def _net_leg_cost(leg: Offer) -> float:
    """Costo della tratta con gli abbonamenti (0 se coperta)."""
    if leg.net_cost is not None:
        return leg.net_cost
    return leg.price or 0.0


def _net_info(legs: list[Offer]) -> tuple[float, list[str]]:
    """Totale con abbonamenti e elenco dei pass che coprono il viaggio."""
    totale = round(sum(_net_leg_cost(leg) for leg in legs), 2)
    pass_nomi: list[str] = []
    for leg in legs:
        nome = (leg.details or {}).get("pass")
        if nome and nome not in pass_nomi:
            pass_nomi.append(str(nome))
    return totale, pass_nomi


def _walk_times(
    legs: list[Offer], ctx: SearchContext
) -> tuple[int, int, float | None, float | None]:
    """Il tempo a piedi all'inizio e alla fine del viaggio.

    Il punto di ritrovo e' quello che l'utente ha indicato (o, se non c'e',
    il centro citta'): 20 minuti a piedi possono valere piu' di 20 euro.
    """
    from ..geo import walking_km, walking_minutes

    out_km: float | None = None
    in_km: float | None = None
    if legs:
        first = legs[0]
        origin = ctx.origin_coords
        if origin and first.pickup_lat is not None and first.pickup_lon is not None:
            out_km = walking_km(
                haversine_km(origin[0], origin[1], first.pickup_lat, first.pickup_lon)
            )
        last = legs[-1]
        destination = ctx.destination_coords
        if destination and last.dropoff_lat is not None and last.dropoff_lon is not None:
            in_km = walking_km(
                haversine_km(
                    last.dropoff_lat, last.dropoff_lon, destination[0], destination[1]
                )
            )
    return (
        walking_minutes(out_km) or 0,
        walking_minutes(in_km) or 0,
        out_km,
        in_km,
    )


def _access_cost(offer: Offer, ctx: SearchContext) -> float:
    distance = offer.pickup_distance_km(ctx)
    if distance is None or distance <= 1.2:
        return 0.0
    # Con un abbonamento urbano puoi prendere il bus: lo spostamento e' gratis.
    user_passes = normalize_passes((ctx.prefs or {}).get("passes"))
    if user_passes and has_urban_pass(user_passes) and distance <= 5:
        return 0.0
    return round(min(2.0, distance * 0.9) if distance <= 5 else max(6.0, distance * 1.1), 2)


def _transfer_km(leg1: Offer, leg2: Offer) -> float | None:
    """Il cammino nel cambio: distanza reale, non linea d'aria."""
    if None in (leg1.dropoff_lat, leg1.dropoff_lon, leg2.pickup_lat, leg2.pickup_lon):
        return None
    from ..geo import walking_km

    return walking_km(
        haversine_km(
            leg1.dropoff_lat, leg1.dropoff_lon, leg2.pickup_lat, leg2.pickup_lon  # type: ignore[arg-type]
        )
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


def _arrives_in_window(offer: Offer, ctx: SearchContext, latest: datetime) -> bool:
    """True se la tratta arriva nella finestra richiesta.

    L'arrivo deve essere nel giorno indicato dall'utente e non oltre l'ora
    richiesta: "devo essere li' entro le 11 di giovedi" non ammette un
    treno che arriva mercoledi sera.
    """
    if not offer.arrive_at:
        return True
    if offer.arrive_at > latest:
        return False
    if offer.arrive_at < ctx.arrival_day_start:
        return False
    return True


def _duration(depart: datetime | None, arrive: datetime | None) -> int | None:
    if not depart or not arrive:
        return None
    return max(0, int((arrive - depart).total_seconds() // 60))


def _legs_connected(
    leg1: Offer,
    leg2: Offer,
    ctx: SearchContext,
    *,
    max_transfer_km: float,
    transfer_min: int,
    max_wait_min: int,
) -> tuple[bool, float | None]:
    """True (con la distanza del cambio) se si puo' prendere leg2 dopo leg1.

    Le tratte senza prezzo (es. orari GTFS) sono ammesse: il piano viene
    segnalato come "parziale" e il totale e' solo la somma dei prezzi noti.
    """
    if not leg1.arrive_at or not leg2.depart_at:
        return False, None
    if leg1.source == leg2.source and leg1.external_id == leg2.external_id:
        return False, None
    transfer_km = _transfer_km(leg1, leg2)
    if transfer_km is not None and transfer_km > max_transfer_km + ctx.radius_km:
        return False, None
    earliest = leg1.arrive_at + timedelta(minutes=transfer_min)
    if leg2.depart_at < earliest:
        return False, None
    if leg2.depart_at > leg1.arrive_at + timedelta(minutes=max_wait_min):
        return False, None
    return True, transfer_km


def _price_sort(offer: Offer) -> tuple[int, float]:
    """Prima le tratte con prezzo, poi le altre (più economiche prima)."""
    return (1 if offer.price is None else 0, offer.price or 0.0)


def _build_triples(
    offers: list[Offer],
    ctx: SearchContext,
    *,
    latest: datetime,
    max_transfer_km: float,
    transfer_min: int,
    max_wait_min: int,
) -> list[Candidate]:
    """Viaggi a tre tratte: accesso + tratta principale + ultimo miglio.

    Esempio: carsharing fino alla stazione, treno, poi autobus per l'ultimo
    miglio. Almeno una tratta deve avere un prezzo noto: se ne manca qualcuno
    il piano e' "parziale" e va verificato.
    """
    access_legs = sorted(
        (o for o in offers if o.mode in FIRST_MODES and o.arrive_at),
        key=_price_sort,
    )[:MAX_SIDE_LEGS]
    main_legs = sorted(
        (o for o in offers if o.mode in MAIN_MODES and o.depart_at and o.arrive_at),
        key=_price_sort,
    )[:MAX_SIDE_LEGS]
    last_legs = sorted(
        (
            o
            for o in offers
            if o.mode in LAST_MODES
            and o.depart_at
            and _reaches_destination(o, ctx)
            and (o.arrive_at is None or o.arrive_at <= latest)
            and _arrives_in_window(o, ctx, latest)
        ),
        key=_price_sort,
    )[:MAX_SIDE_LEGS]

    found: list[Candidate] = []
    for leg2 in main_legs:
        for leg3 in last_legs:
            ok23, transfer23 = _legs_connected(
                leg2,
                leg3,
                ctx,
                max_transfer_km=max_transfer_km,
                transfer_min=transfer_min,
                max_wait_min=max_wait_min,
            )
            if not ok23:
                continue
            for leg1 in access_legs:
                if leg1 is leg2 or leg1 is leg3:
                    continue
                if _reaches_destination(leg1, ctx):
                    continue
                ok12, transfer12 = _legs_connected(
                    leg1,
                    leg2,
                    ctx,
                    max_transfer_km=max_transfer_km,
                    transfer_min=transfer_min,
                    max_wait_min=max_wait_min,
                )
                if not ok12:
                    continue
                legs = [leg1, leg2, leg3]
                priced_legs = [leg for leg in legs if leg.price is not None]
                if not priced_legs:
                    continue
                if not _arrives_in_window(leg3, ctx, latest):
                    continue
                notes = []
                if transfer12 is not None:
                    notes.append(f"cambio a {leg1.dropoff_name} (~{transfer12:.1f} km)")
                if transfer23 is not None:
                    notes.append(f"cambio a {leg2.dropoff_name} (~{transfer23:.1f} km)")
                candidate = Candidate(
                    id="",
                    kind="triple",
                    legs=legs,
                    total_price=round(sum(leg.price for leg in priced_legs), 2),
                    total_duration_min=_duration(leg1.depart_at, leg3.arrive_at),
                    depart_at=leg1.depart_at,
                    arrive_at=leg3.arrive_at,
                    estimated=any(leg.price_is_estimate for leg in priced_legs),
                    access_cost=_access_cost(leg1, ctx),
                    transfer_km=transfer12,
                    notes=notes,
                    unpriced_legs=len(legs) - len(priced_legs),
                )
                net_total, pass_nomi = _net_info(legs)
                candidate.net_total_price = net_total
                candidate.passes = pass_nomi
                (
                    candidate.walk_out_min,
                    candidate.walk_in_min,
                    candidate.walk_out_km,
                    candidate.walk_in_km,
                ) = _walk_times(candidate.legs, ctx)
                if candidate.covered:
                    candidate.notes.append(
                        f"Coperto da {', '.join(pass_nomi)}: i biglietti non li paghi."
                    )
                found.append(candidate)
        if len(found) >= MAX_TRIPLES * 5:
            break
    found.sort(key=lambda c: (c.unpriced_legs, c.total_price, c.total_duration_min or 10**6))
    return found


def build_candidates(
    offers: list[Offer],
    ctx: SearchContext,
    *,
    ranked: dict[str, dict] | None = None,
    flexible_minutes: int = 0,
    max_transfer_km: float = 3.0,
    transfer_min: int = 15,
    max_wait_min: int = 360,
    allow_multi_leg: bool = True,
    pool: list[Offer] | None = None,
) -> list[Candidate]:
    """Restituisce le combinazioni valide: prima quelle con tutti i prezzi,
    poi quelle con tratte senza prezzo (segnalate come parziali).

    `pool` è l'insieme di offerte usato per le tratte intermedie (accesso e
    ultimo miglio): serve il set grezzo, perché un'offerta che porta a uno
    snodo intermedio verrebbe scartata dai filtri di destinazione.
    """
    priced = [o for o in offers if o.price is not None]
    if not priced:
        return []

    latest = ctx.arrive_by + timedelta(minutes=flexible_minutes)
    candidates: dict[tuple, Candidate] = {}
    # Candidati che superano il budget: si tengono da parte. Se il budget
    # esaurisce i candidati, si mostrano i piu' economici anche se superano
    # il tetto, con la nota "sopra budget".
    over_budget: dict[tuple, Candidate] = {}

    prefer_fast = bool((ctx.prefs or {}).get("prefer_fast"))
    singles = sorted(priced, key=lambda o: (o.price or 0) + _access_cost(o, ctx))[:MAX_SINGLES]
    if prefer_fast:
        # Un travel agent tiene in serbo anche le piu' rapide: il trade-off
        # "5 euro in piu' ma 4 ore meno" va mostrato, non nascosto.
        fast = sorted(
            (o for o in priced if o.duration_min),
            key=lambda o: (o.duration_min, o.price or 0),
        )[:MAX_FAST_SINGLES]
        seen = {o.fingerprint for o in singles}
        singles += [o for o in fast if o.fingerprint not in seen]
    for offer in singles:
        if offer.arrive_at and offer.arrive_at > latest:
            continue
        if not _arrives_in_window(offer, ctx, latest):
            continue
        if not _reaches_destination(offer, ctx):
            continue
        candidate = Candidate(
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
        net_total, pass_nomi = _net_info([offer])
        candidate.net_total_price = net_total
        candidate.passes = pass_nomi
        (
            candidate.walk_out_min,
            candidate.walk_in_min,
            candidate.walk_out_km,
            candidate.walk_in_km,
        ) = _walk_times(candidate.legs, ctx)
        if candidate.covered:
            candidate.notes.append(
                f"Coperto da {', '.join(pass_nomi)}: il biglietto non lo paghi."
            )
        if ctx.max_price is not None and candidate.net_total > ctx.max_price:
            over_budget[("single", offer.fingerprint)] = candidate
            continue
        candidates[("single", offer.fingerprint)] = candidate

    # Combinazioni a piu' tratte: accesso per raggiungere la tratta principale,
    # ed eventualmente un ultimo miglio per arrivare a destinazione.
    if allow_multi_leg:
        legs_pool = pool if pool is not None else offers
        access_legs = sorted(
            (o for o in legs_pool if o.mode in FIRST_MODES and o.arrive_at),
            key=_price_sort,
        )[:MAX_SIDE_LEGS]
        main_legs = sorted(
            (o for o in legs_pool if o.mode in SECOND_MODES and o.depart_at and o.arrive_at),
            key=_price_sort,
        )[:MAX_SIDE_LEGS]
        for leg1 in access_legs:
            if _reaches_destination(leg1, ctx):
                # La prima tratta arriva gia' a destinazione: non serve altro.
                continue
            for leg2 in main_legs:
                if leg2 is leg1:
                    continue
                if leg2.arrive_at and leg2.arrive_at > latest:
                    continue
                if not _arrives_in_window(leg2, ctx, latest):
                    continue
                if not _reaches_destination(leg2, ctx):
                    continue
                ok, transfer_km = _legs_connected(
                    leg1,
                    leg2,
                    ctx,
                    max_transfer_km=max_transfer_km,
                    transfer_min=transfer_min,
                    max_wait_min=max_wait_min,
                )
                if not ok:
                    continue
                priced_legs = [leg for leg in (leg1, leg2) if leg.price is not None]
                if not priced_legs:
                    continue
                total = round(sum(leg.price for leg in priced_legs), 2)
                candidate = Candidate(
                    id="",
                    kind="combo",
                    legs=[leg1, leg2],
                    total_price=total,
                    total_duration_min=_duration(leg1.depart_at, leg2.arrive_at),
                    depart_at=leg1.depart_at,
                    arrive_at=leg2.arrive_at,
                    estimated=any(leg.price_is_estimate for leg in priced_legs),
                    access_cost=_access_cost(leg1, ctx),
                    transfer_km=transfer_km,
                    notes=[f"cambio a {leg1.dropoff_name} (~{transfer_km:.1f} km)"]
                    if transfer_km is not None
                    else [],
                    unpriced_legs=2 - len(priced_legs),
                )
                net_total, pass_nomi = _net_info([leg1, leg2])
                candidate.net_total_price = net_total
                candidate.passes = pass_nomi
                (
                    candidate.walk_out_min,
                    candidate.walk_in_min,
                    candidate.walk_out_km,
                    candidate.walk_in_km,
                ) = _walk_times(candidate.legs, ctx)
                if candidate.covered:
                    candidate.notes.append(
                        f"Coperto da {', '.join(pass_nomi)}: i biglietti non li paghi."
                    )
                if ctx.max_price is not None and candidate.net_total > ctx.max_price:
                    over_budget[("combo", leg1.fingerprint, leg2.fingerprint)] = candidate
                    continue
                candidates[("combo", leg1.fingerprint, leg2.fingerprint)] = candidate

        for candidate in _build_triples(
            legs_pool,
            ctx,
            latest=latest,
            max_transfer_km=max_transfer_km,
            transfer_min=transfer_min,
            max_wait_min=max_wait_min,
        ):
            key = ("triple", *(leg.fingerprint for leg in candidate.legs))
            if ctx.max_price is not None and candidate.net_total > ctx.max_price:
                over_budget[key] = candidate
                continue
            candidates.setdefault(key, candidate)

    if not candidates and over_budget:
        # Il budget ha esaurito i candidati. Non se ne propongono: il prezzo
        # reale viene comunicato dalla domanda "vuoi alzare il budget?".
        pass

    all_candidates = list(candidates.values())

    # Prima i piani con tutti i prezzi (ordinati per costo effettivo), poi quelli
    # con tratte senza prezzo: il loro totale e' solo la somma dei prezzi noti.
    def _multi_sort(items: list[Candidate]) -> list[Candidate]:
        return sorted(
            items,
            key=lambda c: (c.unpriced_legs, c.effective_cost, c.total_duration_min or 10**6),
        )

    singles_list = [c for c in all_candidates if c.kind == "single"]
    combos = _multi_sort([c for c in all_candidates if c.kind == "combo"])
    triples = _multi_sort([c for c in all_candidates if c.kind == "triple"])

    complete = singles_list + [c for c in combos if not c.partial][:MAX_COMBOS] + [
        c for c in triples if not c.partial
    ][:MAX_TRIPLES]
    if prefer_fast:
        # "Preferisco arrivare prima": prima la durata, poi il prezzo.
        complete.sort(key=lambda c: (c.total_duration_min or 10**6, c.effective_cost))
    else:
        complete.sort(key=lambda c: (c.effective_cost, c.total_duration_min or 10**6))

    partial = [c for c in combos if c.partial][:MAX_COMBOS] + [
        c for c in triples if c.partial
    ][:MAX_TRIPLES]
    partial.sort(key=lambda c: (c.unpriced_legs, c.effective_cost, c.total_duration_min or 10**6))

    ordered = complete + partial
    for index, candidate in enumerate(ordered, start=1):
        candidate.id = f"c{index}"
    return ordered
