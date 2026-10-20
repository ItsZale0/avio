"""Avvia e cura il tunnel Cloudflare, e tiene allineato GitHub Pages.

Il tunnel "quick" (trycloudflare.com) assegna un URL **nuovo a ogni avvio**.
Se il frontend su GitHub Pages continua a puntare al vecchio indirizzo, la
dashboard resta "offline". Questo script:

1. avvia `cloudflared tunnel --url http://127.0.0.1:<porta>`;
2. aspetta che compaia l'URL pubblico;
3. aggiorna `CARPOOL_PUBLIC_URL` nel file `.env` locale;
4. scrive `backend.json` nel repo e lo pubblica: e' il puntatore **fresco**
   che il frontend legge direttamente da GitHub, senza aspettare il deploy
   di Pages (che ci mette un paio di minuti);
5. aggiorna la variabile di repository `CARPOOL_API_BASE` su GitHub, cosi'
   il prossimo deploy di Pages punterà al tunnel giusto;
6. lascia il tunnel in primo piano: premere CTRL+C lo chiude.

Uso:
    python scripts/tunnel.py                 # avvia e sincronizza
    python scripts/tunnel.py --no-github     # senza toccare GitHub
    python scripts/tunnel.py --port 8787
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
CLOUDFLARED = r"C:\Program Files (x86)\cloudflared\cloudflared.exe"


def find_cloudflared() -> str | None:
    """Cerca l'eseguibile di cloudflared."""
    candidates = [
        CLOUDFLARED,
        r"C:\Program Files\cloudflared\cloudflared.exe",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    found = shutil_which("cloudflared")
    return found


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)


def update_env(url: str) -> bool:
    """Scrive l'URL pubblico nel .env locale. True se e' cambiato."""
    env_path = BASE / ".env"
    if not env_path.exists():
        print("  .env non trovato: lo creo da .env.example", file=sys.stderr)
        example = BASE / ".env.example"
        if example.exists():
            env_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            return False

    lines = env_path.read_text(encoding="utf-8").splitlines()
    changed = False
    for index, line in enumerate(lines):
        if line.startswith("CARPOOL_PUBLIC_URL="):
            new_line = f"CARPOOL_PUBLIC_URL={url}"
            if line != new_line:
                lines[index] = new_line
                changed = True
            break
    else:
        lines.append(f"CARPOOL_PUBLIC_URL={url}")
        changed = True

    if changed:
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed


def publish_backend_pointer(url: str, repo: str = "ItsZale0/carpool-intelligence") -> bool:
    """Scrive `backend.json` nel repo e lo pubblica su GitHub.

    E' il puntatore che il frontend legge quando la config.json di Pages e'
    vecchia (il deploy ci mette un paio di minuti): appena il file e' nel
    branch, il browser lo vede subito su raw.githubusercontent.com.
    """
    pointer = BASE / "backend.json"
    content = json.dumps(
        {"apiBase": url, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
        indent=2,
    ) + "\n"
    try:
        if pointer.exists() and pointer.read_text(encoding="utf-8") == content:
            return True
        pointer.write_text(content, encoding="utf-8")
    except OSError as exc:
        print(f"  Scrittura di backend.json fallita: {exc}", file=sys.stderr)
        return False

    commands = [
        ["git", "add", "backend.json"],
        ["git", "commit", "-m", f"backend: tunnel {url}"],
        ["git", "push", "origin", "main"],
    ]
    for command in commands:
        try:
            result = subprocess.run(
                command, cwd=BASE, capture_output=True, text=True, timeout=60
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"  {' '.join(command)} fallito: {exc}", file=sys.stderr)
            return False
        if result.returncode != 0:
            # "nothing to commit" non e' un errore.
            if "nothing to commit" in (result.stdout or "") + (result.stderr or ""):
                continue
            print(
                f"  {' '.join(command)} fallito: {(result.stderr or result.stdout or '').strip()[:200]}",
                file=sys.stderr,
            )
            return False
    print("  Puntatore del backend pubblicato su GitHub (backend.json).")
    return True


def update_github_variable(url: str, repo: str = "ItsZale0/carpool-intelligence") -> bool:
    """Aggiorna CARPOOL_API_BASE nel repo GitHub e rilancia il deploy di Pages.

    Serve un token con scope `repo`: si legge dalla variabile d'ambiente
    GITHUB_TOKEN (o GH_TOKEN). Senza token la variabile non viene toccata.
    """
    token = (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()
    if not token:
        print("  GITHUB_TOKEN non impostato: salto l'aggiornamento del repo.")
        return False

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "carpool-tunnel",
    }
    base = f"https://api.github.com/repos/{repo}/actions/variables"
    body = json.dumps({"name": "CARPOOL_API_BASE", "value": url}).encode()

    try:
        request = urllib.request.Request(
            f"{base}/CARPOOL_API_BASE", data=body, method="PATCH", headers=headers
        )
        with urllib.request.urlopen(request, timeout=20):
            print(f"  Variabile GitHub aggiornata: CARPOOL_API_BASE = {url}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # La variabile non esiste ancora: creala.
            try:
                request = urllib.request.Request(
                    base, data=body, method="POST", headers=headers
                )
                with urllib.request.urlopen(request, timeout=20):
                    print(f"  Variabile GitHub creata: CARPOOL_API_BASE = {url}")
            except urllib.error.HTTPError as inner:
                print(f"  Errore GitHub: {inner}", file=sys.stderr)
                return False
        else:
            print(f"  Errore GitHub: {exc}", file=sys.stderr)
            return False
    except Exception as exc:  # noqa: BLE001
        print(f"  Errore GitHub: {exc}", file=sys.stderr)
        return False

    return redeploy_pages(token, repo)


def redeploy_pages(token: str, repo: str = "ItsZale0/carpool-intelligence") -> bool:
    """Rilancia il workflow 'pages.yml' cosi' config.json prende il nuovo URL."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "carpool-tunnel",
    }
    try:
        url = f"https://api.github.com/repos/{repo}/actions/workflows/pages.yml/runs?per_page=10"
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=20) as response:
            runs = json.loads(response.read().decode()).get("workflow_runs") or []
        if not runs:
            print("  Nessun run precedente del workflow: niente da rilanciare.")
            return False

        # GitHub rifiuta (403) il rilancio di un run ancora in corso: si
        # prende quindi l'ultimo run concluso.
        candidates = [r for r in runs if r.get("status") == "completed"] or runs
        run_id = candidates[0]["id"]
        body = json.dumps({"ref": "main"}).encode()
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/rerun",
            data=body,
            method="POST",
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=20):
            print("  Deploy di GitHub Pages rilanciato: la dashboard si aggiorna.")
        return True
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            # Il run precedente e' ancora in corso: il deploy partira' comunque.
            print("  Un deploy e' gia' in corso: non serve rilanciarlo.")
            return True
        print(f"  Rilancio del deploy non riuscito: {exc}", file=sys.stderr)
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"  Rilancio del deploy non riuscito: {exc}", file=sys.stderr)
        return False


def check_tunnel(url: str, attempts: int = 6, wait: int = 5) -> bool:
    """Verifica che il tunnel pubblico risponda.

    Cloudflare quick tunnel usa la porta 7844: se la rete la blocca, il
    tunnel non si registra e l'indirizzo risponde 530. Meglio accorgersene
    subito che aspettare una dashboard muta.
    """
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(f"{url.rstrip('/')}/api/auth/status")
            with urllib.request.urlopen(request, timeout=12) as response:
                response.read()
            return True
        except urllib.error.HTTPError as exc:
            if exc.code not in (502, 503, 530):
                return True  # risponde: e' un errore dell'app, non del tunnel
        except Exception:  # noqa: BLE001
            pass
        if attempt + 1 < attempts:
            time.sleep(wait)
    return False


def find_ngrok() -> str | None:
    """Cerca l'eseguibile di ngrok (percorso noto o PATH)."""
    candidates = [
        BASE / "ngrok.exe",
        Path(r"C:\Program Files\ngrok\ngrok.exe"),
        Path(r"C:\Program Files (x86)\ngrok\ngrok.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return shutil_which("ngrok")


def ngrok_token() -> str:
    """Il token ngrok dalla variabile d'ambiente o dal file .env."""
    token = (os.getenv("NGROK_AUTHTOKEN") or "").strip()
    if token:
        return token
    env_path = BASE / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("NGROK_AUTHTOKEN="):
                return line.split("=", 1)[1].strip()
    return ""


def start_ngrok(port: int) -> tuple[subprocess.Popen, str | None]:
    """Avvia ngrok e restituisce (processo, URL pubblico).

    ngrok gira sulla 443 (quindi passa anche dove la 7844 e' bloccata) e
    con un account gratuito dà un indirizzo **stabile**.
    """
    binary = find_ngrok()
    if not binary:
        raise RuntimeError("ngrok non trovato")
    token = ngrok_token()
    if not token:
        raise RuntimeError("manca NGROK_AUTHTOKEN")
    command = [binary, "http", str(port), "--log", "stdout", "--log-format", "logfmt"]
    env = dict(os.environ)
    env["NGROK_AUTHTOKEN"] = token
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
    )
    url: str | None = None
    deadline = time.time() + 40
    for line in process.stdout:  # type: ignore[union-attr]
        sys.stdout.write("  " + line)
        sys.stdout.flush()
        match = re.search(r"url=(\S+)", line)
        if match:
            url = match.group(1).strip().rstrip('"')
            break
        if time.time() > deadline:
            break
    return process, url


def start_cloudflared(port: int) -> tuple[subprocess.Popen, str | None]:
    """Avvia cloudflared e restituisce (processo, URL pubblico)."""
    binary = find_cloudflared()
    if not binary:
        raise RuntimeError("cloudflared non trovato")
    print("Avvio il tunnel verso http://127.0.0.1:%d ..." % port)
    # http2 invece di quic: su reti domestiche il QUIC cade spesso
    # ("timeout: no recent network activity") e il tunnel si interrompe.
    command = [
        binary, "tunnel", "--url", f"http://127.0.0.1:{port}",
        "--no-autoupdate", "--protocol", "http2",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    url: str | None = None
    deadline = time.time() + 40
    for line in process.stdout:  # type: ignore[union-attr]
        sys.stdout.write("  " + line)
        sys.stdout.flush()
        match = URL_PATTERN.search(line)
        if match:
            url = match.group(0)
            break
        if time.time() > deadline:
            break
    return process, url


def main() -> int:
    parser = argparse.ArgumentParser(description="Tunnel pubblico per Carpool Hunter")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-github", action="store_true", help="non toccare GitHub")
    parser.add_argument(
        "--provider",
        choices=("auto", "cloudflared", "ngrok"),
        default="auto",
        help="auto: ngrok se configurato, altrimenti Cloudflare",
    )
    args = parser.parse_args()

    # ngrok gira sulla 443 e dà un URL stabile: se è configurato, è la scelta
    # migliore. Altrimenti si prova Cloudflare.
    providers: list[str] = []
    if args.provider in ("auto", "ngrok") and find_ngrok() and ngrok_token():
        providers.append("ngrok")
    if args.provider in ("auto", "cloudflared"):
        providers.append("cloudflared")

    if not providers:
        print(
            "Nessun tunnel disponibile.\n"
            "  - Cloudflare:  winget install --id Cloudflare.cloudflared\n"
            "  - ngrok (URL stabile): scarica l'eseguibile, mettilo nella cartella\n"
            "    del progetto e imposta NGROK_AUTHTOKEN (authtoken su ngrok.com).",
            file=sys.stderr,
        )
        return 1

    process: subprocess.Popen | None = None
    url: str | None = None
    provider = ""
    for provider in providers:
        print(f"\n=== Tunnel {provider} verso http://127.0.0.1:{args.port} ===")
        try:
            if provider == "ngrok":
                process, url = start_ngrok(args.port)
            else:
                process, url = start_cloudflared(args.port)
        except Exception as exc:  # noqa: BLE001
            print(f"  {provider} non avviabile: {exc}", file=sys.stderr)
            continue
        if not url:
            print(f"  {provider}: nessun URL ricevuto.", file=sys.stderr)
            if process is not None and process.poll() is None:
                _terminate_tree(process)
            process = None
            continue
        break

    if process is None or not url:
        return 1

    try:
        print(f"\nTunnel attivo ({provider}): {url}")
        if update_env(url):
            print("  .env aggiornato (CARPOOL_PUBLIC_URL)")
        if not args.no_github:
            publish_backend_pointer(url)
            update_github_variable(url)
        print("  Verifico che il tunnel sia collegato...")
        if check_tunnel(url):
            print("  Tunnel OK: l'indirizzo pubblico risponde.")
        else:
            print(
                "  ATTENZIONE: il tunnel non risponde.\n"
                "    Il tunnel Cloudflare usa la porta 7844: se la rete (router, "
                "ISP, filtro) la blocca,\n"
                "    il collegamento non si stabilisce e la dashboard su "
                "GitHub Pages resta muta.\n"
                "    Prove: cambia rete (es. hotspot del telefono), riavvia il "
                "router,\n"
                "    oppure usa ngrok (lavora sulla 443): scaricalo nella "
                "cartella del progetto\n"
                "    e imposta NGROK_AUTHTOKEN. L'app in locale "
                "(http://127.0.0.1:8787) funziona comunque.",
                file=sys.stderr,
            )
        print("  Per aggiornare anche la dashboard su GitHub Pages:")
        print("    -> Actions -> Deploy frontend su GitHub Pages -> Re-run all jobs")
        print("\nPremi CTRL+C per chiudere il tunnel.")
        process.wait()
    except KeyboardInterrupt:
        print("\nChiudo il tunnel.")
    finally:
        if process is not None and process.poll() is None:
            _terminate_tree(process)
    return 0


def _terminate_tree(process: subprocess.Popen) -> None:
    """Chiude il tunnel e tutti i suoi processi figli.

    Su Windows `terminate()` uccide solo il processo diretto: cloudflared
    puo' aver avviato un helper che resta vivo. `taskkill /T` chiude
    l'intero albero.
    """
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
