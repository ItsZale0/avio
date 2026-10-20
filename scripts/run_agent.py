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

# Le impostazioni nel file .env valgono anche per il supervisore.
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
try:
    from app.config import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001 - senza .env si va con i valori di sotto
    pass

PORT = int(os.getenv("PORT", "8787"))
# Dove il server ascolta. Di default solo sul PC (127.0.0.1): con HOST=0.0.0.0
# l'app si raggiunge anche dagli altri dispositivi della rete (telefono,
# tablet) via http://<ip-del-pc>:<porta>. Serve una password attiva.
HOST = os.getenv("HOST", "127.0.0.1")
RESTART_DELAY = 5  # secondi fra un riavvio e l'altro


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _comando_agente(python: str) -> list[str]:
    """Il comando che avvia il server (ascolta su HOST:PORT)."""
    return [
        python,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        HOST,
        "--port",
        str(PORT),
        "--log-level",
        "warning",
    ]


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


def _prendi_lock():
    """Un solo supervisore alla volta.

    Due agenti insieme avviano due tunnel sullo stesso token e si pestano
    i piedi (ngrok ne accetta uno, l'altro resta appeso); in piu' il
    secondo sovrascrive il puntatore del backend. Il lock e' del sistema:
    se il processo muore, Windows lo rilascia da solo.
    """
    if os.name != "nt":
        return None
    try:
        import msvcrt
    except ImportError:  # pragma: no cover - solo Windows
        return None
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = DATA_DIR / "agent.lock"
    handle = lock_path.open("w")
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    lock = _prendi_lock()
    if lock is None:
        _log("supervisore", "gia' in esecuzione: non avvio un secondo agente")
        print("L'agente e' gia' in esecuzione: non ne avvio un secondo.")
        return 0
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
        children["agente"] = _start("agente", _comando_agente(python))
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
                    children[name] = _start("agente", _comando_agente(python))
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
