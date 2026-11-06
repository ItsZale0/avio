"""Adattatore per i gruppi Telegram di carsharing (account utente).

Legge i messaggi recenti dei gruppi marcati come "carsharing" e li trasforma
in offerte reali con AI. Non inventa nulla: prezzi e orari vengono dal
messaggio; se mancano, l'offerta viene scartata o stimata dal km (prezzo
indicativo, marcato come stima).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from ..adapters.base import Adapter, AdapterResult, Offer, SearchContext
from ..geo import haversine_km, offline_lookup
from ..timeutil import ROME, parse_dt
from .. import store
from . import carsharing as cs

log = logging.getLogger("carpool.adapter.telegram_carsharing")


class TelegramCarsharingAdapter(Adapter):
    id = "telegram_carsharing"
    label = "Carsharing Telegram (gruppi)"
    description = "Passaggi in auto dai gruppi Telegram dell'utente (prezzi reali dai messaggi)."
    modes = ("rideshare", "carsharing")
    requires = "telegram_user_session"
    timeout_s = 60.0

    async def search(self, ctx: SearchContext) -> AdapterResult:
        from ..services import telegram_user

        if not telegram_user.session_exists():
            return AdapterResult(
                hints=[
                    "Carsharing Telegram non configurato: esegui il login utente "
                    "(python -m app.telegram_login)."
                ]
            )

        chats = store.list_carsharing_chats()
        if not chats:
            return AdapterResult(
                hints=[
                    "Nessun gruppo Telegram marcato come carsharing: "
                    "segna un gruppo dalle impostazioni."
                ]
            )

        since = (ctx.window_start or datetime.now(timezone.utc)) - timedelta(days=2)
        all_messages = []
        errors = []
        for chat in chats:
            try:
                msgs = await telegram_user.fetch_messages(
                    chat["chat_id"], limit=80, since=since
                )
                for m in msgs:
                    m["_chat"] = chat
                all_messages.extend(msgs)
            except Exception as exc:
                errors.append(f"telegram/{chat.get('title')}: {type(exc).__name__}")
                log.debug("lettura gruppo fallita: %s", exc)

        if not all_messages:
            hints = ["Nessun messaggio recente nei gruppi carsharing configurati."]
            hints.extend(errors)
            return AdapterResult(offers=[], hints=hints)

        parsed = await _parse_messages(all_messages)
        offers = []
        for item in parsed:
            offer = _to_offer(item, ctx, all_messages)
            if offer is not None:
                offers.append(offer)

        hints = []
        if not offers:
            hints.append(
                "Nessun passaggio Telegram per questa tratta e questa finestra."
            )
        hints.extend(errors)
        return AdapterResult(offers=offers, hints=hints)


async def _parse_messages(messages):
    from ..ai.groq import client_from_settings, ai_enabled
    from datetime import date as _date

    client = client_from_settings()
    if not (ai_enabled() and client.available):
        return _parse_regex(messages)

    offer_re = re.compile(r"offro|passaggio|posto|posti", re.IGNORECASE)
    filtered = [m for m in messages if offer_re.search(m.get("text") or "")]
    if not filtered:
        filtered = messages

    oggi = _date.today().isoformat()
    all_items = []
    batch_size = 5
    for start in range(0, len(filtered), batch_size):
        batch = filtered[start:start + batch_size]
        payload = "\n".join(
            f"{messages.index(m)}: [{m.get('sender')}] {m.get('text')}" for m in batch
        )
        prompt = (
            "Oggi e' " + oggi + ". Estrai le offerte di PASSAGGIO IN AUTO dai messaggi "
            "Telegram seguenti.\n"
            "Rispondi SOLO con un array JSON, un oggetto per offerta, con campi:\n"
            "from (citta' di PARTENZA, solo il nome della citta'),\n"
            "to (citta' di ARRIVO, solo il nome della citta'),\n"
            "date (YYYY-MM-DD: risolvi i giorni della settimana e le date relative "
            "rispetto a oggi; null se assente),\n"
            "time (HH:MM 24h; 'mattina'->09:00, 'pomeriggio'->14:00, 'sera'->18:00; "
            "null se assente),\n"
            "seats (int o null), price (numero EUR o null),\n"
            "contact (nome autore), notes (breve), msg_index (indice del messaggio).\n"
            "Il giorno della settimana o la data (es. 'per lunedi 9 novembre', 'domani') "
            "NON e' una citta': mettilo in date, mai in from/to.\n"
            "Esempi:\n"
            "  'Offro Schio-Rovereto per lunedi 9 novembre partenza 8.30' -> "
            '{"from":"Schio","to":"Rovereto","date":"2026-11-09","time":"08:30"}\n'
            "  'Offro Valdagno/Schio - Trento per domenica 8 novembre ore 16.00, 6euro' -> "
            '{"from":"Valdagno","to":"Trento","date":"2026-11-08","time":"16:00","price":6}\n'
            "Se un messaggio non e' un'offerta di passaggio, ignoralo.\n"
            "Messaggi:\n" + payload
        )
        try:
            result = await client.chat(
                [{"role": "user", "content": prompt}], max_tokens=1000, temperature=0.0
            )
            data = result.json() if hasattr(result, "json") else None
            items = None
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict) and isinstance(data.get("offers"), list):
                items = data["offers"]
            if items is not None:
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    idx = it.get("msg_index")
                    if isinstance(idx, int) and 0 <= idx < len(messages):
                        it["_msg"] = messages[idx]
                        it["_chat"] = messages[idx].get("_chat")
                all_items.extend(items)
        except Exception as exc:
            log.debug("parsing AI fallito: %s", exc)
    if all_items:
        return all_items
    return _parse_regex(messages)


def _parse_regex(messages):
    import re as _re

    out = []
    pattern = _re.compile(
        r"([A-Za-z]{3,30})\s*(?:->|a | per )\s*([A-Za-z]{3,30})",
        _re.IGNORECASE,
    )
    price_re = _re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:euro|eur)", _re.IGNORECASE)
    time_re = _re.compile(r"\b(\d{1,2}:\d{2})\b")
    for m in messages:
        text = m.get("text") or ""
        match = pattern.search(text)
        if not match:
            continue
        price_m = price_re.search(text)
        time_m = time_re.search(text)
        out.append(
            {
                "from": match.group(1).strip(),
                "to": match.group(2).strip(),
                "date": None,
                "time": time_m.group(1) if time_m else None,
                "seats": None,
                "price": float(price_m.group(1).replace(",", ".")) if price_m else None,
                "contact": m.get("sender"),
                "notes": text[:120],
                "_msg": m,
                "_chat": m.get("_chat"),
            }
        )
    return out


def _to_offer(item, ctx, messages):
    from_city = (item.get("from") or "").strip()
    to_city = (item.get("to") or "").strip()
    if not from_city or not to_city:
        return None

    from_coords = _coords(from_city)
    to_coords = _coords(to_city)
    if from_coords is None or to_coords is None:
        return None

    if ctx.from_lat is None or ctx.to_lat is None:
        return None
    if haversine_km(from_coords[0], from_coords[1], ctx.from_lat, ctx.from_lon) > 60:
        return None
    if haversine_km(to_coords[0], to_coords[1], ctx.to_lat, ctx.to_lon) > 60:
        return None

    date_str = item.get("date")
    time_str = item.get("time")
    base_date = ctx.arrival_day_start.date()
    if date_str:
        try:
            base_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            base_date = ctx.arrival_day_start.date()
    if not time_str:
        return None
    try:
        hh, mm = time_str.split(":")
        depart = datetime(
            base_date.year, base_date.month, base_date.day,
            int(hh), int(mm), tzinfo=ROME,
        ).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None

    if depart < ctx.window_start - timedelta(minutes=1):
        return None
    if depart > ctx.arrive_by + timedelta(hours=2):
        return None

    distance = haversine_km(from_coords[0], from_coords[1], to_coords[0], to_coords[1])
    road_km = distance * 1.28
    duration_min = max(12, int(road_km / 26 * 60))
    arrive = depart + timedelta(minutes=duration_min)

    price = item.get("price")
    price_is_estimate = False
    if price is None:
        provider = _first_provider()
        if provider:
            cost = (
                float(provider.get("unlock", 0))
                + duration_min * float(provider.get("per_min", 0))
                + road_km * float(provider.get("per_km", 0))
            )
            cost = min(cost, float(provider.get("day_cap", 999)))
            price = round(cost, 2)
            price_is_estimate = True
        else:
            return None

    seats = item.get("seats")
    contact = item.get("contact") or ""
    notes = item.get("notes") or ""
    msg = item.get("_msg") or {}
    chat = item.get("_chat") or {}
    external_id = f"tgcs:{msg.get('id', 'x')}:{from_city}:{to_city}"

    return Offer(
        source="telegram_carsharing",
        external_id=external_id,
        mode="rideshare",
        operator=f"Carsharing Telegram ({contact})" if contact else "Carsharing Telegram",
        depart_at=depart,
        arrive_at=arrive,
        price=float(price),
        price_is_estimate=price_is_estimate,
        pickup_name=f"Auto in zona {from_city.title()}",
        pickup_lat=from_coords[0],
        pickup_lon=from_coords[1],
        dropoff_name=f"Rilascio in zona {to_city.title()}",
        dropoff_lat=to_coords[0],
        dropoff_lon=to_coords[1],
        duration_min=duration_min,
        seats=seats or 4,
        deeplink=None,
        details={
            "stima": price_is_estimate,
            "distanza_km": round(road_km, 1),
            "contatto": contact,
            "note": notes[:200],
            "gruppo": chat.get("title"),
            "messaggio_id": msg.get("id"),
        },
    )


def _coords(city):
    res = offline_lookup(city)
    if res:
        return (res[0], res[1])
    from ..geo import CITIES

    key = city.strip().lower()
    if key in CITIES:
        return CITIES[key]
    for k, v in CITIES.items():
        if key in k or k in key:
            return v
    return None


def _first_provider():
    try:
        providers = cs.load_providers()
    except Exception:
        return None
    return providers[0] if providers else None
