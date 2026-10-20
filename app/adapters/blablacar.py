"""BlaBlaCar - passaggi in auto e autobus, con i DATI VERI.

BlaBlaCar non ha piu' un'API pubblica e il sito e' protetto da un filtro
anti-bot. I dati si ottengono aprendo la pagina di ricerca con un browser
vero (vedi `app/services/blablacar_browser.py`): la pagina contiene gia' la
risposta JSON del backend, con prezzi, orari e punti di ritrovo reali.

Quello che l'app ricava per ogni viaggio:
- il prezzo vero (non una stima);
- l'orario di partenza e di arrivo;
- il punto di ritrovo esatto ("Autostazione San Donato"), con le coordinate
  -> l'app calcola i minuti a piedi per arrivarci davvero;
- se il posto e' tra gli ultimi.

Nessuna stima, nessun prezzo inventato: se il browser non e' disponibile
l'adapter non propone nulla e lascia il link di ricerca.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..timeutil import ROME
from .base import Adapter, AdapterResult, Offer, SearchContext

log = logging.getLogger("carpool.blablacar")

BASE = Path(__file__).resolve().parent.parent.parent
PROFILO = BASE / "data" / "blablacar-profile"

# La ricerca mostra i viaggi in ordine di partenza: la prima pagina basta.
MAX_VIAGGI = 12


def _prezzo(prezzo_grezzo: Any) -> float | None:
    """Il prezzo vero, letto dal tokenizzato ("29", ",99", " EUR")."""
    if not isinstance(prezzo_grezzo, dict):
        return None
    parti = prezzo_grezzo.get("tokenizedPrice")
    if not isinstance(parti, list) or not parti:
        return None
    testo = ""
    for parte in parti:
        if not isinstance(parte, dict):
            continue
        valore = str(parte.get("value") or "").strip()
        if parte.get("type") == "CURRENCY":
            continue
        testo += valore
    testo = testo.replace(".", "").replace(",", ".").replace(" ", "")
    try:
        return round(float(testo), 2)
    except (TypeError, ValueError):
        return None


def _orario(testo: str | None) -> tuple[int, int] | None:
    """'00:30' -> (ora, minuto)."""
    if not testo or ":" not in testo:
        return None
    ora, _, minuto = testo.partition(":")
    try:
        return int(ora), int(minuto)
    except (TypeError, ValueError):
        return None


def _momento(giorno, ora_e_minuto: tuple[int, int] | None) -> datetime | None:
    """Un datetime (Rome) nel giorno della ricerca."""
    if giorno is None or ora_e_minuto is None:
        return None
    ora, minuto = ora_e_minuto
    try:
        return datetime(
            giorno.year, giorno.month, giorno.day, ora, minuto, tzinfo=ROME
        )
    except ValueError:
        return None


class BlaBlaCarAdapter(Adapter):
    id = "blablacar"
    label = "BlaBlaCar (dati reali)"
    description = (
        "Passaggi in auto e autobus BlaBlaCar con prezzi, orari e punti di "
        "ritrovo reali (la ricerca viene letta dal sito con un browser)."
    )
    modes = ("rideshare", "bus")

    def search_url(self, ctx: SearchContext) -> str:
        """L'URL della ricerca sul sito."""
        from urllib.parse import quote

        partenza = ctx.depart_after or ctx.arrive_by
        giorno = partenza.astimezone(ROME).strftime("%Y-%m-%d")
        return (
            "https://www.blablacar.it/search"
            f"?fn={quote(ctx.from_place or '')}"
            f"&tn={quote(ctx.to_place or '')}"
            f"&db={giorno}"
            f"&seats={max(1, ctx.passengers)}"
        )

    async def search(self, ctx: SearchContext) -> AdapterResult:
        from ..services import blablacar_browser

        if not blablacar_browser.browser_disponibile():
            return AdapterResult(
                hints=[
                    "BlaBlaCar: per leggere i viaggi serve un browser. "
                    "Installa Playwright (pip install playwright) e Chrome: "
                    "la ricerca si apre con un tocco nella scheda «Link utili»."
                ]
            )

        url = self.search_url(ctx)
        try:
            dati = await asyncio.to_thread(
                blablacar_browser.scarica_ricerca, url, PROFILO, 45
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("BlaBlaCar non raggiungibile: %s", exc)
            return AdapterResult(
                hints=[
                    "BlaBlaCar: non sono riuscito a leggere i viaggi "
                    f"({type(exc).__name__}). La ricerca si apre con un tocco "
                    "nella scheda «Link utili»."
                ]
            )

        if not dati:
            return AdapterResult(
                hints=[
                    "BlaBlaCar: nessun dato nella pagina (la ricerca potrebbe "
                    "essere scambiata o bloccata). Prova il link diretto."
                ]
            )

        offerte = self._offerte(dati, ctx, url)
        if not offerte:
            return AdapterResult(
                hints=[
                    "BlaBlaCar: nessun viaggio per questa tratta e questa data."
                ]
            )
        return AdapterResult(
            offers=offerte,
            hints=[
                f"BlaBlaCar: {len(offerte)} viaggi reali letti dal sito "
                "(prezzi e orari veri)."
            ],
        )

    # ------------------------------------------------------------------ #
    def _offerte(
        self, dati: dict[str, Any], ctx: SearchContext, url: str
    ) -> list[Offer]:
        # La cache avvolge la risposta in {"value": {...}, "expiresAt": ...}
        if isinstance(dati.get("value"), dict):
            dati = dati["value"]
        try:
            contenuto = (dati.get("resultsContent") or {}).get("searchItems") or []
        except AttributeError:
            contenuto = []
        giorno = None
        if ctx.depart_after or ctx.arrive_by:
            giorno = (ctx.depart_after or ctx.arrive_by).astimezone(ROME).date()

        offerte: list[Offer] = []
        for item in contenuto:
            if not isinstance(item, dict) or item.get("type") != "TRIP":
                continue
            if len(offerte) >= MAX_VIAGGI:
                break
            offerta = self._offerta(item, giorno, url)
            if offerta is not None:
                offerte.append(offerta)
        return offerte

    def _offerta(
        self, item: dict[str, Any], giorno, url: str
    ) -> Offer | None:
        prezzo = _prezzo(item.get("price"))
        if prezzo is None or prezzo <= 0:
            return None
        itinerario = (
            (item.get("travelDetails") or {}).get("itinerary") or {}
        )
        partenza = itinerario.get("departure") or {}
        arrivo = itinerario.get("arrival") or {}
        ora_partenza = _momento(giorno, _orario(partenza.get("time")))
        ora_arrivo = _momento(giorno, _orario(arrivo.get("time")))
        if ora_partenza is None:
            return None
        if ora_arrivo is None:
            ora_arrivo = ora_partenza
        # Se l'arrivo e' il giorno dopo (viaggio notturno), si aggiusta.
        if ora_arrivo < ora_partenza:
            ora_arrivo += timedelta(days=1)

        multi = item.get("multimodalId") or {}
        fonte = str(multi.get("source") or "")
        e_partenza = (
            "BlaBlaCar Bus"
            if fonte == "PRO_PARTNER"
            else "BlaBlaCar (passaggio in auto)"
        )
        dettagli: dict[str, Any] = {
            "fonte": "BlaBlaCar (dati reali dal sito)",
            "multimodal_id": str(multi.get("id") or ""),
            "supply": fonte,
            "cambi": 0,
        }
        if item.get("scarcity"):
            dettagli["scarcita"] = str(item.get("scarcity"))
        # Il punto di ritrovo vero (quello scelto dall'autista/operatore).
        punto_partenza = partenza.get("secondaryLocation")
        punto_arrivo = arrivo.get("secondaryLocation")
        if punto_partenza:
            dettagli["punto_partenza"] = punto_partenza
        if punto_arrivo:
            dettagli["punto_arrivo"] = punto_arrivo
        if not punto_partenza:
            dettagli["nota_punto"] = (
                "Il punto di ritrovo esatto si vede aprendo il viaggio: "
                "per i passaggi in auto lo sceglie l'autista."
            )

        coordinate_partenza = partenza.get("coordinates") or {}
        coordinate_arrivo = arrivo.get("coordinates") or {}

        return Offer(
            source=self.id,
            external_id=str(item.get("id") or multi.get("id") or ""),
            mode="rideshare" if fonte != "PRO_PARTNER" else "bus",
            operator=e_partenza,
            depart_at=ora_partenza,
            arrive_at=ora_arrivo,
            price=prezzo,
            pickup_name=str(partenza.get("primaryLocation") or ctx.from_place or ""),
            pickup_lat=coordinate_partenza.get("latitude"),
            pickup_lon=coordinate_partenza.get("longitude"),
            dropoff_name=str(arrivo.get("primaryLocation") or ctx.to_place or ""),
            dropoff_lat=coordinate_arrivo.get("latitude"),
            dropoff_lon=coordinate_arrivo.get("longitude"),
            duration_min=int((ora_arrivo - ora_partenza).total_seconds() // 60),
            deeplink=url,
            details=dettagli,
        )
