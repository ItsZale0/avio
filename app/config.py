"""Configurazione applicativa.

Le impostazioni "di processo" (host, porta, cartelle) arrivano da variabili
d'ambiente / file .env. Le impostazioni modificabili a runtime (Telegram, SMTP)
vivono nella tabella `settings` del database e sono gestite da services/settings.py.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def load_dotenv() -> None:
    """Lettore .env minimale, senza dipendenze esterne.

    Non sovrascrive variabili gia' presenti nell'ambiente.
    """
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# Il .env va letto PRIMA di leggere le variabili qui sotto.
load_dotenv()

DATA_DIR = Path(os.getenv("CARPOOL_DATA_DIR", str(BASE_DIR / "data")))
# Il frontend statico sta in `static/` (deploy GitHub Pages) oppure nella
# cartella `web/` (vecchio layout): usa la prima che contiene index.html.
_WEB_CANDIDATES = (BASE_DIR / "static", BASE_DIR / "web")
WEB_DIR = next((p for p in _WEB_CANDIDATES if (p / "index.html").exists()), BASE_DIR / "static")

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8787"))
DEFAULT_INTERVAL_MIN = max(10, int(os.getenv("DEFAULT_INTERVAL_MIN", "60")))

# Origini extra ammesse (es. interfaccia su GitHub Pages o Cloudflare Pages).
CORS_ORIGINS = [o.strip() for o in os.getenv("CARPOOL_CORS_ORIGINS", "").split(",") if o.strip()]
# URL pubblico di questa istanza (usato nei link dentro le notifiche).
PUBLIC_BASE_URL = os.getenv("CARPOOL_PUBLIC_URL", "").strip()

APP_NAME = "Carpool Hunter"
USER_AGENT = "CarpoolHunter/0.1 (strumento personale di ricerca passaggi)"
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "18"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
