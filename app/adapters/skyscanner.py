"""Skyscanner - la ricerca si apre sul sito, i prezzi non si leggono da qui.

Skyscanner non ha un'API pubblica aperta e il sito risponde a un server con
la pagina "Sei una persona o un robot?": provato il 22/10/2026 sulla VM, con
Chrome vero e senza fingere niente. Quindi questa fonte non inventa niente:
prepara il link della ricerca gia' compilata, che l'app mostra in
«Link utili».

Il link usa i codici degli aeroporti (mxp, fco...): Skyscanner non accetta i
nomi delle citta' nell'indirizzo, li rifiuta con "Pagina non trovata".
"""

from __future__ import annotations

import logging
from datetime import date

from ..services import airports
from ..timeutil import ROME
from .base import Adapter, AdapterResult, SearchContext

log = logging.getLogger("carpool.voli")

# Sotto questa distanza l'aereo non ha senso: si evitano ricerche inutili.
DISTANZA_MINIMA_KM = 200


def link_ricerca(
    partenza: str, arrivo: str, giorno: date | None, passeggeri: int = 1
) -> str | None:
    """Il link alla ricerca su Skyscanner, o None se non ci sono aeroporti."""
    da = airports.aeroporti_di(partenza)
    a = airports.aeroporti_di(arrivo)
    if da is None or a is None:
        return None
    giorno = giorno or date.today()
    return (
        "https://www.skyscanner.it/transport/flights/"
        f"{da.principale.lower()}/{a.principale.lower()}/{giorno.strftime('%y%m%d')}/"
        f"?adultsv2={max(1, passeggeri)}&cabinclass=economy&rtn=0"
    )


class SkyscannerAdapter(Adapter):
    id = "skyscanner"
    label = "Skyscanner"
    description = (
        "Voli: apre la ricerca già compilata su Skyscanner. Il sito blocca le "
        "letture automatiche da un server, quindi da qui non arrivano prezzi."
    )
    modes = ("flight",)

    def search_url(self, ctx: SearchContext) -> str | None:
        giorno = (ctx.depart_after or ctx.arrive_by).astimezone(ROME).date()
        return link_ricerca(ctx.from_place, ctx.to_place, giorno, ctx.passengers)

    async def search(self, ctx: SearchContext) -> AdapterResult:
        # Nessun prezzo letto, nessun prezzo inventato: solo il link.
        if ctx.distance_km is not None and ctx.distance_km < DISTANZA_MINIMA_KM:
            return AdapterResult()
        da = airports.aeroporti_di(ctx.from_place)
        a = airports.aeroporti_di(ctx.to_place)
        if da is None or a is None:
            return AdapterResult()
        return AdapterResult(
            hints=[
                f"Skyscanner: il volo {da.nome} -> {a.nome} si cerca sul sito "
                "(link in «Link utili»)."
            ]
        )
