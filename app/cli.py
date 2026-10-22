"""Comandi da riga di comando.

Utili per lanciare un giro di ricerca a mano o da un cron esterno
(es. GitHub Actions) senza tenere il server sempre acceso.

Uso:
    python -m app.cli run-due            # esegue solo le ricerche scadute
    python -m app.cli run-due --all      # forza tutte le ricerche attive
    python -m app.cli list               # elenca le ricerche
    python -m app.cli users              # elenca gli account
    python -m app.cli user-add NOME      # crea un account (amministratore se e' il primo)
    python -m app.cli cert-check         # controlla il certificato HTTPS
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import ssl
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from . import auth, db, store
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


def _cert_scadenza(percorso: str) -> datetime | None:
    """Quando scade il certificato PEM. None se non si capisce."""
    try:
        # Funzione standard (non pubblica) ma la piu' affidabile su Linux.
        info = ssl._ssl._test_decode_cert(percorso)  # type: ignore[attr-defined]
        return datetime.strptime(info["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(
            tzinfo=timezone.utc
        )
    except Exception:
        pass
    try:
        esito = subprocess.run(
            ["openssl", "x509", "-enddate", "-noout", "-in", percorso],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if esito.returncode == 0 and "notAfter=" in esito.stdout:
            return datetime.strptime(
                esito.stdout.split("notAfter=")[1].strip(), "%b %d %H:%M:%S %Y %Z"
            ).replace(tzinfo=timezone.utc)
    except Exception:
        pass
    return None


def _cerca_certificato(percorso: str) -> str:
    """Il certificato da controllare: l'indicato, la variabile d'ambiente
    o quello di Let's Encrypt (l'unica cartella con dentro l'IP)."""
    if percorso:
        return percorso
    percorso = (os.getenv("CARPOOL_TLS_CERT") or "").strip()
    if percorso:
        return percorso
    base = "/etc/letsencrypt/live"
    if os.path.isdir(base):
        for nome in sorted(os.listdir(base)):
            candidato = os.path.join(base, nome, "fullchain.pem")
            if os.path.exists(candidato):
                return candidato
    return ""


async def _avvisa_certificato(titolo: str, testo: str) -> None:
    """L'avviso esce dai canali gia' configurati (Telegram, email)."""
    from . import notify

    try:
        ok, errore = await notify.send_telegram(f"{titolo}\n\n{testo}")
        print(f"  Telegram: {'inviato' if ok else 'non inviato'} ({errore})")
    except Exception as exc:
        print(f"  Telegram: errore {exc}", file=sys.stderr)
    try:
        ok, errore = await notify.send_email(titolo, testo)
        print(f"  Email: {'inviato' if ok else 'non inviato'} ({errore})")
    except Exception as exc:
        print(f"  Email: errore {exc}", file=sys.stderr)


def _cert_check(percorso: str, warn_days: int) -> int:
    """Controlla la scadenza del certificato e avvisa se sta per finire.

    Serve per i certificati brevi (6 giorni) che si emettono per l'IP
    pubblico: se il rinnovo fallisce, l'app resta senza HTTPS e senza
    accetto. Il controllo gira ogni ora da un timer systemd.
    """
    db.init_db()
    percorso = _cerca_certificato(percorso)
    if not percorso or not os.path.exists(percorso):
        asyncio.run(
            _avvisa_certificato(
                "AVIO: certificato HTTPS assente",
                f"Il file {percorso or '(non trovato)'} non c'è.\n"
                "L'app non accetterà più collegamenti finché non è rinnovato.",
            )
        )
        return 1
    scadenza = _cert_scadenza(percorso)
    if scadenza is None:
        print(f"  Non riesco a leggere la scadenza di {percorso}")
        return 1
    giorni = (scadenza - datetime.now(timezone.utc)).total_seconds() / 86400
    print(f"  Certificato: {percorso}")
    print(f"  Scade: {scadenza.isoformat()} (fra {giorni:.1f} giorni)")
    if giorni < warn_days:
        asyncio.run(
            _avvisa_certificato(
                "AVIO: il certificato HTTPS sta per scadere",
                f"Scade fra {giorni:.1f} giorni ({scadenza.isoformat()}).\n"
                "Se il rinnovo automatico fallisce, l'app resta senza HTTPS.",
            )
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="AVIO da riga di comando")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run-due", help="esegue le ricerche scadute una volta")
    run_parser.add_argument("--all", action="store_true", help="forza tutte le ricerche attive")
    run_parser.add_argument("--limit", type=int, default=5)

    sub.add_parser("list", help="elenca le ricerche salvate")

    sub.add_parser("users", help="elenca gli account e il loro stato")

    user_parser = sub.add_parser("user-add", help="crea un account")
    user_parser.add_argument("username", help="nome utente")
    user_parser.add_argument("--password", help="password (chiede l'interattivamente se omessa)")
    user_parser.add_argument("--admin", action="store_true", help="ruolo amministratore")

    auth_parser = sub.add_parser("auth", help="alias di 'access' (deprecated)")
    auth_parser.add_argument("state", choices=("on", "off"), help="on = chiede la password")

    access_parser = sub.add_parser("access", help="password d'accesso e chiave segreta")
    access_parser.add_argument(
        "action", choices=("set", "clear", "key", "status"), help="cosa fare"
    )
    access_parser.add_argument("--password", help="password (se non e' indicata viene chiesta)")

    sub.add_parser("gtfs-list", help="mostra i feed GTFS e lo stato dell'import")

    gtfs_parser = sub.add_parser("gtfs-update", help="scarica e importa gli orari GTFS")
    gtfs_parser.add_argument("--feed", action="append", help="id del feed (ripetibile); default: tutti")
    gtfs_parser.add_argument("--force", action="store_true", help="riscarica anche se gia' presente")

    cert_parser = sub.add_parser(
        "cert-check", help="controlla il certificato HTTPS e avvisa se sta per scadere"
    )
    cert_parser.add_argument("--cert", default="", help="percorso del certificato PEM")
    cert_parser.add_argument("--warn-days", type=int, default=3, help="giorni di preavviso")

    args = parser.parse_args(argv)

    if args.command == "list":
        for watch in store.list_watches():
            state = "attiva" if watch["active"] else "in pausa"
            print(
                f"[{watch['id']}] {watch['from_place']} → {watch['to_place']} · {state}"
                f" · ogni {watch['interval_min']} min · {watch['offers_count']} offerte"
            )
        return 0

    if args.command == "users":
        db.init_db()
        for user in auth.list_users():
            flags = []
            if user.get("is_admin"):
                flags.append("admin")
            flags.append(user.get("status") or "?")
            print(f"[{user['id']}] {user['username']} · {', '.join(flags)}")
        return 0

    if args.command == "user-add":
        db.init_db()
        password = args.password or getpass.getpass("Password: ")
        try:
            user = auth.create_user(
                args.username,
                password,
                is_admin=args.admin or auth.count_users() == 0,
                status=auth.STATUS_ACTIVE,
            )
        except ValueError as exc:
            print(f"Errore: {exc}", file=sys.stderr)
            return 1
        if user:
            print(f"Creato: {user['username']} ({'admin' if user['is_admin'] else 'member'})")
        return 0

    if args.command == "auth":
        # Alias comodo: 'auth on' imposta la password, 'auth off' la toglie.
        from . import access

        db.init_db()
        if args.state == "off":
            access.set_password("")
            access.clear_key()
            print("Accesso disattivato: l'app si apre direttamente.")
        else:
            password = getpass.getpass("Password d'accesso: ")
            try:
                access.set_password(password)
            except ValueError as exc:
                print(f"Errore: {exc}", file=sys.stderr)
                return 1
            print("Password d'accesso impostata.")
        return 0

    if args.command == "access":
        from . import access

        db.init_db()
        if args.action == "status":
            print(f"Password impostata: {'si' if access.has_password() else 'no'}")
            print(f"Chiave segreta attiva: {'si' if access.has_key() else 'no'}")
            return 0
        if args.action == "clear":
            access.set_password("")
            access.clear_key()
            print("Password e chiave rimosse: l'app si apre senza accesso.")
            return 0
        if args.action == "key":
            key = access.new_key()
            print("Chiave segreta (si vede solo adesso):")
            print(f"  {key}")
            print("\nUsala nell'URL:  https://<indirizzo>/?key=" + key)
            return 0
        # action == "set"
        password = args.password or getpass.getpass("Password d'accesso: ")
        try:
            access.set_password(password)
        except ValueError as exc:
            print(f"Errore: {exc}", file=sys.stderr)
            return 1
        access.revoke_all_sessions()
        print("Password d'accesso impostata.")
        return 0

    if args.command == "cert-check":
        return _cert_check(args.cert, args.warn_days)

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
