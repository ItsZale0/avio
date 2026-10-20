"""Stato dell'agente: processi, URL pubblico e ultime righe di log.

Uso:
    python scripts\\agent_status.py
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data"
LOG_DIR = DATA_DIR / "logs"
PID_FILE = DATA_DIR / "agent.pid"
ENV_FILE = BASE / ".env"


def _running(pid: int) -> bool:
    """True se il processo esiste ancora.

    Su Windows `os.kill(pid, 0)` non va bene: si controlla con tasklist.
    """
    import subprocess

    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True,
        text=True,
    )
    return str(pid) in (result.stdout or "")


def _supervisor_pids() -> list[int]:
    """PID dei processi che stanno eseguendo run_agent.py.

    Il venv di Windows lancia l'interprete reale in un processo separato:
    per questo non basta il PID salvato, si cerca per riga di comando.
    """
    import subprocess

    marker = "run_agent.py"
    if os.name != "nt":
        result = subprocess.run(
            ["pgrep", "-f", marker], capture_output=True, text=True
        )
        return [int(p) for p in result.stdout.split() if p.strip().isdigit()]

    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process | "
            f"Where-Object {{ $_.CommandLine -like '*{marker}*' }} | "
            "ForEach-Object { $_.ProcessId }",
        ],
        capture_output=True,
        text=True,
    )
    pids: list[int] = []
    for line in (result.stdout or "").splitlines():
        stripped = line.strip()
        if stripped.isdigit():
            pids.append(int(stripped))
    return pids


def _check_tunnel(public: str) -> str:
    """Verifica che il tunnel pubblico risponda davvero.

    Cloudflare quick tunnel usa la porta 7844: se la rete (router, ISP,
    filtro DPI) la blocca, il tunnel non si registra e l'indirizzo risponde
    con un errore 530. Meglio dirlo chiaramente che lasciare una dashboard
    muta.
    """
    request = urllib.request.Request(f"{public.rstrip('/')}/api/auth/status")
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            response.read()
        return "collegato (risponde)"
    except urllib.error.HTTPError as exc:
        if exc.code in (502, 503, 530):
            return (
                f"NON collegato (HTTP {exc.code}): il tunnel non ha raggiunto "
                "l'edge Cloudflare. Causa tipica: la rete blocca la porta 7844."
            )
        return f"non risponde (HTTP {exc.code})"
    except Exception as exc:  # noqa: BLE001
        return (
            f"non raggiungibile da questa rete ({type(exc).__name__}): "
            "il tunnel c'e', ma questa rete lo blocca (filtro del router "
            "o ISP). Da un'altra rete funziona."
        )


def _ip_di_rete() -> str:
    """L'IP di rete del PC (serve per aprirlo dal telefono)."""
    import socket

    try:
        # Non manda nulla: sceglie l'interfaccia con cui si esce.
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.255.255.255", 1))
        indirizzo = probe.getsockname()[0]
        probe.close()
        return str(indirizzo)
    except Exception:  # noqa: BLE001
        return ""


def main() -> int:
    print("=" * 58)
    print("CARPOOL HUNTER - stato")
    print("=" * 58)

    pids = _supervisor_pids()
    if pids:
        print(f"Supervisore: attivo (pid {', '.join(str(p) for p in pids)})")
    elif PID_FILE.exists():
        print("Supervisore: NON in esecuzione")
    else:
        print("Supervisore: non registrato (l'agente non e' avviato)")

    # Indirizzo locale: con HOST=0.0.0.0 l'app si apre anche dal telefono.
    porta = "8787"
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("PORT="):
                porta = line.split("=", 1)[1].strip() or porta
    ip = _ip_di_rete()
    if ip:
        print(f"IP locale:     http://{ip}:{porta}   (dal telefono, stesso Wi-Fi)")
    print(f"Sul PC:        http://127.0.0.1:{porta}")

    # URL pubblico
    public = ""
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("CARPOOL_PUBLIC_URL="):
                public = line.split("=", 1)[1].strip()
    print(f"URL pubblico: {public or '(non configurato)'}")

    # Il tunnel e' davvero collegato? Se la rete blocca la porta 7844 di
    # Cloudflare, il tunnel non si registra e la dashboard resta muta.
    if public:
        print(f"Tunnel: {_check_tunnel(public)}")

    # API locale
    try:
        request = urllib.request.Request("http://127.0.0.1:8787/api/auth/status")
        with urllib.request.urlopen(request, timeout=5) as response:
            data = json.loads(response.read().decode())
        if data.get("authenticated"):
            print("API locale: OK  (sessione attiva)")
        else:
            print("API locale: OK  (serve il login)")
    except Exception:  # noqa: BLE001
        print("API locale: non risponde")

    # Log
    log = LOG_DIR / "agent.log"
    if log.exists():
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-8:]
        print("\nUltime righe di log (data\\logs\\agent.log):")
        for line in lines:
            print("  " + line)
    else:
        print("\nNessun log in data\\logs\\.")

    print("\nPer fermare l'agente:  python scripts\\stop_agent.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
