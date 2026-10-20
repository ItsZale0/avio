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

    # URL pubblico
    public = ""
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("CARPOOL_PUBLIC_URL="):
                public = line.split("=", 1)[1].strip()
    print(f"URL pubblico: {public or '(non configurato)'}")

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
