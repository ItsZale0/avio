"""Backup cifrato del database, spedito su Telegram.

Perche' esiste: se Oracle spegne l'istanza, i dati restano sul volume e
tornano su con un avvio; ma se la istanza viene *cancellata*, perdi
ricerche, preferenze e chiavi, e non c'e' modo di ricostruirle. Ogni
notte il database viene copiato in modo coerente, cifrato e spedito al
proprietario su Telegram: la copia esce dalla VM e sopravvive a tutto.

Come funziona:
- la copia coerente la fa SQLite (backup API), non un `cp` a caldo;
- il file e' cifrato con AES-256 (openssl, PBKDF2 con 200k iterazioni);
- la chiave sta in /var/lib/carpool/backup.key (600) e va conservata
  ANCHE fuori dalla VM: senza quella chiave il backup non si apre.

Per aprire un backup (su un PC con openssl):
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in carpool-backup.tar.gz.enc \\
        -out carpool-backup.tar.gz -pass file:backup.key
    tar xzf carpool-backup.tar.gz
"""

from __future__ import annotations

import base64
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import settings as app_settings  # noqa: E402

DATA_DIR = Path(os.environ.get("CARPOOL_DATA_DIR", "/var/lib/carpool"))
KEY_FILE = DATA_DIR / "backup.key"
WORK_DIR = Path("/tmp/carpool-backup")
KEEP_LOCAL = 3
ARCHIVE_NAME = "carpool-backup.tar.gz"
DB_FILES = ("carpool.db", "gtfs.db")


def log(msg: str) -> None:
    print(f"[backup] {msg}", flush=True)


def chiave() -> str:
    """La chiave di cifratura: la crea al primo giro e la riusa dopo."""
    if not KEY_FILE.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        segreto = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        KEY_FILE.write_text(segreto + "\n", encoding="utf-8")
        KEY_FILE.chmod(0o600)
        log(f"nuova chiave in {KEY_FILE} (600) - COPIALA nel gestore di password")
    return KEY_FILE.read_text(encoding="utf-8").strip()


def snapshot(sorgente: Path, destinazione: Path) -> bool:
    """Copia coerente del database (non un cp a caldo)."""
    if not sorgente.exists():
        return False
    try:
        with sqlite3.connect(f"file:{sorgente}?mode=ro", uri=True) as src:
            with sqlite3.connect(destinazione) as dst:
                src.backup(dst)
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"snapshot di {sorgente.name} fallito: {type(exc).__name__}: {exc}")
        return False


def archivio() -> Path | None:
    """Mette insieme i database in un unico file compresso."""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    tar_path = WORK_DIR / ARCHIVE_NAME
    trovati: list[Path] = []
    for nome in DB_FILES:
        copia = WORK_DIR / nome
        if snapshot(DATA_DIR / nome, copia):
            trovati.append(copia)
    if not trovati:
        log("nessun database da archiviare")
        return None
    with tarfile.open(tar_path, "w:gz") as tar:
        for file in trovati:
            tar.add(file, arcname=file.name)
    return tar_path


def cifra(tar_path: Path) -> Path | None:
    """Cifra l'archivio con la chiave del file."""
    enc_path = tar_path.with_suffix(tar_path.suffix + ".enc")
    cmd = [
        "openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000",
        "-salt", "-in", str(tar_path), "-out", str(enc_path),
        "-pass", f"file:{KEY_FILE}",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        return enc_path
    except Exception as exc:  # noqa: BLE001
        log(f"cifratura fallita: {type(exc).__name__}: {exc}")
        return None


def invia_telegram(file_path: Path) -> bool:
    """Manda il backup come documento su Telegram."""
    valori = app_settings.get_all()
    token = (valori.get("telegram_token") or "").strip()
    chat_id = (valori.get("telegram_chat_id") or "").strip()
    if not token or not chat_id:
        log("Telegram non configurato: il backup resta solo in locale")
        return False
    if chat_id.startswith("-"):
        log("chat di gruppo: non mando il backup li' (contiene segreti)")
        return False

    url = f"https://api.telegram.org/bot{token}/sendDocument"
    quando = time.strftime("%d/%m %H:%M")
    try:
        import httpx

        with open(file_path, "rb") as handle:
            with httpx.Client(timeout=120) as client:
                risposta = client.post(
                    url,
                    data={
                        "chat_id": chat_id,
                        "caption": (
                            f"Backup Carpool Hunter ({quando}). "
                            "Cifrato: serve la chiave per aprirlo."
                        ),
                    },
                    files={"document": (file_path.name, handle, "application/octet-stream")},
                )
        corpo = risposta.json()
        if not corpo.get("ok"):
            log(f"Telegram: {corpo.get('description', 'errore')}")
            return False
        log(f"backup inviato su Telegram ({file_path.stat().st_size} byte)")
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"invio Telegram fallito: {type(exc).__name__}: {exc}")
        return False


def pulisci_locali() -> None:
    """Tiene solo le ultime copie locali."""
    archivio_dir = DATA_DIR / "backup"
    archivio_dir.mkdir(parents=True, exist_ok=True)
    copie = sorted(archivio_dir.glob("carpool-backup-*.tar.gz.enc"))
    for vecchia in copie[:-KEEP_LOCAL]:
        vecchia.unlink(missing_ok=True)
        log(f"rimossa la copia locale vecchia {vecchia.name}")


def main() -> int:
    log("inizio")
    chiave()
    tar_path = archivio()
    if tar_path is None:
        return 1
    enc_path = cifra(tar_path)
    if enc_path is None:
        return 1

    archivio_dir = DATA_DIR / "backup"
    archivio_dir.mkdir(parents=True, exist_ok=True)
    copia = archivio_dir / f"carpool-backup-{time.strftime('%Y%m%d-%H%M')}.tar.gz.enc"
    shutil.copy2(enc_path, copia)
    copia.chmod(0o600)
    pulisci_locali()

    ok = invia_telegram(enc_path)
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    log(f"fine (Telegram: {'ok' if ok else 'non inviato'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
