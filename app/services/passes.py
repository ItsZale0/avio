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

from ..geo import CITIES, haversine_km, normalize
from . import rail_km

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
            "39 EUR/mese, U26 153 EUR/anno o 21 EUR/mese, U19 21 EUR/anno "
            "(gratis con Fix365), 65+ 21, 77 o 153 EUR/anno. Se invece "
            "paghi a km, il pass e' l'altoadigemobilita Flex."
        ),
        "fonte": "https://www.suedtirolmobil.info/it/ticket/altoadigemobilita-pass-la-connessione-di-cui-hai-bisogno",
    },
    "altoadigemobilita_flex": {
        "nome": "altoadigemobilita Flex (tariffa chilometrica)",
        "area": [
            "bolzano",
            "merano",
            "bressanone",
            "brunico",
            "silandro",
            "egna",
            "vipiteno",
            "laives",
            "san candido",
            "chiusa",
            "fortezza",
            "ora",
            "malles",
            "dobbiaco",
            "lana",
            "terlano",
            "san lorenzo di sebato",
            "valdaora",
            "monguelfo",
            "villabassa",
            "laces",
            "naturno",
            "castelbello",
        ],
        "raggio_km": 20,
        "copre": {"train", "bus", "tram", "transit"},
        "solo_regionali": True,
        "parziale": True,
        "chiavi": ["flex", "altoadigemobilita flex"],
        "tariffa_km": {
            "fasce": [
                (1000, 0.13),
                (2000, 0.08),
                (10000, 0.03),
                (20000, 0.02),
                (None, 0.0),
            ],
            "min_km": 0,
            "urbano_km": 10.0,
            "urbano_km_altre": 5.0,
            "tetto_giornaliero": 22.0,
        },
        "nota": (
            "Pass annuale a tariffa chilometrica: 0,13 EUR/km nella prima "
            "fascia, poi 0,08, 0,03, 0,02 e infine 0 EUR/km. Massimo 650 "
            "EUR/anno e massimo 22 EUR al giorno. Per le connessioni urbane "
            "si contano 10 km tariffari a Bolzano e Merano, 5 km nelle altre "
            "zone; sulle tratte extraurbane si pagano i km percorsi. Vale "
            "sui treni, sugli autobus e sulle funivie dell'Alto Adige e sui "
            "treni regionali fino a Trento: oltre Trento serve un altro "
            "biglietto."
        ),
        "fonte": "https://www.suedtirolmobil.info/it/ticket/altoadigemobilita-pass-la-connessione-di-cui-hai-bisogno",
    },
    "altoadigemobilita_flexfamily": {
        "nome": "altoadigemobilita FlexFamily (famiglie, tariffa chilometrica)",
        "area": [
            "bolzano",
            "merano",
            "bressanone",
            "brunico",
            "silandro",
            "egna",
            "vipiteno",
            "laives",
            "san candido",
            "chiusa",
            "fortezza",
            "ora",
            "malles",
            "dobbiaco",
            "lana",
            "terlano",
            "san lorenzo di sebato",
            "valdaora",
            "monguelfo",
            "villabassa",
            "laces",
            "naturno",
            "castelbello",
        ],
        "raggio_km": 20,
        "copre": {"train", "bus", "tram", "transit"},
        "solo_regionali": True,
        "parziale": True,
        "chiavi": ["flexfamily", "family pass"],
        "tariffa_km": {
            "fasce": [
                (1000, 0.11),
                (2000, 0.07),
                (10000, 0.02),
                (20000, 0.02),
                (None, 0.0),
            ],
            "min_km": 0,
            "urbano_km": 10.0,
            "urbano_km_altre": 5.0,
            "tetto_giornaliero": 22.0,
        },
        "nota": (
            "Pass annuale personale per famiglie (ex Euregio Family Pass) a "
            "tariffa chilometrica ridotta: 0,11 EUR/km nella prima fascia, "
            "poi 0,07, 0,02, 0,02 e 0. Massimo 540 EUR/anno e 22 EUR al "
            "giorno. Connessioni urbane: 10 km tariffari a Bolzano e Merano, "
            "5 km nelle altre zone. Tessera prepagata: ricarica minima 20 "
            "EUR, credito minimo 10 EUR."
        ),
        "fonte": "https://www.suedtirolmobil.info/it/ticket/trovaticket/altoadigemobilita-flexfamily-pass",
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
    # 0) le parole chiave esplicite: il segnale piu' forte in assoluto
    #    ("ho il pass flex" -> altoadigemobilita Flex, non l'abbonamento
    #    generico altoadigemobilita).
    migliore_chiave: tuple[int, str] | None = None
    for chiave, dati in PASS_TYPES.items():
        for parola in dati.get("chiavi") or []:
            parola = parola.lower()
            if re.search(r"(?<![a-z0-9])" + re.escape(parola) + r"(?![a-z0-9])", basso):
                if migliore_chiave is None or len(parola) > migliore_chiave[0]:
                    migliore_chiave = (len(parola), chiave)
    if migliore_chiave:
        return migliore_chiave[1]
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


# I centri dell'area di un abbonamento non cambiano mai: si calcolano una
# volta sola (la copertura viene ricalcolata per ogni offerta).
_AREA_CACHE: dict[str, list[tuple[float, float]]] = {}


def _area_points(pass_info: dict[str, Any]) -> list[tuple[float, float]]:
    tipo_id = str(pass_info.get("tipo") or "")
    if tipo_id in _AREA_CACHE:
        return _AREA_CACHE[tipo_id]
    points: list[tuple[float, float]] = []
    for name in pass_info.get("area") or []:
        coords = CITIES.get(str(name).lower())
        if coords:
            points.append((coords[0], coords[1]))
    _AREA_CACHE[tipo_id] = points
    return points


def _station_coords(name: Any) -> tuple[float, float] | None:
    """Le coordinate di una stazione, cercando fra le citta' note.

    I nomi arrivano in tante forme: "Bolzano", "Bolzano/Bozen",
    "Bressanone Brixen", "Verona Porta Nuova". Si normalizza e poi si
    cerca il nome piu' lungo contenuto (le bilingui diventano "bolzano
    bozen", che riconosce "bolzano").
    """
    if not name:
        return None
    testo = normalize(str(name)).replace("/", " ")
    testo = " ".join(testo.split())
    if not testo:
        return None
    coords = CITIES.get(testo)
    if coords:
        return coords
    migliore: tuple[int, tuple[float, float]] | None = None
    for chiave, punto in CITIES.items():
        if chiave in testo.split() or testo.startswith(chiave + " ") or (" " + chiave) in testo:
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


# Quanti punti si campionano su ogni tratto del percorso.
_CAMPIONI_TRATTO = 12

# I binari non sono una linea retta: quando la stazione non e' nella
# tabella dei km tariffari (app/services/rail_km.py) la distanza in linea
# d'aria va corretta. Per l'Alto Adige la tabella ufficiale vale sempre
# (Bressanone-Bolzano 38,1 km, non 32,8), questo resta il ripiego.
FATTORE_ROTAIA = 1.13

# Le strade curvano piu' dei binari: per gli autobus senza fermate
# intermedie la retta va corretta di piu'.
FATTORE_STRADA = 1.2


def _punto_dentro(
    lat: float, lon: float, punti_area: list[tuple[float, float]], raggio: float
) -> bool:
    """Il punto e' dentro l'area (distanza da uno dei centri <= raggio)?"""
    for plat, plon in punti_area:
        dist = haversine_km(lat, lon, plat, plon)
        if dist is not None and dist <= raggio:
            return True
    return False


def _segment_covered(
    primo: tuple[float, float],
    secondo: tuple[float, float],
    punti_area: list[tuple[float, float]],
    raggio: float,
    fattore: float = 1.0,
) -> tuple[float, float]:
    """Quanti km del tratto sono dentro l'area (e quanto e' lungo il tratto).

    Il tratto viene campionato: conta la parte che cade dentro l'area, non
    una stima dagli estremi (che su un tratto lungo sbaglia di parecchio).
    Il fattore corregge la distanza in linea d'aria (rotaia, non retta).
    """
    lunghezza = haversine_km(primo[0], primo[1], secondo[0], secondo[1]) * fattore
    if not lunghezza:
        return 0.0, 0.0
    if not punti_area:
        return 0.0, lunghezza
    dentro = 0
    for i in range(_CAMPIONI_TRATTO + 1):
        t = i / _CAMPIONI_TRATTO
        lat = primo[0] + (secondo[0] - primo[0]) * t
        lon = primo[1] + (secondo[1] - primo[1]) * t
        if _punto_dentro(lat, lon, punti_area, raggio):
            dentro += 1
    quota = dentro / (_CAMPIONI_TRATTO + 1)
    return lunghezza * quota, lunghezza


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


def _copertura_base(
    pass_info: dict[str, Any], offer: Any, when: date | None = None
) -> tuple[float, float, str, bool] | None:
    """Quanto del viaggio copre l'abbonamento, misurato in km.

    Ritorna (km coperti, km totali, zona, copertura completa) oppure None se
    l'abbonamento non copre niente. Per gli abbonamenti regionali la copertura
    e' parziale: si somma, tratto per tratto, la parte di viaggio che cade
    dentro l'area dell'abbonamento. Un treno che entra in Trentino a meta'
    percorso e' coperto solo da li'.
    """
    if _is_expired(pass_info, when):
        return None
    tipo = PASS_TYPES.get(str(pass_info.get("tipo") or ""))
    if not tipo:
        return None
    mode = str(getattr(offer, "mode", "") or "")
    if mode not in tipo["copre"]:
        return None
    if tipo.get("solo_regionali") and mode == "train" and not _is_regional_train(offer):
        return None

    # I treni si prezzano sui km tariffari veri (tabella RFI): la retta
    # corretta a occhio sbagliava del 10-30% sulle tratte lunghe (Merano-
    # Bolzano 27,7 invece di 31,5; Bressanone-Bolzano 37,0 invece di 38,1).
    ferroviaria = None
    if mode == "train":
        ferroviaria = rail_km.copertura(
            getattr(offer, "pickup_name", None), getattr(offer, "dropoff_name", None)
        )

    if not tipo.get("parziale"):
        # Abbonamento valido su tutto il viaggio: conta solo la lunghezza.
        if ferroviaria is not None:
            lunghezza = ferroviaria[1]
        else:
            lunghezza = 0.0
            for primo, secondo in zip(_route_points(offer), _route_points(offer)[1:]):
                lunghezza += (
                    haversine_km(primo[0], primo[1], secondo[0], secondo[1]) * FATTORE_ROTAIA
                )
        return lunghezza, lunghezza, "tutto il viaggio", True

    if ferroviaria is not None:
        km_coperti, km_totali, completo = ferroviaria
        if km_coperti <= 0.01:
            return None
        return km_coperti, km_totali, "rete ferroviaria provinciale", completo

    percorso = _route_points(offer)
    # Strade e ferrovie non sono rette. Per i treni (quando la stazione non
    # e' in tabella) si usa il fattore rotaia; per gli autobus la strada
    # curva di piu', ma se l'offerta elenca gia' le fermate la spezzata e'
    # gia' realistica.
    tappe = (getattr(offer, "details", None) or {}).get("tappe") or []
    if mode == "train":
        fattore = FATTORE_ROTAIA
    elif mode in ("bus", "tram", "transit") and len(tappe) < 2:
        fattore = FATTORE_STRADA
    else:
        fattore = 1.0

    punti_area = _area_points(tipo)
    if not punti_area and str(pass_info.get("tipo")) == "trenitalia_regionale":
        # Abbonamento regionale "quello di casa": l'area e' quella da cui
        # parte il viaggio.
        punti_area = _regione_del_viaggio(offer)
    if not punti_area:
        return None
    if len(percorso) < 2:
        return None

    raggio = float(tipo.get("raggio_km") or 40)
    coperto = 0.0
    totale = 0.0
    for primo, secondo in zip(percorso, percorso[1:]):
        km_coperti, km_totali = _segment_covered(primo, secondo, punti_area, raggio, fattore)
        coperto += km_coperti
        totale += km_totali
    if not totale:
        return None
    return coperto, totale, "parte del viaggio coperta", False


def coverage(
    pass_info: dict[str, Any], offer: Any, when: date | None = None
) -> tuple[float, str]:
    """Quota del viaggio coperta dall'abbonamento (0-1) e la zona."""
    base = _copertura_base(pass_info, offer, when)
    if base is None:
        return 0.0, ""
    km_coperti, km_totali, zona, completo = base
    if completo:
        return 1.0, zona
    quota = min(1.0, km_coperti / km_totali)
    if quota <= 0.01:
        return 0.0, ""
    return quota, zona


def _zona_urbana(offer: Any) -> str | None:
    """La citta' della zona tariffaria, se il viaggio e' una connessione urbana.

    Le "connessioni urbane" del Flex pagano un numero fisso di km
    tariffari (10 a Bolzano e Merano, 5 altrove), non i km percorsi. Una
    connessione e' urbana quando entrambi i capi stanno nella stessa
    citta': un autobus Bolzano-Merano non lo e'.
    """
    mode = str(getattr(offer, "mode", "") or "")
    if mode not in ("bus", "tram", "transit"):
        return None
    lat1 = getattr(offer, "pickup_lat", None)
    lon1 = getattr(offer, "pickup_lon", None)
    lat2 = getattr(offer, "dropoff_lat", None)
    lon2 = getattr(offer, "dropoff_lon", None)
    if None in (lat1, lon1, lat2, lon2):
        return None
    for nome, (clat, clon) in CITIES.items():
        raggio = 8.0 if nome in ("bolzano", "merano") else 6.0
        da = haversine_km(lat1, lon1, clat, clon)
        a = haversine_km(lat2, lon2, clat, clon)
        if da is not None and a is not None and da <= raggio and a <= raggio:
            return nome
    return None


def _costo_tariffa_km(
    tariffa: dict[str, Any], km_coperti: float, offer: Any = None
) -> tuple[float, float, str]:
    """Costo in euro di un pass a tariffa chilometrica.

    Ritorna (costo, km tariffati, descrizione). Gli scaglioni sono annuali e
    l'app non sa quanti km hai gia' percorso quest'anno: usa il primo
    scaglione, il piu' caro, quindi la stima non e' mai ottimista. Per le
    connessioni urbane valgono i km tariffari fissi della zona (10 a
    Bolzano e Merano, 5 nelle altre), come da tariffa 41/3.
    """
    fasce = tariffa.get("fasce") or []
    prezzo_km = float(fasce[0][1]) if fasce else 0.0
    min_km = float(tariffa.get("min_km") or 0)
    tetto = float(tariffa.get("tetto_giornaliero") or 0)
    km_tariffati = max(min_km, km_coperti)
    citta = _zona_urbana(offer) if offer is not None else None
    if citta:
        if citta in ("bolzano", "merano"):
            km_urbani = float(tariffa.get("urbano_km") or 0)
        else:
            km_urbani = float(tariffa.get("urbano_km_altre") or 0)
        if km_urbani:
            km_tariffati = km_urbani
    costo = km_tariffati * prezzo_km
    if tetto:
        costo = min(costo, tetto)
    descrizione = f"{prezzo_km:.2f} EUR/km (primo scaglione)"
    return costo, km_tariffati, descrizione


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
    migliore: tuple[float, dict[str, Any], str, float] | None = None
    for pass_info in passes:
        base = _copertura_base(pass_info, offer, when)
        if base is None:
            continue
        km_coperti, km_totali, zona, completo = base
        quota = 1.0 if completo else (min(1.0, km_coperti / km_totali) if km_totali else 0.0)
        if quota <= 0.01:
            continue
        if migliore is None or quota > migliore[0]:
            migliore = (quota, pass_info, zona, km_coperti)
    if migliore is None:
        return offer
    quota, pass_info, zona, km_coperti = migliore
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
    tariffa = tipo.get("tariffa_km")
    if tariffa:
        # Pass a tariffa chilometrica: la parte coperta non e' gratis, si
        # paga il chilometro. La parte scoperta si paga come sempre.
        costo_km, km_tariffati, descrizione = _costo_tariffa_km(
            tariffa, km_coperti, offer
        )
        parte_scoperta = (offer.price or 0.0) * (1 - quota)
        # Non si paga mai piu' del biglietto.
        details["net_cost"] = round(min(offer.price or 0.0, parte_scoperta + costo_km), 2)
        details["pass_tariffa"] = descrizione
        details["pass_km"] = round(km_tariffati, 1)
        details["pass_costo_km"] = round(costo_km, 2)
    else:
        details["net_cost"] = round((offer.price or 0.0) * (1 - quota), 2)
    offer.details = details
    offer.net_cost = details["net_cost"]
    return offer


def km_tratta(da: str, a: str) -> float | None:
    """Km tariffari del tratto: tabella RFI, altrimenti stima geografica."""
    distanza = rail_km.km(da, a)
    if distanza:
        return distanza
    coords_da = _station_coords(da)
    coords_a = _station_coords(a)
    if not coords_da or not coords_a:
        return None
    return round(haversine_km(*coords_da, *coords_a) * FATTORE_ROTAIA, 1)


def netto_tratta(
    passes: list[dict[str, Any]],
    da: str,
    a: str,
    prezzo: float,
    quando: date | None = None,
    *,
    intera: bool = True,
) -> tuple[float, str | None]:
    """Costo di un biglietto singolo con gli abbonamenti: (costo, nome pass).

    Serve alla spezzatura: ogni biglietto si paga a parte, quindi i km e il
    pass vanno calcolati sul singolo tratto (Bressanone->Bolzano 38,1 km,
    4,96 EUR col Flex; Bolzano->Rovereto prezzo intero perche' esce
    dall'Alto Adige).

    Con ``intera`` un abbonamento vale solo se copre tutto il tratto: su un
    biglietto separato che esce dall'area non si puo' scontare "una parte",
    sarebbe una promessa falsa.
    """
    valore = float(prezzo or 0)
    coords_da = _station_coords(da)
    coords_a = _station_coords(a)
    if not passes or valore <= 0 or not coords_da or not coords_a:
        return round(valore, 2), None

    from types import SimpleNamespace

    finta = SimpleNamespace(
        mode="train",
        operator="Trenitalia Regionale",
        price=valore,
        pickup_name=da,
        dropoff_name=a,
        pickup_lat=coords_da[0],
        pickup_lon=coords_da[1],
        dropoff_lat=coords_a[0],
        dropoff_lon=coords_a[1],
        details={"treni": "Regionale", "tappe": []},
        net_cost=None,
    )
    migliore = valore
    nome_migliore: str | None = None
    for info in passes:
        quota, _ = coverage(info, finta, quando)
        if quota <= 0.01:
            continue
        if intera and quota < 0.99:
            continue
        apply_passes([info], finta, quando)
        costo = finta.net_cost if finta.net_cost is not None else valore
        if costo < migliore - 0.005:
            migliore = float(costo)
            nome_migliore = info.get("nome") or pass_name(str(info.get("tipo")))
    return round(migliore, 2), nome_migliore


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
