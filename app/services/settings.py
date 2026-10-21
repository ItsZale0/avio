"""Impostazioni a runtime (token, SMTP, quiet hours) salvate su SQLite."""

from __future__ import annotations

import os
from datetime import datetime, time

from .. import db
from ..timeutil import ROME

SECRET_KEYS = {
    "telegram_token",
    "smtp_password",
    "groq_api_key",
    "vapid_private_key",
    "access_password",
    "access_key",
}
MASK = "••••••••"

# Impostazioni condivise da tutta l'istanza: servono a tutti (una sola chiave
# Groq, un solo paio di chiavi VAPID). Le preferenze personali stanno in
# `user_settings` e sono per-utente.
GLOBAL_KEYS = {
    "groq_api_key",
    "ai_enabled",
    "ai_model_smart",
    "ai_model_fast",
    "gtfs_enabled",
    "vapid_private_key",
    "vapid_public_key",
    "telegram_offset",
    "sources_migrated_gtfs",
    # Gli abbonamenti dell'utente: valgono per tutte le ricerche.
    "passes",
    # Agevolazione X-GO di Trenitalia ("giovane" / "senior" / "").
    "xgo",
}

# Impostazioni personali: valgono per il singolo utente.
PER_USER_KEYS = {
    "telegram_token",
    "telegram_chat_id",
    "smtp_host",
    "smtp_port",
    "smtp_user",
    "smtp_password",
    "smtp_from",
    "smtp_to",
    "smtp_tls",
    "quiet_from",
    "quiet_to",
    "notify_enabled",
}

DEFAULTS: dict[str, str] = {
    "telegram_token": "",
    "telegram_chat_id": "",
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_user": "",
    "smtp_password": "",
    "smtp_from": "",
    "smtp_to": "",
    "smtp_tls": "1",
    "quiet_from": "",
    "quiet_to": "",
    "notify_enabled": "1",
    "groq_api_key": "",
    "ai_enabled": "1",
    "gtfs_enabled": "1",
    "sources_migrated_gtfs": "",
    "ai_model_smart": "openai/gpt-oss-120b",
    "ai_model_fast": "openai/gpt-oss-20b",
    "vapid_private_key": "",
    "vapid_public_key": "",
    "telegram_offset": "0",
    # Hash bcrypt della password d'accesso e hash SHA-256 della chiave segreta.
    "access_password": "",
    "access_key": "",
    # Lista JSON degli abbonamenti in corso.
    "passes": "[]",
    # Agevolazione X-GO: "giovane" (under 30), "senior" (60+), "" (nessuna).
    "xgo": "",
}


def _env_defaults() -> dict[str, str]:
    mapping = {
        "telegram_token": "TELEGRAM_TOKEN",
        "telegram_chat_id": "TELEGRAM_CHAT_ID",
        "smtp_host": "SMTP_HOST",
        "smtp_port": "SMTP_PORT",
        "smtp_user": "SMTP_USER",
        "smtp_password": "SMTP_PASSWORD",
        "smtp_from": "SMTP_FROM",
        "smtp_to": "SMTP_TO",
        "smtp_tls": "SMTP_TLS",
        "quiet_from": "QUIET_FROM",
        "quiet_to": "QUIET_TO",
        "groq_api_key": "GROQ_API_KEY",
        "ai_enabled": "AI_ENABLED",
        "ai_model_smart": "AI_MODEL_SMART",
        "ai_model_fast": "AI_MODEL_FAST",
        "gtfs_enabled": "GTFS_ENABLED",
    }
    values = dict(DEFAULTS)
    for key, env_name in mapping.items():
        value = os.getenv(env_name)
        if value:
            values[key] = value
    return values


def get_all(mask: bool = False) -> dict[str, str]:
    values = _env_defaults()
    with db.db() as conn:
        for row in conn.execute("SELECT key, value FROM settings"):
            if row["key"] in DEFAULTS:
                values[row["key"]] = row["value"]
    if mask:
        for key in SECRET_KEYS:
            if values.get(key):
                values[key] = MASK
    return values


def get(key: str, default: str = "") -> str:
    values = get_all(mask=False)
    return values.get(key, default)


def update(values: dict[str, str | None]) -> dict[str, str]:
    with db.db() as conn:
        for key, value in values.items():
            if key not in DEFAULTS or value is None:
                continue
            if key in SECRET_KEYS and value == MASK:
                continue
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
    return get_all(mask=True)


def _parse_hhmm(value: str) -> time | None:
    try:
        hour, minute = value.strip().split(":")
        return time(int(hour), int(minute))
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Impostazioni per utente
# --------------------------------------------------------------------------- #
def get_user_all(user_id: int, mask: bool = False) -> dict[str, str]:
    """Impostazioni di un utente: globali + personali, con le personali che sovrascrivono."""
    values = {key: DEFAULTS[key] for key in PER_USER_KEYS}
    with db.db() as conn:
        for row in conn.execute("SELECT key, value FROM user_settings WHERE user_id = ?", (user_id,)):
            if row["key"] in PER_USER_KEYS:
                values[row["key"]] = row["value"]
    if mask:
        for key in SECRET_KEYS:
            if values.get(key):
                values[key] = MASK
    return values


def get_user(user_id: int, key: str, default: str = "") -> str:
    if key in GLOBAL_KEYS:
        return get(key, default)
    return get_user_all(user_id).get(key, default)


def update_user(user_id: int, values: dict[str, str | None]) -> dict[str, str]:
    """Salva le impostazioni personali. Le globali restano nel loro posto."""
    with db.db() as conn:
        for key, value in values.items():
            if key not in PER_USER_KEYS or value is None:
                continue
            if key in SECRET_KEYS and value == MASK:
                continue
            conn.execute(
                "INSERT INTO user_settings(user_id, key, value) VALUES (?,?,?) "
                "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value",
                (user_id, key, str(value)),
            )
    return get_user_all(user_id, mask=True)


def is_quiet_for(user_id: int | None, moment: datetime | None = None) -> bool:
    """Fascia di silenzio: quella dell'utente se conosciuto, altrimenti quella globale."""
    values = get_user_all(user_id) if user_id else get_all()
    start = _parse_hhmm(values.get("quiet_from", ""))
    end = _parse_hhmm(values.get("quiet_to", ""))
    if not start or not end:
        return False
    current = (moment or datetime.now(ROME)).astimezone(ROME).time()
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def notifications_enabled_for(user_id: int | None) -> bool:
    values = get_user_all(user_id) if user_id else get_all()
    return values.get("notify_enabled", "1") == "1"


def is_quiet(moment: datetime | None = None) -> bool:
    """True se siamo nella fascia di silenzio (nessuna notifica push)."""
    values = get_all()
    start = _parse_hhmm(values.get("quiet_from", ""))
    end = _parse_hhmm(values.get("quiet_to", ""))
    if not start or not end:
        return False
    current = (moment or datetime.now(ROME)).astimezone(ROME).time()
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def notifications_enabled() -> bool:
    return get_all().get("notify_enabled", "1") == "1"
