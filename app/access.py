"""Accesso all'app: una password e una chiave segreta.

Modello scelto (volutamente semplice):
- **una sola password** per l'ingresso, senza nome utente;
- una **chiave segreta** (stringa casuale lunga) che puoi mettere
  nell'URL: `https://.../?key=XXXX` apre l'app con un tocco.

La password si conserva come hash bcrypt, la chiave come hash SHA-256:
nel database non c'e' mai il testo in chiaro, quindi non e' leggibile
nemmeno da chi apre il file.

Le sessioni restano quelle di `auth.py` (token in cookie `HttpOnly`).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

from . import auth
from .services import settings as app_settings

PASSWORD_KEY = "access_password"
KEY_KEY = "access_key"

# La password non deve essere troppo corta: e' l'unica barriera.
MIN_PASSWORD_LENGTH = 8
KEY_LENGTH = 32


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Password
# --------------------------------------------------------------------------- #
def has_password() -> bool:
    """True se e' stata impostata una password di accesso."""
    return bool(app_settings.get(PASSWORD_KEY, "").strip())


def set_password(password: str) -> bool:
    """Imposta la password di accesso. Svuotarla la rimuove.

    Ogni cambio chiude le sessioni aperte: se la password cambia, quelle
    vecchie non devono piu' dare accesso.
    """
    password = (password or "").strip()
    if not password:
        app_settings.update({PASSWORD_KEY: ""})
        revoke_all_sessions()
        return False
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"La password deve avere almeno {MIN_PASSWORD_LENGTH} caratteri")
    app_settings.update({PASSWORD_KEY: auth.hash_password(password)})
    revoke_all_sessions()
    return True


def check_password(password: str) -> bool:
    """Verifica la password contro l'hash salvato."""
    stored = app_settings.get(PASSWORD_KEY, "").strip()
    if not stored:
        return False
    return auth.verify_password(password or "", stored)


# --------------------------------------------------------------------------- #
# Chiave segreta (accesso con un tocco)
# --------------------------------------------------------------------------- #
def has_key() -> bool:
    return bool(app_settings.get(KEY_KEY, "").strip())


def new_key() -> str:
    """Genera una chiave segreta nuova e la restituisce (si vede una volta sola)."""
    key = secrets.token_urlsafe(KEY_LENGTH)
    app_settings.update({KEY_KEY: _sha256(key)})
    return key


def check_key(key: str) -> bool:
    """Verifica la chiave segreta (confronto a tempo costante)."""
    stored = app_settings.get(KEY_KEY, "").strip()
    if not stored or not key:
        return False
    return hmac.compare_digest(_sha256(key.strip()), stored)


def clear_key() -> None:
    app_settings.update({KEY_KEY: ""})


# --------------------------------------------------------------------------- #
# Sblocco
# --------------------------------------------------------------------------- #
def is_locked() -> bool:
    """True se l'app chiede la password."""
    return has_password()


def unlock(password: str, *, user_agent: str | None = None) -> tuple[str | None, str]:
    """Sblocca l'app.

    Accetta la password o la chiave segreta. Restituisce
    (token di sessione, messaggio d'errore).
    """
    if not is_locked():
        return None, ""

    if check_key(password):
        pass  # la chiave vale come password
    elif not check_password(password):
        return None, "Password non corretta"

    owner = auth.implicit_owner() or auth.ensure_owner()
    token, _ = auth.create_session(int(owner["id"]), user_agent)
    auth.touch_login(int(owner["id"]))
    return token, ""


def revoke_all_sessions() -> int:
    """Chiude tutte le sessioni (usato quando si cambia la password)."""
    owner = auth.implicit_owner()
    if not owner:
        return 0
    return auth.revoke_sessions(int(owner["id"]))


def status() -> dict[str, Any]:
    """Stato dell'accesso, per il frontend e per l'API."""
    return {
        "locked": is_locked(),
        "has_password": has_password(),
        "has_key": has_key(),
    }
