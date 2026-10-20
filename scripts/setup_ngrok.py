"""Configura ngrok: salva l'authtoken, riavvia l'agente e verifica il tunnel.

Uso (il token NON va scritto nella cronologia della shell):

    $env:NGROK_AUTHTOKEN="2abcDEF..."; python scripts\setup_ngrok.py

oppure, in alternativa, dalla web: Impostazioni -> "Accesso da fuori (tunnel)".
In quel caso questo script legge il token gia' salvato e si limita a riavviare
l'agente e verificare.

Cosa fa:
1. salva l'authtoken nelle impostazioni dell'app (tabella `settings`) e nel
   file `.env` come copia di sicurezza;
2. lo registra anche nella configurazione di ngrok, cosi' `ngrok` da riga di
   comando funziona;
3. riavvia l'agente (che avvia il tunnel ngrok al posto di Cloudflare);
4. aspetta che il tunnel risponda e stampa l'indirizzo pubblico.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


def save_token(token: str) -> None:
    """Salva il token nelle impostazioni dell'app e nel .env."""
    from app.services import settings as app_settings

    app_settings.update({"ngrok_authtoken": token})
    env_path = BASE / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    kept = [line for line in lines if not line.startswith("NGROK_AUTHTOKEN=")]
    kept.append(f"NGROK_AUTHTOKEN={token}")
    env_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    print("  Token salvato nelle impostazioni e nel .env")


def register_with_ngrok(token: str) -> None:
    """Registra il token nella configurazione di ngrok (per la CLI)."""
    binary = os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps", "ngrok.exe"
    )
    if not os.path.exists(binary):
        return
    try:
        subprocess.run(
            [binary, "config", "add-authtoken", token],
            capture_output=True,
            text=True,
            timeout=30,
        )
        print("  Token registrato nella configurazione di ngrok")
    except Exception as exc:  # noqa: BLE001
        print(f"  (config ngrok non aggiornata: {exc})")


def restart_agent() -> None:
    print("  Riavvio dell'agente...")
    subprocess.run(
        [sys.executable, str(BASE / "scripts" / "stop_agent.py")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    time.sleep(4)
    subprocess.run(
        [
            "taskkill",
            "/IM",
            "cloudflared.exe",
            "/F",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    subprocess.run(
        [
            "taskkill",
            "/IM",
            "ngrok.exe",
            "/F",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    time.sleep(3)
    subprocess.Popen(
        [
            "wscript.exe",
            str(BASE / "avvia-agente.vbs"),
        ],
        cwd=str(BASE),
    )


def wait_for_tunnel(seconds: int = 90) -> str | None:
    """Aspetta che l'indirizzo pubblico risponda. Restituisce l'URL."""
    env_path = BASE / ".env"
    deadline = time.time() + seconds
    url = ""
    while time.time() < deadline:
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("CARPOOL_PUBLIC_URL="):
                    url = line.split("=", 1)[1].strip()
        if url:
            try:
                request = urllib.request.Request(f"{url.rstrip('/')}/api/auth/status")
                with urllib.request.urlopen(request, timeout=10) as response:
                    response.read()
                return url
            except Exception:  # noqa: BLE001
                pass
        time.sleep(4)
    return None


def main() -> int:
    token = (os.getenv("NGROK_AUTHTOKEN") or "").strip()
    if not token and not sys.stdin.isatty():
        token = sys.stdin.read().strip()

    if token:
        print("1. Salvataggio del token")
        save_token(token)
        register_with_ngrok(token)
    else:
        print("1. Uso il token gia' salvato nelle impostazioni")

    print("2. Riavvio dell'agente")
    restart_agent()

    print("3. Attesa del tunnel")
    url = wait_for_tunnel()
    if not url:
        print("   Il tunnel non ha risposto entro 90 secondi.")
        print("   Controlla: python scripts\\agent_status.py")
        return 1

    print(f"   Tunnel attivo: {url}")
    print(f"   Dashboard:      {url}")
    print()
    print("   Il link GitHub Pages usa gia' questo indirizzo:")
    print("   https://itszale0.github.io/carpool-intelligence/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
