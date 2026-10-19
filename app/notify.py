"""Invio notifiche su Telegram ed email + formattazione del digest."""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

import httpx

from .services import settings as app_settings
from .timeutil import human_rome

TELEGRAM_BASE = "https://api.telegram.org/bot{token}/{method}"


async def telegram_call(method: str, payload: dict) -> tuple[bool, dict | str]:
    """Chiama l'API Telegram. Restituisce (ok, dato_o_errore)."""
    values = app_settings.get_all()
    token = values.get("telegram_token", "").strip()
    if not token:
        return False, "Telegram non configurato (token mancante)"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(TELEGRAM_BASE.format(token=token, method=method), json=payload)
            body = response.json()
    except Exception as exc:
        return False, f"Telegram: {type(exc).__name__}"
    if not body.get("ok"):
        return False, f"Telegram: {body.get('description', 'errore sconosciuto')}"
    return True, body.get("result")


async def send_telegram_message(
    text: str, *, reply_markup: dict | None = None, chat_id: str | None = None
) -> tuple[bool, str, int | None]:
    values = app_settings.get_all()
    chat = (chat_id or values.get("telegram_chat_id", "")).strip()
    if not (values.get("telegram_token", "").strip() and chat):
        return False, "Telegram non configurato (token o chat_id mancante)", None
    payload: dict = {"chat_id": chat, "text": text, "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    ok, result = await telegram_call("sendMessage", payload)
    if not ok:
        return False, str(result), None
    message_id = result.get("message_id") if isinstance(result, dict) else None
    return True, "", message_id


async def telegram_get_updates(offset: int = 0, timeout: int = 0) -> list[dict]:
    ok, result = await telegram_call(
        "getUpdates",
        {
            "offset": offset,
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"],
        },
    )
    return result if ok and isinstance(result, list) else []


async def telegram_answer_callback(callback_id: str, text: str = "") -> None:
    await telegram_call(
        "answerCallbackQuery", {"callback_query_id": callback_id, "text": text, "show_alert": False}
    )


async def telegram_edit_message(chat_id: str | int, message_id: int, text: str) -> None:
    await telegram_call(
        "editMessageText",
        {"chat_id": chat_id, "message_id": message_id, "text": text, "disable_web_page_preview": True},
    )


async def send_telegram(text: str) -> tuple[bool, str]:
    ok, error, _ = await send_telegram_message(text)
    return ok, error


def _send_email_sync(subject: str, text: str) -> tuple[bool, str]:
    values = app_settings.get_all()
    host = values.get("smtp_host", "").strip()
    to_addr = values.get("smtp_to", "").strip()
    from_addr = values.get("smtp_from", "").strip() or values.get("smtp_user", "").strip()
    if not host or not to_addr or not from_addr:
        return False, "Email non configurata (host, mittente o destinatario mancante)"

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_addr
    message["To"] = to_addr
    message.set_content(text)

    port = int(values.get("smtp_port") or 587)
    use_tls = values.get("smtp_tls", "1") == "1"
    user = values.get("smtp_user", "").strip()
    password = values.get("smtp_password", "").strip()

    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
            if use_tls:
                server.starttls()
        if user:
            server.login(user, password)
        server.send_message(message)
        server.quit()
        return True, ""
    except Exception as exc:
        return False, f"Email: {type(exc).__name__}: {exc}"


async def send_email(subject: str, text: str) -> tuple[bool, str]:
    return await asyncio.to_thread(_send_email_sync, subject, text)


async def send_push(text: str, subject: str, channels: list[str]) -> list[dict]:
    """Invia sui canali richiesti. Restituisce l'esito per canale."""
    results: list[dict] = []
    if not app_settings.notifications_enabled():
        return [{"channel": "push", "ok": False, "error": "Notifiche disattivate nelle impostazioni"}]
    if app_settings.is_quiet():
        return [{"channel": "push", "ok": False, "error": "Fascia di silenzio attiva"}]

    for channel in channels:
        if channel in ("web", "desktop"):
            continue
        if channel == "telegram":
            ok, error = await send_telegram(text)
        elif channel == "email":
            ok, error = await send_email(subject, text)
        elif channel == "push":
            from .services import push as push_service

            outcomes = await push_service.send(subject, text)
            ok = any(item["ok"] for item in outcomes)
            error = ""
            if not ok:
                error = "; ".join(
                    item["error"] for item in outcomes if item.get("error")
                )[:200] or "nessun dispositivo raggiunto"
        else:
            continue
        results.append({"channel": channel, "ok": ok, "error": error})
    return results


def format_digest(
    watch: dict,
    new_offers: list[dict],
    drops: list[dict],
    best: dict | None,
    base_url: str,
    plan: dict | None = None,
) -> tuple[str, str]:
    route = f"{watch['from_place']} → {watch['to_place']}"
    subject = f"[Carpool Hunter] {len(new_offers)} nuovi passaggi · {route}"

    lines: list[str] = []
    lines.append(f"🚗 {route}")
    lines.append(f"Arrivo entro: {human_rome(watch['arrive_by'], '%a %d %b, %H:%M')}")
    if watch.get("max_price"):
        lines.append(f"Budget: €{watch['max_price']:.2f}")
    lines.append("")

    if plan:
        lines.append(f"🧠 Piano più economico: €{plan.get('total_price', 0):.2f} · {plan.get('title', '')}")
        explanation = (plan.get("explanation") or "").strip()
        if explanation:
            lines.append(explanation)
        for step in plan.get("steps", [])[:3]:
            dep = human_rome(step.get("depart_at"), "%H:%M") if step.get("depart_at") else "--:--"
            arr = human_rome(step.get("arrive_at"), "%H:%M") if step.get("arrive_at") else "--:--"
            price = f"€{step['price']:.2f}" if step.get("price") is not None else "n/d"
            lines.append(
                f"  {step.get('leg', 1)}. {price} · {step.get('operator') or step.get('source')} · {dep}→{arr}"
            )
        for note in (plan.get("watchouts") or [])[:2]:
            lines.append(f"⚠️ {note}")
        lines.append("")

    if new_offers:
        lines.append(f"🆕 {len(new_offers)} nuove offerte")
        for offer in new_offers[:4]:
            price = f"€{offer['price']:.2f}" if offer.get("price") is not None else "prezzo da verificare"
            dep = human_rome(offer.get("depart_at"), "%H:%M") if offer.get("depart_at") else "--:--"
            arr = human_rome(offer.get("arrive_at"), "%H:%M") if offer.get("arrive_at") else "--:--"
            lines.append(f"• {price} · {offer.get('operator') or offer.get('source')} · {dep}→{arr}")
        lines.append("")

    if drops:
        lines.append("🔻 Ribassi di prezzo")
        for drop in drops[:3]:
            lines.append(
                f"• {drop.get('operator') or drop.get('source')}: "
                f"€{drop['old_price']:.2f} → €{drop['new_price']:.2f}"
            )
        lines.append("")

    if best and not plan:
        price = f"€{best['price']:.2f}" if best.get("price") is not None else "n/d"
        lines.append(f"🏆 Migliore offerta: {price} · {best.get('operator') or ''}")
        if best.get("score"):
            lines.append(f"Punteggio {best['score']:.0f}/100")
        lines.append("")

    lines.append(f"🔗 Dashboard: {base_url}")
    return subject, "\n".join(lines)


def format_question(question: dict, watch: dict | None = None) -> str:
    header = f"❓ {watch['from_place']} → {watch['to_place']}" if watch else "❓ Domanda di viaggio"
    lines = [header, "", question.get("text", "")]
    options = question.get("options") or []
    if options:
        lines.append("")
        lines.append("Rispondi con un pulsante qui sotto o scrivi la tua risposta.")
    return "\n".join(lines)


def question_keyboard(question: dict) -> dict | None:
    options = question.get("options") or []
    if not options:
        return None
    rows = []
    for option in options[:4]:
        rows.append(
            [
                {
                    "text": str(option.get("label") or option.get("id")),
                    "callback_data": f"ans:{question['id']}:{option.get('id')}",
                }
            ]
        )
    return {"inline_keyboard": rows}
