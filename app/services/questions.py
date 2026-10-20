"""Domande dell'agente all'utente: ciclo di vita e invio su Telegram.

L'agente, quando un dato manca o cambierebbe il piano, chiede. Le domande
vivono in SQLite, si rispondono dal web o da Telegram (pulsanti inline) e la
risposta diventa una preferenza (`watches.prefs`) usata al giro successivo.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from .. import db, notify, store
from ..timeutil import now_utc

# Massimo due domande in attesa per ricerca: oltre, l'utente smette di
# rispondere e le opzioni si contraddicono.
MAX_PENDING_QUESTIONS = 2

# Dopo quante ore una domanda gia' posta puo' essere riproposta. Serve per
# il caso "ti ho chiesto di alzare il budget e hai detto no": l'agente non
# deve insistere ogni ora, ma dopo qualche giorno i prezzi possono essere
# cambiati e vale la pena riproporla.
REASK_AFTER_HOURS = 12

# Chiavi "note" a bassa priorita': non orientano i filtri, arricchiscono il contesto.
KNOWN_PREFS = {
    "flexible_days",
    "allow_multi_leg",
    "max_walk_km",
    "luggage",
    "prefer_modes",
    "no_changes",
    "notes",
    "arrivo_giorno_prima",
    # "True" = arrivare prima conta piu' del prezzo (travel agent).
    "prefer_fast",
    # Lista di abbonamenti in corso (vedi app/services/passes.py).
    "passes",
}

# Pseudo-preferenze che agiscono sulla lista degli abbonamenti.
PASS_ACTIONS = {"add_pass", "clear_passes"}


def _row_to_question(row) -> dict[str, Any]:
    item = db.row_to_dict(row) or {}
    item["options"] = db.load_json(item.get("options"), [])
    item["allow_free_text"] = bool(item.get("allow_free_text"))
    item["watch_id"] = item.get("watch_id")
    return item


def _owner_clause(user_id: int | None) -> tuple[str, list[Any]]:
    """Vincola le domande al proprietario della ricerca a cui appartengono."""
    if user_id is None:
        return "", []
    return " AND watch_id IN (SELECT id FROM watches WHERE user_id = ?)", [user_id]


def get(question_id: int, *, user_id: int | None = None) -> dict[str, Any] | None:
    clause, params = _owner_clause(user_id)
    with db.db() as conn:
        row = conn.execute(
            "SELECT * FROM questions WHERE id = ?" + clause, [question_id, *params]
        ).fetchone()
    return _row_to_question(row) if row else None


def list_for_watch(
    watch_id: int, status: str | None = None, limit: int = 20, *, user_id: int | None = None
) -> list[dict[str, Any]]:
    query = "SELECT * FROM questions WHERE watch_id = ?"
    params: list[Any] = [watch_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    clause, extra = _owner_clause(user_id)
    query += clause
    params.extend(extra)
    query += " ORDER BY (status = 'pending') DESC, id DESC LIMIT ?"
    params.append(limit)
    with db.db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_question(row) for row in rows]


def pending(limit: int = 20, *, user_id: int | None = None) -> list[dict[str, Any]]:
    clause, params = _owner_clause(user_id)
    with db.db() as conn:
        rows = conn.execute(
            "SELECT * FROM questions WHERE status = 'pending'" + clause + " ORDER BY id ASC LIMIT ?",
            [*params, limit],
        ).fetchall()
    return [_row_to_question(row) for row in rows]


def list_all(
    status: str | None = None, limit: int = 100, *, user_id: int | None = None
) -> list[dict[str, Any]]:
    query = "SELECT * FROM questions WHERE 1=1"
    params: list[Any] = []
    if status:
        query += " AND status = ?"
        params.append(status)
    clause, extra = _owner_clause(user_id)
    query += clause
    params.extend(extra)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with db.db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_question(row) for row in rows]


def create_many(watch_id: int, run_id: int | None, questions: list[dict]) -> list[dict[str, Any]]:
    """Crea le domande nuove. Salta quelle gia' poste o gia' risposte (stessa key).

    Al massimo due domande in attesa per ricerca: oltre, l'utente non
    risponderebbe e le opzioni si contraddicono.
    """
    created: list[dict[str, Any]] = []
    if not questions:
        return created

    with db.db() as conn:
        pending = conn.execute(
            "SELECT COUNT(*) AS n FROM questions WHERE watch_id = ? AND status = 'pending'",
            (watch_id,),
        ).fetchone()
        if int(pending["n"]) >= MAX_PENDING_QUESTIONS:
            return created

        # Domande gia' poste per questa ricerca: con data, cosi' si puo'
        # riproporre una domanda dopo un po' (i prezzi cambiano) senza
        # insistere a ogni giro.
        previous = conn.execute(
            "SELECT key, text, status, created_at, answered_at FROM questions "
            "WHERE watch_id = ? ORDER BY id DESC",
            (watch_id,),
        ).fetchall()
        cutoff = (now_utc() - timedelta(hours=REASK_AFTER_HOURS)).isoformat()
        recent_keys: set[str] = set()
        recent_texts: set[str] = set()
        for row in previous:
            if row["status"] == "pending":
                recent_keys.add(str(row["key"]))
                recent_texts.add(_normalize_text(row["text"]))
                continue
            stamp = row["answered_at"] or row["created_at"]
            if stamp and str(stamp) >= cutoff:
                recent_keys.add(str(row["key"]))
                recent_texts.add(_normalize_text(row["text"]))

        for question in questions[:MAX_PENDING_QUESTIONS]:
            key = str(question.get("key") or "").strip()
            text = str(question.get("text") or "").strip()
            if not key or not text:
                continue
            if key in recent_keys:
                continue  # gia' chiesta (in attesa) o gia' risposta di recente
            if _normalize_text(text) in recent_texts:
                continue  # stessa domanda, detta diversamente
            cursor = conn.execute(
                "INSERT INTO questions(watch_id, run_id, key, text, options, allow_free_text,"
                " status, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    watch_id,
                    run_id,
                    key,
                    text,
                    db.dump_json(question.get("options") or []),
                    1,
                    "pending",
                    now_utc().isoformat(),
                ),
            )
            recent_keys.add(key)
            recent_texts.add(_normalize_text(text))
            # Leggiamo dalla stessa connessione: la riga non e' ancora committata.
            row = conn.execute(
                "SELECT * FROM questions WHERE id = ?", (int(cursor.lastrowid),)
            ).fetchone()
            if row:
                created.append(_row_to_question(row))
    return created


def _normalize_text(text: Any) -> str:
    """Chiave di confronto fra domande: minuscole, senza punteggiatura."""
    cleaned = "".join(
        ch for ch in str(text or "").lower() if ch.isalnum() or ch.isspace()
    )
    return " ".join(cleaned.split())


def merge_prefs(watch: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Applica una patch di preferenze alla ricerca. Gestisce budget e note."""
    if not patch:
        return {"prefs": store_prefs(watch), "changes": {}}

    prefs = store_prefs(watch)
    changes: dict[str, Any] = {}

    for key, value in patch.items():
        if key == "budget_raise":
            try:
                amount = float(value)
            except (TypeError, ValueError):
                continue
            if amount > 0:
                changes["max_price"] = round(amount, 2)
            continue
        if key == "add_pass":
            # Aggiunge un abbonamento a quelli gia' dichiarati.
            from . import passes as passes_service

            current = passes_service.normalize_passes(prefs.get("passes"))
            tipo = str(value or "").strip()
            if not tipo:
                continue
            if any(p["tipo"] == tipo for p in current):
                continue
            current.append({"tipo": tipo, "nome": passes_service.pass_name(tipo)})
            prefs["passes"] = current
            continue
        if key == "clear_passes":
            prefs["passes"] = []
            continue
        if key not in KNOWN_PREFS and key not in PASS_ACTIONS:
            continue
        if key == "notes":
            previous = str(prefs.get("notes") or "").strip()
            new_note = str(value or "").strip()
            prefs["notes"] = (previous + " | " + new_note).strip(" |")[:400] if new_note else previous
            continue
        prefs[key] = value

    with db.db() as conn:
        conn.execute(
            "UPDATE watches SET prefs = ? WHERE id = ?",
            (db.dump_json(prefs), watch["id"]),
        )
        if changes:
            assignments = ", ".join(f"{key} = ?" for key in changes)
            conn.execute(
                f"UPDATE watches SET {assignments} WHERE id = ?", list(changes.values()) + [watch["id"]]
            )
    return {"prefs": prefs, "changes": changes}


def store_prefs(watch: dict[str, Any]) -> dict[str, Any]:
    value = watch.get("prefs")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            value = {}
    return dict(value) if isinstance(value, dict) else {}


async def answer(
    question_id: int, text: str, source: str = "web", *, user_id: int | None = None
) -> dict[str, Any] | None:
    """Registra la risposta, aggiorna le preferenze e pianifica un nuovo giro."""
    question = get(question_id, user_id=user_id)
    if not question:
        return None

    with db.db() as conn:
        conn.execute(
            "UPDATE questions SET status='answered', answer=?, answer_source=?, answered_at=? WHERE id=?",
            (text, source, now_utc().isoformat(), question_id),
        )

    watch = store.get_watch(int(question["watch_id"]), user_id=user_id)
    applied: dict[str, Any] = {}
    if watch:
        from ..ai import interpret_answer

        interpreted = await interpret_answer(question, text)
        applied = merge_prefs(watch, interpreted.get("prefs") or {})
        with db.db() as conn:
            conn.execute(
                "UPDATE watches SET next_run_at=? WHERE id=?", (now_utc().isoformat(), watch["id"])
            )

    # Le altre domande in attesa restano: se ne risponde una per volta.
    return {"question": get(question_id, user_id=user_id), "applied": applied}


def skip(question_id: int, *, user_id: int | None = None) -> dict[str, Any] | None:
    question = get(question_id, user_id=user_id)
    if not question:
        return None
    with db.db() as conn:
        conn.execute(
            "UPDATE questions SET status='skipped', answered_at=? WHERE id=?",
            (now_utc().isoformat(), question_id),
        )
    return get(question_id, user_id=user_id)


async def send_to_telegram(
    question: dict[str, Any], watch: dict[str, Any] | None = None
) -> tuple[bool, str]:
    text = notify.format_question(question, watch)
    keyboard = notify.question_keyboard(question)
    owner = (watch or {}).get("user_id")
    ok, error, message_id = await notify.send_telegram_message(
        text, reply_markup=keyboard, user_id=owner
    )
    if ok and message_id:
        # Registra la consegna: serve a capire se la domanda e' arrivata in chat.
        with db.db() as conn:
            conn.execute(
                "UPDATE questions SET sent_at = ? WHERE id = ?",
                (now_utc().isoformat(), question["id"]),
            )
    return ok, error


def count_pending(watch_id: int | None = None, *, user_id: int | None = None) -> int:
    clause, extra = _owner_clause(user_id)
    with db.db() as conn:
        if watch_id is None:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM questions WHERE status='pending'" + clause, extra
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM questions WHERE status='pending' AND watch_id=?" + clause,
                [watch_id, *extra],
            ).fetchone()
    return int(row["n"]) if row else 0
