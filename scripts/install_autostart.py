"""Installa l'avvio automatico dell'agente (Task Scheduler).

Così l'agente riparte da solo quando accendi il PC, e il tunnel torna su
un URL pubblico (che cambia): per questo, quando parte, aggiorna anche la
variabile GitHub `CARPOOL_API_BASE` e il file `.env` locale.

Requisito: un token GitHub con scope `repo` nella variabile d'ambiente
`GITHUB_TOKEN` dell'utente che esegue lo script.

Uso:
    python scripts\install_autostart.py            # installa
    python scripts\install_autostart.py --remove   # rimuove
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TASK_NAME = "Carpool Hunter"

# Il tunnel deve poter scrivere nel .env e aggiornare GitHub: usa l'utente
# corrente, non SYSTEM.
PS_SCRIPT = r"""
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath "__BASE__"
$python = Join-Path "__BASE__" ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

# 1) l'agente
& $python -m uvicorn app.main:app --host 127.0.0.1 --port 8787
"""


def task_command() -> str:
    return (
        f'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden '
        f'-File "{BASE / "start.ps1"}" -NoInstall'
    )


def remove_task() -> int:
    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print(f"Task '{TASK_NAME}' rimossa.")
    else:
        print(f"Nessuna task da rimuovere: {result.stdout.strip()}")
    return 0


def install_task() -> int:
    token = (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()
    if not token:
        print(
            "GITHUB_TOKEN non e' impostato.\n"
            "L'agente partira' comunque all'accensione, ma il tunnel non potra'\n"
            "aggiornare la variabile GitHub: il frontend su Pages restera'\n"
            "collegato all'URL precedente.\n"
            "\nPer impostarlo:\n"
            "  setx GITHUB_TOKEN \"ghp_...\"\n"
            "e riapri la sessione (o riavvia il PC).",
            file=sys.stderr,
        )

    command = task_command()
    result = subprocess.run(
        [
            "schtasks", "/Create",
            "/TN", TASK_NAME,
            "/TR", command,
            "/SC", "ONLOGON",
            "/RL", "LIMITED",
            "/F",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            "Errore nella creazione del task (serve un account amministratore).\n"
            "Apri PowerShell come amministratore e rilancia:\n"
            "  python scripts\\install_autostart.py\n"
            "\nAlternativa senza Task Scheduler: metti un collegamento a start.bat\n"
            "nella cartella di avvio (Win+R -> shell:startup).",
            file=sys.stderr,
        )
        return 1

    print(f"Task '{TASK_NAME}' installata: l'agente parte a ogni accesso.")
    print(f"  {command}")
    print()
    print("Per partire subito senza riavviare:")
    print(f'  schtasks /Run /TN "{TASK_NAME}"')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Avvio automatico dell'agente")
    parser.add_argument("--remove", action="store_true", help="rimuove la task")
    args = parser.parse_args()

    if args.remove:
        return remove_task()
    return install_task()


if __name__ == "__main__":
    raise SystemExit(main())
