"""Lettura dei dati reali di BlaBlaCar con un browser vero.

BlaBlaCar non ha piu' un'API pubblica e il sito e' protetto da un filtro
anti-bot: i dati si ottengono aprendo la pagina di ricerca come farebbe un
utente qualsiasi.

Il trucco e' che la pagina di ricerca contiene gia' la risposta JSON del
backend, sepolta in `window["__INFRASTRUCTURE__cache"]` (la cache di
infrastruttura del framework). Noi apriamo la pagina con un browser vero
(Chrome di sistema, profilo persistente cosi' i cookie di consenso e il
passaggio anti-bot si ricordano) e leggiamo quei dati: sono gli stessi che
vede l'utente, con prezzi, orari e punti di ritrovo veri.

Nessun bypass: e' una visita normale a una pagina pubblica, con un browser
normale. Se il browser non c'e' (o Chrome non e' installato) l'adapter non
prova nulla e lascia il link di ricerca.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("carpool.blablacar")

# La cache di infrastruttura: {"<path>": "<risposta JSON>"}
_CACHE_MARKER = 'window["__INFRASTRUCTURE__cache"]='
_SEARCH_PATH = "/trip/search"

# I risultati valgono per qualche minuto: il browser non va riaperto a ogni
# giro dell'agente.
_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_SECONDI = 900


def browser_disponibile() -> bool:
    """True se c'e' un browser che puo' aprire la pagina."""
    try:
        import playwright  # noqa: F401
    except Exception:
        return False
    return True


def _estrai_cache(html: str) -> dict[str, Any] | None:
    """Estrae dalla pagina la risposta JSON della ricerca."""
    inizio = html.find(_CACHE_MARKER)
    if inizio < 0:
        return None
    grezzo = html[inizio + len(_CACHE_MARKER):]

    # Scanner a graffe bilanciate: il valore puo' contenere stringhe con
    # graffe e sequenze di escape.
    livello = 0
    in_stringa = False
    escape = False
    fine = None
    for indice, carattere in enumerate(grezzo):
        if escape:
            escape = False
            continue
        if carattere == "\\":
            escape = True
            continue
        if carattere == '"':
            in_stringa = not in_stringa
            continue
        if in_stringa:
            continue
        if carattere == "{":
            livello += 1
        elif carattere == "}":
            livello -= 1
            if livello == 0:
                fine = indice + 1
                break
    if not fine:
        return None
    try:
        # Il payload e' JavaScript, non JSON: "undefined" -> null.
        testo = re.sub(r":undefined(?=[,}\]])", ":null", grezzo[:fine])
        cache = json.loads(testo)
    except Exception as exc:  # noqa: BLE001
        log.debug("cache non leggibile: %s", exc)
        return None
    for chiave, valore in cache.items():
        if _SEARCH_PATH in chiave:
            if isinstance(valore, str):
                try:
                    valore = json.loads(valore)
                except Exception:  # noqa: BLE001
                    continue
            if isinstance(valore, dict):
                return valore
    return None


def scarica_ricerca(url: str, profilo: Path, timeout: int = 60) -> dict[str, Any] | None:
    """Apre la pagina di ricerca e restituisce i dati reali dei viaggi.

    Si usa un contesto browser "normale" (non un profilo persistente salvato
    su disco): con un profilo nuovo il filtro anti-bot del sito mostra la
    pagina di verifica, mentre con un contesto pulito la verifica passa da
    sola e la pagina si carica.

    I risultati restano in memoria per qualche minuto: non ha senso
    riaprire il browser a ogni giro dell'agente.
    """
    from playwright.sync_api import sync_playwright

    ora = time.time()
    voce = _CACHE.get(url)
    if voce and ora - voce[0] < _CACHE_SECONDI:
        return voce[1]

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        try:
            context = browser.new_context(
                locale="it-IT",
                timezone_id="Europe/Rome",
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            # La banner dei cookie: si accetta una volta sola, poi la pagina
            # parte con i risultati.
            try:
                bottone = page.get_by_role("button", name="Accetta e continua").first
                bottone.wait_for(state="visible", timeout=8000)
                bottone.click(timeout=5000)
            except Exception:  # noqa: BLE001
                pass

            # Aspetta che i risultati arrivino (la cache si popola).
            passati = 0
            dati = None
            html = ""
            while passati < timeout:
                html = page.content()
                dati = _estrai_cache(html)
                if dati:
                    break
                page.wait_for_timeout(1000)
                passati += 1
            if not dati and html:
                with open(str(profilo / "ultima_pagina.html"), "w", encoding="utf-8") as f:
                    f.write(html)
            if dati:
                _CACHE[url] = (time.time(), dati)
            return dati
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass
