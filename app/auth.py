"""Autenticazione multi-utente: account, sessioni e separazione dei dati.

Modello scelto:
- il **primo** account registrato diventa amministratore;
- chi si registra dopo resta in attesa di approvazione (`status='pending'`)
  finche' l'amministratore non lo abilita;
- la sessione e' un token casuale in un cookie `HttpOnly`, memorizzato a
  parte in tabella `sessions` (per poter revocare i dispositivi).

Le password non si conservano mai in chiaro: si usa bcrypt.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db
from .timeutil import now_utc

log = logging.getLogger("carpool.auth")

COOKIE_NAME = "carpool_session"
SESSION_DAYS = 30
MIN_PASSWORD_LENGTH = 8
BCRYPT_ROUNDS = 12

# Ruoli: admin approva gli account e vede tutto; member usa l'app.
STATUS_ACTIVE = "active"
STATUS_PENDING = "pending"
STATUS_DISABLED = "disabled"


# --------------------------------------------------------------------------- #
# Hash delle password
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    """Hash bcrypt della password (mai in chiaro nel database)."""
    import bcrypt

    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(password: str, hashed: str | None) -> bool:
    """Confronta la password con l'hash salvato. Mai eccezioni, mai timing leaks."""
    if not hashed:
        return False
    import bcrypt

    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------- #
# Utenti
# --------------------------------------------------------------------------- #
def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def get_user(user_id: int) -> dict[str, Any] | None:
    with db.db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return db.row_to_dict(row) if row else None


def get_user_by_username(username: str) -> dict[str, Any] | None:
    key = normalize_username(username)
    if not key:
        return None
    with db.db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (key,)).fetchone()
    return db.row_to_dict(row) if row else None


def count_users(status: str | None = None) -> int:
    query = "SELECT COUNT(*) AS n FROM users"
    params: list[Any] = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    with db.db() as conn:
        row = conn.execute(query, params).fetchone()
    return int(row["n"]) if row else 0


def list_users() -> list[dict[str, Any]]:
    with db.db() as conn:
        rows = conn.execute(
            "SELECT id, username, display_name, is_admin, status, created_at, "
            "approved_at, last_login_at, telegram_chat_id FROM users ORDER BY id"
        ).fetchall()
    return db.rows_to_dicts(rows)


def create_user(
    username: str,
    password: str,
    *,
    display_name: str | None = None,
    is_admin: bool = False,
    status: str = STATUS_ACTIVE,
) -> dict[str, Any] | None:
    """Crea un utente. Restituisce None se lo username esiste gia'."""
    key = normalize_username(username)
    if not key or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError("Username o password non validi")
    if get_user_by_username(key):
        raise ValueError("Nome utente gia' in uso")

    now = now_utc().isoformat()
    with db.db() as conn:
        cursor = conn.execute(
            "INSERT INTO users(username, display_name, password_hash, is_admin, status, "
            "created_at, approved_at) VALUES (?,?,?,?,?,?,?)",
            (
                key,
                (display_name or username).strip()[:80] or key,
                hash_password(password),
                1 if is_admin else 0,
                status,
                now,
                now if status == STATUS_ACTIVE else None,
            ),
        )
        user_id = int(cursor.lastrowid)
    return get_user(user_id)


def set_user_status(user_id: int, status: str) -> dict[str, Any] | None:
    if status not in (STATUS_ACTIVE, STATUS_PENDING, STATUS_DISABLED):
        raise ValueError("stato non valido")
    with db.db() as conn:
        conn.execute(
            "UPDATE users SET status = ?, approved_at = COALESCE(approved_at, ?) WHERE id = ?",
            (status, now_utc().isoformat() if status == STATUS_ACTIVE else None, user_id),
        )
    if status != STATUS_ACTIVE:
        # Un account non attivo non deve avere sessioni valide.
        revoke_sessions(user_id)
    return get_user(user_id)


def set_password(user_id: int, password: str) -> bool:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError("Password troppo corta")
    with db.db() as conn:
        cursor = conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(password), user_id),
        )
    revoke_sessions(user_id)
    return cursor.rowcount > 0


def set_telegram_chat(user_id: int, chat_id: str | None) -> None:
    with db.db() as conn:
        conn.execute(
            "UPDATE users SET telegram_chat_id = ? WHERE id = ?",
            ((chat_id or "").strip() or None, user_id),
        )


def user_for_telegram_chat(chat_id: str) -> dict[str, Any] | None:
    """Trova l'utente proprietario di una chat Telegram."""
    key = (chat_id or "").strip()
    if not key:
        return None
    with db.db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_chat_id = ? AND status = ?",
            (key, STATUS_ACTIVE),
        ).fetchone()
    return db.row_to_dict(row) if row else None


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    """Dati utente senza segreti, per le risposte API."""
    if not user:
        return {}
    return {
        "id": user.get("id"),
        "username": user.get("username"),
        "display_name": user.get("display_name") or user.get("username"),
        "is_admin": bool(user.get("is_admin")),
        "status": user.get("status"),
        "telegram_chat_id": user.get("telegram_chat_id"),
        "created_at": user.get("created_at"),
        "last_login_at": user.get("last_login_at"),
    }


def implicit_owner() -> dict[str, Any] | None:
    """Il proprietario quando l'accesso e' disattivato.

    Serve per la modalita' "senza login": l'app usa sempre il primo
    account (di norma l'amministratore) come proprietario delle ricerche,
    cosi' i dati, le impostazioni e le notifiche restano quelli gia' salvati.
    """
    with db.db() as conn:
        row = conn.execute(
            "SELECT * FROM users ORDER BY is_admin DESC, id ASC LIMIT 1"
        ).fetchone()
    return db.row_to_dict(row) if row else None


def ensure_owner() -> dict[str, Any]:
    """Come `implicit_owner`, ma crea l'account se non ce n'e' nessuno."""
    owner = implicit_owner()
    if owner:
        return owner
    # Password casuale: l'utente non deve autenticarsi (accesso disattivato),
    # ma se in futuro lo riattiva deve poterla cambiare.
    password = secrets.token_urlsafe(12)
    user = create_user("admin", password, is_admin=True, status=STATUS_ACTIVE)
    if not user:
        raise RuntimeError("impossibile creare l'account proprietario")
    return user


# --------------------------------------------------------------------------- #
# Sessioni
# --------------------------------------------------------------------------- #
def _token_hash(token: str) -> str:
    """Il token in chiaro non finisce mai nel database: ne teniamo solo l'hash."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user_id: int, user_agent: str | None = None) -> tuple[str, dict[str, Any]]:
    """Crea una sessione e restituisce (token in chiaro, dati sessione)."""
    token = secrets.token_urlsafe(32)
    expires = (now_utc() + timedelta(days=SESSION_DAYS)).isoformat()
    with db.db() as conn:
        conn.execute(
            "INSERT INTO sessions(token_hash, user_id, created_at, expires_at, user_agent) "
            "VALUES (?,?,?,?,?)",
            (_token_hash(token), user_id, now_utc().isoformat(), expires, (user_agent or "")[:200]),
        )
    return token, {"user_id": user_id, "expires_at": expires}


def resolve_session(token: str | None) -> dict[str, Any] | None:
    """Dato il token del cookie, restituisce l'utente attivo (o None)."""
    if not token:
        return None
    with db.db() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE token_hash = ?", (_token_hash(token),)
        ).fetchone()
        if not row:
            return None
        if str(row["expires_at"] or "") < now_utc().isoformat():
            conn.execute("DELETE FROM sessions WHERE id = ?", (row["id"],))
            return None
        user = conn.execute(
            "SELECT * FROM users WHERE id = ? AND status = ?", (row["user_id"], STATUS_ACTIVE)
        ).fetchone()
        if not user:
            conn.execute("DELETE FROM sessions WHERE id = ?", (row["id"],))
            return None
        conn.execute(
            "UPDATE sessions SET last_seen_at = ? WHERE id = ?", (now_utc().isoformat(), row["id"])
        )
    return db.row_to_dict(user)


def revoke_session(token: str) -> bool:
    with db.db() as conn:
        cursor = conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
        return cursor.rowcount > 0


def revoke_sessions(user_id: int) -> int:
    with db.db() as conn:
        cursor = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return cursor.rowcount


def purge_expired_sessions() -> int:
    with db.db() as conn:
        cursor = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now_utc().isoformat(),))
        return cursor.rowcount


def touch_login(user_id: int) -> None:
    with db.db() as conn:
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now_utc().isoformat(), user_id))


# --------------------------------------------------------------------------- #
# Password dimenticata: token temporaneo via email
# --------------------------------------------------------------------------- #
def create_reset_token(user_id: int) -> str:
    token = secrets.token_urlsafe(24)
    with db.db() as conn:
        conn.execute(
            "INSERT INTO password_resets(token_hash, user_id, created_at, expires_at) "
            "VALUES (?,?,?,?)",
            (
                _token_hash(token),
                user_id,
                now_utc().isoformat(),
                (now_utc() + timedelta(hours=2)).isoformat(),
            ),
        )
    return token


def resolve_reset_token(token: str) -> int | None:
    """Restituisce l'id utente se il token di reset e' valido, altrimenti None."""
    if not token:
        return None
    with db.db() as conn:
        row = conn.execute(
            "SELECT * FROM password_resets WHERE token_hash = ?", (_token_hash(token),)
        ).fetchone()
        if not row:
            return None
        if str(row["expires_at"] or "") < now_utc().isoformat():
            conn.execute("DELETE FROM password_resets WHERE id = ?", (row["id"],))
            return None
        conn.execute("DELETE FROM password_resets WHERE id = ?", (row["id"],))
    return int(row["user_id"])


def purge_expired_resets() -> int:
    with db.db() as conn:
        cursor = conn.execute("DELETE FROM password_resets WHERE expires_at < ?", (now_utc().isoformat(),))
        return cursor.rowcount
