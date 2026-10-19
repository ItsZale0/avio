"""Offerte e codici sconto reali dalle pagine ufficiali dei vettori.

Nessun dato inventato: qui si leggono solo pagine pubbliche raggiungibili e si
estraggono i titoli delle offerte con il relativo link. I "codici" veri e propri
restano sulle pagine ufficiali, a cui puntiamo direttamente.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import logging
import re
import time
from typing import Any
from urllib.parse import urljoin

import httpx

log = logging.getLogger("carpool.promos")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126 Safari/537.36"
)

# Pagine da cui estrarre le "offerte del momento" (titolo + link).
OFFER_PAGES: list[dict[str, Any]] = [
    {
        "operator": "Trainline",
        "url": "https://www.thetrainline.com/it/offerte",
        "href": r"^https://www\.thetrainline\.com/it/offerte/[a-z0-9\-]+/?$",
        "deny": {
            "offerte treni",
            "come trovare le offerte",
            "offerte compagnie ferroviarie",
            "principali offerte",
            "tipi di offerte",
            "offerte e sconti treni",
        },
    },
    {
        "operator": "Italo",
        "url": "https://www.italotreno.it/it/offerte",
        "href": r"^https://www\.italotreno\.(it|com)/(it/)?(offerte|offerte-treno)(/[a-z0-9\-]+)*$",
        "deny": {
            "lingua italiana",
            "english language",
            "scopri di piu",
            "scopri di pi\u00f9",
            "iscriviti a",
            "sito ufficiale",
        },
    },
]

# Pagine ufficiali/strumenti per i codici sconto (link verificati).
COUPON_LINKS: list[dict[str, str]] = [
    {
        "operator": "Italo",
        "title": "Codici promo ufficiali Italo",
        "url": "https://www.italotreno.com/it/offerte-treno/codicepromo",
        "note": "Pagina ufficiale con i codici sconto del momento",
    },
    {
        "operator": "Trenitalia",
        "title": "Codice sconto Trenitalia",
        "url": (
            "https://www.thetrainline.com/it/compagnie-ferroviarie/trenitalia/codice-sconto"
        ),
        "note": "Codici e promozioni Trenitalia",
    },
    {
        "operator": "FlixBus",
        "title": "Voucher e buoni FlixBus",
        "url": "https://support.flixbus.com/global/en/vouchers",
        "note": "Come funzionano e come si usano i voucher",
    },
    {
        "operator": "Italo",
        "title": "Offerte ufficiali Italo",
        "url": "https://www.italotreno.it/it/offerte",
        "note": "Tutte le promozioni Italo attive",
    },
    {
        "operator": "Trainline",
        "title": "Offerte treni Trainline",
        "url": "https://www.thetrainline.com/it/offerte",
        "note": "Offerte Trenitalia, Frecciarossa e Italo",
    },
    {
        "operator": "BlaBlaCar",
        "title": "Promozioni BlaBlaCar",
        "url": "https://www.blablacar.it/",
        "note": "Passaggi e bus con sconti",
    },
    {
        "operator": "Cuponation",
        "title": "Codici sconto Cuponation",
        "url": "https://www.cuponation.it/",
        "note": "Aggregatore di codici sconto attivi",
    },
    {
        "operator": "Groupon",
        "title": "Coupon Groupon",
        "url": "https://www.groupon.it/",
        "note": "Coupon e offerte locali",
    },
]

CACHE_TTL = 6 * 3600  # 6 ore
MAX_PER_PAGE = 14

_cache: dict[str, Any] = {"at": 0.0, "data": None}
_lock = asyncio.Lock()


def _clean_text(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html_lib.unescape(text)
    return " ".join(text.split())


def _extract_offers(page: dict[str, Any], body: str) -> list[dict[str, str]]:
    href_re = re.compile(page["href"], re.I)
    deny = page.get("deny") or set()
    operators = page["operator"]
    offers: list[dict[str, str]] = []
    seen: set[str] = set()

    for raw_href, inner in re.findall(
        r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', body, re.S | re.I
    ):
        text = _clean_text(inner)
        if not text or len(text) < 3 or len(text) > 80:
            continue
        if text.lower() in deny:
            continue
        href = urljoin(page["url"], raw_href)
        if not href_re.match(href) or href in seen:
            continue
        seen.add(href)
        offers.append(
            {
                "operator": operators,
                "title": text,
                "url": href,
                "source": page["url"],
            }
        )
        if len(offers) >= MAX_PER_PAGE:
            break
    return offers


async def _fetch(client: httpx.AsyncClient, url: str) -> tuple[str | None, str | None]:
    try:
        response = await client.get(url)
        if response.status_code != 200:
            return None, f"{url}: HTTP {response.status_code}"
        return response.text, None
    except Exception as exc:  # noqa: BLE001
        return None, f"{url}: {type(exc).__name__}"


async def _build() -> dict[str, Any]:
    errors: list[str] = []
    offers: list[dict[str, str]] = []
    async with httpx.AsyncClient(
        timeout=20, follow_redirects=True, headers={"User-Agent": UA}
    ) as client:
        results = await asyncio.gather(
            *[_fetch(client, page["url"]) for page in OFFER_PAGES]
        )

    for page, (body, error) in zip(OFFER_PAGES, results):
        if error:
            errors.append(error)
            continue
        offers.extend(_extract_offers(page, body or ""))

    # dedupe per titolo tenendo il primo
    unique: list[dict[str, str]] = []
    seen_titles: set[str] = set()
    for offer in offers:
        key = offer["title"].lower()
        if key in seen_titles:
            continue
        seen_titles.add(key)
        unique.append(offer)

    return {
        "updated_at": _now_iso(),
        "offers": unique,
        "coupons": COUPON_LINKS,
        "sources": [page["url"] for page in OFFER_PAGES],
        "errors": errors,
    }


def _now_iso() -> str:
    from ..timeutil import iso, now_utc

    return iso(now_utc())


async def get_promos(force: bool = False) -> dict[str, Any]:
    """Offerte e codici (con cache in memoria di 6 ore)."""
    if not force and _cache["data"] and time.time() - _cache["at"] < CACHE_TTL:
        return _cache["data"]
    async with _lock:
        if not force and _cache["data"] and time.time() - _cache["at"] < CACHE_TTL:
            return _cache["data"]
        data = await _build()
        if data["offers"] or data["coupons"]:
            _cache["data"] = data
            _cache["at"] = time.time()
        return data
