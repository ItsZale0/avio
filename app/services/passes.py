"""Abbonamenti e pass: il viaggio che hai gia' pagato.

Dati raccolti dalle fonti ufficiali (2026). Per ogni abbonamento:
- l'area in cui vale (citta' o regione, con un raggio in km);
- i mezzi che copre;
- le condizioni reali (non copre i Frecce, non vale in certe regioni, scade...).

Il punto importante: la copertura e' quasi sempre **parziale**. Un
abbonamento regionale copre solo la parte di viaggio nella sua area, e un
abbonamento urbano copre solo la citta'. Per questo il calcolo e' fatto
tratto per tratto (`coverage`), e la quota non coperta resta a carico
dell'utente.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from ..geo import CITIES, haversine_km

# --------------------------------------------------------------------------- #
# Trenitalia: l'abbonamento regionale esiste in ogni regione (tariffe
# 40/xx/REGIONE) e vale sui treni regionali di QUELLA regione.
_REGIONI_TRENITALIA: dict[str, list[str]] = {
    "lombardia": ["milano", "bergamo", "brescia", "como", "varese", "pavia", "monza", "cremona"],
    "piemonte": ["torino", "novara", "alessandria", "asti", "cuneo"],
    "veneto": ["venezia", "mestre", "padova", "vicenza", "verona", "treviso"],
    "emilia": ["bologna", "parma", "modena", "reggio emilia", "ferrara", "rimini", "ravenna", "piacenza"],
    "toscana": ["firenze", "pisa", "livorno", "lucca", "siena", "arezzo"],
    "lazio": ["roma", "frosinone", "viterbo", "rieti"],
    "campania": ["napoli", "salerno", "caserta"],
    "calabria": ["reggio calabria", "catanzaro"],
    "puglia": ["bari", "brindisi", "lecce", "foggia", "taranto"],
    "sicilia": ["palermo", "catania", "messina"],
    "sardegna": ["cagliari", "sassari", "olbia"],
    "umbria": ["perugia"],
    "marche": ["ancona"],
    "abruzzo": ["pescara", "l'aquila"],
    "fvg": ["trieste", "udine"],
    "trentino": ["trento"],
    "alto_adige": ["bolzano", "bressanone", "cortina d'ampezzo"],
    "valle_aosta": ["aosta"],
    "liguria": ["genova", "savona", "imperia", "la spezia"],
    "molise": ["campobasso"],
    "basilicata": ["potenza", "matera"],
}

# I nomi leggibili delle regioni (per il pulsante nella UI).
NOMI_REGIONI = {
    "lombardia": "Lombardia",
    "piemonte": "Piemonte",
    "veneto": "Veneto",
    "emilia": "Emilia-Romagna",
    "toscana": "Toscana",
    "lazio": "Lazio",
    "campania": "Campania",
    "calabria": "Calabria",
    "puglia": "Puglia",
    "sicilia": "Sicilia",
    "sardegna": "Sardegna",
    "umbria": "Umbria",
    "marche": "Marche",
    "abruzzo": "Abruzzo",
    "fvg": "Friuli Venezia Giulia",
    "trentino": "Trentino",
    "alto_adige": "Alto Adige",
    "valle_aosta": "Valle d'Aosta",
    "liguria": "Liguria",
    "molise": "Molise",
    "basilicata": "Basilicata",
}

# Gli abbonamenti urbani: il nome dell'operatore (dove e' noto) e la citta'.
# La copertura e' la rete urbana (bus, tram, metro) piu' le linee
# extraurbane che fanno capolinea in citta'.
_URBANI: list[tuple[str, str, str, list[str]]] = [
    ("atm_milano", "ATM", "Milano", ["milano", "monza", "saronno", "busto arsizio", "gallarate"]),
    ("atac_roma", "ATAC", "Roma", ["roma"]),
    ("gtt_torino", "GTT", "Torino", ["torino"]),
    ("actv_venezia", "ACTV", "Venezia", ["venezia", "mestre"]),
    ("tper_bologna", "Tper", "Bologna", ["bologna"]),
    ("metrocitta_napoli", "EAV / Metrocitta", "Napoli", ["napoli", "caserta"]),
    ("amt_genova", "AMT", "Genova", ["genova"]),
    ("autolinee_firenze", "Autolinee / TFT", "Firenze", ["firenze", "prato"]),
    ("actr_pisa", "ACTR / AT", "Pisa", ["pisa"]),
    ("amt_verona", "AMT", "Verona", ["verona"]),
    ("moovie_padova", "Moovie", "Padova", ["padova"]),
    ("sms_bergamo", "SMS / Trasporti Bergamo", "Bergamo", ["bergamo"]),
    ("atp_palermo", "ATP", "Palermo", ["palermo"]),
    ("amt_catania", "AMT", "Catania", ["catania"]),
    ("amt_cagliari", "AMT", "Cagliari", ["cagliari"]),
    ("asu_foggia", "Asu / RTP", "Foggia", ["foggia"]),
    ("autolinee_savona", "Autolinee / TPL", "Savona", ["savona", "imperia"]),
    ("atac_ancona", "ATRC", "Ancona", ["ancona"]),
]

# I pass integrati regionali (non ferroviari): coprono bus, tram e metro
# della regione, spesso anche i treni regionali.
_INTEGRATI: dict[str, dict[str, Any]] = {
    "ivol_lombardia": {
        "nome": "IVOL (Io Viaggio Ovunque in Lombardia)",
        "area": ["milano", "bergamo", "brescia", "como", "varese", "pavia", "monza", "lecco"],
        "raggio_km": 45,
        "copre": {"train", "bus", "tram", "transit"},
        "solo_regionali": True,
        "parziale": True,
        "nota": (
            "Autobus urbani e interurbani, tram, metropolitane, treni R, RV, RE, "
            "Malpensa Express e S (Suburbani) in seconda classe, nei limiti "
            "della tariffa regionale lombarda. Prezzo: 118 EUR/mese, "
            "341 EUR/trimestre, 1.137 EUR/anno."
        ),
        "fonte": "https://www.regione.lombardia.it/infrastrutture-trasporti-e-mobilita/trasporto-pubblico/biglietti-e-agevolazioni/io-viaggio-abbonamenti",
    },
    "aur_piemonte": {
        "nome": "AUR (Abbonamento Unico Regionale, Piemonte)",
        "area": ["torino", "novara", "alessandria", "asti", "cuneo"],
        "raggio_km": 45,
        "copre": {"train", "bus", "tram", "transit"},
        "solo_regionali": True,
        "parziale": True,
        "nota": "Treni regionali, bus urbani ed extraurbani, tram e metro del Piemonte.",
    },
    "salta_su_emilia": {
        "nome": "Salta Su (Emilia-Romagna)",
        "area": [
            "bologna", "parma", "modena", "reggio emilia", "ferrara", "rimini",
            "ravenna", "piacenza",
        ],
        "raggio_km": 45,
        "copre": {"train", "bus", "tram", "transit"},
        "solo_regionali": True,
        "parziale": True,
        "nota": (
            "Card Unica con abbonamento ferroviario regionale; la card "
            "\"Mi Muovo Tutto Treno\" apre anche gli Intercity sulla stessa "
            "tratta del proprio abbonamento regionale."
        ),
        "fonte": "https://mobilita.regione.emilia-romagna.it/saltasu/cosa-viene-offerto/abbonamento-ferroviario",
    },
    "umbria_vai": {
        "nome": "Umbria Vai (ex Pass TPL Umbria-UNIPG)",
        "area": ["perugia"],
        "raggio_km": 45,
        "copre": {"train", "bus", "tram", "transit"},
        "solo_regionali": True,
        "parziale": True,
        "nota": "Bus urbani ed extraurbani dell'Umbria e treni regionali (dal 2026 anche per chi non e' studente).",
        "fonte": "https://www.umbriamobilita.it/",
    },
    "fvg_unico": {
        "nome": "Abbonamento unico FVG (Tpl Fvg)",
        "area": ["trieste", "udine"],
        "raggio_km": 45,
        "copre": {"train", "bus"},
        "solo_regionali": True,
        "parziale": True,
        "nota": "Dal maggio 2026 un unico abbonamento per i treni Trenitalia e gli autobus extraurbani regionali (app MaasFVG).",
        "fonte": "https://tplfvg.it/it/abbonamenti/abbonamenti-integrati-trenobus",
    },
    "toscana_mobilita": {
        "nome": "Toscana Mobilita",
        "area": ["firenze", "pisa", "livorno", "lucca", "siena", "arezzo"],
        "raggio_km": 45,
        "copre": {"train", "bus", "tram", "transit"},
        "solo_regionali": True,
        "parziale": True,
        "nota": "Autobus urbani ed extraurbani e treni regionali della Toscana.",
    },
    "altoadigemobilita": {
        "nome": "altoadigemobilita (Alto Adige Mobilita)",
        "area": ["bolzano", "bressanone", "cortina d'ampezzo", "trento"],
        "raggio_km": 45,
        "copre": {"train", "bus", "tram", "transit"},
        "solo_regionali": True,
        "parziale": True,
        "nota": (
            "Tutti i mezzi di sudtirolmobil: treni regionali delle 4 linee "
            "(Brennero-Bolzano-Trento, Val Venosta, Merano-Bolzano, Val Pusteria), "
            "autobus urbani ed extraurbani, impianti di risalita, trenino del "
            "Renon, funicolare della Mendola. Fix365 250 EUR/anno, Fix30 "
            "39 EUR/mese, U26 153 EUR/anno o 21 EUR/mese, U19 max 21 EUR/anno."
        ),
        "fonte": "https://www.suedtirolmobil.info/it/ticket/altoadigemobilita-pass-la-connessione-di-cui-hai-bisogno",
    },
    "euregio_ticket": {
        "nome": "Euregio Ticket (Trentino, Alto Adige, Tirolo)",
        "area": ["trento", "bolzano", "bressanone", "cortina d'ampezzo"],
        "raggio_km": 60,
        "copre": {"train", "bus", "tram", "transit"},
        "solo_regionali": True,
        "parziale": True,
        "nota": (
            "In Alto Adige treni regionali, autobus urbani e regionali e le "
            "funivie; in Trentino treni regionali, autobus e la funivia "
            "Trento-Sardagna; in Tirolo tutta la rete VVT. 750 EUR/anno."
        ),
    },
    "trentino_provinciale": {
        "nome": "Abbonamento provinciale Trentino",
        "area": ["trento"],
        "raggio_km": 60,
        "copre": {"train", "bus", "tram", "transit"},
        "solo_regionali": True,
        "parziale": True,
        "nota": (
            "Tutta la rete provinciale: autobus urbani ed extraurbani di "
            "Trentino Trasporti, ferrovia Trento-Male, ferrovia della "
            "Valsugana (Trento-Primolano), linea del Brennero "
            "(Borghetto-Ora) e funivia Trento-Sardagna."
        ),
    },
    "guest_card_trentino": {
        "nome": "Guest Card Trentino / Val di Sole",
        "area": ["trento"],
        "raggio_km": 60,
        "copre": {"train", "bus", "tram", "transit"},
        "solo_regionali": True,
        "parziale": True,
        "nota": "Viaggio gratuito sul trenino Trento-Male e sui bus del Trentino.",
    },
}

# I pass ferroviari nazionali e delle compagnie.
_FERROVIARI: dict[str, dict[str, Any]] = {
    "trenitalia_pass": {
        "nome": "Trenitalia Pass",
        "copre": {"train"},
        "solo_regionali": False,
        "nota": (
            "Destinato ai viaggiatori stranieri: Frecce (Standard, Premium), "
            "FrecciaLink, Intercity e Intercity Notte, EuroCity Italia-Svizzera."
        ),
        "fonte": "https://www.trenitalia.com/it/abbonamenti-pass-e-carnet/trenitalia-pass.html",
    },
    "interrail": {
        "nome": "Interrail Pass",
        "copre": {"train"},
        "solo_regionali": False,
        "nota": "Pass ferroviario europeo: valido sui treni italiani iscritti al.pass.",
    },
    "carta_plus_lombardia": {
        "nome": "Carta Plus Lombardia (Trenord)",
        "area": ["milano", "bergamo", "brescia", "como", "varese", "pavia", "monza", "lecco"],
        "raggio_km": 45,
        "copre": {"train"},
        "solo_regionali": False,
        "parziale": True,
        "nota": (
            "Accesso in 2a classe ai treni a lunga percorrenza Trenitalia "
            "(Frecciabianca, Intercity, Intercity Notte, EuroCity): esclude "
            "Frecciarossa e Frecciargento."
        ),
        "fonte": "https://www.trenord.it/biglietti/agevolazioni-e-supplementi/carta-plus-lombardia",
    },
    "italo_carnet": {
        "nome": "Carnet Italo (10 viaggi)",
        "copre": {"train"},
        "solo_regionali": False,
        "consuma": True,
        "nota": "10 viaggi sui treni Italo (NTV) con sconto fino al 30%: consuma 1 biglietto a ogni viaggio.",
    },
}

PASS_TYPES: dict[str, dict[str, Any]] = {}

# 1) Gli abbonamenti regionali Trenitalia: uno per regione.
for _regione, _citta in _REGIONI_TRENITALIA.items():
    PASS_TYPES[f"trenitalia_{_regione}"] = {
        "nome": f"Abbonamento regionale Trenitalia {NOMI_REGIONI.get(_regione, _regione)}",
        "tipo": "ferrovia",
        "area": _citta,
        "raggio_km": 45,
        "copre": {"train"},
        "solo_regionali": True,
        "parziale": True,
        "nota": (
            f"Treni regionali della {_regione} (tariffa 40/xx): copre solo "
            "quella parte del viaggio, e solo in seconda classe."
        ),
    }

# 2) I pass integrati regionali.
for _chiave, _dati in _INTEGRATI.items():
    PASS_TYPES[_chiave] = {**_dati, "tipo": "regione"}

# 3) I pass ferroviari nazionali.
for _chiave, _dati in _FERROVIARI.items():
    PASS_TYPES[_chiave] = {**_dati, "tipo": "ferrovia"}

# 4) Gli abbonamenti urbani.
for _chiave, _operatore, _citta, _area in _URBANI:
    PASS_TYPES[_chiave] = {
        "nome": f"Abbonamento urbano {_operatore} ({_citta})",
        "tipo": "citta",
        "area": _area,
        "raggio_km": 15,
        "copre": {"bus", "tram", "transit"},
        "solo_regionali": False,
        "parziale": True,
        "nota": f"Rete urbana di {_citta} (bus, tram, metro) e linee extraurbane con capolinea in citta'.",
    }

# 5) I titoli generici e i carnet.
PASS_TYPES.update(
    {
        "trenitalia_regionale": {
            "nome": "Abbonamento regionale Trenitalia (quello di casa)",
            "tipo": "ferrovia",
            "copre": {"train"},
            "solo_regionali": True,
            "parziale": True,
            "nota": (
                "Vale sui treni regionali della regione in cui e' stato preso: "
                "l'area viene dedotta dal punto di partenza."
            ),
        },
        "trenitalia_tutti": {
            "nome": "Abbonamento Trenitalia (tutti i treni)",
            "tipo": "ferrovia",
            "copre": {"train"},
            "solo_regionali": False,
            "nota": "Copre tutti i treni, Frecce e Intercity comprese.",
        },
        "carnet_regionale": {
            "nome": "Carnet 10 biglietti regionali",
            "tipo": "ferrovia",
            "copre": {"train", "bus"},
            "solo_regionali": True,
            "parziale": True,
            "consuma": True,
            "nota": "Consuma 1 biglietto del carnet a ogni viaggio.",
        },
        "carnet_bus": {
            "nome": "Carnet 10 biglietti bus",
            "tipo": "ferrovia",
            "copre": {"bus"},
            "solo_regionali": False,
            "consuma": True,
            "nota": "Consuma 1 biglietto del carnet a ogni viaggio.",
        },
        "urbano": {
            "nome": "Abbonamento urbano (bus/metro/tram)",
            "tipo": "citta",
            "copre": {"bus", "tram", "transit"},
            "solo_regionali": False,
            "nota": "Copre i mezzi urbani della citta' in cui e' attivo.",
        },
        "carnet_urbano": {
            "nome": "Carnet 10 biglietti urbani",
            "tipo": "citta",
            "copre": {"bus", "tram", "transit"},
            "solo_regionali": False,
            "consuma": True,
            "nota": "Consuma 1 biglietto del carnet a ogni viaggio.",
        },
    }
)

MASK = "••••••••"

# Treni su cui l'abbonamento regionale NON vale (fonte Trenitalia: "validi
# esclusivamente sui treni regionali: Regionale, Regionale Veloce e Regionale
# Express. Non sono accettati su Frecciarossa, Frecciargento, Frecciabianca
# o Intercity"). Ci metto anche le Frecce Regionale (ibride premium) e i
# treni degli operatori privati (Italo/NTV).
NON_REGIONALI = (
    "frecciarossa",
    "frecciargento",
    "frecciabianca",
    "frecce regionale",
    "intercity",
    "eurocity",
    "italo",
    "ntv",
    "notte",
    "night",
)
# Compagnie che fanno solo treni regionali.
OPERATORI_REGIONALI = (
    "trenord",
    "tper",
    "fse",
    "suedtirolmobil",
    "südtirolmobil",
    "taf",
    "arst",
    "ferrovie del sud est",
    "treni del sud est",
    "tpl",
    "fvg",
    "stazione ferroviaria",
    "trenitalia tper",
)


def trova_per_testo(testo: str) -> str | None:
    """Riconosce un abbonamento scritto a parole.

    "ho l'abbonamento alto adige mobilita" -> "altoadigemobilita".
    """
    if not testo:
        return None
    basso = " ".join(testo.lower().split())
    # 1) le parole distintive del nome: il segnale piu' forte
    #    ("abbonamento urbano di Venezia" -> quello di Venezia, non quello
    #    generico).
    migliore: tuple[int, int, str] | None = None
    for chiave, dati in PASS_TYPES.items():
        parole = [
            parola
            for parola in "".join(
                c if c.isalnum() else " " for c in dati.get("nome", "").lower()
            ).split()
            if len(parola) > 3
            and parola
            not in ("abbonamento", "urbano", "regionale", "trenitalia", "carnet", "pass")
        ]
        if not parole:
            continue
        trovate = [parola for parola in parole if parola in basso]
        if not trovate:
            continue
        # A parita' di punteggio vince il nome piu' corto (quello piu'
        # specifico: "urbano ACTV (Venezia)" batte "regionale Friuli Venezia
        # Giulia").
        punteggio = (sum(len(parola) for parola in trovate), -len(dati.get("nome", "")))
        if migliore is None or punteggio > migliore[:2]:
            migliore = (*punteggio, chiave)
    if migliore:
        return migliore[2]
    # 2) l'identificatore esatto (con i confini di parola: "urbano" non deve
    #    stare dentro "abbonamento urbano di Venezia")
    for chiave in PASS_TYPES:
        if re.search(r"(?<![a-z0-9])" + re.escape(chiave) + r"(?![a-z0-9])", basso):
            return chiave
    return None


def pass_name(pass_type: str) -> str:
    entry = PASS_TYPES.get(pass_type)
    return entry["nome"] if entry else str(pass_type or "Abbonamento")


def pass_type(pass_type: str) -> str:
    entry = PASS_TYPES.get(pass_type)
    return entry.get("tipo", "altro") if entry else "altro"


def pass_note(pass_type: str) -> str:
    entry = PASS_TYPES.get(pass_type)
    return entry.get("nota", "") if entry else ""


def pass_source(pass_type: str) -> str:
    entry = PASS_TYPES.get(pass_type)
    return entry.get("fonte", "") if entry else ""


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
        if not tipo or tipo not in PASS_TYPES:
            continue
        out.append(
            {
                "tipo": tipo,
                "nome": str(item.get("nome") or pass_name(tipo))[:90],
                "valido_alla": _as_date(item.get("valido_alla")),
            }
        )
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


def _is_regional_train(offer: Any) -> bool:
    """True se l'offerta e' fatta solo di treni regionali.

    Si guarda sia la categoria del treno (delle API Trenitalia) sia la
    compagnia (delle API Omio, che danno il nome dell'operatore).
    """
    details = getattr(offer, "details", None) or {}
    categorie = str(details.get("treni") or "")
    compagnia = str(details.get("compagnia") or getattr(offer, "operator", "") or "")
    testo = f"{categorie} {compagnia}".lower()
    if any(parola in testo for parola in NON_REGIONALI):
        return False
    if any(parola in categorie.lower() for parola in ("regionale", "regio")):
        return True
    if any(op in compagnia.lower() for op in OPERATORI_REGIONALI):
        return True
    return False


def _area_points(pass_info: dict[str, Any]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for name in pass_info.get("area") or []:
        coords = CITIES.get(str(name).lower())
        if coords:
            points.append((coords[0], coords[1]))
    return points


def _distance_to_area(
    lat: float | None, lon: float | None, points: list[tuple[float, float]], radius: float
) -> float | None:
    """Distanza dal punto all'area coperta (0 se dentro)."""
    if lat is None or lon is None or not points:
        return None
    best = None
    for plat, plon in points:
        dist = haversine_km(lat, lon, plat, plon)
        if best is None or dist < best:
            best = dist
    if best is None:
        return None
    return max(0.0, best - radius)


def _station_coords(name: Any) -> tuple[float, float] | None:
    """Le coordinate di una stazione, cercando fra le citta' note."""
    if not name:
        return None
    testo = str(name).strip().lower()
    if not testo:
        return None
    coords = CITIES.get(testo)
    if coords:
        return coords
    # "Verona Porta Nuova" -> "verona", "Bassano Del Grappa" -> la piu' vicina
    migliore: tuple[int, tuple[float, float]] | None = None
    for chiave, punto in CITIES.items():
        if testo.startswith(chiave + " ") or (" " + chiave) in testo:
            if migliore is None or len(chiave) > migliore[0]:
                migliore = (len(chiave), punto)
    if migliore:
        return migliore[1]
    return None


def _route_points(offer: Any) -> list[tuple[float, float]]:
    """I punti del percorso: partenza, le tappe intermedie, arrivo."""
    punti: list[tuple[float, float]] = []
    if getattr(offer, "pickup_lat", None) is not None:
        punti.append((offer.pickup_lat, offer.pickup_lon))
    for tappa in (getattr(offer, "details", None) or {}).get("tappe") or []:
        if not isinstance(tappa, dict):
            continue
        for chiave in ("da", "a"):
            punto = _station_coords(tappa.get(chiave))
            if punto:
                punti.append(punto)
    if getattr(offer, "dropoff_lat", None) is not None:
        punti.append((offer.dropoff_lat, offer.dropoff_lon))
    unici: list[tuple[float, float]] = []
    for punto in punti:
        if not unici or unici[-1] != punto:
            unici.append(punto)
    return unici


def _segment_covered(
    primo: tuple[float, float],
    secondo: tuple[float, float],
    punti_area: list[tuple[float, float]],
    raggio: float,
) -> tuple[float, float]:
    """Quanti km del tratto sono dentro l'area (e quanto e' lungo il tratto)."""
    lunghezza = haversine_km(primo[0], primo[1], secondo[0], secondo[1])
    if not lunghezza:
        return 0.0, 0.0
    fuori_primo = _distance_to_area(primo[0], primo[1], punti_area, raggio)
    fuori_secondo = _distance_to_area(secondo[0], secondo[1], punti_area, raggio)
    if fuori_primo is None or fuori_secondo is None:
        return 0.0, lunghezza
    if fuori_primo == 0 and fuori_secondo == 0:
        return lunghezza, lunghezza
    if fuori_primo == 0:
        return max(0.0, lunghezza - fuori_secondo), lunghezza
    if fuori_secondo == 0:
        return max(0.0, lunghezza - fuori_primo), lunghezza
    # Il tratto non tocca l'area: niente coperto (stima prudente).
    return 0.0, lunghezza


def regione_di(lat: float | None, lon: float | None) -> str | None:
    """La regione in cui si trova un punto (per le condizioni degli sconti)."""
    if lat is None or lon is None:
        return None
    for regione, citta in _REGIONI_TRENITALIA.items():
        for nome in citta:
            coords = CITIES.get(nome)
            if not coords:
                continue
            dist = haversine_km(lat, lon, coords[0], coords[1])
            # Attenzione: la distanza 0 e' un valore valido (non usare
            # "or 1e9", che la tratterebbe come falsa).
            if dist is not None and dist <= 45:
                return regione
    return None


def _regione_del_viaggio(offer: Any) -> list[tuple[float, float]]:
    """Le citta' della regione in cui parte il viaggio.

    Serve per l'abbonamento regionale "quello di casa": la regione si deduce
    dal punto di partenza.
    """
    lat = getattr(offer, "pickup_lat", None)
    lon = getattr(offer, "pickup_lon", None)
    if lat is None or lon is None:
        return []
    for _regione, citta in _REGIONI_TRENITALIA.items():
        punti = []
        for nome in citta:
            coords = CITIES.get(nome)
            if coords:
                punti.append(coords)
        if not punti:
            continue
        for plat, plon in punti:
            dist = haversine_km(lat, lon, plat, plon)
            if dist is not None and dist <= 45:
                return punti
    return []


def coverage(
    pass_info: dict[str, Any], offer: Any, when: date | None = None
) -> tuple[float, str]:
    """Quota del viaggio coperta dall'abbonamento (0-1) e la zona.

    Per gli abbonamenti regionali la copertura e' parziale: si somma, tratto
    per tratto, la parte di viaggio che cade dentro l'area dell'abbonamento.
    Un treno che entra in Trentino a meta' percorso e' coperto solo da li'.
    """
    if _is_expired(pass_info, when):
        return 0.0, ""
    tipo = PASS_TYPES.get(str(pass_info.get("tipo") or ""))
    if not tipo:
        return 0.0, ""
    mode = str(getattr(offer, "mode", "") or "")
    if mode not in tipo["copre"]:
        return 0.0, ""
    if tipo.get("solo_regionali") and mode == "train" and not _is_regional_train(offer):
        return 0.0, ""

    if not tipo.get("parziale"):
        return 1.0, "tutto il viaggio"

    punti_area = _area_points(tipo)
    if not punti_area and str(pass_info.get("tipo")) == "trenitalia_regionale":
        # Abbonamento regionale "quello di casa": l'area e' quella da cui
        # parte il viaggio.
        punti_area = _regione_del_viaggio(offer)
    if not punti_area:
        return 0.0, ""

    raggio = float(tipo.get("raggio_km") or 40)
    percorso = _route_points(offer)
    if len(percorso) < 2:
        return 0.0, ""

    coperto = 0.0
    totale = 0.0
    for primo, secondo in zip(percorso, percorso[1:]):
        km_coperti, km_totali = _segment_covered(primo, secondo, punti_area, raggio)
        coperto += km_coperti
        totale += km_totali
    if not totale:
        return 0.0, ""
    quota = min(1.0, coperto / totale)
    if quota <= 0.01:
        return 0.0, ""
    return quota, "parte del viaggio coperta"


def pass_covers(pass_info: dict[str, Any], offer: Any, when: date | None = None) -> bool:
    """True se l'abbonamento copre l'offerta (anche in parte)."""
    quota, _ = coverage(pass_info, offer, when)
    return quota > 0.01


def apply_passes(
    passes: list[dict[str, Any]], offer: Any, when: date | None = None
) -> Any:
    """Applica gli abbonamenti: il costo effettivo e' la parte non coperta."""
    if when is None:
        depart = getattr(offer, "depart_at", None)
        if isinstance(depart, datetime):
            when = depart.date()
    migliore: tuple[float, dict[str, Any], str] | None = None
    for pass_info in passes:
        quota, zona = coverage(pass_info, offer, when)
        if quota <= 0.01:
            continue
        if migliore is None or quota > migliore[0]:
            migliore = (quota, pass_info, zona)
    if migliore is None:
        return offer
    quota, pass_info, zona = migliore
    tipo = PASS_TYPES.get(str(pass_info.get("tipo")), {})
    details = dict(getattr(offer, "details", None) or {})
    details["pass"] = pass_info.get("nome") or pass_name(str(pass_info.get("tipo")))
    details["pass_tipo"] = pass_info.get("tipo")
    details["pass_quota"] = round(quota * 100)
    details["pass_zona"] = zona
    if tipo.get("consuma"):
        details["pass_note"] = "consuma 1 biglietto del carnet"
    if quota < 0.99:
        details["pass_note_parziale"] = (
            f"copre solo il {round(quota * 100)}% del viaggio"
        )
    details["net_cost"] = round((offer.price or 0.0) * (1 - quota), 2)
    offer.details = details
    offer.net_cost = details["net_cost"]
    return offer


def has_urban_pass(passes: list[dict[str, Any]], when: date | None = None) -> bool:
    """C'è un abbonamento che copre bus/tram/transit cittadini."""
    for pass_info in passes:
        tipo = PASS_TYPES.get(str(pass_info.get("tipo") or ""))
        if not tipo or _is_expired(pass_info, when):
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
