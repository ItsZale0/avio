"""Spezzatura dei biglietti: pagare meno (o sapere quanto costa) con due biglietti.

Su molte tratte italiane il biglietto unico costa piu' di due biglietti
partenziali: un regionale fino a una citta' intermedia e un secondo
biglietto da li' a destinazione. E' una pratica diffusa, e per trovarla
serve chiedere all'API i prezzi dei singoli segmenti.

Regole:
- si valutano solo i viaggi ferroviari con tappe note (l'itinerario reale);
- per ogni punto di spezzatura si chiedono i prezzi dei due segmenti;
- la spezzatura entra nel confronto **solo se e' piu' economica** del
  biglietto diretto: mai un piano con prezzi inventati o gonfiati;
- ma **tutte** le spezzature vengono riportate come confronto ("quanto
  cambia"): prezzo, tempo e numero di cambi rispetto al diretto. Serve a
  decidere con tutti i numeri davanti, non solo con il piu' economico.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

import httpx

from ..adapters.base import Offer, SearchContext, tappa
from ..adapters.trenitalia import (
    HEADERS,
    SOLUTIONS_URL,
    _lookup_station,
    query_starts,
)
from ..timeutil import ROME, iso, parse_dt
from . import passes as passes_service

log = logging.getLogger("carpool.split")

# Un segmento deve partire almeno dopo che il precedente e' arrivato.
MIN_TRANSFER_MIN = 25
# Quanti segmenti di partenza si provano per ogni nodo: il piu' economico
# potrebbe non permettere il cambio, il secondo potrebbe costare poco di piu'.
MAX_FIRST_LEGS = 4
# Quanti punti di spezzatura si provano per viaggio.
MAX_SPLIT_POINTS = 5
# Con "preferisco arrivare prima", una spezzatura che arriva almeno
# MIN_FASTER_MINUTES prima entra nel confronto anche se costa di piu'.
MIN_FASTER_MINUTES = 30
# Quanto puo' costare in piu' una spezzatura piu' veloce per essere scelta.
MAX_EXTRA_PRICE = 8.0
# Quanti viaggi si provano a spezzare (i piu' economici diretti).
MAX_SPLIT_OFFERS = 2

# I nodi ferroviari italiani, in ordine di importanza: e' li' che la
# spezzatura conviene, perche' offrono regionali economici che il treno
# diretto non usa.
RAIL_HUBS = [
    "bologna", "firenze", "verona", "padova", "roma", "milano", "napoli",
    "torino", "venezia", "genova", "ancona", "parma", "modena", "livorno",
    "pisa", "perugia", "pescara", "rimini", "ferrara", "arezzo", "salerno",
    "bari", "lecce", "reggio calabria", "taranto", "potenza", "ravenna",
    "piacenza", "la spezia", "genova piazza principe", "mestre", "venice",
]


async def _segment_price(
    client: httpx.AsyncClient,
    from_id: str,
    to_id: str,
    when,
) -> list[dict[str, Any]]:
    """Le soluzioni piu' economiche per un singolo segmento.

    L'API restituisce anche corse che partono prima dell'ora richiesta:
    qui vengono scartate, altrimenti il "cambio" sarebbe impossibile.
    """
    body = {
        "departureLocationId": from_id,
        "arrivalLocationId": to_id,
        "departureTime": when.astimezone(ROME).strftime("%Y-%m-%dT%H:%M:%S.000"),
        "adults": 1,
        "children": 0,
        "criteria": {
            "frecceOnly": False,
            "regionalOnly": False,
            "intercityOnly": False,
            "noChanges": False,
            "order": "DEPARTURE_DATE",
            "offset": 0,
            "limit": 12,
        },
        "advancedSearchRequest": {"bestFare": False, "bikeFilter": False},
    }
    try:
        response = await client.post(SOLUTIONS_URL, json=body)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        log.debug("segmento non disponibile: %s", exc)
        return []

    found: list[dict[str, Any]] = []
    for entry in payload.get("solutions") or []:
        solution = entry.get("solution") if isinstance(entry, dict) else None
        if not isinstance(solution, dict):
            continue
        if str(solution.get("status")) != "SALEABLE":
            continue
        try:
            price = float((solution.get("price") or {}).get("amount"))
        except (TypeError, ValueError):
            continue
        depart_at = parse_dt(solution.get("departureTime"))
        arrive_at = parse_dt(solution.get("arrivalTime"))
        if price <= 0 or not depart_at or not arrive_at:
            continue
        if depart_at < when:
            continue  # parte prima di quando richiesto
        found.append(
            {
                "price": round(price, 2),
                "depart_at": depart_at,
                "arrive_at": arrive_at,
                "origin": solution.get("origin") or "",
                "destination": solution.get("destination") or "",
                "id": str(solution.get("id") or ""),
            }
        )
    return sorted(found, key=lambda s: s["price"])


def _is_endpoint(hub: str, ctx: SearchContext) -> bool:
    """True se l'hub coincide con la partenza o l'arrivo."""
    for place in (ctx.from_place or "", ctx.to_place or ""):
        place = place.strip().lower()
        if not place:
            continue
        if hub in place or place in hub:
            return True
    return False


def _split_points(offer: Offer, ctx: SearchContext) -> list[str]:
    """Le citta' dove provare a spezzare il biglietto, in ordine di promessa.

    Prima i **nodi ferroviari in linea** sul percorso (Bologna, Firenze,
    Verona...): e' li' che la spezzatura conviene, perche' offrono
    regionali economici che il treno diretto non usa. Poi le fermate reali
    dell'itinerario, che sono un buon piano B.

    "In linea" vuol dire che la deviazione e' minima:
    dist(partenza, X) + dist(X, arrivo) - dist(partenza, arrivo) < 70 km.
    """
    points: list[str] = []

    if None not in (ctx.from_lat, ctx.from_lon, ctx.to_lat, ctx.to_lon):
        from ..geo import CITIES, haversine_km

        direct = haversine_km(ctx.from_lat, ctx.from_lon, ctx.to_lat, ctx.to_lon)  # type: ignore[arg-type]
        if direct:
            on_the_way: list[tuple[int, str]] = []
            for priority, hub in enumerate(RAIL_HUBS):
                coords = CITIES.get(hub)
                if not coords or _is_endpoint(hub, ctx):
                    continue
                first = haversine_km(ctx.from_lat, ctx.from_lon, coords[0], coords[1])  # type: ignore[arg-type]
                second = haversine_km(coords[0], coords[1], ctx.to_lat, ctx.to_lon)  # type: ignore[arg-type]
                if first is None or second is None:
                    continue
                if first + second - direct < 70:
                    on_the_way.append((priority, hub))
            on_the_way.sort()
            for _, hub in on_the_way:
                label = hub.title()
                if label not in points:
                    points.append(label)

    # Poi le fermate reali del treno.
    tappe = offer.details.get("tappe") or []
    for step in tappe:
        if not isinstance(step, dict):
            continue
        for key in ("da", "a"):
            name = str(step.get(key) or "").strip()
            if name and name not in points:
                points.append(name)

    # Escludi partenza e arrivo.
    start = str(offer.pickup_name or "").strip().lower()
    end = str(offer.dropoff_name or "").strip().lower()
    return [p for p in points if p.lower() not in (start, end)]


def _duration(depart: datetime | None, arrive: datetime | None) -> int:
    if not depart or not arrive:
        return 0
    return max(0, int((arrive - depart).total_seconds() // 60))


async def _split_via(
    client: httpx.AsyncClient,
    offer: Offer,
    ctx: SearchContext,
    origin: dict,
    destination: dict,
    point: str,
) -> dict[str, Any] | None:
    """Calcola lo spezzamento attraverso un nodo e il confronto col diretto."""
    # Il nodo puo' essere una citta' ("Verona") o una stazione vera
    # ("Verona Porta Nuova"): per le citta' si passano le coordinate del
    # centro, cosi' l'API non viene scambiata per l'aeroporto o la stazione
    # dei bus.
    from ..geo import CITIES

    coords = CITIES.get(point.strip().lower())
    middle = await _lookup_station(
        client,
        point,
        coords[0] if coords else None,
        coords[1] if coords else None,
    )
    if not middle or middle["id"] in (origin["id"], destination["id"]):
        return None

    # Il primo biglietto parte dall'inizio della finestra di ricerca,
    # non dall'ora del treno diretto: altrimenti si perdono i regionali
    # mattutini, che sono quelli economici (e' il trucco).
    # Si provano piu' partenze (sera prima e giorno di arrivo): l'API
    # restituisce solo una decina di soluzioni per richiesta.
    # E non basta il segmento piu' economico: se arriva troppo tardi per
    # fare il cambio, si prova il secondo, il terzo, e cosi' via. Un
    # segmento 50 centesimi piu' caro puo' far risparmiare l'intero
    # spezzamento.
    best: tuple[dict[str, Any], dict[str, Any], float] | None = None
    for start in query_starts(ctx):
        first = await _segment_price(client, origin["id"], middle["id"], start)
        if not first:
            continue
        # Candidati: la prima tratta deve arrivare nel giorno richiesto,
        # altrimenti si aspetta una notte in stazione (che non e' un
        # viaggio). Un treno notturno che parte la sera prima e arriva
        # dopo mezzanotte resta valido.
        candidati = [
            leg1
            for leg1 in first[:MAX_FIRST_LEGS]
            if leg1["arrive_at"] >= ctx.arrival_day_start
            and leg1["depart_at"] >= ctx.window_start - timedelta(minutes=15)
        ]
        if not candidati:
            continue
        # I secondi segmenti si cercano in parallelo: ogni candidato
        # arriva in un momento diverso, ma le richieste non dipendono
        # l'una dall'altra.
        secondi = await asyncio.gather(
            *(
                _segment_price(
                    client,
                    middle["id"],
                    destination["id"],
                    leg1["arrive_at"] + timedelta(minutes=MIN_TRANSFER_MIN),
                )
                for leg1 in candidati
            ),
            return_exceptions=True,
        )
        for leg1, second in zip(candidati, secondi):
            if isinstance(second, Exception) or not second:
                continue
            # Il secondo biglietto deve arrivare nel giorno giusto E in
            # tempo: si sceglie il piu' economico fra quelli che valgono.
            utili = [
                s
                for s in second
                if ctx.arrival_day_start
                <= s["arrive_at"]
                <= ctx.arrive_by + timedelta(minutes=10)
            ]
            if not utili:
                continue
            leg2 = utili[0]
            total = round(leg1["price"] + leg2["price"], 2)
            if best is None or total < best[2]:
                best = (leg1, leg2, total)

    if best is None:
        return None
    leg1, leg2, total = best

    if leg2["arrive_at"] > ctx.arrive_by + timedelta(minutes=10):
        return None  # arriva oltre il limite
    if leg2["arrive_at"] < ctx.arrival_day_start:
        return None  # arriva il giorno prima
    if leg1["depart_at"] < ctx.window_start - timedelta(minutes=15):
        return None  # parte prima della finestra

    diretto = offer.price or 0.0
    durata = _duration(leg1["depart_at"], leg2["arrive_at"])
    durata_diretto = offer.duration_min or _duration(offer.depart_at, offer.arrive_at)
    return {
        "via": middle["name"],
        "tratte": [
            {
                "da": leg1["origin"] or origin["name"],
                "a": leg1["destination"] or middle["name"],
                "prezzo": leg1["price"],
                "partenza": iso(leg1["depart_at"]),
                "arrivo": iso(leg1["arrive_at"]),
                "durata_min": _duration(leg1["depart_at"], leg1["arrive_at"]),
            },
            {
                "da": leg2["origin"] or middle["name"],
                "a": leg2["destination"] or destination["name"],
                "prezzo": leg2["price"],
                "partenza": iso(leg2["depart_at"]),
                "arrivo": iso(leg2["arrive_at"]),
                "durata_min": _duration(leg2["depart_at"], leg2["arrive_at"]),
            },
        ],
        "totale": total,
        "diretto_prezzo": diretto,
        # Positivo = la spezzatura costa di piu' del diretto.
        "delta_prezzo": round(total - diretto, 2),
        "durata_min": durata,
        "diretto_durata_min": durata_diretto,
        # Positivo = la spezzatura ci mette di piu'.
        "delta_minuti": durata - durata_diretto,
        "partenza": iso(leg1["depart_at"]),
        "arrivo": iso(leg2["arrive_at"]),
        "diretto_partenza": iso(offer.depart_at),
        "diretto_arrivo": iso(offer.arrive_at),
        "cambi": 1,
        "conveniente": total < diretto,
        "leg1": leg1,
        "leg2": leg2,
        "origin": origin,
        "middle": middle,
        "destination": destination,
        "offer": offer,
    }


def _comparison(confronto: dict[str, Any]) -> dict[str, Any]:
    """Il confronto, senza i dati interni (per la UI e per l'AI)."""
    return {
        k: v
        for k, v in confronto.items()
        if k not in ("leg1", "leg2", "origin", "middle", "destination", "offer")
    }


def _scontato(listino: float, sconto_xgo: float) -> float:
    """Il prezzo del biglietto con lo sconto X-GO (se spetta)."""
    if sconto_xgo and listino > 0:
        return round(listino * (1 - sconto_xgo), 2)
    return listino


def applica_abbonamenti(
    comparisons: list[dict[str, Any]],
    passes: list[dict[str, Any]] | None,
    sconto_xgo: float = 0.0,
) -> None:
    """Prezza ogni biglietto della spezzatura con abbonamenti e X-GO.

    Spezzando si compra un titolo per tratta: il pass va applicato al
    singolo biglietto (Flex solo dove e' valido), non alla somma. Cosi'
    Bressanone→Bolzano viene 4,96 col Flex e non 8,00, mentre
    Bolzano→Rovereto resta a prezzo intero perche' esce dall'Alto Adige.
    """
    if not comparisons:
        return
    if not passes and not sconto_xgo:
        for confronto in comparisons:
            confronto.setdefault("totale_effettivo", confronto.get("totale"))
            confronto.setdefault("diretto_effettivo", confronto.get("diretto_prezzo"))
        return

    for confronto in comparisons:
        totale_effettivo = 0.0
        for tratta in confronto.get("tratte") or []:
            listino = float(tratta.get("prezzo") or 0)
            scontato = _scontato(listino, sconto_xgo)
            if sconto_xgo and scontato < listino:
                tratta["sconto_xgo"] = round(listino - scontato, 2)
            costo, nome = passes_service.netto_tratta(
                passes or [], tratta.get("da") or "", tratta.get("a") or "", scontato
            )
            tratta["prezzo_effettivo"] = costo
            if nome:
                tratta["pass"] = nome
                km = passes_service.km_tratta(
                    tratta.get("da") or "", tratta.get("a") or ""
                )
                if km:
                    tratta["pass_km"] = round(km, 1)
            totale_effettivo += costo

        diretta = confronto.get("offer")
        listino_diretto = float(getattr(diretta, "price", 0) or 0)
        scontato_diretto = _scontato(listino_diretto, sconto_xgo)
        diretto_eff, nome_diretto = passes_service.netto_tratta(
            passes or [],
            getattr(diretta, "pickup_name", "") or "",
            getattr(diretta, "dropoff_name", "") or "",
            scontato_diretto,
        )
        confronto["totale_effettivo"] = round(totale_effettivo, 2)
        confronto["diretto_effettivo"] = diretto_eff
        if nome_diretto:
            confronto["diretto_pass"] = nome_diretto
        confronto["delta_prezzo"] = round(
            confronto["totale_effettivo"] - diretto_eff, 2
        )
        confronto["conveniente"] = confronto["delta_prezzo"] < -0.01


async def analyze_splits(
    offers: list[Offer],
    ctx: SearchContext,
    *,
    prefer_fast: bool = False,
    passes: list[dict[str, Any]] | None = None,
    sconto_xgo: float = 0.0,
) -> tuple[list[Offer], list[dict[str, Any]]]:
    """Spezza il viaggio e confronta ogni spezzatura col diretto.

    Restituisce due cose:
    - le offerte "spezzate" che BATONO il diretto (entrano nel pool e
      competono con le altre);
    - l'elenco dei confronti (anche di quelli che costano di piu'): serve a
      mostrare quanto cambia davvero, prezzo e tempo.

    ``passes`` sono gli abbonamenti dell'utente: si applicano a ogni singolo
    biglietto, perche' spezzando si compra un titolo per tratta.
    """
    candidates = [
        offer
        for offer in offers
        if offer.mode == "train" and offer.price and (offer.details.get("tappe") or [])
    ]
    if not candidates:
        return [], []

    # Il viaggio da battere e' il piu' economico in assoluto: e' quello su
    # cui la spezzatura ha piu' margine.
    candidates.sort(key=lambda o: o.price or 0)
    candidates = candidates[:MAX_SPLIT_OFFERS]

    comparisons: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=25, headers=HEADERS) as client:
            for offer in candidates:
                origin = await _lookup_station(
                    client, offer.pickup_name or ctx.from_place
                )
                destination = await _lookup_station(
                    client, offer.dropoff_name or ctx.to_place
                )
                if not origin or not destination:
                    continue
                points = _split_points(offer, ctx)[:MAX_SPLIT_POINTS]
                # I nodi si provano in parallelo: ogni punto costa due
                # richieste all'API e la ricerca non puo' durare troppo.
                results = await asyncio.gather(
                    *(
                        _split_via(client, offer, ctx, origin, destination, point)
                        for point in points
                    ),
                    return_exceptions=True,
                )
                for result in results:
                    if isinstance(result, dict):
                        comparisons.append(result)
    except Exception as exc:  # noqa: BLE001
        log.debug("spezzatura non disponibile: %s", exc)
        return [], []

    # Solo una spezzatura per nodo: quella col costo migliore.
    best_per_via: dict[str, dict[str, Any]] = {}
    for confronto in comparisons:
        key = str(confronto["via"]).lower()
        current = best_per_via.get(key)
        if current is None or confronto["totale"] < current["totale"]:
            best_per_via[key] = confronto
    comparisons = list(best_per_via.values())

    # Gli abbonamenti si applicano ai singoli biglietti: e' qui che la
    # spezzatura cambia davvero prezzo (es. Flex sul tratto in Alto Adige:
    # Bressanone-Bolzano 4,96 invece di 8,00).
    applica_abbonamenti(comparisons, passes, sconto_xgo)

    # Le offerte: quelle che battono il diretto. Se l'utente preferisce
    # arrivare prima, entra anche la spezzatura piu' veloce (arriva prima
    # anche se costa qualcosa in piu'): e' un piano, non solo un dato.
    spezzate: list[Offer] = []
    for confronto in comparisons:
        if not confronto["conveniente"]:
            if not (
                prefer_fast
                and confronto["delta_minuti"] <= -MIN_FASTER_MINUTES
                and confronto["delta_prezzo"] <= MAX_EXTRA_PRICE
            ):
                continue
        leg1 = confronto["leg1"]
        leg2 = confronto["leg2"]
        offer = confronto["offer"]
        spezzate.append(
            Offer(
                source="spezzatura",
                external_id=f"split:{leg1['id']}+{leg2['id']}",
                mode="train",
                operator="Trenitalia (2 biglietti)",
                depart_at=leg1["depart_at"],
                arrive_at=leg2["arrive_at"],
                price=confronto["totale"],
                pickup_name=confronto["tratte"][0]["da"],
                dropoff_name=confronto["tratte"][1]["a"],
                pickup_lat=offer.pickup_lat,
                pickup_lon=offer.pickup_lon,
                dropoff_lat=offer.dropoff_lat,
                dropoff_lon=offer.dropoff_lon,
                duration_min=confronto["durata_min"],
                deeplink="https://www.trenitalia.com/it.html",
                details={
                    "fonte": "Trenitalia",
                    "spezzatura": True,
                    "prezzo_diretto": confronto["diretto_prezzo"],
                    "risparmio": abs(confronto["delta_prezzo"]),
                    "delta_minuti": confronto["delta_minuti"],
                    "biglietti": 2,
                    "cambi": 0,
                    "treni": "2 biglietti regionali",
                    "tratte": confronto["tratte"],
                    "totale_effettivo": confronto.get("totale_effettivo"),
                    "diretto_effettivo": confronto.get("diretto_effettivo"),
                    "tappe": [
                        tappa(
                            confronto["tratte"][0]["da"],
                            confronto["tratte"][0]["a"],
                            leg1["depart_at"],
                            leg1["arrive_at"],
                            "Trenitalia",
                        ),
                        tappa(
                            confronto["tratte"][1]["da"],
                            confronto["tratte"][1]["a"],
                            leg2["depart_at"],
                            leg2["arrive_at"],
                            "Trenitalia",
                        ),
                    ],
                    "durata": f"{confronto['durata_min']} min",
                },
            )
        )

    # I confronti: prima quelli che fanno risparmiare, poi i piu' vicini.
    comparisons.sort(
        key=lambda c: (not c["conveniente"], abs(c["delta_prezzo"]), abs(c["delta_minuti"]))
    )
    return spezzate, [_comparison(c) for c in comparisons]


async def find_splits(
    offers: list[Offer], ctx: SearchContext, *, prefer_fast: bool = False
) -> list[Offer]:
    """Le spezzature che battono il diretto (o che arrivano prima, se preferito)."""
    spezzate, _ = await analyze_splits(offers, ctx, prefer_fast=prefer_fast)
    return spezzate


async def split_comparisons(offers: list[Offer], ctx: SearchContext) -> list[dict[str, Any]]:
    """Tutti i confronti fra spezzatura e diretto (anche se costano di piu')."""
    _, confronti = await analyze_splits(offers, ctx)
    return confronti
