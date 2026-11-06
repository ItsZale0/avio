"""Partenze alternative: il biglietto piu' economico puo' partire da una citta' vicina.

C'e' un caso che la spezzatura da sola non vede: il viaggio conviene
comprarlo *iniziando* da una citta' vicina, raggiunta con un altro treno.
Thiene→Vicenza + Vicenza→Bressanone, per esempio, e' due biglietti reali
il cui totale puo' essere piu' basso del biglietto unico da Thiene.

Regole:
- solo corse reali dell'API Trenitalia, con il loro prezzo: niente stime;
- l'accesso alla citta' alternativa e' un biglietto regionale vero, con
  almeno ``MIN_TRANSFER_MIN`` minuti per il cambio;
- una partenza alternativa diventa un piano solo se costa *meno* di quello
  che l'app ha gia' trovato, e non allunga il viaggio oltre il limite della
  spezzatura.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx

from ..adapters.base import Offer, SearchContext, tappa
from ..adapters.trenitalia import query_starts
from ..timeutil import ROME, iso, parse_dt
from . import split_tickets

log = logging.getLogger("carpool.nearby")


MAX_ORIGIN_KM = 60.0
MIN_ORIGIN_KM = 3.0
MAX_ORIGIN_CITIES = 4
MAX_MAIN_LEGS = 3
MAX_ACCESS_LEGS = 3
ACCESS_WINDOW_MIN = 240

VALORE_ORA_EUR = split_tickets.VALORE_ORA_EUR
MIN_TRANSFER_MIN = split_tickets.MIN_TRANSFER_MIN
MAX_EXTRA_TOTAL_MIN = split_tickets.MAX_EXTRA_TOTAL_MIN


@dataclass
class NearbyResult:
    offers: list[Offer] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    confronti: list[dict[str, Any]] = field(default_factory=list)


def _costo_utente(offer: Offer) -> float:
    if offer.net_cost is not None:
        return float(offer.net_cost)
    return float(offer.price or 0)


def _riferimento(offers: list[Offer]) -> tuple[float | None, int | None]:
    """Il costo e la durata del piano migliore gia' trovato (o None)."""
    validi = [o for o in offers if (o.net_cost if o.net_cost is not None else o.price)]
    if not validi:
        return None, None
    best = min(validi, key=_costo_utente)
    durata = best.duration_min
    if durata is None:
        durata = split_tickets._duration(best.depart_at, best.arrive_at)
    return _costo_utente(best), durata


def _citta_vicine(ctx: SearchContext, escludi: set[str]) -> list[tuple[str, float]]:
    if ctx.from_lat is None or ctx.from_lon is None:
        return []
    from ..geo import CITIES, haversine_km, normalize

    destinazione = normalize(ctx.to_place)
    out: list[tuple[str, float]] = []
    for nome, (lat, lon) in CITIES.items():
        if normalize(nome) in escludi or normalize(nome) == destinazione:
            continue
        km = haversine_km(ctx.from_lat, ctx.from_lon, lat, lon)
        if km < MIN_ORIGIN_KM or km > MAX_ORIGIN_KM:
            continue
        out.append((nome, km))
    out.sort(key=lambda item: item[1])
    return out[:MAX_ORIGIN_CITIES]


def _punteggio(
    accesso: dict[str, Any], principale: dict[str, Any], passes
) -> tuple[float, float, int]:
    costo = split_tickets._prezzo_netto_tratta(passes, accesso) + split_tickets._prezzo_netto_tratta(
        passes, principale
    )
    durata = split_tickets._duration(accesso.get("depart_at"), principale.get("arrive_at"))
    return round(costo + VALORE_ORA_EUR * (durata / 60.0), 4), round(costo, 2), durata


def _prezza_tratta(tratta: dict[str, Any], passes) -> float:
    """Prezzo effettivo del biglietto singolo, con il pass giusto."""
    from . import passes as passes_service

    partenza = parse_dt(tratta.get("partenza"))
    costo, nome = passes_service.netto_tratta(
        passes or [],
        tratta.get("da") or "",
        tratta.get("a") or "",
        float(tratta.get("prezzo") or 0),
        partenza.date() if partenza else None,
        treni=str(tratta.get("treni") or "Regionale"),
    )
    tratta["prezzo_effettivo"] = costo
    if nome:
        tratta["pass"] = nome
    return costo


async def _candidata(
    client: httpx.AsyncClient,
    ctx: SearchContext,
    origine: dict[str, Any],
    destinazione: dict[str, Any],
    citta: str,
    km: float,
    passes,
) -> dict[str, Any] | None:
    from ..geo import haversine_km

    coords = _coordinate_citta(citta)
    lat = coords[0] if coords else None
    lon = coords[1] if coords else None
    stazione = await split_tickets._lookup_station(client, _nome_citta(citta), lat, lon)
    if not stazione or stazione["id"] in (origine["id"], destinazione["id"]):
        return None

    # Tratte principali dalla citta' alternativa a destinazione.
    principali: list[dict[str, Any]] = []
    for start in query_starts(ctx):
        soluzioni = await split_tickets._segment_price(
            client, stazione["id"], destinazione["id"], start
        )
        principali.extend(soluzioni)
    principali = [
        s
        for s in principali
        if s["arrive_at"] >= ctx.arrival_day_start
        and s["arrive_at"] <= getattr(ctx, "effective_arrive_by", ctx.arrive_by) + timedelta(minutes=10)
        and s["depart_at"] >= ctx.window_start - timedelta(minutes=15)
    ]
    principali = split_tickets._plausibili_per_tempo(principali)
    if not principali:
        return None
    principali.sort(
        key=lambda s: split_tickets._prezzo_netto_tratta(passes, s)
        + VALORE_ORA_EUR * (split_tickets._durata_soluzione(s) / 60.0)
    )
    principali = principali[:MAX_MAIN_LEGS]

    # Per ogni tratta principale si cerca il treno di accesso, che deve
    # arrivare almeno MIN_TRANSFER_MIN prima della partenza.
    migliori: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for principale in principali:
        da = max(
            ctx.window_start,
            principale["depart_at"] - timedelta(minutes=ACCESS_WINDOW_MIN),
        )
        accessi = await split_tickets._segment_price(
            client, origine["id"], stazione["id"], da
        )
        accessi = [
            a
            for a in accessi
            if a["depart_at"] >= ctx.window_start - timedelta(minutes=1)
            and a["arrive_at"] + timedelta(minutes=MIN_TRANSFER_MIN)
            <= principale["depart_at"]
        ]
        accessi = split_tickets._plausibili_per_tempo(accessi)
        if not accessi:
            continue
        accessi.sort(
            key=lambda s: split_tickets._prezzo_netto_tratta(passes, s)
            + VALORE_ORA_EUR * (split_tickets._durata_soluzione(s) / 60.0)
        )
        accessi = accessi[:MAX_ACCESS_LEGS]
        for accesso in accessi:
            punteggio, costo, durata = _punteggio(accesso, principale, passes)
            migliori.append((punteggio, accesso, principale))

    if not migliori:
        return None
    migliori.sort(key=lambda item: item[0])
    _, accesso, principale = migliori[0]

    # Tratte normalizzate come la spezzatura (stesso formato della UI).
    tratta1 = {
        "da": accesso.get("origin") or origine["name"],
        "a": accesso.get("destination") or stazione["name"],
        "prezzo": accesso["price"],
        "partenza": iso(accesso["depart_at"]),
        "arrivo": iso(accesso["arrive_at"]),
        "durata_min": split_tickets._duration(accesso["depart_at"], accesso["arrive_at"]),
        "cambi": int(accesso.get("cambi") or 0),
        "treni": accesso.get("treni") or "Trenitalia",
        "regionale": bool(accesso.get("regionale")),
    }
    tratta2 = {
        "da": principale.get("origin") or stazione["name"],
        "a": principale.get("destination") or destinazione["name"],
        "prezzo": principale["price"],
        "partenza": iso(principale["depart_at"]),
        "arrivo": iso(principale["arrive_at"]),
        "durata_min": split_tickets._duration(principale["depart_at"], principale["arrive_at"]),
        "cambi": int(principale.get("cambi") or 0),
        "treni": principale.get("treni") or "Trenitalia",
        "regionale": bool(principale.get("regionale")),
    }
    _prezza_tratta(tratta1, passes)
    _prezza_tratta(tratta2, passes)
    totale = round(accesso["price"] + principale["price"], 2)
    effettivo = round(tratta1["prezzo_effettivo"] + tratta2["prezzo_effettivo"], 2)
    durata = split_tickets._duration(accesso["depart_at"], principale["arrive_at"])

    return {
        "via": stazione["name"],
        "km": round(km, 1),
        "tratte": [tratta1, tratta2],
        "totale": totale,
        "totale_effettivo": effettivo,
        "durata_min": durata,
        "cambi": int(accesso.get("cambi") or 0) + int(principale.get("cambi") or 0) + 1,
        "treni": ", ".join(
            dict.fromkeys(t for t in (tratta1["treni"], tratta2["treni"]) if t)
        ),
        "partenza": iso(accesso["depart_at"]),
        "arrivo": iso(principale["arrive_at"]),
        "leg1": accesso,
        "leg2": principale,
        "offer": None,
    }


def _coordinate_citta(nome: str) -> tuple[float, float] | None:
    from ..geo import CITIES

    return CITIES.get(str(nome or "").strip().lower())


def _nome_citta(chiave: str) -> str:
    # Prima lettera maiuscola, con le preposizioni italiane minuscole.
    minuscole = {"di", "del", "dello", "della", "dei", "degli", "delle", "da", "su", "in", "e"}
    parole = str(chiave or "").split()
    out: list[str] = []
    for i, parola in enumerate(parole):
        if i and parola in minuscole:
            out.append(parola)
        else:
            out.append(parola[:1].upper() + parola[1:])
    # "l'aquila" -> "L'Aquila"
    return " ".join(out).replace("L'aquila", "L'Aquila")


async def analyze_nearby_origins(
    offers: list[Offer],
    ctx: SearchContext,
    *,
    passes: list[dict[str, Any]] | None = None,
    sconto_xgo: float = 0.0,
    escludi: set[str] | None = None,
) -> NearbyResult:
    """Cerca la partenza alternativa piu' economica (se esiste).

    Va bene solo se costa *meno* di quello gia' trovato: non aggiunge piani
    piu' cari, ma registra i confronti per mostrarli all'utente.
    """
    from ..geo import normalize

    result = NearbyResult()
    if ctx.from_lat is None or ctx.from_lon is None:
        return result

    escludi_norm = {normalize(n) for n in (escludi or set())}
    # Le citta' che sono gia' punti di spezzatura valutati si saltano:
    # partire da li' sarebbe lo stesso viaggio.
    escludi_norm.add(normalize(ctx.from_place))

    vicine = _citta_vicine(ctx, escludi_norm)
    if not vicine:
        return result

    ref_costo, ref_durata = _riferimento(offers)

    async with httpx.AsyncClient(timeout=25, headers=split_tickets.HEADERS) as client:
        origine = await split_tickets._lookup_station(
            client, ctx.from_place, ctx.from_lat, ctx.from_lon
        )
        destinazione = await split_tickets._lookup_station(
            client, ctx.to_place, ctx.to_lat, ctx.to_lon
        )
        if not origine or not destinazione:
            return result

        confronti: list[dict[str, Any]] = []
        for citta, km in vicine:
            try:
                c = await _candidata(
                    client, ctx, origine, destinazione, citta, km, passes
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("partenza alternativa '%s' non disponibile: %s", citta, exc)
                c = None
            if c is None:
                continue
            confronti.append(c)
            log.debug(
                "partenza alternativa via %s: %s EUR effettivi, %s min",
                c["via"],
                c["totale_effettivo"],
                c["durata_min"],
            )

    if not confronti:
        return result

    # Una sola proposta per citta'. Si tengono le piu' economiche.
    best_per_via: dict[str, dict[str, Any]] = {}
    for confronto in confronti:
        key = str(confronto["via"]).lower()
        current = best_per_via.get(key)
        if current is None or (
            confronto["totale_effettivo"],
            confronto["durata_min"],
        ) < (
            current["totale_effettivo"],
            current["durata_min"],
        ):
            best_per_via[key] = confronto
    confronti = list(best_per_via.values())

    # Solo quelle che battono il riferimento (costo) e non allungano troppo.
    for confronto in confronti:
        confronto["conveniente"] = (
            ref_costo is None or confronto["totale_effettivo"] < ref_costo - 0.01
        )
        if (
            ref_durata is not None
            and confronto["durata_min"] > ref_durata + MAX_EXTRA_TOTAL_MIN
        ):
            confronto["conveniente"] = False

    # Confronti per la UI (stesso formato della spezzatura).
    result.confronti = [split_tickets._comparison(c) for c in confronti]

    # Offerte reali solo per quelle che convengono.
    for confronto in confronti:
        if not confronto.get("conveniente"):
            continue
        accesso = confronto["leg1"]
        principale = confronto["leg2"]
        durata = confronto["durata_min"]
        effettivo = confronto["totale_effettivo"]
        offer = Offer(
            source="partenza_alternativa",
            external_id=f"near:{accesso['id']}+{principale['id']}",
            mode="train",
            operator=f"Trenitalia (2 biglietti da {confronto['via']})",
            depart_at=accesso["depart_at"],
            arrive_at=principale["arrive_at"],
            price=confronto["totale"],
            net_cost=effettivo,
            pickup_name=ctx.from_place,
            pickup_lat=ctx.from_lat,
            pickup_lon=ctx.from_lon,
            dropoff_name=principale.get("destination") or ctx.to_place,
            dropoff_lat=ctx.to_lat,
            dropoff_lon=ctx.to_lon,
            duration_min=durata,
            deeplink="https://www.trenitalia.com/it.html",
            details={
                "fonte": "Trenitalia",
                "origine_alternativa": confronto["via"],
                "biglietti": 2,
                "cambi": confronto["cambi"],
                "treni": confronto["treni"],
                "tratte": confronto["tratte"],
                "totale_effettivo": effettivo,
                "tappe": [
                    tappa(
                        confronto["tratte"][0]["da"],
                        confronto["tratte"][0]["a"],
                        accesso["depart_at"],
                        accesso["arrive_at"],
                        "Trenitalia",
                    ),
                    tappa(
                        confronto["tratte"][1]["da"],
                        confronto["tratte"][1]["a"],
                        principale["depart_at"],
                        principale["arrive_at"],
                        "Trenitalia",
                    ),
                ],
                "durata": f"{durata} min",
                "risparmio": round(ref_costo - effettivo, 2) if ref_costo is not None else None,
            },
        )
        result.offers.append(offer)

    # Hint di confronto: sempre trasparente su cosa si e' provato.
    confronti_ordinati = sorted(
        confronti, key=lambda c: c["totale_effettivo"]
    )
    parti: list[str] = []
    for c in confronti_ordinati[:3]:
        diff = c["totale_effettivo"] - ref_costo if ref_costo is not None else 0
        if c.get("conveniente"):
            parti.append(
                f"da {c['via']} {c['totale_effettivo']:.2f} EUR (risparmi {ref_costo - c['totale_effettivo']:.2f} EUR)"
            )
        else:
            parti.append(
                f"da {c['via']} {c['totale_effettivo']:.2f} EUR "
                + (f"(+{diff:.2f} EUR)" if ref_costo is not None else "")
            )
    if parti:
        result.hints.append("Partenze alternative: " + " | ".join(parti) + ".")

    # Hint per ciascuna offerta che entra nel pool.
    for offer in result.offers:
        dettagli = offer.details or {}
        tratto = " + ".join(
            f"{t.get('da')}→{t.get('a')}" for t in (dettagli.get("tratte") or []) if isinstance(t, dict)
        )
        pezzi = " + ".join(
            f"{t.get('da')}→{t.get('a')} "
            f"{float(t.get('prezzo_effettivo', t.get('prezzo')) or 0):.2f} EUR"
            + (f" ({t.get('pass')})" if t.get("pass") else "")
            for t in (dettagli.get("tratte") or [])
            if isinstance(t, dict)
        )
        result.hints.append(
            f"Partenza alternativa: 2 biglietti Trenitalia ({tratto}): "
            f"{pezzi} = {offer.net_cost:.2f} EUR "
            f"(partendo da {dettagli.get('origine_alternativa')})."
        )

    return result
