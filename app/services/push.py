"""Notifiche push per la PWA (Web Push + VAPID).

Le chiavi VAPID si generano da sole la prima volta e restano nel database locale
(la privata e' mascherata nelle risposte API). Ogni dispositivo che attiva le
notifiche registra il proprio *endpoint*; l'agente ci manda un avviso quando
trova novita'. La libreria `pywebpush` e' opzionale: senza, tutto il resto
dell'app funziona e le push semplicemente non partono.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

from .. import db
from ..timeutil import now_utc
from . import settings as app_settings

try:  # dipendenza opzionale
    from pywebpush import WebPushException, webpush

    PUSH_AVAILABLE = True
except Exception:  # pragma: no cover - dipende dall'installazione
    WebPushException = Exception  # type: ignore[assignment]
    webpush = None  # type: ignore[assignment]
    PUSH_AVAILABLE = False

log = logging.getLogger("carpool.push")

DEFAULT_CONTACT = "mailto:carpool@localhost"


def available() -> bool:
    return PUSH_AVAILABLE


def contact() -> str:
    """Indirizzo di contatto richiesto da VAPID (mailto: o https:)."""
    sender = (app_settings.get("smtp_from") or app_settings.get("smtp_user") or "").strip()
    if "@" in sender:
        return f"mailto:{sender}"
    return DEFAULT_CONTACT


def _generate_keys() -> tuple[str, str]:
    from py_vapid import Vapid01
    from cryptography.hazmat.primitives import serialization

    vapid = Vapid01()
    vapid.generate_keys()
    private_pem = vapid.private_pem().decode()
    raw = vapid.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    public = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return private_pem, public


def ensure_keys() -> tuple[str, str]:
    """Restituisce (privata PEM, pubblica base64url), generandole se mancano."""
    if not PUSH_AVAILABLE:
        return "", ""
    values = app_settings.get_all()
    private = values.get("vapid_private_key", "").strip()
    public = values.get("vapid_public_key", "").strip()
    if private and public:
        return private, public
    private, public = _generate_keys()
    app_settings.update({"vapid_private_key": private, "vapid_public_key": public})
    return private, public


def public_key() -> str:
    return ensure_keys()[1]


def subscribe(
    subscription: dict[str, Any], user_agent: str | None = None, *, user_id: int | None = None
) -> int:
    endpoint = (subscription.get("endpoint") or "").strip()
    keys = subscription.get("keys") or {}
    p256dh = (keys.get("p256dh") or "").strip()
    auth = (keys.get("auth") or "").strip()
    if not (endpoint and p256dh and auth):
        raise ValueError("iscrizione push incompleta")
    label = (subscription.get("label") or "").strip() or None
    with db.db() as conn:
        conn.execute(
            "INSERT INTO push_subscriptions(user_id, endpoint, p256dh, auth, label, user_agent, created_at, last_ok_at, fail_count) "
            "VALUES (?,?,?,?,?,?,?,NULL,0) "
            "ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth, "
            "user_id=COALESCE(excluded.user_id, push_subscriptions.user_id), "
            "label=COALESCE(excluded.label, push_subscriptions.label), user_agent=excluded.user_agent, fail_count=0",
            (user_id, endpoint, p256dh, auth, label, user_agent, now_utc().isoformat()),
        )
    return count(user_id=user_id)


def unsubscribe(endpoint: str, *, user_id: int | None = None) -> int:
    query = "DELETE FROM push_subscriptions WHERE endpoint = ?"
    params: list[Any] = [endpoint]
    if user_id is not None:
        query += " AND user_id = ?"
        params.append(user_id)
    with db.db() as conn:
        conn.execute(query, params)
    return count(user_id=user_id)


def subscriptions(user_id: int | None = None) -> list[dict]:
    query = "SELECT * FROM push_subscriptions"
    params: list[Any] = []
    if user_id is not None:
        query += " WHERE user_id = ?"
        params.append(user_id)
    query += " ORDER BY id"
    with db.db() as conn:
        return db.rows_to_dicts(conn.execute(query, params).fetchall())


def count(user_id: int | None = None) -> int:
    query = "SELECT COUNT(*) AS n FROM push_subscriptions"
    params: list[Any] = []
    if user_id is not None:
        query += " WHERE user_id = ?"
        params.append(user_id)
    with db.db() as conn:
        row = conn.execute(query, params).fetchone()
    return int(row["n"]) if row else 0


def _label_for(row: dict) -> str:
    text = (row.get("label") or row.get("user_agent") or "dispositivo").strip()
    return text[:60]


def _drop(endpoint: str, *, failed: bool = False) -> None:
    with db.db() as conn:
        if failed:
            conn.execute(
                "UPDATE push_subscriptions SET fail_count = fail_count + 1 WHERE endpoint = ?",
                (endpoint,),
            )
        else:
            conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))


def _mark_ok(endpoint: str) -> None:
    with db.db() as conn:
        conn.execute(
            "UPDATE push_subscriptions SET last_ok_at = ?, fail_count = 0 WHERE endpoint = ?",
            (now_utc().isoformat(), endpoint),
        )


def _send_sync(row: dict, payload: dict, private_pem: str) -> tuple[bool, str, bool]:
    """(ok, errore, da_rimuovere)."""
    subscription = {
        "endpoint": row["endpoint"],
        "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
    }
    try:
        webpush(  # type: ignore[misc]
            subscription_info=subscription,
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=private_pem,
            vapid_claims={"sub": contact()},
            ttl=3600,
            timeout=15,
        )
        return True, "", False
    except WebPushException as exc:  # type: ignore[misc]
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (404, 410):
            return False, f"iscrizione scaduta ({status})", True
        return False, f"{type(exc).__name__}: {exc}"[:300], False
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"[:300], False


async def send(
    title: str, body: str, url: str = "/", *, user_id: int | None = None
) -> list[dict]:
    """Manda una notifica ai dispositivi iscritti. Restituisce l'esito per dispositivo.

    Con `user_id` si avvisa solo quel proprietario; senza, tutti i dispositivi.
    """
    if not PUSH_AVAILABLE:
        return [{"endpoint": "", "ok": False, "error": "pywebpush non installato (pip install pywebpush)"}]
    rows = subscriptions(user_id=user_id)
    if not rows:
        return [{"endpoint": "", "ok": False, "error": "nessun dispositivo iscritto alle notifiche push"}]

    private_pem, _ = ensure_keys()
    payload = {"title": title, "body": body, "url": url}
    results: list[dict] = []
    for row in rows:
        ok, error, remove = await asyncio.to_thread(_send_sync, row, payload, private_pem)
        if ok:
            _mark_ok(row["endpoint"])
        elif remove:
            _drop(row["endpoint"])
        else:
            _drop(row["endpoint"], failed=True)
        results.append({"endpoint": row["endpoint"], "label": _label_for(row), "ok": ok, "error": error})
    return results
