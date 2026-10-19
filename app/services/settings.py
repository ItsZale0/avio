"""Impostazioni a runtime (token, SMTP, quiet hours) salvate su SQLite."""

from __future__ import annotations

import os
from datetime import datetime, time

from .. import db
from ..timeutil import ROME

SECRET_KEYS = {"telegram_token", "smtp_password", "blablacar_api_key", "groq_api_key"}
MASK = "••••••••"

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
    "blablacar_api_key": "",
    "quiet_from": "",
    "quiet_to": "",
    "notify_enabled": "1",
    "groq_api_key": "",
    "ai_enabled": "1",
    "gtfs_enabled": "1",
    "sources_migrated_gtfs": "",
    "ai_model_smart": "llama-3.3-70b-versatile",
    "ai_model_fast": "llama-3.1-8b-instant",
    "telegram_offset": "0",
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
        "blablacar_api_key": "BLABLACAR_API_KEY",
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
