"""Ferma l'agente: scrive il file di stop e chiude il supervisore.

Uso:
    python scripts\\stop_agent.py
"""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data"
PID_FILE = DATA_DIR / "agent.pid"
STOP_FILE = DATA_DIR / "agent.stop"


def _supervisor_pids() -> list[int]:
    """PID dei processi che stanno eseguendo run_agent.py.

    Il venv di Windows avvia l'interprete reale in un processo separato:
    per questo il PID salvato non basta, si cerca per riga di comando.
    """
    import subprocess

    marker = "run_agent.py"
    if os.name != "nt":
        result = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
        return [int(p) for p in result.stdout.split() if p.strip().isdigit()]

    # wmic non esiste piu' su Windows recenti: si usa PowerShell con CIM.
    command = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.CommandLine -like '*{marker}*' }} | "
        "ForEach-Object { $_.ProcessId }"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
    )
    pids: list[int] = []
    for line in (result.stdout or "").splitlines():
        stripped = line.strip()
        if stripped.isdigit():
            pids.append(int(stripped))
    return pids


def _kill(pid: int, force: bool = False) -> None:
    """Termina un processo.

    Su Windows `os.kill` con SIGTERM non funziona bene: si usa taskkill
    (e `signal.SIGKILL` non esiste affatto).
    """
    import subprocess

    if os.name == "nt":
        command = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            command.append("/F")
        subprocess.run(command, capture_output=True, text=True)
        return
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except OSError:
        pass


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STOP_FILE.write_text("stop", encoding="utf-8")
    print("Chiusura richiesta al supervisore...")

    pids = _supervisor_pids()
    if not pids and PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            pid = 0
        if pid:
            pids = [pid]

    for pid in pids:
        _kill(pid)
        print(f"  pid {pid}: chiusura richiesta.")

    # Dà tempo al supervisore di chiudere i figli.
    for _ in range(15):
        time.sleep(1)
        if not _supervisor_pids():
            break
    else:
        for pid in _supervisor_pids():
            _kill(pid, force=True)

    STOP_FILE.unlink(missing_ok=True)
    PID_FILE.unlink(missing_ok=True)
    print("Agente fermato.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
