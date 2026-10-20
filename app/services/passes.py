"""Abbonamenti e pass: il viaggio che hai gia' pagato.

Dati raccolti dalle fonti ufficiali (2026):

- **Trenitalia, abbonamento regionale**: esiste in ogni regione (tariffe
  40/xx/REGIONE) e vale sui treni *regionali* della regione interessata.
  Un abbonamento regionale copre solo la parte di viaggio nella propria
  regione: da Thiene a Trento copre il tratto veneto, non quello trentino.
- **altoadigemobilita** (Alto Adige Mobilita): 250 EUR/anno (Fix365),
  39 EUR/mese (Fix30), 153 EUR/anno o 21 EUR/mese (U26, under 26),
  max 21 EUR/anno (U19). Copre tutti i mezzi di sudtirolmobil: i treni
  regionali delle 4 linee (Brennero-Bolzano-Trento, Val Venosta,
  Merano-Bolzano, Val Pusteria), gli autobus urbani ed extraurbani, gli
  impianti di risalita, il trenino del Renon e la funicolare della Mendola.
- **Euregio Ticket** (2026): 750 EUR/anno, per chi vive in Trentino, Alto
  Adige o Tirolo. Copre in Alto Adige treni regionali, autobus urbani e
  regionali e le funivie; in Trentino treni regionali, autobus e la funivia
  Trento-Sardagna; in Tirolo tutta la rete VVT.
- **Trentino, abbonamento provinciale**: tutta la rete provinciale: autobus
  urbani ed extraurbani di Trentino Trasporti, ferrovia Trento-Male, ferrovia
  della Valsugana (Trento-Primolano), linea del Brennero (Borghetto-Ora) e
  funivia Trento-Sardagna.
- **Guest card Trentino / Val di Sole**: viaggio gratuito sul trenino
  Trento-Male e sui bus del Trentino.
- **IVOL** (Lombardia): "Io Viaggio Ovunque in Lombardia", tutti i mezzi
  della regione.
- **Salta Su** (Emilia-Romagna): Card Unica con l'abbonamento ferroviario
  regionale; "Mi Muovo Tutto Treno" apre anche gli Intercity sulla tratta
  del proprio abbonamento regionale.
- **AUR / Formula** (Piemonte), **abbonamento regionale** (Toscana,
  Calabria, Veneto, ...), **CIS Carta Integrata Settimanale** (Lazio, 29 EUR
  per 7 giorni, viaggi illimitati), **Tpl Fvg** (dal maggio 2026 un
  abbonamento unico treni Trenitalia + autobus extraurbani regionali).
- **Carnet** Trenitalia: 10 biglietti, consuma 1 biglietto a ogni viaggio.

Come funziona qui:
- la copertura e' **parziale** quando l'abbonamento vale solo nella sua area:
  si stima la quota di viaggio coperta dalla distanza e il costo e' quella
  quota non coperta;
- il prezzo REALE del biglietto non viene mai cancellato: accanto al costo
  effettivo resta sempre il biglietto quanto costerebbe senza abbonamento.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ..geo import CITIES, haversine_km

# I tipi di abbonamento riconosciuti.
#   area        : citta' (chiavi di app/geo.CITIES) dentro cui vale
#   raggio_km   : quanto vale attorno a quelle citta'
#   copre       : modi di trasporto inclusi
#   solo_regionali: la copertura vale solo per i treni regionali
#   parziale    : copre solo la parte di viaggio dentro l'area
PASS_TYPES: dict[str, dict[str, Any]] = {
    # --- Trenitalia (nazionale) ---
    "trenitalia_regionale": {
        "nome": "Abbonamento regionale Trenitalia",
        "copre": {"train"},
        "solo_regionali": True,
        "parziale": True,
        "nota": (
            "Vale sui treni regionali della regione in cui e' stato preso: "
            "copre solo la parte di viaggio dentro quella regione."
        ),
    },
    "trenitalia_tutti": {
        "nome": "Abbonamento Trenitalia (tutti i treni)",
        "copre": {"train"},
        "solo_regionali": False,
        "nota": "Copre tutti i treni, Frecce e Intercity comprese.",
    },
    "carnet_regionale": {
        "nome": "Carnet 10 biglietti regionali",
        "copre": {"train", "bus"},
        "solo_regionali": True,
        "parziale": True,
        "consuma": True,
        "nota": "Consuma 1 biglietto del carnet a ogni viaggio.",
    },
    "carnet_bus": {
        "nome": "Carnet 10 biglietti bus",
        "copre": {"bus"},
        "solo_regionali": False,
        "consuma": True,
        "nota": "Consuma 1 biglietto del carnet a ogni viaggio.",
    },
    # --- Alto Adige / Trentino / Euregio ---
    "altoadigemobilita": {
        "nome": "altoadigemobilita (Alto Adige Mobilita)",
        "area": ["bolzano", "bressanone", "cortina d'ampezzo", "trento"],
        "raggio_km": 45,
        "copre": {"train", "bus", "tram", "transit"},
        "solo_regionali": True,
        "parziale": True,
        "nota": (
            "Tutti i mezzi di sudtirolmobil: treni regionali (Brennero-Bolzano-"
            "Trento, Val Venosta, Merano-Bolzano, Val Pusteria), autobus urbani e "
            "extraurbani, impianti di risalita, trenino del Renon, funicolare "
            "della Mendola."
        ),
    },
    "euregio_ticket": {
        "nome": "Euregio Ticket (Trentino, Alto Adige, Tirolo)",
        "area": ["trento", "bolzano", "bressanone", "cortina d'ampezzo"],
        "raggio_km": 60,
        "copre": {"train", "bus", "tram", "transit"},
        "solo_regionali": True,
        "parziale": True,
        "nota": (
            "In Alto Adige treni regionali, autobus e funivie; in Trentino treni "
            "regionali, autobus e la funivia Trento-Sardagna; in Tirolo tutta la "
            "rete VVT."
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
            "Tutta la rete provinciale: autobus urbani ed extraurbani di Trentino "
            "Trasporti, ferrovia Trento-Male, ferrovia della Valsugana, linea del "
            "Brennero (Borghetto-Ora) e funivia Trento-Sardagna."
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
    # --- Altre regioni ---
    "ivol_lombardia": {
        "nome": "IVOL (Io Viaggio Ovunque in Lombardia)",
        "area": ["milano", "bergamo", "brescia", "como", "varese", "lecco", "pavia", "monza"],
        "raggio_km": 45,
        "copre": {"train", "bus", "tram", "transit"},
        "solo_regionali": False,
        "parziale": True,
        "nota": "Tutti i mezzi pubblici della Lombardia.",
    },
    "salta_su_emilia": {
        "nome": "Salta Su (Emilia-Romagna)",
        "area": ["bologna", "parma", "modena", "reggio emilia", "ferrara", "rimini", "ravenna", "piacenza"],
        "raggio_km": 45,
        "copre": {"train", "bus", "tram", "transit"},
        "solo_regionali": True,
        "parziale": True,
        "nota": "Card Unica con abbonamento ferroviario regionale.",
    },
    "aur_piemonte": {
        "nome": "AUR / Formula (Piemonte)",
        "area": ["torino", "novara", "alessandria", "asti", "cuneo"],
        "raggio_km": 45,
        "copre": {"train", "bus", "tram", "transit"},
        "solo_regionali": True,
        "parziale": True,
        "nota": "Abbonamento unico regionale del Piemonte.",
    },
    "cis_lazio": {
        "nome": "CIS Carta Integrata Settimanale (Lazio)",
        "area": ["roma", "latina", "frosinone", "viterbo", "rieti"],
        "raggio_km": 50,
        "copre": {"train", "bus", "tram", "transit"},
        "solo_regionali": True,
        "parziale": True,
        "nota": "7 giorni dalla prima timbratura, viaggi illimitati.",
    },
    "toscana_regionale": {
        "nome": "Abbonamento regionale Toscana",
        "area": ["firenze", "pisa", "livorno", "lucca", "siena", "arezzo", "prato"],
        "raggio_km": 45,
        "copre": {"train", "bus", "tram", "transit"},
        "solo_regionali": True,
        "parziale": True,
        "nota": "Treni regionali e autobus della Toscana.",
    },
    "veneto_regionale": {
        "nome": "Abbonamento regionale Veneto",
        "area": ["venezia", "mestre", "padova", "vicenza", "verona", "treviso", "rovigo"],
        "raggio_km": 45,
        "copre": {"train", "bus", "tram", "transit"},
        "solo_regionali": True,
        "parziale": True,
        "nota": "Treni regionali e autobus del Veneto (il biglietto unico regionale arriva nel 2028).",
    },
    "fvg_unico": {
        "nome": "Abbonamento unico FVG (Tpl Fvg)",
        "area": ["trieste", "udine", "pordenone", "gorizia"],
        "raggio_km": 45,
        "copre": {"train", "bus"},
        "solo_regionali": True,
        "parziale": True,
        "nota": "Dal maggio 2026 un unico abbonamento per i treni e gli autobus extraurbani.",
    },
    # --- Città ---
    "urbano": {
        "nome": "Abbonamento urbano (bus/metro/tram)",
        "copre": {"bus", "tram", "transit"},
        "solo_regionali": False,
        "nota": "Copre i mezzi urbani della città in cui è attivo.",
    },
    "carnet_urbano": {
        "nome": "Carnet 10 biglietti urbani",
        "copre": {"bus", "tram", "transit"},
        "solo_regionali": False,
        "consuma": True,
        "nota": "Consuma 1 biglietto del carnet a ogni viaggio.",
    },
}

MASK = "••••••••"

# Abbonamenti regionali Trenitalia: la tariffa 40/xx/REGIONE esiste per ogni
# regione e vale sui treni regionali di quella regione (fonte: condizioni
# generali di trasporto Trenitalia, parte III).
_REGIONI_TRENITALIA: dict[str, list[str]] = {
    "lombardia": ["milano", "bergamo", "brescia", "como", "varese", "pavia", "monza"],
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
}

for _regione, _citta in _REGIONI_TRENITALIA.items():
    PASS_TYPES[f"trenitalia_{_regione}"] = {
        "nome": f"Abbonamento regionale Trenitalia {_regione.capitalize()}",
        "area": _citta,
        "raggio_km": 45,
        "copre": {"train"},
        "solo_regionali": True,
        "parziale": True,
        "nota": f"Treni regionali della {_regione}: copre solo quella parte del viaggio.",
    }


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
    """True se l'offerta e' fatta solo di treni regionali."""
    details = getattr(offer, "details", None) or {}
    categorie = str(details.get("treni") or "")
    if not categorie:
        # Senza informazioni sul tipo di treno non si presume la copertura.
        return False
    testo = categorie.lower()
    if any(
        parola in testo
        for parola in ("frecciarossa", "frecciargento", "frecciabianca", "intercity", "eurostar")
    ):
        return False
    return any(parola in testo for parola in ("regionale", "regio"))


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
    # Togli i punti consecutivi uguali.
    unici: list[tuple[float, float]] = []
    for punto in punti:
        if not unici or unici[-1] != punto:
            unici.append(punto)
    return unici


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
        if any(
            (haversine_km(lat, lon, plat, plon) or 1e9) <= 45 for plat, plon in punti
        ):
            return punti
    return []


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
    # Il tratto non tocca l'area: niente coperto (stimaprudente).
    return 0.0, lunghezza


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
