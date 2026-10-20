"""Chat Telegram dell'utente: privati, gruppi e canali.

Quando aggiungi il bot a un gruppo, Telegram invia un messaggio di servizio
"new_chat_members": il bot lo intercetta e registra la chat da solo. Da quel
momento la chat riceve gli avvisi e puo' creare ricerche scrivendo in chat.
"""

from __future__ import annotations

from typing import Any

from .. import db
from ..timeutil import now_utc

# Tipi di chat Telegram: "private", "group", "supergroup", "channel".
GROUP_TYPES = ("group", "supergroup")


def register_chat(
    user_id: int,
    chat_id: str,
    *,
    chat_type: str = "private",
    title: str | None = None,
    username: str | None = None,
    make_default: bool = False,
) -> int:
    """Registra (o aggiorna) una chat per l'utente. Restituisce l'id interno."""
    chat_id = (chat_id or "").strip()
    if not chat_id:
        raise ValueError("chat_id mancante")
    now = now_utc().isoformat()
    with db.db() as conn:
        existing = conn.execute(
            "SELECT id FROM telegram_chats WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE telegram_chats SET chat_type = ?, title = ?, username = ?, last_seen_at = ? "
                "WHERE id = ?",
                (chat_type, title, username, now, existing["id"]),
            )
            row_id = int(existing["id"])
        else:
            cursor = conn.execute(
                "INSERT INTO telegram_chats(user_id, chat_id, chat_type, title, username, "
                "is_default, created_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?)",
                (user_id, chat_id, chat_type, title, username, 1 if make_default else 0, now, now),
            )
            row_id = int(cursor.lastrowid)
        if make_default:
            conn.execute(
                "UPDATE telegram_chats SET is_default = 0 WHERE user_id = ? AND id != ?",
                (user_id, row_id),
            )
            conn.execute("UPDATE telegram_chats SET is_default = 1 WHERE id = ?", (row_id,))
    return row_id


def remove_chat(user_id: int, chat_id: str) -> bool:
    with db.db() as conn:
        cursor = conn.execute(
            "DELETE FROM telegram_chats WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        )
        return cursor.rowcount > 0


def list_chats(user_id: int) -> list[dict[str, Any]]:
    with db.db() as conn:
        rows = conn.execute(
            "SELECT * FROM telegram_chats WHERE user_id = ? ORDER BY is_default DESC, id",
            (user_id,),
        ).fetchall()
    return db.rows_to_dicts(rows)


def chat_ids(user_id: int, *, only_default: bool = False) -> list[str]:
    """Gli id delle chat dove mandare gli avvisi."""
    query = "SELECT chat_id FROM telegram_chats WHERE user_id = ?"
    params: list[Any] = [user_id]
    if only_default:
        query += " AND is_default = 1"
    query += " ORDER BY is_default DESC, id"
    with db.db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [row["chat_id"] for row in rows]


def find_by_chat(chat_id: str) -> list[dict[str, Any]]:
    """Le chat registrate con questo id (possono essere piu' utenti)."""
    with db.db() as conn:
        rows = conn.execute(
            "SELECT * FROM telegram_chats WHERE chat_id = ?", (chat_id,)
        ).fetchall()
    return db.rows_to_dicts(rows)


def touch(chat_id: str) -> None:
    with db.db() as conn:
        conn.execute(
            "UPDATE telegram_chats SET last_seen_at = ? WHERE chat_id = ?",
            (now_utc().isoformat(), chat_id),
        )


def set_default(user_id: int, chat_id: str) -> bool:
    with db.db() as conn:
        row = conn.execute(
            "SELECT id FROM telegram_chats WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE telegram_chats SET is_default = 0 WHERE user_id = ?", (user_id,)
        )
        conn.execute("UPDATE telegram_chats SET is_default = 1 WHERE id = ?", (row["id"],))
    return True


def describe(chat: dict[str, Any]) -> str:
    """Descrizione leggibile di una chat."""
    kind = {
        "private": "chat privato",
        "group": "gruppo",
        "supergroup": "gruppo",
        "channel": "canale",
    }.get(chat.get("chat_type") or "", "chat")
    label = chat.get("title") or chat.get("username") or chat.get("chat_id")
    default = " (predefinita)" if chat.get("is_default") else ""
    return f"{kind} «{label}» · id {chat.get('chat_id')}{default}"
