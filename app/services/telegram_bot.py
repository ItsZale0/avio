"""Bot Telegram in polling: raccoglie le risposte alle domande dell'agente.

Gira dentro lo stesso processo (nessun webhook, nessun server in piu'): ogni
45 secondi legge gli aggiornamenti pendenti, gestisce i pulsanti delle domande
e, se scrivi un nuovo viaggio in chat, crea la ricerca.
"""

from __future__ import annotations

import logging

from .. import db, geo, notify, store
from ..ai import intelligence
from ..schemas import WatchIn
from ..timeutil import now_utc
from . import questions as questions_service
from . import settings as app_settings

log = logging.getLogger("carpool.telegram")


def _configured_chat() -> str:
    return app_settings.get("telegram_chat_id", "").strip()


async def _reply(text: str) -> None:
    await notify.send_telegram_message(text)


async def poll() -> None:
    if not app_settings.get("telegram_token", "").strip():
        return
    try:
        offset = int(app_settings.get("telegram_offset", "0") or 0)
    except ValueError:
        offset = 0

    updates = await notify.telegram_get_updates(offset)
    if not updates:
        return

    highest = offset
    for update in updates:
        try:
            update_id = int(update.get("update_id") or 0)
            highest = max(highest, update_id + 1)
            await _handle_update(update)
        except Exception as exc:  # un update rotto non ferma gli altri
            log.debug("update ignorato: %s: %s", type(exc).__name__, exc)

    if highest != offset:
        app_settings.update({"telegram_offset": str(highest)})


async def _handle_update(update: dict) -> None:
    callback = update.get("callback_query")
    if callback:
        await _handle_callback(callback)
        return
    message = update.get("message") or update.get("edited_message")
    if message:
        await _handle_message(message)


async def _handle_callback(callback: dict) -> None:
    data = str(callback.get("data") or "")
    callback_id = str(callback.get("id") or "")
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")

    if not data.startswith("ans:"):
        await notify.telegram_answer_callback(callback_id, "")
        return

    parts = data.split(":", 2)
    if len(parts) < 3:
        await notify.telegram_answer_callback(callback_id, "")
        return
    _, question_id, option_id = parts
    try:
        qid = int(question_id)
    except ValueError:
        await notify.telegram_answer_callback(callback_id, "")
        return

    result = await questions_service.answer(qid, option_id, source="telegram")
    if not result:
        await notify.telegram_answer_callback(callback_id, "Domanda non trovata")
        return

    question = result.get("question") or {}
    label = option_id
    for option in question.get("options", []):
        if option.get("id") == option_id:
            label = option.get("label") or option_id
            break

    await notify.telegram_answer_callback(callback_id, f"Salvato: {label}")
    if chat_id and message_id:
        await notify.telegram_edit_message(
            chat_id, int(message_id), f"✅ {question.get('text', '')}\n\nRisposta: {label}"
        )


async def _handle_message(message: dict) -> None:
    chat_id = str((message.get("chat") or {}).get("id") or "")
    text = str(message.get("text") or "").strip()
    if not text:
        return
    configured = _configured_chat()
    if configured and chat_id and chat_id != configured:
        return
    if text.startswith("/"):
        return

    pending = questions_service.pending(limit=1)
    looks_like_trip = (
        len(text) > 60
        or "->" in text
        or "→" in text
        or (
            " a " in text.lower()
            and any(word in text.lower() for word in ("entro", "arrivo", "domani", "dopodomani", "alle"))
        )
    )

    if pending and not looks_like_trip:
        result = await questions_service.answer(int(pending[0]["id"]), text, source="telegram")
        if result:
            await _reply("✅ Risposta salvata. Aggiorno la ricerca al prossimo giro.")
            return

    reply = await _trip_from_text(text)
    if reply:
        await _reply(reply)
        return

    if pending:
        result = await questions_service.answer(int(pending[0]["id"]), text, source="telegram")
        if result:
            await _reply("✅ Risposta salvata. Aggiorno la ricerca al prossimo giro.")
            return

    await _reply(
        "Non ho capito. Scrivi un viaggio, ad esempio: “Thiene → Trento, arrivo entro domani alle 11, max 20€”."
    )


async def _trip_from_text(text: str) -> str | None:
    if len(text) < 6:
        return None
    parsed = await intelligence.parse_trip_text(text)
    if not parsed.get("ok"):
        return None
    data = parsed.get("trip") or {}
    if not data.get("from_place") or not data.get("to_place") or not data.get("arrive_by"):
        return (
            "Mi manca qualcosa per partire (servono partenza, arrivo e orario di arrivo).\n"
            "Esempio: “Thiene → Trento, arrivo entro domani alle 11, max 20€”."
        )

    try:
        payload = WatchIn(
            name=data.get("notes") or f"{data['from_place']} → {data['to_place']}",
            from_place=data["from_place"],
            to_place=data["to_place"],
            depart_after=data.get("depart_after"),
            arrive_by=data["arrive_by"],
            max_price=data.get("max_price"),
            passengers=int(data.get("passengers") or 1),
            radius_km=float(data.get("radius_km") or 5),
            sources=["demo", "flixbus", "blablacar", "carsharing", "transit"],
            notify=["web", "telegram"],
        )
    except Exception as exc:
        return f"Non riesco a interpretare la richiesta ({type(exc).__name__})."

    from_lat, from_lon, _ = await geo.resolve_place(payload.from_place)
    to_lat, to_lon, _ = await geo.resolve_place(payload.to_place)
    if from_lat is None or from_lon is None or to_lat is None or to_lon is None:
        return "Non trovo una delle due localita'. Prova con il nome della citta' (es. Trento, Vicenza)."

    watch = store.create_watch(
        payload,
        {"from_lat": from_lat, "from_lon": from_lon, "to_lat": to_lat, "to_lon": to_lon},
    )
    prefs = data.get("prefs") if isinstance(data.get("prefs"), dict) else {}
    with db.db() as conn:
        conn.execute(
            "UPDATE watches SET prefs = ?, next_run_at = ? WHERE id = ?",
            (db.dump_json(prefs), now_utc().isoformat(), watch["id"]),
        )

    summary = data.get("notes") or ""
    extra = f"\n{summary}" if summary else ""
    return (
        f"🔎 Ricerca creata: {payload.from_place} → {payload.to_place}.{extra}\n"
        "Ti scrivo appena trovo la soluzione piu' economica."
    )


async def send_test() -> tuple[bool, str]:
    ok, error, _ = await notify.send_telegram_message(
        "🤖 Bot pronto: posso farti domande qui in chat e capire nuovi viaggi."
    )
    return ok, error
