"""Prezzi dei voli da Google Voli, letti tramite SerpApi.

Google non pubblica un'API per Google Voli: SerpApi apre la pagina al posto
nostro e restituisce i risultati in JSON. I prezzi sono quelli mostrati da
Google Voli al momento della chiamata, non stime.

La chiave sta in ``SERPAPI_KEY`` (variabile d'ambiente, mai nel repository).
Il piano gratuito da' 250 ricerche al mese: qui il tetto predefinito e' 240,
cosi' non si arriva mai a zero, e ogni rotta viene riletta al massimo una
volta ogni 24 ore. Senza chiave, a tetto esaurito o con un errore la fonte
resta zitta: il link a Google Voli continua a funzionare.

Gli orari restituiti sono locali rispetto all'aeroporto. L'app li interpreta
come ora italiana: per le tratte nazionali e' corretto, per quelle estere
l'arrivo puo' risultare spostato di un'ora o due. Il dettaglio e' scritto
nei ``details`` dell'offerta.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from .. import db
from ..config import USER_AGENT
from ..services import airports
from ..timeutil import ROME, now_utc, parse_dt
from .base import Adapter, AdapterError, AdapterResult, Offer, SearchContext, tappa

log = logging.getLogger("carpool.voli")

API_URL = "https://serpapi.com/search"

# Sotto questa distanza l'aereo non ha senso: si evitano ricerche inutili.
DISTANZA_MINIMA_KM = 200

# Una stessa rotta non si richiede piu' di una volta al giorno.
CACHE_TTL = timedelta(hours=24)

# Il piano gratuito e' 250: ci si ferma prima per non restare senza quota.
TETTO_PREDEFINITO = 240
TIMEOUT = 25


def _chiave() -> str:
    """La chiave SerpApi, se configurata."""
    return os.getenv("SERPAPI_KEY", "").strip()


def _tetto() -> int:
    try:
        return max(1, int(os.getenv("SERPAPI_MONTHLY_CAP", str(TETTO_PREDEFINITO))))
    except ValueError:
        return TETTO_PREDEFINITO


def _mese() -> str:
    return datetime.now(ROME).strftime("%Y-%m")


def chiave_cache(partenza: str, arrivo: str, giorno: date, adulti: int) -> str:
    """Identifica una ricerca: stessi aeroporti, stesso giorno, stessi adulti."""
    return f"{partenza}>{arrivo}>{giorno.isoformat()}>a{adulti}"


def _leggi_cache(chiave: str) -> tuple[dict[str, Any], datetime] | None:
    limite = (now_utc() - CACHE_TTL).isoformat()
    with db.db() as conn:
        riga = conn.execute(
            "SELECT risposta, letto_il FROM voli_cache WHERE chiave = ? AND letto_il >= ?",
            (chiave, limite),
        ).fetchone()
    if not riga:
        return None
    try:
        dati = json.loads(riga["risposta"])
    except (ValueError, TypeError):
        return None
    letto_il = parse_dt(riga["letto_il"]) or now_utc()
    return dati, letto_il


def _scrivi_cache(chiave: str, dati: dict[str, Any], letto_il: datetime) -> None:
    with db.db() as conn:
        conn.execute(
            "INSERT INTO voli_cache(chiave, risposta, letto_il) VALUES (?,?,?) "
            "ON CONFLICT(chiave) DO UPDATE SET "
            "risposta = excluded.risposta, letto_il = excluded.letto_il",
            (chiave, json.dumps(_pulita(dati), ensure_ascii=False), letto_il.isoformat()),
        )


def quota_usata(mese: str | None = None) -> int:
    """Quante ricerche SerpApi sono state fatte nel mese indicato."""
    with db.db() as conn:
        riga = conn.execute(
            "SELECT usate FROM voli_quota WHERE mese = ?", (mese or _mese(),)
        ).fetchone()
    return int(riga["usate"]) if riga else 0


def _conta_ricerca(mese: str) -> None:
    with db.db() as conn:
        conn.execute(
            "INSERT INTO voli_quota(mese, usate) VALUES (?, 1) "
            "ON CONFLICT(mese) DO UPDATE SET usate = usate + 1",
            (mese,),
        )


def _pulita(dati: dict[str, Any]) -> dict[str, Any]:
    """Toglie dalla risposta i campi che contengono la chiave.

    La risposta viene salvata nel database (e finisce nei backup): dentro
    ``search_metadata.json_endpoint`` c'e' l'URL con ``api_key``. Non deve
    uscire da qui.
    """
    puliti = dict(dati)
    meta = dict(puliti.get("search_metadata") or {})
    for campo in ("json_endpoint", "raw_html_file", "prettify_html_file", "markdown_endpoint"):
        meta.pop(campo, None)
    puliti["search_metadata"] = meta
    parametri = dict(puliti.get("search_parameters") or {})
    parametri.pop("api_key", None)
    puliti["search_parameters"] = parametri
    return puliti


def parametri_ricerca(
    partenza: str, arrivo: str, giorno: date, adulti: int
) -> dict[str, str]:
    """I parametri della chiamata. Gli aeroporti si passano separati da virgola."""
    return {
        "engine": "google_flights",
        "departure_id": partenza,
        "arrival_id": arrivo,
        "outbound_date": giorno.isoformat(),
        "type": "2",  # sola andata
        "currency": "EUR",
        "hl": "it",
        "gl": "it",
        "adults": str(max(1, adulti)),
    }


async def _chiama(parametri: dict[str, str]) -> dict[str, Any]:
    """Una chiamata a SerpApi. Non mette mai la chiave nei messaggi d'errore."""
    query = dict(parametri)
    query["api_key"] = _chiave()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
            risposta = await client.get(API_URL, params=query)
    except httpx.HTTPError as exc:
        raise AdapterError(f"SerpApi: connessione non riuscita ({type(exc).__name__})") from exc
    if risposta.status_code == 401:
        raise AdapterError("SerpApi: chiave non valida")
    if risposta.status_code == 429:
        raise AdapterError("SerpApi: troppe richieste, riprovo piu' tardi")
    if risposta.status_code != 200:
        raise AdapterError(f"SerpApi: HTTP {risposta.status_code}")
    try:
        dati = risposta.json()
    except ValueError as exc:
        raise AdapterError("SerpApi: risposta non leggibile") from exc
    if dati.get("error"):
        # Il messaggio di SerpApi non contiene la chiave.
        raise AdapterError(f"SerpApi: {str(dati['error'])[:200]}")
    return dati


def offerte_da_risposta(
    dati: dict[str, Any], letto_il: datetime
) -> list[Offer]:
    """Trasforma la risposta di SerpApi nelle offerte dell'app."""
    voci = list(dati.get("best_flights") or []) + list(dati.get("other_flights") or [])
    link = (dati.get("search_metadata") or {}).get("google_flights_url")
    valuta = (dati.get("search_parameters") or {}).get("currency") or "EUR"
    offerte: list[Offer] = []
    visti: set[str] = set()

    for voce in voci:
        prezzo = voce.get("price")
        tratte = voce.get("flights") or []
        if not isinstance(prezzo, (int, float)) or not tratte:
            continue
        prima = tratte[0].get("departure_airport") or {}
        ultima = tratte[-1].get("arrival_airport") or {}
        partenza = parse_dt(prima.get("time"))
        arrivo = parse_dt(ultima.get("time"))
        if partenza is None or arrivo is None:
            continue

        compagnie: list[str] = []
        for tratta in tratte:
            nome = str(tratta.get("airline") or "").strip()
            if nome and nome not in compagnie:
                compagnie.append(nome)

        numeri = "-".join(
            str(t.get("flight_number") or "").replace(" ", "") for t in tratte
        ).strip("-") or "volo"
        esterno = f"{numeri}@{partenza.isoformat()}"
        if esterno in visti:
            continue
        visti.add(esterno)

        tappe = []
        for tratta in tratte:
            da = tratta.get("departure_airport") or {}
            a = tratta.get("arrival_airport") or {}
            mezzo = " ".join(
                str(x) for x in (tratta.get("airline"), tratta.get("airplane")) if x
            )
            tappe.append(
                tappa(
                    da.get("name") or da.get("id"),
                    a.get("name") or a.get("id"),
                    da.get("time"),
                    a.get("time"),
                    mezzo,
                )
            )

        offerte.append(
            Offer(
                source="serpapi",
                external_id=esterno,
                mode="flight",
                operator=" + ".join(compagnie) or "Volo",
                depart_at=partenza,
                arrive_at=arrivo,
                price=float(prezzo),
                pickup_name=str(prima.get("name") or prima.get("id") or ""),
                dropoff_name=str(ultima.get("name") or ultima.get("id") or ""),
                duration_min=voce.get("total_duration"),
                deeplink=link,
                currency=valuta,
                price_is_estimate=False,
                details={
                    "volo": True,
                    "scali": max(0, len(tratte) - 1),
                    "compagnie": compagnie,
                    "tappe": tappe,
                    "letto_il": letto_il.isoformat(),
                    "fonte": "Google Voli (SerpApi)",
                    "orari_locali": True,
                },
            )
        )
    return offerte


class SerpApiFlightsAdapter(Adapter):
    id = "serpapi"
    label = "Google Voli (prezzi)"
    description = (
        "Voli: legge i prezzi da Google Voli tramite SerpApi. Il piano "
        "gratuito da' 250 ricerche al mese; quando finiscono, o senza chiave, "
        "resta il link."
    )
    modes = ("flight",)

    async def search(self, ctx: SearchContext) -> AdapterResult:
        if ctx.distance_km is not None and ctx.distance_km < DISTANZA_MINIMA_KM:
            return AdapterResult()
        da = airports.aeroporti_di(ctx.from_place)
        a = airports.aeroporti_di(ctx.to_place)
        if da is None or a is None:
            return AdapterResult()

        if not _chiave():
            return AdapterResult(
                hints=["Voli: manca la chiave SerpApi, per ora resta il link a Google Voli."]
            )

        giorno = (ctx.depart_after or ctx.arrive_by).astimezone(ROME).date()
        if giorno < date.today():
            return AdapterResult()

        partenza = ",".join(da.tutti)
        arrivo = ",".join(a.tutti)
        chiave = chiave_cache(partenza, arrivo, giorno, ctx.passengers)

        cache = _leggi_cache(chiave)
        if cache is not None:
            dati, letto_il = cache
            return AdapterResult(offers=offerte_da_risposta(dati, letto_il))

        mese = _mese()
        if quota_usata(mese) >= _tetto():
            return AdapterResult(
                hints=[
                    "Voli: tetto mensile di SerpApi raggiunto, per ora resta il link a Google Voli."
                ]
            )

        parametri = parametri_ricerca(partenza, arrivo, giorno, ctx.passengers)
        try:
            dati = await _chiama(parametri)
        except AdapterError as exc:
            return AdapterResult(hints=[str(exc)])

        if (dati.get("search_metadata") or {}).get("status") != "Success":
            return AdapterResult()

        _conta_ricerca(mese)
        letto_il = now_utc()
        _scrivi_cache(chiave, dati, letto_il)
        return AdapterResult(offers=offerte_da_risposta(dati, letto_il))
