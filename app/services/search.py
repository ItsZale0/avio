"""Motore di ricerca: esegue le fonti, filtra, classifica, salva e avvisa."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from .. import db, notify, store
from ..adapters import get_adapters
from ..adapters.base import AdapterResult, Offer, SearchContext
from ..ai import intelligence
from ..config import HOST, PORT
from ..scoring import score_offers
from ..timeutil import from_iso, iso, now_utc, parse_dt
from . import questions as questions_service

RUNNING: set[int] = set()
_lock = asyncio.Lock()
BASE_URL = f"http://{HOST}:{PORT}"


def build_context(watch: dict[str, Any]) -> SearchContext:
    arrive_by = parse_dt(watch["arrive_by"])
    if not arrive_by:
        raise ValueError("arrive_by mancante")
    return SearchContext(
        watch_id=watch["id"],
        from_place=watch["from_place"],
        to_place=watch["to_place"],
        from_lat=watch.get("from_lat"),
        from_lon=watch.get("from_lon"),
        to_lat=watch.get("to_lat"),
        to_lon=watch.get("to_lon"),
        depart_after=parse_dt(watch.get("depart_after")),
        arrive_by=arrive_by,
        radius_km=float(watch.get("radius_km") or 5),
        passengers=int(watch.get("passengers") or 1),
        max_price=watch.get("max_price"),
    )


def _filter_offers(offers: list[Offer], ctx: SearchContext, late_minutes: int = 0) -> list[Offer]:
    now = now_utc()
    latest = ctx.arrive_by + timedelta(minutes=max(0, late_minutes))
    kept: list[Offer] = []
    for offer in offers:
        if not offer.depart_at and offer.mode != "transit":
            continue
        if offer.depart_at and offer.depart_at < now - timedelta(minutes=5):
            continue
        if ctx.depart_after and offer.depart_at and offer.depart_at < ctx.depart_after - timedelta(minutes=15):
            continue
        if offer.arrive_at and offer.arrive_at > latest + timedelta(minutes=10):
            continue
        if offer.arrive_at and offer.arrive_at > ctx.arrive_by + timedelta(minutes=10):
            # Arrivo oltre il limite ma dentro la flessibilita' concessa dall'utente.
            late = int((offer.arrive_at - ctx.arrive_by).total_seconds() // 60)
            offer.details = {**(offer.details or {}), "oltre_limite_min": late}
        if ctx.max_price is not None and offer.price is not None and offer.price > ctx.max_price:
            continue

        limit = max(ctx.radius_km * 1.4, ctx.radius_km + 2.5)
        # Se la fonte ha dovuto spostarsi su una citta' vicina (es. FlixBus),
        # il punto di ritrovo e' lontano per definizione: lo segnala nei details.
        if not offer.details.get("partenza_alternativa"):
            pickup = offer.pickup_distance_km(ctx)
            if pickup is not None and pickup > limit:
                continue
        if not offer.details.get("arrivo_alternativo"):
            dropoff = offer.dropoff_distance_km(ctx)
            if dropoff is not None and dropoff > limit:
                continue
        kept.append(offer)
    return kept


def _dedupe(offers: list[Offer]) -> list[Offer]:
    by_fp: dict[str, Offer] = {}
    for offer in offers:
        current = by_fp.get(offer.fingerprint)
        if current is None:
            by_fp[offer.fingerprint] = offer
        elif offer.price is not None and (current.price is None or offer.price < current.price):
            by_fp[offer.fingerprint] = offer
    return list(by_fp.values())


async def _run_adapter(adapter, ctx: SearchContext) -> tuple[AdapterResult, str | None]:
    try:
        result = await asyncio.wait_for(adapter.search(ctx), timeout=35)
        return result, None
    except asyncio.TimeoutError:
        return AdapterResult(), f"{adapter.id}: timeout"
    except Exception as exc:  # una fonte rotta non deve fermare le altre
        return AdapterResult(), f"{adapter.id}: {type(exc).__name__}: {exc}"


def _offer_preview(offer: Offer) -> dict[str, Any]:
    return {
        "operator": offer.operator,
        "source": offer.source,
        "price": offer.price,
        "depart_at": iso(offer.depart_at),
        "arrive_at": iso(offer.arrive_at),
        "mode": offer.mode,
    }


async def execute_run(watch_id: int, trigger: str = "schedule") -> dict[str, Any]:
    async with _lock:
        if watch_id in RUNNING:
            return {"status": "skipped", "reason": "run gia' in corso"}
        RUNNING.add(watch_id)

    run_id: int | None = None
    try:
        watch = store.get_watch(watch_id)
        if not watch:
            return {"status": "error", "reason": "ricerca inesistente"}

        started = now_utc()
        with db.db() as conn:
            cursor = conn.execute(
                "INSERT INTO runs(watch_id, trigger, status, started_at) VALUES (?,?,?,?)",
                (watch_id, trigger, "running", started.isoformat()),
            )
            run_id = int(cursor.lastrowid)

        ctx = build_context(watch)
        adapters = get_adapters(watch["sources"])
        results = await asyncio.gather(*(_run_adapter(adapter, ctx) for adapter in adapters))

        offers: list[Offer] = []
        errors: list[str] = []
        hints: list[str] = []
        for result, error in results:
            offers.extend(result.offers)
            hints.extend(result.hints)
            if error:
                errors.append(error)

        prefs = intelligence.prefs_of(watch)
        late_minutes = int(prefs.get("flexible_days", 0) or 0) * 1440
        gathered = _dedupe(offers)
        offers = _filter_offers(gathered, ctx, late_minutes=late_minutes)
        ranked = score_offers(offers, ctx)

        new_offers, drops = _persist(watch_id, offers, ranked)
        _deactivate_past(watch_id)

        best = _best_offer(offers, ranked)

        # Intelligenza: sceglie il piano piu' economico tra le combinazioni reali.
        intel = intelligence.IntelligenceResult()
        try:
            intel = await intelligence.analyze(
                watch, offers, ctx, ranked=ranked, run_id=run_id, raw_offers=gathered
            )
        except Exception as exc:
            errors.append(f"ai: {type(exc).__name__}: {exc}")
        if intel.error:
            errors.append(f"ai: {intel.error}")

        plan = intel.plan
        if plan:
            store.save_plans(watch_id, run_id, plan, intel.alternatives)
            effective = plan.get("effective_cost")
            previous_best = watch.get("ai_best_price")
            new_best = effective if previous_best is None else min(float(previous_best), float(effective))
            store.update_ai_fields(
                watch_id,
                summary=intel.summary,
                fingerprint=plan.get("fingerprint"),
                best_price=new_best,
            )
        else:
            store.update_ai_fields(watch_id, summary=intel.summary)

        # Domande all'utente: al massimo due, mai ripetute.
        created_questions: list[dict] = []
        try:
            created_questions = questions_service.create_many(watch_id, run_id, intel.questions)
        except Exception as exc:
            errors.append(f"questions: {type(exc).__name__}: {exc}")
        if created_questions and "telegram" in (watch.get("notify") or []):
            for question in created_questions:
                ok, error = await questions_service.send_to_telegram(question, watch)
                if not ok and error and "non configurato" not in error:
                    errors.append(f"notify/telegram: {error}")

        owner_id = watch.get("user_id")

        if intel.summary:
            hints.append(intel.summary)

        plan_improved = bool(plan and intel.improved)
        alerts_sent = 0
        if new_offers or drops or plan_improved:
            subject, message = notify.format_digest(watch, new_offers, drops, best, BASE_URL, plan=plan)
            if plan_improved and not new_offers and not drops:
                title = f"Piano migliorato: -€{intel.price_delta:.2f}"
            elif new_offers:
                title = f"{len(new_offers)} nuovi passaggi"
            else:
                title = "Ribasso di prezzo"

            with db.db() as conn:
                conn.execute(
                    "INSERT INTO alerts(watch_id, run_id, kind, channel, title, message, created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (watch_id, run_id, "digest", "web", title, message, now_utc().isoformat()),
                )
                alerts_sent += 1

            channels = [c for c in watch["notify"] if c not in ("web", "desktop")]
            push_results = await notify.send_push(message, subject, channels, user_id=owner_id)
            for item in push_results:
                if item["ok"]:
                    alerts_sent += 1
                elif item.get("error") and not any(
                    word in item["error"]
                    for word in ("silenzio", "disattivate", "nessun dispositivo", "non installato")
                ):
                    errors.append(f"notify/{item['channel']}: {item['error']}")

        finished = now_utc()
        status = "ok" if not errors else "partial"
        with db.db() as conn:
            conn.execute(
                "UPDATE runs SET status=?, finished_at=?, offers_found=?, alerts_sent=?, errors=?, notes=?,"
                " ai_used=?, ai_tokens=?, plan_price=? WHERE id=?",
                (
                    status,
                    finished.isoformat(),
                    len(offers),
                    alerts_sent,
                    db.dump_json(errors),
                    db.dump_json(hints),
                    1 if intel.used_ai else 0,
                    intel.tokens,
                    (plan or {}).get("total_price"),
                    run_id,
                ),
            )

        arrive_by = parse_dt(watch["arrive_by"])
        expired = bool(arrive_by and arrive_by < finished)
        next_run = None if expired else (finished + timedelta(minutes=watch["interval_min"])).isoformat()
        with db.db() as conn:
            conn.execute(
                "UPDATE watches SET last_run_at=?, next_run_at=?, active=? WHERE id=?",
                (finished.isoformat(), next_run, 0 if expired else (1 if watch["active"] else 0), watch_id),
            )

        return {
            "status": status,
            "run_id": run_id,
            "offers": len(offers),
            "new": len(new_offers),
            "drops": len(drops),
            "alerts": alerts_sent,
            "errors": errors,
            "hints": hints,
            "ai": intel.used_ai,
            "questions": len(created_questions),
            "plan": plan,
        }
    except Exception as exc:
        if run_id is not None:
            with db.db() as conn:
                conn.execute(
                    "UPDATE runs SET status='error', finished_at=?, errors=? WHERE id=?",
                    (now_utc().isoformat(), db.dump_json([f"{type(exc).__name__}: {exc}"]), run_id),
                )
        next_at = (now_utc() + timedelta(minutes=30)).isoformat()
        with db.db() as conn:
            conn.execute("UPDATE watches SET next_run_at=? WHERE id=?", (next_at, watch_id))
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
    finally:
        RUNNING.discard(watch_id)


def _persist(
    watch_id: int, offers: list[Offer], ranked: dict[str, dict]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    new_offers: list[dict[str, Any]] = []
    drops: list[dict[str, Any]] = []
    timestamp = now_utc().isoformat()

    with db.db() as conn:
        for offer in offers:
            info = ranked.get(offer.fingerprint, {"score": None, "badges": [], "details": {}})
            badges = list(info.get("badges", []))
            details = {**(offer.details or {}), **info.get("details", {})}

            row = conn.execute(
                "SELECT id, price, prev_price FROM offers WHERE watch_id=? AND fingerprint=?",
                (watch_id, offer.fingerprint),
            ).fetchone()

            price = round(offer.price, 2) if offer.price is not None else None

            if row:
                old_price = row["price"]
                price_drop = (
                    old_price is not None
                    and price is not None
                    and old_price - price >= max(0.5, old_price * 0.04)
                )
                if price_drop:
                    badges.append("price_drop")
                    drops.append(
                        {
                            "operator": offer.operator,
                            "source": offer.source,
                            "old_price": old_price,
                            "new_price": price,
                        }
                    )
                prev_price = (
                    old_price
                    if old_price is not None and price is not None and abs(old_price - price) > 0.001
                    else row["prev_price"]
                )
                conn.execute(
                    """
                    UPDATE offers SET price=?, prev_price=?, duration_min=?, seats=?, rating=?,
                        deeplink=?, score=?, badges=?, details=?, last_seen=?, active=1,
                        depart_at=?, arrive_at=?, pickup_name=?, pickup_lat=?, pickup_lon=?,
                        dropoff_name=?, dropoff_lat=?, dropoff_lon=?
                    WHERE id=?
                    """,
                    (
                        price,
                        prev_price,
                        offer.duration_min,
                        offer.seats,
                        offer.rating,
                        offer.deeplink,
                        info.get("score"),
                        db.dump_json(badges),
                        db.dump_json(details),
                        timestamp,
                        iso(offer.depart_at),
                        iso(offer.arrive_at),
                        offer.pickup_name,
                        offer.pickup_lat,
                        offer.pickup_lon,
                        offer.dropoff_name,
                        offer.dropoff_lat,
                        offer.dropoff_lon,
                        row["id"],
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO offers(
                        watch_id, fingerprint, source, mode, operator, depart_at, arrive_at,
                        price, currency, price_is_estimate, pickup_name, pickup_lat, pickup_lon,
                        dropoff_name, dropoff_lat, dropoff_lon, duration_min, seats, rating,
                        deeplink, score, badges, details, first_seen, last_seen, active
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
                    """,
                    (
                        watch_id,
                        offer.fingerprint,
                        offer.source,
                        offer.mode,
                        offer.operator,
                        iso(offer.depart_at),
                        iso(offer.arrive_at),
                        price,
                        offer.currency,
                        1 if offer.price_is_estimate else 0,
                        offer.pickup_name,
                        offer.pickup_lat,
                        offer.pickup_lon,
                        offer.dropoff_name,
                        offer.dropoff_lat,
                        offer.dropoff_lon,
                        offer.duration_min,
                        offer.seats,
                        offer.rating,
                        offer.deeplink,
                        info.get("score"),
                        db.dump_json(badges),
                        db.dump_json(details),
                        timestamp,
                        timestamp,
                    ),
                )
                if not offer.price_is_estimate and price is not None:
                    new_offers.append(_offer_preview(offer))

    new_offers.sort(key=lambda item: item.get("price") or 999999)
    return new_offers, drops


def _deactivate_past(watch_id: int) -> None:
    threshold = (now_utc() - timedelta(minutes=10)).isoformat()
    with db.db() as conn:
        conn.execute(
            "UPDATE offers SET active=0 WHERE watch_id=? AND active=1 "
            "AND ((arrive_at IS NOT NULL AND arrive_at < ?) OR (arrive_at IS NULL AND depart_at IS NOT NULL AND depart_at < ?))",
            (watch_id, threshold, threshold),
        )


def _best_offer(offers: list[Offer], ranked: dict[str, dict]) -> dict[str, Any] | None:
    candidates = [o for o in offers if o.price is not None and not o.price_is_estimate]
    if not candidates:
        return None
    best = max(candidates, key=lambda o: ranked.get(o.fingerprint, {}).get("score") or 0)
    preview = _offer_preview(best)
    preview["score"] = ranked.get(best.fingerprint, {}).get("score")
    return preview
