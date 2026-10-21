"""Distanze ferroviarie ufficiali dell'Alto Adige (km tariffari).

Il sistema tariffario provinciale non prezza la linea d'aria: usa i
chilometri del grafo delle linee. Bressanone-Bolzano sono 38,1 km di
ferrovia, non 32,8; Merano-Bolzano 31,5 e non 24,5. Con una retta
corretta a occhio (x1,13) il conto sbagliava di parecchio sulle tratte
lunghe, e il Flex a 0,13 EUR/km veniva sotto-prezzo.

Qui ci sono i progressivi chilometrici delle quattro linee altoatesine
(Brennero, Bolzano-Merano, Fortezza-San Candido, Merano-Malles) piu' la
Brennero fino a Trento e Rovereto: sono i km che usa la Provincia per i
biglietti (0,20 EUR/km arrotondati a 0,50) e per il Flex (0,13 EUR/km).
Fonti: Fascicoli Linea RFI, riportati nelle voci Wikipedia delle linee
(aggiornati al 2026). I km del Brennero sono progressivi da Verona.

Il grafo serve anche a capire *quale parte* del viaggio e' coperta: il
Flex vale su tutte le linee provinciali e sui treni regionali fino a
Trento, non oltre. Per questo Trento e' un nodo di confine: si considera
coperto solo se il viaggio tocca l'Alto Adige (Bolzano-Trento si', 
Rovereto-Trento no).
"""

from __future__ import annotations

import heapq
import unicodedata
from functools import lru_cache

# Ogni linea e' una lista ordinata di (stazione, km progressivo).
_LINEE: dict[str, list[tuple[str, float]]] = {
    "brennero": [
        ("brennero", 238.711),
        ("colle_isarco", 223.157),
        ("vipiteno", 217.358),
        ("campo_di_trens", 212.436),
        ("le_cave", 205.662),
        ("fortezza", 198.540),
        ("bressanone", 188.363),
        ("chiusa", 178.240),
        ("ponte_gardena", 172.432),
        ("prato_tires", 157.997),
        ("bolzano", 150.231),
        ("laives", 142.565),
        ("bronzolo", 139.294),
        ("ora", 134.176),
        ("egna", 128.399),
        ("magre", 124.426),
        ("salorno", 118.994),
        ("mezzocorona", 111.624),
        ("lavis", 104.322),
        ("trento_roncafort", 99.740),
        ("trento", 94.792),
        ("mattarello", 87.222),
        ("calliano", 78.915),
        ("rovereto", 71.219),
        ("mori", 66.914),
        ("serravalle", 60.863),
        ("ala", 54.683),
        ("avio", 50.359),
        ("borghetto", 45.485),
        ("peri", 40.361),
        ("dolce", 32.775),
    ],
    "bolzano_merano": [
        ("bolzano", 0.000),
        ("ponte_d_adige", 5.278),
        ("settequerce", 9.221),
        ("terlano", 12.116),
        ("vilpiano", 15.940),
        ("gargazzone", 19.175),
        ("lana_postal", 22.989),
        ("merano_maia_bassa", 29.367),
        ("merano", 31.482),
    ],
    "pusteria": [
        ("fortezza", 0.000),
        ("rio_di_pusteria", 8.072),
        ("vandoies", 13.475),
        ("casteldarne", 23.699),
        ("san_lorenzo", 29.368),
        ("brunico", 32.442),
        ("brunico_nord", 33.344),
        ("perca", 38.455),
        ("valdaora", 43.944),
        ("monguelfo", 50.896),
        ("villabassa", 55.943),
        ("dobbiaco", 60.717),
        ("san_candido", 64.509),
        ("versciaco", 69.115),
    ],
    "venosta": [
        ("merano", 31.482),
        ("lagundo", 33.010),
        ("marlengo", 35.040),
        ("tel", 41.894),
        ("rabla", 43.124),
        ("plaus", 45.490),
        ("naturno", 48.747),
        ("stava", 51.771),
        ("ciardes", 54.020),
        ("castelbello", 57.599),
        ("laces", 60.724),
        ("coldrano", 63.560),
        ("silandro", 69.250),
        ("lasa", 76.424),
        ("oris", 80.470),
        ("spondigna", 83.323),
        ("sluderno", 87.380),
        ("malles", 91.380),
    ],
}

# Stazioni della provincia di Bolzano: il Flex copre tutto il sistema
# integrato. Trento e' l'unico nodo fuori provincia che il Flex raggiunge
# (treni regionali "fino a Trento").
_PROVINCIA = {
    "brennero",
    "colle_isarco",
    "vipiteno",
    "campo_di_trens",
    "le_cave",
    "fortezza",
    "bressanone",
    "chiusa",
    "ponte_gardena",
    "prato_tires",
    "bolzano",
    "laives",
    "bronzolo",
    "ora",
    "egna",
    "magre",
    "salorno",
    # Bolzano-Merano
    "ponte_d_adige",
    "settequerce",
    "terlano",
    "vilpiano",
    "gargazzone",
    "lana_postal",
    "merano_maia_bassa",
    "merano",
    # Pusteria
    "rio_di_pusteria",
    "vandoies",
    "casteldarne",
    "san_lorenzo",
    "brunico",
    "brunico_nord",
    "perca",
    "valdaora",
    "monguelfo",
    "villabassa",
    "dobbiaco",
    "san_candido",
    "versciaco",
    # Venosta
    "lagundo",
    "marlengo",
    "tel",
    "rabla",
    "plaus",
    "naturno",
    "stava",
    "ciardes",
    "castelbello",
    "laces",
    "coldrano",
    "silandro",
    "lasa",
    "oris",
    "spondigna",
    "sluderno",
    "malles",
}

# Nodi di confine: coperti solo se il viaggio tocca la provincia.
_CONFINE = {"trento"}

# Nomi alternativi (italiano/tedesco) per riconoscere le stazioni.
_ALIAS: dict[str, tuple[str, ...]] = {
    "brennero": ("brennero", "brenner"),
    "colle_isarco": ("colle isarco", "gossensass"),
    "vipiteno": ("vipiteno", "sterzing"),
    "campo_di_trens": ("campo di trens", "freienfeld"),
    "le_cave": ("le cave",),
    "fortezza": ("fortezza", "franzensfeste"),
    "bressanone": ("bressanone", "brixen"),
    "chiusa": ("chiusa", "klausen"),
    "ponte_gardena": ("ponte gardena", "w aidbruck", "waidbruck"),
    "prato_tires": ("prato tires", "prato-tires"),
    "bolzano": ("bolzano", "bozen"),
    "laives": ("laives", "leifers"),
    "bronzolo": ("bronzolo", "branzoll"),
    "ora": ("ora", "auer"),
    "egna": ("egna", "neumarkt"),
    "magre": ("magre", "magre cortaccia", "kurtatsch"),
    "salorno": ("salorno", "salurn"),
    "mezzocorona": ("mezzocorona",),
    "lavis": ("lavis",),
    "trento_roncafort": ("trento roncafort", "roncafort"),
    "trento": ("trento", "trient"),
    "mattarello": ("mattarello",),
    "calliano": ("calliano",),
    "rovereto": ("rovereto", "rovereto santa maria"),
    "mori": ("mori",),
    "serravalle": ("serravalle", "serravalle all adige"),
    "ala": ("ala",),
    "avio": ("avio",),
    "borghetto": ("borghetto", "borghetto sull adige"),
    "peri": ("peri",),
    "dolce": ("dolce", "dolce di dolce"),
    "ponte_d_adige": ("ponte d adige", "sigmundskron"),
    "settequerce": ("settequerce", "siebeneich"),
    "terlano": ("terlano", "terlan", "terlano andriano"),
    "vilpiano": ("vilpiano", "vilpian", "vilpiano nalles"),
    "gargazzone": ("gargazzone", "gargazon"),
    "lana_postal": ("lana postal", "lana postall", "lana burgstall"),
    "merano_maia_bassa": ("merano maia bassa", "meran untermais", "maia bassa"),
    "merano": ("merano", "meran"),
    "rio_di_pusteria": ("rio di pusteria", "muhlbach", "muehlbach"),
    "vandoies": ("vandoies", "vintl"),
    "casteldarne": ("casteldarne", "ehrenburg"),
    "san_lorenzo": ("san lorenzo", "st lorenzen"),
    "brunico": ("brunico", "bruneck"),
    "brunico_nord": ("brunico nord", "bruneck nord"),
    "perca": ("perca", "perca plan de corones", "percha"),
    "valdaora": ("valdaora", "olang", "valdaora anterselva"),
    "monguelfo": ("monguelfo", "welsberg"),
    "villabassa": ("villabassa", "niederdorf"),
    "dobbiaco": ("dobbiaco", "toblach"),
    "san_candido": ("san candido", "innichen"),
    "versciaco": ("versciaco", "vierschach"),
    "lagundo": ("lagundo", "algund"),
    "marlengo": ("marlengo", "marling"),
    "tel": ("tel", "toll"),
    "rabla": ("rabla", "rabland"),
    "plaus": ("plaus",),
    "naturno": ("naturno", "naturns"),
    "stava": ("stava", "staben"),
    "ciardes": ("ciardes", "tschars"),
    "castelbello": ("castelbello", "kastelbell"),
    "laces": ("laces", "latsch"),
    "coldrano": ("coldrano", "goldrain"),
    "silandro": ("silandro", "schlanders"),
    "lasa": ("lasa", "laas"),
    "oris": ("oris", "eyrs"),
    "spondigna": ("spondigna", "spondinig"),
    "sluderno": ("sluderno", "schluderns"),
    "malles": ("malles", "mals"),
}


def _normalizza(testo: str) -> str:
    testo = unicodedata.normalize("NFKD", str(testo or ""))
    testo = "".join(c for c in testo if not unicodedata.combining(c))
    testo = testo.lower().replace("/", " ").replace("-", " ").replace("'", " ")
    return " ".join(testo.split())


@lru_cache(maxsize=512)
def stazione(nome: str | None) -> str | None:
    """La chiave della stazione, o None se non e' una stazione nota.

    I nomi arrivano bilingui ("Bressanone Brixen", "Bolzano/Bozen"): si
    cerca l'alias piu' lungo contenuto nel testo, come per le coordinate.
    """
    testo = _normalizza(nome or "")
    if not testo:
        return None
    parole = set(testo.split())
    migliore: tuple[int, str] | None = None
    for chiave, alias in _ALIAS.items():
        for voce in alias:
            voce = _normalizza(voce)
            if not voce:
                continue
            if voce == testo or voce in parole or f" {voce} " in f" {testo} ":
                if migliore is None or len(voce) > migliore[0]:
                    migliore = (len(voce), chiave)
    return migliore[1] if migliore else None


def _grafo() -> dict[str, list[tuple[str, float]]]:
    grafo: dict[str, list[tuple[str, float]]] = {}
    for stazioni in _LINEE.values():
        for (da, km_da), (a, km_a) in zip(stazioni, stazioni[1:]):
            peso = abs(km_a - km_da)
            grafo.setdefault(da, []).append((a, peso))
            grafo.setdefault(a, []).append((da, peso))
    return grafo


_GRAFO = _grafo()


@lru_cache(maxsize=512)
def percorso(da: str, a: str) -> tuple[tuple[str, ...], float]:
    """Il percorso piu' breve fra due stazioni: (stazioni, km).

    Usa Dijkstra sul grafo delle linee; se una delle due stazioni non e'
    nel grafo restituisce ((), 0.0) e il chiamante ricade sulla stima
    geografica.
    """
    if da not in _GRAFO or a not in _GRAFO:
        return (), 0.0
    if da == a:
        return (da,), 0.0
    distanza = {da: 0.0}
    precedente: dict[str, str] = {}
    coda: list[tuple[float, str]] = [(0.0, da)]
    while coda:
        km, nodo = heapq.heappop(coda)
        if nodo == a:
            break
        if km > distanza.get(nodo, float("inf")):
            continue
        for vicino, peso in _GRAFO.get(nodo, []):
            nuovo = km + peso
            if nuovo < distanza.get(vicino, float("inf")):
                distanza[vicino] = nuovo
                precedente[vicino] = nodo
                heapq.heappush(coda, (nuovo, vicino))
    if a not in distanza:
        return (), 0.0
    cammino = [a]
    while cammino[-1] != da:
        cammino.append(precedente[cammino[-1]])
    cammino.reverse()
    # Il percorso va arrotondato come i km tariffari (i progressivi RFI
    # hanno il metro, il prezzo si calcola sul chilometro).
    return tuple(cammino), round(distanza[a], 2)


def km(da: str | None, a: str | None) -> float | None:
    """Distanza ferroviaria in km fra due nomi di stazione (None se ignota)."""
    chiave_da = stazione(da)
    chiave_a = stazione(a)
    if not chiave_da or not chiave_a:
        return None
    _cammino, distanza = percorso(chiave_da, chiave_a)
    return distanza or None


def copertura(da: str | None, a: str | None) -> tuple[float, float, bool] | None:
    """Km coperti dal sistema provinciale e km totali del viaggio.

    Ritorna (km_coperti, km_totali, copertura_completa) oppure None se il
    viaggio non tocca l'Alto Adige o le stazioni non sono note.
    """
    chiave_da = stazione(da)
    chiave_a = stazione(a)
    if not chiave_da or not chiave_a:
        return None
    cammino, totale = percorso(chiave_da, chiave_a)
    if not cammino or totale <= 0:
        return None
    tocca_provincia = chiave_da in _PROVINCIA or chiave_a in _PROVINCIA
    coperte = set(_PROVINCIA)
    if tocca_provincia:
        coperte |= _CONFINE
    indici = [i for i, nodo in enumerate(cammino) if nodo in coperte]
    if not indici:
        return 0.0, totale, False
    primo, ultimo = min(indici), max(indici)
    coperti = 0.0
    for i in range(primo, ultimo):
        coperti += _peso(cammino[i], cammino[i + 1])
    coperti = round(coperti, 2)
    return coperti, totale, coperti >= totale - 0.01


def _peso(da: str, a: str) -> float:
    for vicino, peso in _GRAFO.get(da, []):
        if vicino == a:
            return peso
    return 0.0
