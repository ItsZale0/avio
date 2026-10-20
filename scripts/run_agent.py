"""Supervisore dell'agente: lancia i processi senza finestre di terminale.

Due processi, entrambi senza console:
- l'agente (FastAPI/uvicorn) su 127.0.0.1:8787;
- il tunnel Cloudflare, che allinea .env, variabile GitHub e deploy di Pages.

I log vanno su file (data/logs/), cosi' si controlla anche a processo spento.
Il supervisore rilancia i figli se muoiono, e si chiude con un file di
"stop" oppure con un Ctrl+C.

Uso (normale, con console per vedere i log):
    python scripts/run_agent.py

Uso (senza finestre, da avvio automatico):
    .venv\Scripts\pythonw.exe scripts\run_agent.py
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data"
LOG_DIR = DATA_DIR / "logs"
PID_FILE = DATA_DIR / "agent.pid"
STOP_FILE = DATA_DIR / "agent.stop"

PORT = int(os.getenv("PORT", "8787"))
RESTART_DELAY = 5  # secondi fra un riavvio e l'altro


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _log(stream_name: str, message: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / "agent.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{_now()} [{stream_name}] {message.rstrip()}\n")
    except Exception:  # noqa: BLE001
        pass


def _python() -> str:
    """L'interprete da usare per i figli: pythonw se esiste (niente console)."""
    candidate = BASE / ".venv" / "Scripts" / ("pythonw.exe" if os.name == "nt" else "python")
    if candidate.exists():
        return str(candidate)
    return sys.executable


def _start(name: str, args: list[str], env: dict[str, str] | None = None) -> subprocess.Popen:
    """Avvia un figlio senza finestra, con l'output su file."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out = (LOG_DIR / f"{name}.log").open("a", encoding="utf-8")
    creationflags = 0
    if os.name == "nt":
        # CREATE_NO_WINDOW: nessuna finestra di console.
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        args,
        cwd=str(BASE),
        stdout=out,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env={**os.environ, **(env or {})},
        creationflags=creationflags,
    )
    _log("supervisore", f"{name} avviato (pid {process.pid})")
    return process


def _should_stop() -> bool:
    return STOP_FILE.exists()


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STOP_FILE.unlink(missing_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

    python = _python()
    _log("supervisore", f"avvio (pid {os.getpid()}, interprete {python})")

    stopping = {"value": False}

    def _handle_signal(signum, _frame):
        stopping["value"] = True
        _log("supervisore", f"segnale {signum}: chiusura in corso")

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    children: dict[str, subprocess.Popen] = {}

    def start_all() -> None:
        children["agente"] = _start(
            "agente",
            [
                python,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(PORT),
                "--log-level",
                "warning",
            ],
        )
        children["tunnel"] = _start(
            "tunnel", [python, "-u", "scripts/tunnel.py", "--port", str(PORT)]
        )

    try:
        start_all()
        while not stopping["value"] and not _should_stop():
            time.sleep(2)
            for name, process in list(children.items()):
                if process.poll() is None:
                    continue
                # Il figlio e' morto: lo rilancio.
                _log("supervisore", f"{name} terminato (codice {process.returncode}): rilancio")
                if name == "agente":
                    children[name] = _start(
                        "agente",
                        [
                            python,
                            "-m",
                            "uvicorn",
                            "app.main:app",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            str(PORT),
                            "--log-level",
                            "warning",
                        ],
                    )
                else:
                    children[name] = _start(
                        "tunnel", [python, "-u", "scripts/tunnel.py", "--port", str(PORT)]
                    )
                time.sleep(RESTART_DELAY)
    finally:
        _log("supervisore", "chiusura dei processi figli")
        for name, process in children.items():
            if process.poll() is None:
                _terminate_tree(process)
        PID_FILE.unlink(missing_ok=True)
        STOP_FILE.unlink(missing_ok=True)
        _log("supervisore", "arrestato")
    return 0


def _terminate_tree(process: subprocess.Popen) -> None:
    """Chiude un processo e i suoi figli.

    Su Windows `terminate()` uccide solo il processo diretto: cloudflared
    lascia spesso un helper vivo. `taskkill /T` chiude l'albero intero.
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
