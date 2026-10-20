"""Sconti e agevolazioni reali che si possono applicare ai biglietti.

**X-GO** e' il programma fedeltà di Trenitalia: 2 punti per euro speso,
cashback immediato e, per chi e' iscritto, lo sconto **Young Regionale -20%**
(under 30) o **Senior Regionale -20%** (60 e piu').

Le condizioni sono precise e l'app le rispetta una per una (fonte:
trenitalia.com/it/offerte/young-regionale.html e senior-regionale.html):

1. **solo treni regionali** (Regionale, Regionale Veloce, Regionale Express):
   non su Frecce, Intercity, EuroCity ne' sulle Frecce Regionale;
2. **non vale** nelle regioni di Campania, Abruzzo, Sardegna e Puglia ne'
   nelle province autonome di Trento e Bolzano;
3. **solo sui treni selezionati** (in base a tratta, giorno e orario): non
   tutti i regionali hanno la promozione attiva in quella data;
4. **acquisto entro le 23:59 del giorno prima** della partenza;
5. la promozione **vale fino al 30 novembre 2026**.

Per questo il biglietto scontato resta marcato come stima, con accanto il
prezzo di listino e le condizioni da verificare.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

# Le due agevolazioni X-GO: -20% sui treni regionali.
XGOGIOVANE = "giovane"
XGOSENIOR = "senior"
SCONTO_XGO = 0.20
# Fine della promozione (fonte: trenitalia.com, "viaggi fino al 30 novembre 2026").
XGO_FINE_PROMOZIONE = date(2026, 11, 30)
# Regioni in cui la promozione X-GO non e' valida.
REGIONI_SENZA_XGO = {"campania", "abruzzo", "sardegna", "puglia", "trentino", "alto_adige"}


def xgo_sconto(prefs: dict[str, Any] | None) -> float:
    """Lo sconto X-GO che spetta all'utente (0 se non ne ha diritto)."""
    if not prefs:
        return 0.0
    valore = str(prefs.get("xgo") or "").strip().lower()
    if valore in (XGOGIOVANE, XGOSENIOR):
        return SCONTO_XGO
    return 0.0


def xgo_etichetta(prefs: dict[str, Any] | None) -> str:
    valore = str((prefs or {}).get("xgo") or "").strip().lower()
    if valore == XGOGIOVANE:
        return "X-GO Young (under 30)"
    if valore == XGOSENIOR:
        return "X-GO Senior (60+)"
    return ""


def _xgo_ammissibile(offer: Any, when: date | None) -> tuple[bool, str]:
    """True se a questo treno l'agevolazione X-GO puo' valere.

    Restituisce anche il motivo del rifiuto (per spiegarlo all'utente).
    """
    from .passes import _is_regional_train, regione_di

    if when and when > XGO_FINE_PROMOZIONE:
        return False, "la promozione X-GO vale fino al 30 novembre 2026"
    if not _is_regional_train(offer):
        return False, "lo sconto X-GO vale solo sui treni regionali"
    for lat, lon in (
        (getattr(offer, "pickup_lat", None), getattr(offer, "pickup_lon", None)),
        (getattr(offer, "dropoff_lat", None), getattr(offer, "dropoff_lon", None)),
    ):
        regione = regione_di(lat, lon)
        if regione in REGIONI_SENZA_XGO:
            nome = {
                "campania": "Campania",
                "abruzzo": "Abruzzo",
                "sardegna": "Sardegna",
                "puglia": "Puglia",
                "trentino": "Trento",
                "alto_adige": "Bolzano",
            }.get(regione, regione or "")
            return False, f"lo sconto X-GO non vale in {nome}"
    return True, ""


def apply_xgo(offers: list[Any], prefs: dict[str, Any] | None) -> list[Any]:
    """Applica lo sconto X-GO ai treni regionali che ne hanno diritto.

    Il prezzo di listino resta nei dettagli, cosi' l'utente vede quanto
    paga davvero e quanto risparmia.
    """
    sconto = xgo_sconto(prefs)
    if not sconto:
        return offers
    from .passes import _is_regional_train

    for offer in offers:
        if getattr(offer, "mode", "") != "train":
            continue
        if offer.price is None or offer.price <= 0:
            continue
        details = dict(getattr(offer, "details", None) or {})
        if details.get("sconto_xgo"):
            continue
        when = None
        depart = getattr(offer, "depart_at", None)
        if isinstance(depart, datetime):
            when = depart.date()
        ammesso, motivo = _xgo_ammissibile(offer, when)
        if not ammesso:
            details["xgo_non_applicabile"] = motivo
            offer.details = details
            continue
        listino = offer.price
        offer.price = round(listino * (1 - sconto), 2)
        offer.price_is_estimate = True
        details["prezzo_listino"] = round(listino, 2)
        details["sconto_xgo"] = int(sconto * 100)
        details["sconto_xgo_nome"] = xgo_etichetta(prefs)
        details["nota_sconto"] = (
            f"Prezzo con sconto X-GO del {int(sconto * 100)}% (listino "
            f"{listino:.2f} EUR): vale solo iscritti al programma, sui treni "
            f"selezionati, entro il 30 novembre 2026."
        )
        offer.details = details
    return offers
