"""Abbonamenti e pass: il viaggio che hai gia' pagato.

In Italia un abbonamento cambia il costo marginale di un viaggio: se la
tratta e' coperta, il biglietto costa zero euro. E' spesso la variabile che
decide quale piano conviene davvero (un regionale di 6 ore coperto batte
un Frecciarossa da 30 euro).

Regole:
- si usano solo abbonamenti reali dichiarati dall'utente, con la scadenza
  se nota: se e' scaduto non copre niente;
- un abbonamento regionale copre solo i regionali (non Frecce o Intercity);
- il carnet "consuma" un biglietto: il costo e' zero, ma la nota lo dice;
- il prezzo REALE del biglietto non viene mai cancellato: viene affiancato
  dal costo effettivo con l'abbonamento.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

# I tipi di abbonamento riconosciuti. "copre" elenca i modi di trasporto
# inclusi; "solo_regionali" limita la copertura ai treni regionali.
PASS_TYPES: dict[str, dict[str, Any]] = {
    "trenitalia_regionale": {
        "nome": "Abbonamento Trenitalia (regionale)",
        "copre": {"train"},
        "solo_regionali": True,
    },
    "trenitalia_tutti": {
        "nome": "Abbonamento Trenitalia (tutti i treni)",
        "copre": {"train"},
        "solo_regionali": False,
    },
    "carnet_regionale": {
        "nome": "Carnet 10 biglietti regionali",
        "copre": {"train", "bus"},
        "solo_regionali": True,
        "consuma": True,
    },
    "carnet_bus": {
        "nome": "Carnet bus regionali",
        "copre": {"bus"},
        "solo_regionali": False,
        "consuma": True,
    },
    "abbonamento_unico": {
        "nome": "Abbonamento regionale (tutti i mezzi)",
        "copre": {"train", "bus", "tram", "transit"},
        "solo_regionali": True,
    },
    "urbano": {
        "nome": "Abbonamento urbano (bus/metro/tram)",
        "copre": {"bus", "tram", "transit"},
        "solo_regionali": False,
    },
    "carnet_urbano": {
        "nome": "Carnet 10 biglietti urbani",
        "copre": {"bus", "tram", "transit"},
        "solo_regionali": False,
        "consuma": True,
    },
}

# Come si riconosce un treno "lento" (coperto da un abbonamento regionale).
LENDE = ("regionale", "regio", "r", "intercity notte")
NON_REGIONALI = ("frecciarossa", "frecciargento", "frecciabianca", "intercity", "eurostar", "italia")


def pass_name(pass_type: str) -> str:
    entry = PASS_TYPES.get(pass_type)
    return entry["nome"] if entry else str(pass_type or "Abbonamento")


def normalize_passes(value: Any) -> list[dict[str, Any]]:
    """Normalizza la lista di abbonamenti salvata nelle preferenze."""
    if not value:
        return []
    if isinstance(value, str):
        import json

        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, str):
            item = {"tipo": item}
        if not isinstance(item, dict):
            continue
        tipo = str(item.get("tipo") or "").strip()
        if not tipo:
            continue
        if tipo not in PASS_TYPES:
            continue
        entry = {
            "tipo": tipo,
            "nome": str(item.get("nome") or pass_name(tipo))[:80],
            "valido_alla": _as_date(item.get("valido_alla")),
        }
        out.append(entry)
    return out


def _as_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _is_expired(pass_info: dict[str, Any], when: date | None) -> bool:
    expiry = _as_date(pass_info.get("valido_alla"))
    if not expiry or when is None:
        return False
    return expiry < when


def _regional_only_offer(offer: Any) -> bool:
    """True se l'offerta e' composta solo da treni regionali."""
    details = getattr(offer, "details", None) or {}
    categorie = details.get("treni") or ""
    if not categorie:
        # Senza informazioni sul tipo di treno: non si presume la copertura.
        return False
    testo = str(categorie).lower()
    if any(parola in testo for parola in NON_REGIONALI):
        return False
    return any(parola in testo for parola in LENDE)


def pass_covers(pass_info: dict[str, Any], offer: Any, when: date | None = None) -> bool:
    """True se l'abbonamento copre questa offerta."""
    if _is_expired(pass_info, when):
        return False
    tipo = PASS_TYPES.get(str(pass_info.get("tipo") or ""))
    if not tipo:
        return False
    mode = str(getattr(offer, "mode", "") or "")
    if mode not in tipo["copre"]:
        return False
    if tipo.get("solo_regionali") and mode == "train":
        return _regional_only_offer(offer)
    return True


def covering_pass(
    passes: list[dict[str, Any]], offer: Any, when: date | None = None
) -> dict[str, Any] | None:
    """Il primo abbonamento che copre l'offerta (se c'e')."""
    for pass_info in passes:
        if pass_covers(pass_info, offer, when):
            return pass_info
    return None


def apply_passes(
    passes: list[dict[str, Any]], offer: Any, when: date | None = None
) -> Any:
    """Applica gli abbonamenti all'offerta: net_cost = 0 se coperta.

    Il prezzo reale resta intatto: viene solo annotato il costo effettivo.
    """
    if when is None:
        depart = getattr(offer, "depart_at", None)
        if isinstance(depart, datetime):
            when = depart.date()
    found = covering_pass(passes, offer, when)
    if not found:
        return offer
    tipo = PASS_TYPES.get(str(found.get("tipo")), {})
    details = dict(getattr(offer, "details", None) or {})
    details["pass"] = found.get("nome") or pass_name(str(found.get("tipo")))
    details["pass_tipo"] = found.get("tipo")
    if tipo.get("consuma"):
        details["pass_note"] = "consuma 1 biglietto del carnet"
    if "net_cost" not in details:
        details["net_cost"] = 0.0
    offer.details = details
    offer.net_cost = 0.0
    return offer


def has_urban_pass(passes: list[dict[str, Any]], when: date | None = None) -> bool:
    """C'è un abbonamento che copre bus/tram/transit cittadini."""
    for pass_info in passes:
        tipo = PASS_TYPES.get(str(pass_info.get("tipo") or ""))
        if not tipo:
            continue
        if _is_expired(pass_info, when):
            continue
        if tipo["copre"] & {"bus", "tram", "transit"}:
            return True
    return False


def summary(passes: list[dict[str, Any]]) -> str:
    """Elenco leggibile degli abbonamenti attivi."""
    if not passes:
        return ""
    nomi = []
    for pass_info in passes:
        nome = pass_info.get("nome") or pass_name(str(pass_info.get("tipo")))
        expiry = pass_info.get("valido_alla")
        if expiry:
            nomi.append(f"{nome} (fino al {expiry.strftime('%d/%m/%Y')})")
        else:
            nomi.append(str(nome))
    return ", ".join(nomi)
