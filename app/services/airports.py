"""Le citta' con un aeroporto.

Serve a due cose: capire se i voli hanno senso per una tratta (Bressanone ->
Rovereto in aereo non esiste) e costruire i link di ricerca, perche' i siti dei
voli vogliono i codici degli aeroporti, non i nomi delle citta'.

La tabella e' curata a mano: sono aeroporti che esistono e hanno voli di
linea. Per ogni citta' il primo aeroporto e' quello principale (quello usato
nei link), gli altri restano scritti perche' servono a capire la tratta.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# citta' -> (aeroporto principale, tutti gli aeroporti)
CITTA: dict[str, tuple[str, tuple[str, ...]]] = {
    # Italia
    "alghero": ("AHO", ("AHO",)),
    "ancona": ("AOI", ("AOI",)),
    "bari": ("BRI", ("BRI",)),
    "bergamo": ("BGY", ("BGY",)),
    "bologna": ("BLQ", ("BLQ",)),
    "bolzano": ("BZO", ("BZO",)),
    "brescia": ("VBS", ("VBS",)),
    "brindisi": ("BDS", ("BDS",)),
    "cagliari": ("CAG", ("CAG",)),
    "catania": ("CTA", ("CTA",)),
    "comiso": ("CIY", ("CIY",)),
    "cuneo": ("CUF", ("CUF",)),
    "firenze": ("FLR", ("FLR",)),
    "foggia": ("FOG", ("FOG",)),
    "forli": ("FRL", ("FRL",)),
    "genova": ("GOA", ("GOA",)),
    "grosseto": ("GRS", ("GRS",)),
    "lampedusa": ("LMP", ("LMP",)),
    "lamezia terme": ("SUF", ("SUF",)),
    "milano": ("MXP", ("MXP", "LIN", "BGY")),
    "napoli": ("NAP", ("NAP",)),
    "olbia": ("OLB", ("OLB",)),
    "palermo": ("PMO", ("PMO",)),
    "pantelleria": ("PNL", ("PNL",)),
    "parma": ("PMF", ("PMF",)),
    "perugia": ("PEG", ("PEG",)),
    "pescara": ("PSR", ("PSR",)),
    "pisa": ("PSA", ("PSA",)),
    "reggio calabria": ("REG", ("REG",)),
    "rimini": ("RMI", ("RMI",)),
    "roma": ("FCO", ("FCO", "CIA")),
    "salerno": ("QSR", ("QSR",)),
    "torino": ("TRN", ("TRN",)),
    "trapani": ("TPS", ("TPS",)),
    "treviso": ("TSF", ("TSF",)),
    "trieste": ("TRS", ("TRS",)),
    "venezia": ("VCE", ("VCE",)),
    "verona": ("VRN", ("VRN",)),
    # Europa
    "amsterdam": ("AMS", ("AMS",)),
    "atene": ("ATH", ("ATH",)),
    "barcellona": ("BCN", ("BCN",)),
    "berlino": ("BER", ("BER",)),
    "bruxelles": ("BRU", ("BRU", "CRL")),
    "colonia": ("CGN", ("CGN",)),
    "copenaghen": ("CPH", ("CPH",)),
    "dublino": ("DUB", ("DUB",)),
    "francoforte": ("FRA", ("FRA",)),
    "helsinki": ("HEL", ("HEL",)),
    "lisbona": ("LIS", ("LIS",)),
    "londra": ("LHR", ("LHR", "LGW", "STN", "LTN", "LCY")),
    "madrid": ("MAD", ("MAD",)),
    "monaco di baviera": ("MUC", ("MUC",)),
    "mosca": ("SVO", ("SVO", "DME", "VKO")),
    "oslo": ("OSL", ("OSL",)),
    "parigi": ("CDG", ("CDG", "ORY", "BVA")),
    "stoccolma": ("ARN", ("ARN", "BMA")),
    "vienna": ("VIE", ("VIE",)),
    "zurigo": ("ZRH", ("ZRH",)),
    # Fuori Europa
    "abu dhabi": ("AUH", ("AUH",)),
    "bangkok": ("BKK", ("BKK",)),
    "boston": ("BOS", ("BOS",)),
    "buenos aires": ("EZE", ("EZE", "AEP")),
    "casablanca": ("CMN", ("CMN",)),
    "chicago": ("ORD", ("ORD", "MDW")),
    "citta del messico": ("MEX", ("MEX",)),
    "delhi": ("DEL", ("DEL",)),
    "doha": ("DOH", ("DOH",)),
    "dubai": ("DXB", ("DXB",)),
    "hong kong": ("HKG", ("HKG",)),
    "il cairo": ("CAI", ("CAI",)),
    "istanbul": ("IST", ("IST", "SAW")),
    "los angeles": ("LAX", ("LAX",)),
    "marrakech": ("RAK", ("RAK",)),
    "miami": ("MIA", ("MIA",)),
    "montreal": ("YUL", ("YUL",)),
    "mumbai": ("BOM", ("BOM",)),
    "new york": ("JFK", ("JFK", "EWR", "LGA")),
    "osaka": ("KIX", ("KIX",)),
    "pechino": ("PEK", ("PEK", "PKX")),
    "riyadh": ("RUH", ("RUH",)),
    "rio de janeiro": ("GIG", ("GIG", "SDU")),
    "san francisco": ("SFO", ("SFO",)),
    "san paolo": ("GRU", ("GRU", "CGH")),
    "seul": ("ICN", ("ICN", "GMP")),
    "shanghai": ("PVG", ("PVG", "SHA")),
    "singapore": ("SIN", ("SIN",)),
    "sydney": ("SYD", ("SYD",)),
    "tel aviv": ("TLV", ("TLV",)),
    "tokyo": ("NRT", ("NRT", "HND")),
    "toronto": ("YYZ", ("YYZ",)),
    "tunisi": ("TUN", ("TUN",)),
    "washington": ("IAD", ("IAD", "DCA")),
}

# Nomi alternativi che indicano la stessa citta' (aeroporti, quartieri, grafie).
ALIAS: dict[str, str] = {
    "malpensa": "milano",
    "linate": "milano",
    "orio al serio": "bergamo",
    "fiumicino": "roma",
    "ciampino": "roma",
    "caselle": "torino",
    "marco polo": "venezia",
    "tessera": "venezia",
    "peretola": "firenze",
    "capodichino": "napoli",
    "fontanarossa": "catania",
    "punta raisi": "palermo",
    "elmas": "cagliari",
    "sestri": "genova",
    "villafranca": "verona",
    "gino lisa": "foggia",
    "miramare": "rimini",
    "levaldigi": "cuneo",
    "montichiari": "brescia",
    "zurich": "zurigo",
    "munich": "monaco di baviera",
    "monaco": "monaco di baviera",
    "cologne": "colonia",
    "cairo": "il cairo",
    "washington dc": "washington",
    "sao paulo": "san paolo",
    "beijing": "pechino",
    "moscow": "mosca",
}

# Parole che nei nomi dei posti non aiutano a riconoscere la citta'.
_RUMORE = (
    "centrale",
    "termini",
    "aeroporto",
    "airport",
    "stazione",
    "station",
    "city",
)

# Parole che restano minuscole quando si scrive il nome della citta'.
_MINUSCOLE = {"di", "del", "della", "dei", "degli", "delle", "da", "su", "in", "il", "la", "le", "los"}


@dataclass(frozen=True)
class Aeroporti:
    """Gli aeroporti di una citta'."""

    citta: str
    nome: str
    principale: str
    tutti: tuple[str, ...]

    @property
    def codice_citta(self) -> str:
        """Il codice di citta' IATA (MIL, ROM, TUR...) ricavato dagli aeroporti."""
        return CITTADINANZA.get(self.citta, self.principale)


# I codici di citta' (servono a Skyscanner, che accetta anche quelli).
CITTADINANZA = {
    "milano": "MIL",
    "roma": "ROM",
    "torino": "TUR",
    "venezia": "VCE",
    "londra": "LON",
    "parigi": "PAR",
    "stoccolma": "STO",
    "new york": "NYC",
    "washington": "WAS",
    "mosca": "MOW",
    "il cairo": "CAI",
    "san paolo": "SAO",
    "buenos aires": "BUE",
    "tokyo": "TYO",
    "osaka": "OSA",
    "seul": "SEL",
    "pechino": "BJS",
    "shanghai": "SHA",
    "rio de janeiro": "RIO",
    "istanbul": "IST",
    "bruxelles": "BRU",
}


def _semplifica(testo: str) -> str:
    """Minuscolo, senza accenti e senza punteggiatura."""
    senza_accenti = "".join(
        c for c in unicodedata.normalize("NFKD", testo) if not unicodedata.combining(c)
    )
    minuscolo = senza_accenti.lower().replace("-", " ").replace("'", " ")
    return re.sub(r"[^a-z0-9 ]+", " ", minuscolo).strip()


def _pulito(luogo: str) -> str:
    parti = [p for p in _semplifica(luogo).split() if p not in _RUMORE]
    return " ".join(parti)


def _nome_buono(chiave: str) -> str:
    """'monaco di baviera' -> 'Monaco di Baviera'."""
    parole = []
    for parola in chiave.split():
        if parola in _MINUSCOLE:
            parole.append(parola)
        elif parola == "citta":
            parole.append("Citta")
        else:
            parole.append(parola[:1].upper() + parola[1:])
    return " ".join(parole)


def _costruisci(chiave: str) -> Aeroporti:
    principale, tutti = CITTA[chiave]
    return Aeroporti(
        citta=chiave, nome=_nome_buono(chiave), principale=principale, tutti=tutti
    )


def aeroporti_di(luogo: str) -> Aeroporti | None:
    """Gli aeroporti di questa citta', se ne ha uno.

    Riconosce "Milano", "Milano Centrale", "Malpensa" e "MXP".
    """
    if not luogo:
        return None
    pulito = _pulito(luogo)
    if not pulito:
        return None
    if pulito in CITTA:
        return _costruisci(pulito)
    if pulito in ALIAS:
        return _costruisci(ALIAS[pulito])
    # "milano centrale" e simili: si prova con le parole, dalla piu' lunga
    # alla piu' corta, per non sbagliare sui nomi composti.
    parole = pulito.split()
    for lunghezza in (3, 2, 1):
        for inizio in range(0, max(1, len(parole) - lunghezza + 1)):
            pezzo = " ".join(parole[inizio : inizio + lunghezza])
            if pezzo in CITTA:
                return _costruisci(pezzo)
            if pezzo in ALIAS:
                return _costruisci(ALIAS[pezzo])
    # Il luogo scritto come codice IATA ("MXP", "fco")
    codice = pulito.upper()
    for chiave, (principale, tutti) in CITTA.items():
        if codice == principale or codice in tutti:
            return _costruisci(chiave)
    return None


def hanno_un_aeroporto(partenza: str, arrivo: str) -> bool:
    """True se entrambe le estremita' hanno un aeroporto."""
    return aeroporti_di(partenza) is not None and aeroporti_di(arrivo) is not None
