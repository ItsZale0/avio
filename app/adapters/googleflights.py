"""Google Voli - la ricerca si apre sul sito, i prezzi non si leggono da qui.

Google Voli non ha un'API pubblica. La pagina si apre anche da un server, ma i
risultati arrivano da una chiamata interna che resta vuota: provato il
22/10/2026 sulla VM, con Chrome vero (il modulo si compila, la pagina resta
su "caricamento dei risultati"). Quindi questa fonte non inventa niente:
prepara il link della ricerca gia' compilata, che l'app mostra in
«Link utili».

La ricerca ha senso solo se entrambe le estremita' hanno un aeroporto: per
Bressanone -> Rovereto non c'e' nessun volo e la fonte sta zitta.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from urllib.parse import quote

from ..services import airports
from ..timeutil import ROME
from .base import Adapter, AdapterResult, SearchContext

log = logging.getLogger("carpool.voli")

# Sotto questa distanza l'aereo non ha senso: si evitano ricerche inutili.
DISTANZA_MINIMA_KM = 200


def link_ricerca(
    partenza: str, arrivo: str, giorno: date | None, passeggeri: int = 1
) -> str | None:
    """Il link alla ricerca su Google Voli, o None se non ci sono aeroporti."""
    da = airports.aeroporti_di(partenza)
    a = airports.aeroporti_di(arrivo)
    if da is None or a is None:
        return None
    giorno = giorno or date.today()
    domanda = (
        f"Flights from {da.nome} to {a.nome} on {giorno.isoformat()}"
    )
    return (
        "https://www.google.com/travel/flights"
        f"?q={quote(domanda)}&curr=EUR&hl=it&gl=IT"
    )


class GoogleFlightsAdapter(Adapter):
    id = "googleflights"
    label = "Google Voli"
    description = (
        "Voli: apre la ricerca già compilata su Google Voli. Google non "
        "lascia leggere i risultati da un server, quindi da qui non arrivano "
        "prezzi."
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
                f"Google Voli: il volo {da.nome} -> {a.nome} si cerca sul sito "
                "(link in «Link utili»)."
            ]
        )
