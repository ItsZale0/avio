"""BlaBlaCar - solo link di ricerca.

L'API pubblica di BlaBlaCar e' stata dismessa: non esiste piu' un endpoint aperto
per i passaggi. Questa fonte non inventa nulla e non fa chiamate di rete: rimanda
alla ricerca ufficiale (scheda "Link utili"). Resta registrata con lo stesso id
per non invalidare le ricerche gia' salvate.
"""

from __future__ import annotations

from .base import Adapter, AdapterResult, SearchContext


class BlaBlaCarAdapter(Adapter):
    id = "blablacar"
    label = "BlaBlaCar (link di ricerca)"
    description = (
        "Passaggi in auto. L'API pubblica e' stata dismessa: la ricerca si apre "
        "sul sito ufficiale BlaBlaCar."
    )
    modes = ("rideshare",)

    async def search(self, ctx: SearchContext) -> AdapterResult:
        return AdapterResult(
            hints=[
                "BlaBlaCar: l'API pubblica e' stata dismessa, nessun dato automatico. "
                "Usa il link di ricerca diretto nella scheda «Link utili»."
            ]
        )
