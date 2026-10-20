"""BlaBlaCar - integrazione "nativa" senza API pubblica.

L'API pubblica di BlaBlaCar e' stata dismessa e il sito e' protetto da un
filtro anti-bot: non si possono leggere i passaggi pubblicamente. Quindi
qui NON si inventano offerte ne' si fingono prezzi reali.

Quello che si fa e' utile comunque:

1. si stima il costo del passaggio con la regola che usa il mercato
   (~0,10 EUR per km a posto, minimo 2 EUR) e il tempo di viaggio con la
   velocita' media: la stima e' **marcata come stima** (price_is_estimate),
   mai come prezzo garantito;
2. si calcola il tempo porta a porta partendo dal punto di ritrovo che
   l'utente ha indicato: e' la parte che il sito non puo' dirti, e spesso
   e' quella che conta (il punto esatto lo sceglie l'autista);
3. si lascia il link di ricerca reale, con citta', data e punto di ritrovo:
    un tocco e si vedono i viaggi veri.

La regola dei prezzi e' una stima di mercato, non un dato della fonte: per
questo l'offerta porta sempre la scritta "stima" e il link al sito.
"""

from __future__ import annotations

from datetime import timedelta

from ..geo import haversine_km
from ..timeutil import parse_dt
from .base import Adapter, AdapterResult, Offer, SearchContext

# Prezzo indicativo per km a posto (il conducente decide, la media di
# mercato e' questa) e minimo sotto cui non si scende.
PREZZO_PER_KM = 0.10
PREZZO_MINIMO = 2.0
# Velocita' media in auto: 70 km/h.
KM_ORARI = 70.0
# La strada e' piu' lunga della linea d'aria (in Italia, di norma, ~30% in piu').
FATTORE_STRADA = 1.3
# Tempo da mettere per trovare l'autista e farsi trovare al punto di ritrovo.
MARGINE_INCONTRO_MIN = 20


class BlaBlaCarAdapter(Adapter):
    id = "blablacar"
    label = "BlaBlaCar (passaggi in auto)"
    description = (
        "Passaggi in auto. Senza API pubblica il prezzo e' una stima di "
        "mercato: la ricerca reale si apre con un tocco sul link."
    )
    modes = ("rideshare",)

    async def search(self, ctx: SearchContext) -> AdapterResult:
        hints: list[str] = []
        arrive_by = ctx.arrive_by
        if arrive_by is None:
            return AdapterResult(
                hints=["BlaBlaCar: manca l'orario di arrivo, niente da stimare."]
            )

        # La distanza: fra i due punti indicati (o le citta').
        distance = ctx.distance_km
        if not distance or distance <= 0:
            hints.append(
                "BlaBlaCar: non riesco a calcolare la distanza, apro la ricerca."
            )
            return AdapterResult(hints=hints)

        # La strada e' piu' lunga della linea d'aria: in Italia, di norma, va
        # un 30% circa in piu'. Serve per stimare tempi e prezzi credibili.
        strada = distance * FATTORE_STRADA

        # Prezzo stimato: la regola del mercato, non un dato della fonte.
        price = round(max(PREZZO_MINIMO, strada * PREZZO_PER_KM), 2)

        # Tempo di viaggio + il margine per trovarsi all'incontro.
        drive_min = int(strada / KM_ORARI * 60) + MARGINE_INCONTRO_MIN
        depart_at = arrive_by - timedelta(minutes=drive_min)

        # Il punto di ritrovo: quello indicato dall'utente, se c'e'. Il
        # passaggio parte da li', quindi il cammino e' quello del punto
        # richiesto (l'autista sceglie il posto: se si allontana, il tempo
        # a piedi cresce e va aggiunto a mano).
        pickup_lat, pickup_lon = ctx.origin_coords or (ctx.from_lat, ctx.from_lon)
        dropoff_lat, dropoff_lon = ctx.destination_coords or (ctx.to_lat, ctx.to_lon)

        offer = Offer(
            source=self.id,
            external_id=f"stima:{ctx.from_place}:{ctx.to_place}:{arrive_by.date().isoformat()}",
            mode="rideshare",
            operator="BlaBlaCar (passaggio in auto)",
            depart_at=depart_at,
            arrive_at=arrive_by,
            price=price,
            pickup_name=ctx.from_place,
            pickup_lat=pickup_lat,
            pickup_lon=pickup_lon,
            dropoff_name=ctx.to_place,
            dropoff_lat=dropoff_lat,
            dropoff_lon=dropoff_lon,
            duration_min=drive_min - MARGINE_INCONTRO_MIN,
            price_is_estimate=True,
            deeplink=_search_link(ctx),
            details={
                "fonte": "BlaBlaCar (stima di mercato)",
                "stima": True,
                "distanza_km": round(distance, 1),
                "distanza_stradale_km": round(strada, 1),
                "prezzo_stimato": price,
                "nota": (
                    "Prezzo stimato con la regola del mercato (~0,10 EUR/km a "
                    "posto): il conducente lo decide. Il punto di ritrovo lo "
                    "sceglie l'autista, quindi la stima e' valida se ti fai "
                    "trovare nel punto indicato."
                ),
                "margine_incontro_min": MARGINE_INCONTRO_MIN,
                "cambi": 0,
                "tappe": [],
            },
        )
        return AdapterResult(
            offers=[offer],
            hints=[
                "BlaBlaCar: prezzo e orari stimati (l'API non e' piu' "
                "disponibile): apri il link per i viaggi veri."
            ],
        )


def _search_link(ctx: SearchContext) -> str:
    """Il link di ricerca reale, con citta' e data."""
    from urllib.parse import quote

    arrive = parse_dt(ctx.arrive_by) if ctx.arrive_by else None
    day = arrive.strftime("%Y-%m-%d") if arrive else ""
    return (
        f"https://www.blablacar.it/search?fn={quote(ctx.from_place or '')}"
        f"&tn={quote(ctx.to_place or '')}&db={day}"
    )
