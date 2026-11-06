"""Accesso in lettura a Telegram con l'account utente (MTProto).

Serve per leggere i messaggi dei gruppi di carsharing a cui l'utente
appartiene, senza poter aggiungere il bot. Solo lettura: il codice non
invia mai messaggi.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger("carpool.telegram_user")


def _load_env() -> None:
    for candidate in (
        os.getenv("CARPOOL_ENV_FILE"),
        "/opt/carpool/.env",
        os.path.join(os.getcwd(), ".env"),
    ):
        if not candidate or not os.path.exists(candidate):
            continue
        try:
            with open(candidate, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
        except OSError:
            continue
        break


_load_env()

API_ID = int(os.getenv("TELEGRAM_API_ID", "0") or "0")
API_HASH = os.getenv("TELEGRAM_API_HASH", "")

SESSION_PATH = os.getenv(
    "TELEGRAM_USER_SESSION", "/var/lib/carpool/telegram_user.session"
)


def session_exists() -> bool:
    return os.path.exists(SESSION_PATH)


def _client():
    from telethon import TelegramClient

    return TelegramClient(SESSION_PATH, API_ID, API_HASH)


async def connect():
    client = _client()
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError(
            "Sessione Telegram non autenticata. Esegui il login iniziale."
        )
    return client


async def login_send_code(phone: str) -> str:
    if not API_ID or not API_HASH:
        raise RuntimeError(
            "TELEGRAM_API_ID / TELEGRAM_API_HASH non configurati. "
            "Impostali in /opt/carpool/.env (vedi my.telegram.org)."
        )
    client = _client()
    await client.connect()
    if await client.is_user_authorized():
        await client.disconnect()
        return "already_authorized"
    await client.send_code_request(phone)
    await client.disconnect()
    return "code_sent"


async def login_verify(phone: str, code: str, password: str | None = None) -> str:
    from telethon.errors import SessionPasswordNeededError

    client = _client()
    await client.connect()
    try:
        await client.sign_in(phone, code)
    except SessionPasswordNeededError:
        if not password:
            await client.disconnect()
            return "password_needed"
        await client.sign_in(password=password)
    await client.disconnect()
    return "ok"


async def join_invite(link: str) -> dict:
    from telethon.tl.functions.messages import ImportChatInviteRequest

    client = await connect()
    try:
        hash_ = link.strip().rsplit("/", 1)[-1]
        if hash_.startswith("+"):
            hash_ = hash_[1:]
        result = await client(ImportChatInviteRequest(hash_))
        chat = result.chats[0] if getattr(result, "chats", None) else None
        return {
            "id": getattr(chat, "id", None),
            "title": getattr(chat, "title", None),
        }
    finally:
        await client.disconnect()


async def list_groups() -> list:
    client = await connect()
    try:
        out = []
        async for dialog in client.iter_dialogs():
            if dialog.is_group or dialog.is_channel:
                entity = dialog.entity
                out.append(
                    {
                        "id": dialog.id,
                        "title": dialog.title,
                        "username": getattr(entity, "username", None),
                    }
                )
        return out
    finally:
        await client.disconnect()


async def fetch_messages(chat_id, limit=100, since=None):
    from telethon.errors import FloodWaitError

    try:
        chat_id = int(chat_id)
    except (TypeError, ValueError):
        pass

    client = await connect()
    try:
        out = []
        async for m in client.iter_messages(chat_id, limit=limit):
            if since and m.date < since:
                break
            if not m.message:
                continue
            sender = await m.get_sender()
            name = None
            if sender is not None:
                name = getattr(sender, "first_name", None) or getattr(
                    sender, "title", None
                )
                if name is None:
                    name = str(getattr(sender, "id", ""))
            out.append(
                {
                    "id": m.id,
                    "text": m.message,
                    "date": m.date.astimezone(timezone.utc).isoformat(),
                    "sender": name,
                    "sender_id": getattr(sender, "id", None),
                }
            )
        return out
    except FloodWaitError as exc:
        log.warning("FloodWait %s s su chat %s", exc.seconds, chat_id)
        await asyncio.sleep(min(exc.seconds, 60))
        return []
    finally:
        await client.disconnect()
PY