"""Orari pubblici in formato GTFS: catalogo feed, import e coincidenze."""

from __future__ import annotations

from typing import Callable

from . import db, engine, importer
from .feeds import FEEDS, FEED_BY_ID, download, feed_dir

__all__ = ["FEEDS", "FEED_BY_ID", "download", "feed_dir", "engine", "db", "importer", "status", "update"]


def update(
    feed_ids: list[str] | None = None,
    *,
    force: bool = False,
    progress: Callable[[str], None] | None = None,
) -> list[dict]:
    """Scarica (se serve) e importa i feed indicati. Restituisce l'esito per feed."""
    selected = feed_ids or [feed["id"] for feed in FEEDS]
    results: list[dict] = []
    for feed_id in selected:
        if feed_id not in FEED_BY_ID:
            results.append({"feed": feed_id, "status": "sconosciuto"})
            continue
        try:
            if progress:
                progress(f"Scarico {feed_id}…")
            path = download(feed_id, force=force)
            if progress:
                progress(f"Importo {feed_id}…")
            counts = importer.import_feed(feed_id, path)
            results.append({"feed": feed_id, "status": "ok", **counts})
        except Exception as exc:  # noqa: BLE001
            results.append({"feed": feed_id, "status": "errore", "error": f"{type(exc).__name__}: {exc}"})
    return results


def status() -> list[dict]:
    imported = {row["id"]: row for row in db.imported_feeds()}
    rows: list[dict] = []
    for feed in FEEDS:
        info = imported.get(feed["id"], {})
        rows.append(
            {
                "id": feed["id"],
                "label": feed["label"],
                "region": feed["region"],
                "url": feed["url"],
                "site": feed.get("site"),
                "license": feed.get("license"),
                "imported": bool(info.get("imported_at")),
                "imported_at": info.get("imported_at"),
                "stops": int(info.get("stops") or 0),
                "trips": int(info.get("trips") or 0),
                "bytes": int(info.get("bytes") or 0),
            }
        )
    return rows
