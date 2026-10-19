"""Comandi da riga di comando.

Utili per lanciare un giro di ricerca a mano o da un cron esterno
(es. GitHub Actions) senza tenere il server sempre acceso.

Uso:
    python -m app.cli run-due            # esegue solo le ricerche scadute
    python -m app.cli run-due --all      # forza tutte le ricerche attive
    python -m app.cli list               # elenca le ricerche
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from . import db, store
from .gtfs import status as gtfs_status
from .gtfs import update as gtfs_update
from .services import search


async def _run(force_all: bool, limit: int) -> int:
    db.init_db()
    store.migrate_sources()
    if force_all:
        watches = [w for w in store.list_watches() if w["active"]][:limit]
    else:
        watches = store.due_watches(limit=limit)

    if not watches:
        print("Nessuna ricerca da eseguire.")
        return 0

    failures = 0
    for watch in watches:
        print(f"→ {watch['from_place']} → {watch['to_place']} (id {watch['id']})", flush=True)
        result = await search.execute_run(watch["id"], "cron")
        print("  " + json.dumps(result, ensure_ascii=False, default=str)[:400], flush=True)
        if result.get("status") == "error":
            failures += 1
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="Carpool Hunter da riga di comando")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run-due", help="esegue le ricerche scadute una volta")
    run_parser.add_argument("--all", action="store_true", help="forza tutte le ricerche attive")
    run_parser.add_argument("--limit", type=int, default=5)

    sub.add_parser("list", help="elenca le ricerche salvate")

    sub.add_parser("gtfs-list", help="mostra i feed GTFS e lo stato dell'import")

    gtfs_parser = sub.add_parser("gtfs-update", help="scarica e importa gli orari GTFS")
    gtfs_parser.add_argument("--feed", action="append", help="id del feed (ripetibile); default: tutti")
    gtfs_parser.add_argument("--force", action="store_true", help="riscarica anche se gia' presente")

    args = parser.parse_args(argv)

    if args.command == "list":
        for watch in store.list_watches():
            state = "attiva" if watch["active"] else "in pausa"
            print(
                f"[{watch['id']}] {watch['from_place']} → {watch['to_place']} · {state}"
                f" · ogni {watch['interval_min']} min · {watch['offers_count']} offerte"
            )
        return 0

    if args.command == "gtfs-list":
        for row in gtfs_status():
            state = f"importato {row['imported_at'][:19]}" if row["imported"] else "non importato"
            print(f"[{row['id']}] {row['label']} · {row['region']} · {state} · {row['stops']} fermate")
        return 0

    if args.command == "gtfs-update":
        def echo(message: str) -> None:
            print("  " + message, flush=True)

        results = gtfs_update(args.feed, force=args.force, progress=echo)
        failures = 0
        for row in results:
            print(f"→ {row['feed']}: {row.get('status')}")
            if row.get("status") != "ok":
                failures += 1
        return 1 if failures else 0

    return asyncio.run(_run(args.all, max(1, args.limit)))


if __name__ == "__main__":
    sys.exit(main())
