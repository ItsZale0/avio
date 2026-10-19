"""Catalogo dei feed GTFS aperti: orari reali di treni e bus.

Solo fonti ufficiali con licenza aperta. Nessun dato viene inventato: quello che
non e' nel feed (es. il prezzo) non viene mostrato come se fosse reale.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from ..config import DATA_DIR, USER_AGENT

GTFS_DIR = DATA_DIR / "gtfs"

FEEDS: list[dict] = [
    {
        "id": "trentino-extraurbano",
        "label": "Trentino Trasporti · extraurbano (bus + treni)",
        "region": "Trentino",
        "url": "https://www.trentinotrasporti.it/opendata/google_transit_extraurbano_tte.zip",
        "license": "CC BY 4.0",
        "site": "https://www.trentinotrasporti.it/en/opendata-en",
    },
    {
        "id": "trentino-urbano",
        "label": "Trentino Trasporti · urbano (Trento e Rovereto)",
        "region": "Trentino",
        "url": "https://www.trentinotrasporti.it/opendata/google_transit_urbano_tte.zip",
        "license": "CC BY 4.0",
        "site": "https://www.trentinotrasporti.it/en/opendata-en",
    },
    {
        "id": "trenord",
        "label": "Trenord · treni regionali della Lombardia",
        "region": "Lombardia",
        "url": "https://www.dati.lombardia.it/download/3z4k-mxz9/application/zip",
        "license": "CC BY 4.0",
        "site": "https://www.dati.lombardia.it/Mobilit-e-trasporti/Orario-Ferroviario-Regionale-Gtfs/3z4k-mxz9",
    },
    {
        "id": "busitalia-veneto-tram",
        "label": "Busitalia Veneto · tram (Padova)",
        "region": "Veneto",
        "url": "https://gtfs-biv.fsbusitalia.com/GTFS-BIV-TRAM/gtfs-biv-tram.zip",
        "license": "CC BY 4.0",
        "site": "https://gtfs-biv.fsbusitalia.com/",
    },
]

FEED_BY_ID = {feed["id"]: feed for feed in FEEDS}


def feed_dir(feed_id: str) -> Path:
    return GTFS_DIR / feed_id


def download(feed_id: str, *, force: bool = False, timeout: float = 180) -> Path:
    """Scarica (se serve) il feed e restituisce il percorso dello zip locale."""
    feed = FEED_BY_ID.get(feed_id)
    if feed is None:
        raise KeyError(f"feed sconosciuto: {feed_id}")
    target_dir = feed_dir(feed_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "feed.zip"
    if target.exists() and not force:
        return target
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
        with client.stream("GET", feed["url"]) as response:
            response.raise_for_status()
            with target.open("wb") as handle:
                for chunk in response.iter_bytes(1 << 16):
                    handle.write(chunk)
    return target
