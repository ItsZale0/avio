"""Bot Telegram in polling: raccoglie le risposte alle domande dell'agente.

Gira dentro lo stesso processo (nessun webhook, nessun server in piu'): ogni
45 secondi legge gli aggiornamenti pendenti, gestisce i pulsanti delle domande
e, se scrivi un nuovo viaggio in chat, crea la ricerca.
"""

from __future__ import annotations

import logging

from .. import db, geo, notify, store
from ..ai import intelligence
from ..schemas import DEFAULT_SOURCES, WatchIn
from ..timeutil import human_rome, now_utc
from . import questions as questions_service
from . import settings as app_settings
from . import telegram_chats

log = logging.getLogger("carpool.telegram")


def _configured_chat() -> str:
    """Chat di fallback quando non c'e' un utente associato (istanza a utente singolo)."""
    return app_settings.get("telegram_chat_id", "").strip()


def _user_for_chat(chat_id: str) -> dict | None:
    """Utente proprietario della chat Telegram (se registrato e attivo)."""
    from .. import auth

    return auth.user_for_telegram_chat(chat_id)


def _owner_user() -> dict | None:
    """Il proprietario unico dell'istanza: e' lui che possiede le ricerche."""
    from .. import auth

    return auth.implicit_owner()


async def _reply(text: str, *, user_id: int | None = None) -> None:
    await notify.send_telegram_message(text, user_id=user_id)


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
        # Il bot e' stato aggiunto a un gruppo o a un canale: si registra.
        if await _handle_bot_added(message):
            return
        await _handle_message(message)


async def _handle_bot_added(message: dict) -> bool:
    """Registra la chat quando il bot viene aggiunto a un gruppo o canale.

    Telegram invia un messaggio di servizio con "new_chat_members": e' il
    modo piu' semplice per dare "l'accesso" senza codici da copiare.
    """
    chat = message.get("chat") or {}
    chat_type = str(chat.get("type") or "")
    if chat_type not in telegram_chats.GROUP_TYPES and chat_type != "channel":
        return False
    new_members = message.get("new_chat_members") or []
    bot_id = await _bot_id()
    added = False
    for member in new_members:
        if str((member or {}).get("id") or "") == bot_id:
            added = True
            break
    if not added:
        return False

    chat_id = str(chat.get("id") or "")
    title = chat.get("title") or chat.get("username")
    username = chat.get("username")
    user = _owner_user()
    if not user:
        await notify.send_telegram_message(
            "Ciao! Sono l'agente di viaggio. Per usarmi apri l'app, genera un codice "
            "in Impostazioni → Telegram e scrivimi /link CODICE.",
            chat_id=chat_id,
        )
        return True

    telegram_chats.register_chat(
        int(user["id"]),
        chat_id,
        chat_type=chat_type,
        title=title,
        username=username,
        make_default=False,
    )
    await notify.send_telegram_message(
        f"{telegram_chats.describe({'chat_id': chat_id, 'chat_type': chat_type, 'title': title, 'username': username})}\n"
        "Ora qui puoi chiedermi un viaggio (“Thiene → Trento, arrivo domani alle 11”) "
        "e riceverai qui gli avvisi.",
        chat_id=chat_id,
    )
    return True


async def _bot_id() -> str:
    """L'id del bot, per riconoscere gli aggiornamenti che lo riguardano."""
    ok, result = await notify.telegram_call("getMe")
    if ok and isinstance(result, dict):
        return str(result.get("id") or "")
    return ""


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

    owner = _user_for_chat(str(chat_id or ""))
    result = await questions_service.answer(
        qid, option_id, source="telegram", user_id=owner["id"] if owner else None
    )
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
            chat_id, int(message_id), f"{question.get('text', '')}\n\nRisposta: {label}"
        )


async def _handle_command(text: str, chat_id: str, message_chat: dict | None = None) -> None:
    """Comandi del bot: /start, /link, /chi, /chat, /chats, /removechat."""
    command = text.split()[0].lower().split("@")[0]

    if command in ("/start", "/help"):
        owner = _user_for_chat(chat_id)
        if owner:
            await notify.send_telegram_message(
                "Ciao! Sono il tuo agente di viaggio.\n"
                "Rispondi alle domande con i pulsanti, oppure scrivi un viaggio:\n"
                "“Thiene → Trento, arrivo entro domani alle 11, max 20€”.",
                user_id=int(owner["id"]),
            )
        else:
            await notify.send_telegram_message(
                "Per collegare questa chat al tuo profilo:\n"
                "1) apri l'app e vai in Impostazioni → Telegram;\n"
                "2) premi “Collega” e copia il codice che compare;\n"
                "3) scrivimi qui: /link CODICE",
                chat_id=chat_id,
            )
        return

    if command == "/link":
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await notify.send_telegram_message(
                "Scrivi: /link CODICE (il codice lo trovi nell'app, in Impostazioni → Telegram).",
                chat_id=chat_id,
            )
            return
        code = parts[1].strip().upper()
        with db.db() as conn:
            row = conn.execute(
                "SELECT * FROM telegram_links WHERE code = ?", (code,)
            ).fetchone()
            if not row:
                await notify.send_telegram_message("Codice non valido. Riprova.", chat_id=chat_id)
                return
            if str(row["expires_at"] or "") < now_utc().isoformat():
                conn.execute("DELETE FROM telegram_links WHERE id = ?", (row["id"],))
                await notify.send_telegram_message("Codice scaduto. Generane uno nuovo.", chat_id=chat_id)
                return
            conn.execute(
                "UPDATE telegram_links SET chat_id = ? WHERE id = ?", (chat_id, row["id"])
            )
            user_id = int(row["user_id"])
        from .. import auth

        auth.set_telegram_chat(user_id, chat_id)
        await notify.send_telegram_message(
            "Chat collegata. Da qui in poi ti scrivo solo per le tue ricerche.",
            user_id=user_id,
        )
        return

    if command == "/chi":
        owner = _owner_user()
        if owner:
            watches = store.list_watches(int(owner["id"]))
            if not watches:
                await notify.send_telegram_message(
                    "Non hai ancora ricerche attive.", user_id=int(owner["id"])
                )
                return
            righe = "\n".join(
                f"• {w['from_place']} → {w['to_place']} (entro "
                f"{human_rome(w['arrive_by'], '%a %d %b %H:%M')})"
                for w in watches[:8]
            )
            await notify.send_telegram_message(
                f"Le tue ricerche:\n{righe}", user_id=int(owner["id"])
            )
        return

    if command in ("/chat", "/addchat"):
        # Registra la chat corrente (gruppo, canale o privato).
        owner = _owner_user()
        chat = message_chat or {}
        chat_type = str(chat.get("type") or "private")
        title = chat.get("title") or chat.get("username")
        username = chat.get("username")
        if not owner:
            await notify.send_telegram_message(
                "Prima collegami: apri l'app → Impostazioni → Telegram → "
                "“Collega la mia chat”, poi scrivi /link CODICE.",
                chat_id=chat_id,
            )
            return
        telegram_chats.register_chat(
            int(owner["id"]),
            chat_id,
            chat_type=chat_type,
            title=title,
            username=username,
            make_default=True,
        )
        await notify.send_telegram_message(
            f"Chat registrata: {telegram_chats.describe({'chat_id': chat_id, 'chat_type': chat_type, 'title': title, 'username': username})}.\n"
            "Qui riceverai gli avvisi e potrai chiedermi viaggi.",
            chat_id=chat_id,
        )
        return

    if command == "/chats":
        owner = _owner_user()
        if not owner:
            return
        chats = telegram_chats.list_chats(int(owner["id"]))
        if not chats:
            await notify.send_telegram_message(
                "Nessuna chat registrata. Aggiungimi a un gruppo o scrivi /chat qui.",
                user_id=int(owner["id"]),
            )
            return
        righe = "\n".join(f"• {telegram_chats.describe(c)}" for c in chats)
        await notify.send_telegram_message(
            f"Le tue chat:\n{righe}\n\nUsa /removechat per toglierne una.",
            user_id=int(owner["id"]),
        )
        return

    if command == "/removechat":
        owner = _owner_user()
        if not owner:
            return
        removed = telegram_chats.remove_chat(int(owner["id"]), chat_id)
        await notify.send_telegram_message(
            "Chat rimossa: non ti scrivo piu' qui." if removed else "Questa chat non era registrata.",
            chat_id=chat_id,
        )
        return


async def _handle_message(message: dict) -> None:
    chat_id = str((message.get("chat") or {}).get("id") or "")
    text = str(message.get("text") or "").strip()
    if not text:
        return

    if text.startswith("/"):
        await _handle_command(text, chat_id, message.get("chat") or {})
        return

    owner = _user_for_chat(chat_id)
    if owner:
        user_id: int | None = int(owner["id"])
    else:
        # Chat registrata (per esempio un gruppo): l'utente proprietario e'
        # quello dell'istanza, e il messaggio vale per le sue ricerche.
        registered = telegram_chats.find_by_chat(chat_id)
        if registered:
            user_id = int(registered[0]["user_id"])
            telegram_chats.touch(chat_id)
        else:
            # Chat non registrata: se l'istanza ha una chat globale, si accetta
            # solo quella (comportamento a utente singolo).
            configured = _configured_chat()
            if configured and chat_id and chat_id != configured:
                return
            user_id = None

    pending = questions_service.pending(limit=1, user_id=user_id)
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
        result = await questions_service.answer(
            int(pending[0]["id"]), text, source="telegram", user_id=user_id
        )
        if result:
            await _reply("Risposta salvata. Aggiorno la ricerca al prossimo giro.", user_id=user_id)
            return

    reply = await _trip_from_text(text, user_id=user_id)
    if reply:
        await _reply(reply, user_id=user_id)
        return

    if pending:
        result = await questions_service.answer(
            int(pending[0]["id"]), text, source="telegram", user_id=user_id
        )
        if result:
            await _reply("Risposta salvata. Aggiorno la ricerca al prossimo giro.", user_id=user_id)
            return

    await _reply(
        "Non ho capito. Scrivi un viaggio, ad esempio: “Thiene → Trento, arrivo entro domani alle 11, max 20€”.",
        user_id=user_id,
    )


async def _trip_from_text(text: str, *, user_id: int | None = None) -> str | None:
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
            sources=list(DEFAULT_SOURCES),
            notify=["web", "telegram"],
        )
    except Exception as exc:
        return f"Non riesco a interpretare la richiesta ({type(exc).__name__})."

    from_lat, from_lon, _ = await geo.resolve_place(payload.from_place)
    to_lat, to_lon, _ = await geo.resolve_place(payload.to_place)
    if from_lat is None or from_lon is None or to_lat is None or to_lon is None:
        return "Non trovo una delle due località. Prova con il nome della città (es. Trento, Vicenza)."

    watch = store.create_watch(
        payload,
        {"from_lat": from_lat, "from_lon": from_lon, "to_lat": to_lat, "to_lon": to_lon},
        user_id=user_id,
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
        f"Ricerca creata: {payload.from_place} → {payload.to_place}.{extra}\n"
        "Ti scrivo appena trovo la soluzione più economica."
    )


async def send_test(*, user_id: int | None = None) -> tuple[bool, str]:
    ok, error, _ = await notify.send_telegram_message(
        "Bot pronto: posso farti domande qui in chat e capire nuovi viaggi.",
        user_id=user_id,
    )
    return ok, error
