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
import math
import re
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
from . import rail_km

log = logging.getLogger("carpool.split")

# Un segmento deve partire almeno dopo che il precedente e' arrivato.
# Venti minuti bastano per un cambio alla stessa stazione anche con due
# biglietti separati; venticinque tagliavano coincidenze vere di un minuto
# (Trento 12:30 -> 12:54) e facevano perdere un'ora di viaggio.
MIN_TRANSFER_MIN = 20
# Quanti segmenti di partenza si provano per ogni nodo: il piu' economico
# potrebbe non permettere il cambio, il secondo potrebbe costare poco di piu'.
MAX_FIRST_LEGS = 4
# Quanti punti di spezzatura si provano per viaggio. Non basta guardare le
# tappe commerciali: il punto giusto può essere una stazione che il treno
# attraversa senza fermarsi (Trento sul Brennero, per esempio), quindi si
# tiene un margine più largo.
MAX_SPLIT_POINTS = 8
# Con "preferisco arrivare prima", una spezzatura che arriva almeno
# MIN_FASTER_MINUTES prima entra nel confronto anche se costa di piu'.
MIN_FASTER_MINUTES = 30
# Quanto puo' costare in piu' una spezzatura piu' veloce per essere scelta.
MAX_EXTRA_PRICE = 8.0
# Quanti viaggi si provano a spezzare (i piu' economici diretti).
MAX_SPLIT_OFFERS = 2
# Quanto vale un'ora di viaggio in piu', in euro. Non e' denaro che l'utente
# paga: serve solo a non proporre un itinerario che impiega due ore in piu'
# per risparmiare un euro. Senza questo, il confronto fra spezzature sceglieva
# sempre il biglietto piu' economico in assoluto, anche se la corsa era
# assurda (es. un regionale con due attese per una tratta da due ore).
VALORE_ORA_EUR = 8.0
# Un singolo segmento che impiega piu' del piu' veloce per la stessa tratta
# oltre questo margine e' una corsa sbagliata (attese lunghe, coincidenze
# perse): si scarta prima ancora di confrontarne il prezzo.
MAX_EXTRA_SEGMENT_MIN = 60
# Quante soluzioni del secondo segmento si confrontano davvero. Sono gia'
# filtrate per tempo: qui si tengono le piu' promettenti, non tutte.
MAX_SECOND_LEGS = 4
# Una spezzatura che ci mette piu' di due ore in piu' del diretto resta nel
# confronto (l'utente vede quanto cambia) ma non diventa un piano: meglio
# pagare il diretto che passare la giornata in stazione.
MAX_EXTRA_TOTAL_MIN = 120

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

# I nodi in linea della rete regionale altoatesina/brenner: stazioni
# principali dove ha senso spezzare il biglietto. Servono perche' molti
# adapter descrivono solo le tratte commerciali del viaggio (es. cambio a
# Bolzano) e non tutte le fermate: senza questi nodi una spezzatura a
# Trento, che e' il confine del sistema provinciale, non verrebbe mai provata.
NODI_IN_LINEA = (
    "brennero", "fortezza", "bressanone", "brunico", "san candido",
    "merano", "silandro", "malles", "bolzano", "trento", "rovereto",
)


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
        treni = solution.get("trains") or []
        categorie: list[str] = []
        for treno in treni:
            if not isinstance(treno, dict):
                continue
            categoria = str(treno.get("trainCategory") or "").strip()
            if categoria and categoria not in categorie:
                categorie.append(categoria)
        # I cambi reali del segmento: ogni nodo in piu' del primo e' un
        # cambio di treno. Serve a non dichiarare "0 cambi" su un viaggio
        # che ne ha due.
        nodi = solution.get("nodes") or []
        cambi = max(0, len(nodi) - 1) if nodi else max(0, len(treni) - 1)
        found.append(
            {
                "price": round(price, 2),
                "depart_at": depart_at,
                "arrive_at": arrive_at,
                "durata_min": _duration(depart_at, arrive_at),
                "cambi": cambi,
                "treni": ", ".join(categorie) or "Trenitalia",
                "regionale": _e_regionale(categorie),
                "origin": solution.get("origin") or "",
                "destination": solution.get("destination") or "",
                "id": str(solution.get("id") or ""),
            }
        )
    return sorted(found, key=lambda s: s["price"])


def _e_regionale(categorie: list[str]) -> bool:
    """True se il segmento e' fatto solo di treni regionali.

    Serve per gli abbonamenti: il Flex altoatesino vale sui regionali, non
    sulle Frecce. Senza questo controllo il pass veniva applicato anche a
    un Frecciarossa, e il prezzo mostrato era piu' basso del vero.
    """
    if not categorie:
        return False
    testo = " ".join(categorie).lower()
    return not any(parola in testo for parola in passes_service.NON_REGIONALI)


def _is_endpoint(hub: str, ctx: SearchContext) -> bool:
    """True se l'hub coincide con la partenza o l'arrivo."""
    for place in (ctx.from_place or "", ctx.to_place or ""):
        place = place.strip().lower()
        if not place:
            continue
        if hub in place or place in hub:
            return True
    return False


def _chiave_punto(nome: str | None) -> str:
    """Chiave per capire se due nomi indicano la stessa stazione."""
    return rail_km.stazione(nome) or str(nome or "").strip().lower()


def _nome_stazione(nome: str) -> str:
    """Toglie la citta' fra parentesi: "Povo-Mesiano (Trento)" -> "Povo-Mesiano"."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", str(nome or "")).strip()


def _coordinate_stazione(nome: str | None) -> tuple[float, float] | None:
    """Le coordinate di una stazione, anche quando il nome è bilingue.

    Il grafo ferroviario conosce solo le linee dell'Alto Adige: Thiene,
    Vicenza o Verona non ci sono. Per capire se Trento è davvero sulla
    strada, però, bastano le coordinate della città.
    """
    from ..geo import CITIES
    from . import rail_km

    chiave = rail_km.stazione(nome)
    if chiave and chiave in CITIES:
        return CITIES[chiave]

    testo = _nome_stazione(nome).lower()
    if not testo:
        return None
    for città, coordinate in CITIES.items():
        if città == testo or città in testo or testo in città:
            return coordinate
    return None


def _posizione_sul_percorso(
    lat: float, lon: float, ctx: SearchContext, *, max_deviazione_km: float = 70.0
) -> float | None:
    """Dove cade un punto lungo la linea partenza→arrivo (0 = partenza, 1 = arrivo).

    Restituisce None se il punto è fuori dalla linea o se allunga troppo il
    viaggio. Serve a provare le stazioni giuste **in ordine di percorso**,
    anche quando l'adapter descrive solo le tappe commerciali.
    """
    from ..geo import haversine_km

    if None in (ctx.from_lat, ctx.from_lon, ctx.to_lat, ctx.to_lon):
        return None
    from_lat = float(ctx.from_lat)  # type: ignore[arg-type]
    from_lon = float(ctx.from_lon)  # type: ignore[arg-type]
    to_lat = float(ctx.to_lat)  # type: ignore[arg-type]
    to_lon = float(ctx.to_lon)  # type: ignore[arg-type]

    diretto = haversine_km(from_lat, from_lon, to_lat, to_lon)
    primo = haversine_km(from_lat, from_lon, lat, lon)
    secondo = haversine_km(lat, lon, to_lat, to_lon)
    if not diretto or primo is None or secondo is None:
        return None
    if primo + secondo - diretto > max_deviazione_km:
        return None

    # Proiezione in coordinate locali (km): dà l'ordine lungo la tratta.
    cos_lat = math.cos(math.radians(from_lat))
    x = (lon - from_lon) * 111.32 * cos_lat
    y = (lat - from_lat) * 111.32
    x2 = (to_lon - from_lon) * 111.32 * cos_lat
    y2 = (to_lat - from_lat) * 111.32
    lunghezza2 = x2 * x2 + y2 * y2
    if lunghezza2 <= 0:
        return None
    t = (x * x2 + y * y2) / lunghezza2
    # Un punto oltre la destinazione (o prima della partenza) non è un
    # cambio intermedio: allungherebbe il viaggio tornando indietro.
    if not (0.02 <= t <= 0.98):
        return None
    return t


def _split_points(offer: Offer, ctx: SearchContext) -> list[str]:
    """Le citta' dove provare a spezzare il biglietto, in ordine di promessa.

    Prima i **nodi ferroviari in linea** sul percorso (Bologna, Firenze,
    Verona...): e' li' che la spezzatura conviene, perche' offrono
    regionali economici che il treno diretto non usa. Poi i nodi della rete
    regionale attraversati (Fortezza, Bolzano, Trento...), infine le fermate
    reali dell'itinerario, che sono un buon piano B.

    I nodi regionali non si ricavano solo dal grafo ferroviario: Thiene non
    e' nel grafo dell'Alto Adige, ma Trento e' comunque sulla strada. Per
    questo si guarda anche la posizione geografica lungo la tratta. Senza
    questo controllo una spezzatura a Trento (spesso la piu' conveniente)
    non veniva mai provata.
    """
    points: list[str] = []
    visti: set[str] = set()

    def aggiungi(nome: str) -> None:
        nome = str(nome or "").strip()
        if not nome:
            return
        chiave = _chiave_punto(nome)
        if chiave in visti:
            return
        visti.add(chiave)
        points.append(nome)

    # 1. I grandi nodi italiani, nell'ordine di importanza: sono i piu'
    #    promettenti per una spezzatura. Restano davanti anche quando la
    #    geografia metterebbe altre stazioni prima.
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
                aggiungi(hub.title())

    # 2. Nodi in linea e fermate reali, ordinati lungo il percorso. Il
    #    grafo dice quali stazioni si attraversano quando conosce la linea;
    #    la geografia copre i casi in cui la partenza non e' nel grafo
    #    (Thiene, per esempio).
    in_linea: list[tuple[float, str]] = []

    if offer.mode == "train":
        partenza = rail_km.stazione(offer.pickup_name)
        arrivo = rail_km.stazione(offer.dropoff_name)
        if partenza and arrivo:
            cammino, _ = rail_km.percorso(partenza, arrivo)
            for nodo in cammino:
                if nodo in NODI_IN_LINEA:
                    nome = nodo.replace("_", " ").title()
                    coordinate = _coordinate_stazione(nome)
                    t = (
                        _posizione_sul_percorso(coordinate[0], coordinate[1], ctx)
                        if coordinate
                        else None
                    )
                    in_linea.append((t if t is not None else 0.5, nome))

        # Anche quando il grafo non conosce partenza o arrivo, i nodi
        # regionali sulla linea vanno provati. Trento e Bolzano sono i
        # casi che contano di piu' sulla Brennero.
        for nodo in NODI_IN_LINEA:
            if _is_endpoint(nodo, ctx):
                continue
            coordinate = _coordinate_stazione(nodo)
            if not coordinate:
                continue
            t = _posizione_sul_percorso(coordinate[0], coordinate[1], ctx)
            if t is not None:
                in_linea.append((t, nodo.replace("_", " ").title()))

    # Le fermate reali del treno restano un piano B: si aggiungono anche
    # quando la geografia non le riconosce, perche' l'adapter le ha viste.
    tappe = offer.details.get("tappe") or []
    for step in tappe:
        if not isinstance(step, dict):
            continue
        for key in ("da", "a"):
            nome = str(step.get(key) or "").strip()
            if not nome:
                continue
            coordinate = _coordinate_stazione(nome)
            t = (
                _posizione_sul_percorso(coordinate[0], coordinate[1], ctx)
                if coordinate
                else None
            )
            in_linea.append((t if t is not None else 0.5, nome))

    # Deduplica e ordina: un nodo che compare sia nel grafo sia nelle tappe
    # si prova una volta sola, nel punto giusto del viaggio.
    visti_in_linea: set[str] = set()
    for _, nome in sorted(in_linea, key=lambda item: (item[0], item[1])):
        chiave = _chiave_punto(nome)
        if chiave in visti_in_linea:
            continue
        visti_in_linea.add(chiave)
        aggiungi(nome)

    # Escludi partenza e arrivo.
    start = _chiave_punto(offer.pickup_name)
    end = _chiave_punto(offer.dropoff_name)
    return [p for p in points if _chiave_punto(p) not in (start, end)]


def _duration(depart: datetime | None, arrive: datetime | None) -> int:
    if not depart or not arrive:
        return 0
    return max(0, int((arrive - depart).total_seconds() // 60))


def _durata_soluzione(soluzione: dict[str, Any]) -> int:
    """La durata del segmento, anche quando la fonte non la dichiara."""
    dichiarata = int(soluzione.get("durata_min") or 0)
    if dichiarata:
        return dichiarata
    return _duration(soluzione.get("depart_at"), soluzione.get("arrive_at"))


def _plausibili_per_tempo(soluzioni: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scarta le corse troppo piu' lente del meglio disponibile sulla tratta.

    Un regionale che impiega quattro ore per una tratta da due e' una corsa
    con attese lunghe: comprarlo a pezzi non e' "risparmiare", e' arrivare
    distrutti. Il confronto deve restare su corse sensate.
    """
    if not soluzioni:
        return []
    veloce = min(_durata_soluzione(s) for s in soluzioni)
    if not veloce:
        return list(soluzioni)
    return [
        s
        for s in soluzioni
        if _durata_soluzione(s) <= veloce + MAX_EXTRA_SEGMENT_MIN
    ]


def _prezzo_netto_tratta(
    passes: list[dict[str, Any]] | None, tratta: dict[str, Any]
) -> float:
    """Il prezzo del biglietto singolo con gli abbonamenti validi su quella corsa.

    La categoria del treno conta: il Flex altoatesino non vale sulle Frecce.
    Per questo si passa ``treni`` e non si da' per scontato che ogni corsa
    sia un regionale (era il motivo per cui un Frecciarossa sembrava
    costare quanto un regionale).
    """
    listino = float(tratta.get("price") or 0)
    da = tratta.get("origin") or tratta.get("da") or ""
    a = tratta.get("destination") or tratta.get("a") or ""
    when = tratta.get("depart_at")
    costo, _ = passes_service.netto_tratta(
        passes or [],
        da,
        a,
        listino,
        when.date() if isinstance(when, datetime) else None,
        treni=str(tratta.get("treni") or "Regionale"),
    )
    return costo


def _punteggio_coppia(
    prima: dict[str, Any],
    seconda: dict[str, Any],
    passes: list[dict[str, Any]] | None,
) -> tuple[float, float, int]:
    """Quanto "costa" davvero la coppia di biglietti: prezzo + tempo.

    Ritorna (punteggio, costo effettivo, durata). Il punteggio non e' un
    prezzo da mostrare: serve solo a scegliere fra due itinerari reali.
    """
    costo = _prezzo_netto_tratta(passes, prima) + _prezzo_netto_tratta(passes, seconda)
    durata = _duration(prima.get("depart_at"), seconda.get("arrive_at"))
    punteggio = costo + VALORE_ORA_EUR * (durata / 60.0)
    return round(punteggio, 4), round(costo, 2), durata


async def same_trains_comparison(
    offer: Offer,
    passes: list[dict[str, Any]] | None = None,
    sconto_xgo: float = 0.0,
) -> dict[str, Any] | None:
    """Il confronto "tratte singole" per l'offerta scelta come piano.

    Si calcola dopo la scelta del piano: il confronto deve parlare del treno
    che l'utente vede nella card, non di un'altra offerta piu' economica ma
    scartata (troppo lenta, fuori orario, ...).
    """
    async with httpx.AsyncClient(timeout=25, headers=HEADERS) as client:
        confronto = await _same_trains_comparison(client, offer)
    if not confronto:
        return None
    applica_abbonamenti([confronto], passes, sconto_xgo)
    return _comparison(confronto)


async def _corsa_alle(
    client: httpx.AsyncClient,
    da: str,
    a: str,
    partenza: datetime,
    arrivo: datetime,
) -> dict[str, Any] | None:
    """La soluzione che parte e arriva agli orari indicati (stessa corsa)."""
    origine = await _lookup_station(client, _nome_stazione(da))
    destinazione = await _lookup_station(client, _nome_stazione(a))
    if not origine or not destinazione:
        log.debug("stesse corse: stazione non trovata (%s -> %s)", da, a)
        return None
    soluzioni = await _segment_price(
        client, origine["id"], destinazione["id"], partenza
    )
    return next(
        (
            s
            for s in soluzioni
            if abs((s["depart_at"] - partenza).total_seconds()) <= 90
            and abs((s["arrive_at"] - arrivo).total_seconds()) <= 90
        ),
        None,
    )


async def _same_trains_comparison(
    client: httpx.AsyncClient,
    offer: Offer,
) -> dict[str, Any] | None:
    """Lo stesso itinerario comprato a pezzi: stesse corse, due biglietti.

    Serve a rispondere a "conviene comprare tratte singole?": si prova a
    tagliare il viaggio in ogni stazione intermedia, comprando **due**
    biglietti per le stesse corse (stessi orari, nessuna attesa in piu').
    Si tiene il taglio piu' economico. Se un taglio non ha prezzi certi non
    si inventa nulla e si passa al successivo.
    """
    tappe = [t for t in (offer.details.get("tappe") or []) if isinstance(t, dict)]
    if len(tappe) < 2:
        return None

    stazioni: list[str] = []
    for step in tappe:
        da = str(step.get("da") or "").strip()
        a = str(step.get("a") or "").strip()
        if not da or not a:
            return None
        if not stazioni:
            stazioni.append(da)
        stazioni.append(a)

    partenza = parse_dt(tappe[0].get("partenza"))
    arrivo = parse_dt(tappe[-1].get("arrivo"))
    if not partenza or not arrivo:
        return None

    migliore: tuple[int, dict[str, Any], dict[str, Any], float] | None = None
    for i in range(1, len(stazioni) - 1):
        cambio_arrivo = parse_dt(tappe[i - 1].get("arrivo"))
        cambio_partenza = parse_dt(tappe[i].get("partenza"))
        if not cambio_arrivo or not cambio_partenza:
            continue
        prima = await _corsa_alle(
            client, stazioni[0], stazioni[i], partenza, cambio_arrivo
        )
        dopo = await _corsa_alle(
            client, stazioni[i], stazioni[-1], cambio_partenza, arrivo
        )
        if not prima or not dopo:
            continue
        totale = round(prima["price"] + dopo["price"], 2)
        if migliore is None or totale < migliore[3]:
            migliore = (i, prima, dopo, totale)

    if migliore is None:
        return None
    i, prima, dopo, totale = migliore
    # Le due corse sono quelle del diretto: la categoria e' la sua. Serve
    # per non applicare un abbonamento regionale a un Frecciarossa.
    categoria = str((offer.details or {}).get("treni") or "Regionale")
    tratte = [
        {
            "da": stazioni[0],
            "a": stazioni[i],
            "prezzo": prima["price"],
            "partenza": iso(prima["depart_at"]),
            "arrivo": iso(prima["arrive_at"]),
            "durata_min": _duration(prima["depart_at"], prima["arrive_at"]),
            "treni": categoria,
            "cambi": int(prima.get("cambi") or 0),
        },
        {
            "da": stazioni[i],
            "a": stazioni[-1],
            "prezzo": dopo["price"],
            "partenza": iso(dopo["depart_at"]),
            "arrivo": iso(dopo["arrive_at"]),
            "durata_min": _duration(dopo["depart_at"], dopo["arrive_at"]),
            "treni": categoria,
            "cambi": int(dopo.get("cambi") or 0),
        },
    ]

    # Se l'offerta ha gia' ricevuto lo sconto X-GO, il confronto parte dal
    # listino: cosi' lo sconto non viene applicato due volte al diretto.
    diretto = float((offer.details or {}).get("prezzo_listino") or offer.price or 0)
    durata = offer.duration_min or _duration(offer.depart_at, offer.arrive_at)
    return {
        "via": "tratte singole (stesse corse)",
        "tratte": tratte,
        "totale": totale,
        "diretto_prezzo": diretto,
        "delta_prezzo": round(totale - diretto, 2),
        "durata_min": durata,
        "diretto_durata_min": durata,
        "delta_minuti": 0,
        "partenza": tratte[0]["partenza"],
        "arrivo": tratte[-1]["arrivo"],
        "diretto_partenza": iso(offer.depart_at),
        "diretto_arrivo": iso(offer.arrive_at),
        "cambi": max(0, len(tratte) - 1),
        "conveniente": totale < diretto,
        "offer": offer,
    }


async def _split_via(
    client: httpx.AsyncClient,
    offer: Offer,
    ctx: SearchContext,
    origin: dict,
    destination: dict,
    point: str,
    passes: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Calcola lo spezzamento attraverso un nodo e il confronto col diretto.

    Fra le coppie di corse reali non si prende la piu' economica in assoluto:
    si sceglie quella col miglior rapporto prezzo/tempo (``VALORE_ORA_EUR``),
    usando il costo effettivo con gli abbonamenti. Le corse troppo piu'
    lente del meglio disponibile sulla tratta vengono scartate: senza questo
    filtro la spezzatura "risparmiava" comprando un regionale con due ore di
    attese, e il piano sembrava assurdo.
    """
    # Il nodo puo' essere una citta' ("Verona") o una stazione vera
    # ("Verona Porta Nuova"): per le citta' si passano le coordinate del
    # centro, cosi' l'API non viene scambiata per l'aeroporto o la stazione
    # dei bus.
    from ..geo import CITIES

    coords = CITIES.get(point.strip().lower())
    middle = await _lookup_station(
        client,
        _nome_stazione(point),
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
    best: tuple[float, dict[str, Any], dict[str, Any], float, float, int] | None = None
    # (punteggio, leg1, leg2, totale listino, costo effettivo, durata)
    for start in query_starts(ctx):
        first = await _segment_price(client, origin["id"], middle["id"], start)
        if not first:
            continue
        # Candidati: la prima tratta deve arrivare nel giorno richiesto,
        # altrimenti si aspetta una notte in stazione (che non e' un
        # viaggio). Un treno notturno che parte la sera prima e arriva
        # dopo mezzanotte resta valido.
        # Il filtro sul tempo si applica a tutte le soluzioni restituite
        # (non solo alle prime, ordinate per prezzo): altrimenti otto corse
        # economiche ma lentissime potrebbero nascondere quella veloce.
        candidati = [
            leg1
            for leg1 in _plausibili_per_tempo(first)
            if leg1["arrive_at"] >= ctx.arrival_day_start
            and leg1["depart_at"] >= ctx.window_start - timedelta(minutes=15)
        ]
        candidati.sort(
            key=lambda s: _prezzo_netto_tratta(passes, s)
            + VALORE_ORA_EUR * (_durata_soluzione(s) / 60.0)
        )
        candidati = candidati[:MAX_FIRST_LEGS]
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
            # tempo. Fra quelli validi non si prende il primo della lista
            # (ordinata per prezzo): si confrontano prezzo effettivo e
            # durata, perche' due corse possono costare uguale con il pass
            # ma impiegare un'ora di differenza.
            utili = [
                s
                for s in second
                if ctx.arrival_day_start
                <= s["arrive_at"]
                <= getattr(ctx, "effective_arrive_by", ctx.arrive_by) + timedelta(minutes=10)
            ]
            utili = _plausibili_per_tempo(utili)
            if not utili:
                continue
            utili.sort(
                key=lambda s: _prezzo_netto_tratta(passes, s)
                + VALORE_ORA_EUR * (_durata_soluzione(s) / 60.0)
            )
            for leg2 in utili[:MAX_SECOND_LEGS]:
                total = round(leg1["price"] + leg2["price"], 2)
                punteggio, effettivo, durata = _punteggio_coppia(leg1, leg2, passes)
                if best is None or punteggio < best[0]:
                    best = (punteggio, leg1, leg2, total, effettivo, durata)

    if best is None:
        return None
    _, leg1, leg2, total, effettivo, durata = best
    log.debug(
        "spezzatura via %s: %s EUR effettivi, %s min, %s + %s cambi",
        middle["name"],
        effettivo,
        durata,
        leg1.get("cambi"),
        leg2.get("cambi"),
    )

    if leg2["arrive_at"] > getattr(ctx, "effective_arrive_by", ctx.arrive_by) + timedelta(minutes=10):
        return None  # arriva oltre il limite
    if leg2["arrive_at"] < ctx.arrival_day_start:
        return None  # arriva il giorno prima
    if leg1["depart_at"] < ctx.window_start - timedelta(minutes=15):
        return None  # parte prima della finestra

    diretto = offer.price or 0.0
    durata = _duration(leg1["depart_at"], leg2["arrive_at"])
    durata_diretto = offer.duration_min or _duration(offer.depart_at, offer.arrive_at)
    # I cambi veri: quelli dentro i due segmenti piu' il cambio di biglietto
    # al nodo. Prima si dichiarava "0 cambi" anche su un viaggio con due
    # coincidenze, e la card del piano sembrava quella di un diretto.
    cambi = int(leg1.get("cambi") or 0) + int(leg2.get("cambi") or 0) + 1
    treni = ", ".join(
        dict.fromkeys(
            t for t in (str(leg1.get("treni") or ""), str(leg2.get("treni") or "")) if t
        )
    )
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
                "cambi": int(leg1.get("cambi") or 0),
                "treni": leg1.get("treni") or "Trenitalia",
                "regionale": bool(leg1.get("regionale")),
            },
            {
                "da": leg2["origin"] or middle["name"],
                "a": leg2["destination"] or destination["name"],
                "prezzo": leg2["price"],
                "partenza": iso(leg2["depart_at"]),
                "arrivo": iso(leg2["arrive_at"]),
                "durata_min": _duration(leg2["depart_at"], leg2["arrive_at"]),
                "cambi": int(leg2.get("cambi") or 0),
                "treni": leg2.get("treni") or "Trenitalia",
                "regionale": bool(leg2.get("regionale")),
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
        "cambi": cambi,
        "treni": treni or "Trenitalia",
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

    from . import discounts as discounts_service

    for confronto in comparisons:
        # X-GO non vale sui viaggi che toccano Trentino/Alto Adige (e le
        # altre regioni escluse): in quel caso non si sconta nemmeno la
        # spezzatura, altrimenti il confronto sembrerebbe piu' conveniente
        # del vero. Meglio stimare per difetto che per eccesso.
        sconto = sconto_xgo
        diretta = confronto.get("offer")
        if sconto and diretta is not None:
            quando = getattr(diretta, "depart_at", None)
            ammesso, _motivo = discounts_service._xgo_ammissibile(
                diretta, quando.date() if isinstance(quando, datetime) else None
            )
            if not ammesso:
                sconto = 0.0

        totale_effettivo = 0.0
        for tratta in confronto.get("tratte") or []:
            listino = float(tratta.get("prezzo") or 0)
            scontato = _scontato(listino, sconto)
            if sconto and scontato < listino:
                tratta["sconto_xgo"] = round(listino - scontato, 2)
            partenza_tratta = parse_dt(tratta.get("partenza"))
            costo, nome = passes_service.netto_tratta(
                passes or [],
                tratta.get("da") or "",
                tratta.get("a") or "",
                scontato,
                partenza_tratta.date() if partenza_tratta else None,
                treni=str(tratta.get("treni") or "Regionale"),
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

        listino_diretto = float(getattr(diretta, "price", 0) or 0)
        scontato_diretto = _scontato(listino_diretto, sconto)
        dettagli_diretta = getattr(diretta, "details", None) or {}
        quando_diretta = getattr(diretta, "depart_at", None)
        diretto_eff, nome_diretto = passes_service.netto_tratta(
            passes or [],
            getattr(diretta, "pickup_name", "") or "",
            getattr(diretta, "dropoff_name", "") or "",
            scontato_diretto,
            quando_diretta.date() if isinstance(quando_diretta, datetime) else None,
            treni=str(dettagli_diretta.get("treni") or "Regionale"),
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
    log.debug(
        "spezzatura: %d treni con tappe su %d offerte",
        len(candidates),
        len(offers),
    )
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
                        _split_via(
                            client,
                            offer,
                            ctx,
                            origin,
                            destination,
                            point,
                            passes=passes,
                        )
                        for point in points
                    ),
                    return_exceptions=True,
                )
                for result in results:
                    if isinstance(result, dict):
                        comparisons.append(result)
            log.debug("spezzatura: %d confronti", len(comparisons))
    except Exception as exc:  # noqa: BLE001
        log.debug("spezzatura non disponibile: %s", exc)
        return [], []

    # Solo una spezzatura per nodo: quella col costo migliore. A pari
    # costo vince quella che arriva prima: due nodi con lo stesso prezzo non
    # sono equivalenti se uno fa aspettare due ore.
    best_per_via: dict[str, dict[str, Any]] = {}
    for confronto in comparisons:
        key = str(confronto["via"]).lower()
        current = best_per_via.get(key)
        if current is None or (
            confronto["totale"],
            confronto["durata_min"],
        ) < (
            current["totale"],
            current["durata_min"],
        ):
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
        if "leg1" not in confronto or "leg2" not in confronto:
            # Confronto informativo (stesse corse): non e' un piano diverso.
            continue
        if confronto["delta_minuti"] > MAX_EXTRA_TOTAL_MIN:
            # Troppo lenta per essere un piano: resta solo nel confronto.
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
                    "cambi": int(confronto.get("cambi") or 1),
                    "treni": confronto.get("treni") or "2 biglietti Trenitalia",
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

    # I confronti: prima quelli che fanno risparmiare e costano meno, poi
    # gli altri. A pari costo vince chi arriva prima.
    comparisons.sort(
        key=lambda c: (
            not c["conveniente"],
            c.get("totale_effettivo") or c["totale"],
            c["durata_min"],
        )
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
