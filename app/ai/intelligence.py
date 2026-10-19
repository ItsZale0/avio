"""Orchestratore dell'intelligenza: pianifica, spiega, fa domande.

Punti chiave:
- Il modello non inventa offerte: sceglie tra le combinazioni candidate
  costruite dal planner con dati reali (app/ai/planner.py).
- Se l'AI non e' disponibile l'app resta pienamente funzionante: il piano
  deterministico e' la combinazione piu' economica trovata.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..adapters.base import Offer, SearchContext
from ..timeutil import human_rome, now_rome
from . import prompts
from .groq import ChatResult, ai_enabled, client_from_settings, log_call
from .planner import Candidate, _reaches_destination, _transfer_km, build_candidates

MAX_CANDIDATES_IN_PROMPT = 14


@dataclass
class IntelligenceResult:
    plan: dict | None = None
    alternatives: list[dict] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)
    summary: str = ""
    used_ai: bool = False
    tokens: int = 0
    model: str | None = None
    error: str | None = None
    candidates_count: int = 0
    improved: bool = False
    price_delta: float | None = None


def prefs_of(watch: dict[str, Any]) -> dict[str, Any]:
    prefs = watch.get("prefs")
    if isinstance(prefs, str):
        try:
            prefs = json.loads(prefs)
        except (ValueError, TypeError):
            prefs = {}
    return prefs if isinstance(prefs, dict) else {}


def _constraints_text(watch: dict[str, Any], prefs: dict[str, Any]) -> str:
    parts = [
        f"Percorso: {watch['from_place']} -> {watch['to_place']}",
        f"Arrivo entro: {human_rome(watch['arrive_by'], '%a %d %b %Y, %H:%M')}",
    ]
    if watch.get("depart_after"):
        parts.append(f"Partenza non prima di: {human_rome(watch['depart_after'], '%a %d %b, %H:%M')}")
    if watch.get("max_price"):
        parts.append(f"Budget: {watch['max_price']:.2f} EUR")
    parts.append(f"Passeggeri: {watch.get('passengers', 1)}")
    if prefs:
        parts.append("Preferenze apprese: " + json.dumps(prefs, ensure_ascii=False))
    return "\n".join(parts)


def _candidates_payload(candidates: list[Candidate]) -> list[dict[str, Any]]:
    payload = []
    for candidate in candidates[:MAX_CANDIDATES_IN_PROMPT]:
        legs = []
        for leg in candidate.legs:
            legs.append(
                {
                    "mode": leg.mode,
                    "operator": leg.operator or leg.source,
                    "from": leg.pickup_name,
                    "to": leg.dropoff_name,
                    "depart": human_rome(leg.depart_at, "%d/%m %H:%M") if leg.depart_at else None,
                    "arrive": human_rome(leg.arrive_at, "%d/%m %H:%M") if leg.arrive_at else None,
                    "price": round(leg.price, 2) if leg.price is not None else None,
                    "estimate": leg.price_is_estimate,
                    "link": leg.deeplink,
                }
            )
        payload.append(
            {
                "id": candidate.id,
                "kind": candidate.kind,
                "total_price": round(candidate.total_price, 2),
                "effective_cost": candidate.effective_cost,
                "arrive_local": human_rome(candidate.arrive_at, "%d/%m %H:%M")
                if candidate.arrive_at
                else None,
                "duration_min": candidate.total_duration_min,
                "partial": candidate.partial,
                "transfer_km": round(candidate.transfer_km, 1)
                if candidate.transfer_km is not None
                else None,
                "legs": legs,
            }
        )
    return payload


def _templated_explanation(candidate: Candidate, watch: dict[str, Any]) -> str:
    partial = (
        " Il totale è parziale: una tratta non ha prezzo, verifica la tariffa ufficiale."
        if candidate.partial
        else ""
    )
    if candidate.kind in ("combo", "triple"):
        parts = []
        for index, leg in enumerate(candidate.legs):
            if index:
                previous = candidate.legs[index - 1]
                transfer = _transfer_note(previous, leg)
                parts.append(f"{transfer} poi " if transfer else " poi ")
            price = f"{leg.price:.2f} EUR" if leg.price is not None else "prezzo da verificare"
            parts.append(
                f"{leg.operator or leg.source} "
                f"({human_rome(leg.depart_at, '%H:%M')} → {human_rome(leg.arrive_at, '%H:%M')}, {price})"
            )
        return (
            f"Più economico trovato: {''.join(parts)}. "
            f"Totale {candidate.total_price:.2f} EUR, "
            f"arrivo {human_rome(candidate.arrive_at, '%a %d %b, %H:%M')}.{partial}"
        )
    leg = candidate.legs[0]
    return (
        f"Più economico trovato: {leg.operator or leg.source} "
        f"({human_rome(leg.depart_at, '%H:%M')} → {human_rome(leg.arrive_at, '%H:%M')}) "
        f"a {candidate.total_price:.2f} EUR. "
        f"Arrivo {human_rome(candidate.arrive_at, '%a %d %b, %H:%M')}."
    )


def _transfer_note(previous: Offer, following: Offer) -> str:
    """Nota sul cambio tra due tratte (con la distanza, se nota)."""
    km = _transfer_km(previous, following)
    if km is None:
        return ""
    return f"cambio a {previous.dropoff_name} (~{km:.1f} km),"


def _plan_from_candidate(
    candidate: Candidate,
    ranked: dict[str, dict] | None,
    *,
    title: str | None = None,
    explanation: str | None = None,
    confidence: float | None = None,
    watchouts: list[str] | None = None,
    ai: bool = False,
) -> dict[str, Any]:
    plan = candidate.as_dict(ranked=ranked)
    notes = list(plan.pop("notes", []) or [])
    if candidate.partial:
        notes.append(
            "Alcune tratte non hanno un prezzo pubblico (es. orari GTFS): "
            "verifica la tariffa ufficiale prima di partire."
        )
    plan["title"] = title or candidate.label()
    plan["explanation"] = explanation or ""
    plan["confidence"] = confidence
    plan["watchouts"] = list(watchouts or []) + notes
    plan["ai"] = ai
    plan["fingerprint"] = _fingerprint(plan)
    return plan


def _fingerprint(plan: dict[str, Any]) -> str:
    ids = ",".join(str(step.get("offer_id")) for step in plan.get("steps", []))
    return f"{plan.get('total_price')}|{ids}"


def _relax_questions(
    offers: list[Offer], ctx: SearchContext, prefs: dict[str, Any]
) -> list[dict]:
    """Domande mirate quando non c'e' nessuna combinazione valida."""
    latest = ctx.arrive_by + timedelta(minutes=int(prefs.get("flexible_days", 0)) * 1440)
    late = [
        o
        for o in offers
        if o.arrive_at
        and o.arrive_at > latest
        and _reaches_destination(o, ctx)
        and (o.price is None or ctx.max_price is None or o.price <= ctx.max_price)
    ]
    over_budget = [
        o
        for o in offers
        if ctx.max_price is not None
        and o.price is not None
        and o.price > ctx.max_price
    ]
    questions: list[dict] = []
    if late and not prefs.get("flexible_days"):
        soonest = min(late, key=lambda o: o.arrive_at or latest)
        questions.append(
            {
                "key": "flexible_days",
                "text": (
                    f"Ho trovato una corsa ma arriva {human_rome(soonest.arrive_at, '%a %d %b alle %H:%M')}, "
                    "dopo il limite. Posso considerare anche arrivi più tardi?"
                ),
                "options": [
                    {"id": "0", "label": "No, entro il limite", "prefs": {"flexible_days": 0}},
                    {"id": "1", "label": "Si, anche il giorno dopo", "prefs": {"flexible_days": 1}},
                    {"id": "2", "label": "Si, fino a 2 giorni dopo", "prefs": {"flexible_days": 2}},
                ],
            }
        )
    if over_budget and ctx.max_price:
        cheapest = min(over_budget, key=lambda o: o.price or 0)
        questions.append(
            {
                "key": "budget_flex",
                "text": (
                    f"La soluzione più economica costa {cheapest.price:.2f} EUR, sopra il budget "
                    f"di {ctx.max_price:.2f} EUR. Vuoi alzare il budget?"
                ),
                "options": [
                    {"id": "keep", "label": "No, resto sul budget", "prefs": {}},
                    {
                        "id": "raise",
                        "label": f"Alzo a {round((cheapest.price or 0) * 1.1, 2):.0f} EUR",
                        "prefs": {"budget_raise": round((cheapest.price or 0) * 1.1, 2)},
                    },
                ],
            }
        )
    return questions[:2]


async def analyze(
    watch: dict[str, Any],
    offers: list[Offer],
    ctx: SearchContext,
    *,
    ranked: dict[str, dict] | None = None,
    run_id: int | None = None,
    raw_offers: list[Offer] | None = None,
) -> IntelligenceResult:
    prefs = prefs_of(watch)
    flexible_minutes = int(prefs.get("flexible_days", 0) or 0) * 1440
    max_transfer = float(prefs.get("max_walk_km") or 3.0)
    if prefs.get("no_changes"):
        max_transfer = 0

    candidates = build_candidates(
        offers,
        ctx,
        ranked=ranked,
        flexible_minutes=flexible_minutes,
        max_transfer_km=max(1.0, max_transfer),
        allow_multi_leg=prefs.get("allow_multi_leg") is not False,
        pool=raw_offers,
    )
    result = IntelligenceResult(candidates_count=len(candidates))

    if not candidates:
        # Usa le offerte grezze: una corsa scartata perche' arriva tardi
        # e' proprio il motivo per cui conviene chiedere all'utente.
        diagnostics = raw_offers if raw_offers is not None else offers
        if not diagnostics:
            result.summary = "Ancora nessuna offerta dalle fonti attive."
            return result
        result.questions = _relax_questions(diagnostics, ctx, prefs)
        result.summary = "Nessuna combinazione rispetta i vincoli attuali."
        return result

    client = client_from_settings()
    use_ai = ai_enabled() and client.available

    chosen = candidates[0]
    alternatives = candidates[1:]
    ai_questions: list[dict] = []
    watchouts: list[str] = []
    title = None
    explanation = None
    confidence: float | None = None

    if use_ai:
        messages = [
            {"role": "system", "content": prompts.PLAN_SYSTEM},
            {
                "role": "user",
                "content": (
                    "RICHIESTA E VINCOLI\n"
                    + _constraints_text(watch, prefs)
                    + "\n\nCOMBINAZIONI CANDIDATE (JSON)\n"
                    + json.dumps(_candidates_payload(candidates), ensure_ascii=False)
                    + "\n\nScegli il piano più economico e rispondi in JSON."
                ),
            },
        ]
        chat: ChatResult = await client.chat(
            messages, max_tokens=2000, temperature=0.2, timeout=45
        )
        log_call("plan", watch_id=watch["id"], run_id=run_id, result=chat)
        if chat.ok:
            data = chat.json() or {}
            by_id = {candidate.id: candidate for candidate in candidates}
            picked = by_id.get(str(data.get("chosen_id") or ""))
            if picked is None:
                picked = chosen
            chosen = picked
            alternatives = [c for c in candidates if c.id != chosen.id]
            title = str(data.get("title") or "").strip() or None
            explanation = str(data.get("explanation") or "").strip() or None
            try:
                confidence = float(data.get("confidence"))
            except (TypeError, ValueError):
                confidence = None
            watchouts = [str(w) for w in data.get("watchouts", []) if str(w).strip()][:4]
            for raw in data.get("questions", [])[:2]:
                if isinstance(raw, dict) and raw.get("key") and raw.get("text"):
                    options = []
                    for opt in raw.get("options", [])[:4]:
                        if isinstance(opt, dict) and opt.get("id"):
                            options.append(
                                {
                                    "id": str(opt["id"]),
                                    "label": str(opt.get("label") or opt["id"]),
                                }
                            )
                    ai_questions.append(
                        {
                            "key": str(raw["key"]),
                            "text": str(raw["text"]),
                            "options": options,
                        }
                    )
            result.used_ai = True
            result.tokens = chat.tokens
            result.model = chat.model
        else:
            result.error = chat.error

    if not explanation:
        explanation = _templated_explanation(chosen, watch)
    if confidence is None:
        confidence = 0.62 if result.used_ai else 0.55

    plan = _plan_from_candidate(
        chosen,
        ranked,
        title=title,
        explanation=explanation,
        confidence=confidence,
        watchouts=watchouts,
        ai=result.used_ai,
    )
    result.plan = plan
    result.alternatives = [
        _plan_from_candidate(candidate, ranked, ai=result.used_ai) for candidate in alternatives[:3]
    ]
    result.questions = ai_questions
    parziale = " (prezzo parziale)" if plan.get("partial") else ""
    result.summary = f"{plan['title']} · {plan['total_price']:.2f} EUR{parziale}"

    previous = watch.get("ai_best_price")
    if previous is not None and plan.get("effective_cost") is not None:
        delta = float(previous) - float(plan["effective_cost"])
        if delta >= max(1.0, float(previous) * 0.04):
            result.improved = True
            result.price_delta = round(delta, 2)
    return result


async def parse_trip_text(text: str) -> dict[str, Any]:
    """Trasforma una frase in italiano in campi strutturati per una ricerca."""
    clean = (text or "").strip()
    if not clean:
        return {"ok": False, "error": "Testo vuoto"}

    client = client_from_settings()
    if not (ai_enabled() and client.available):
        return {
            "ok": False,
            "error": "AI non configurata: inserisci la chiave Groq nelle impostazioni.",
        }

    local = now_rome()
    system = prompts.render(prompts.PARSE_SYSTEM, today=local.strftime("%A %d %B %Y"), year=local.year)
    chat = await client.chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": clean},
        ],
        json_mode=True,
        max_tokens=1800,
        temperature=0.1,
        timeout=30,
    )
    log_call("parse", watch_id=None, run_id=None, result=chat)
    if not chat.ok:
        return {"ok": False, "error": chat.error or "Errore AI"}

    data = chat.json()
    if not data:
        return {"ok": False, "error": "Risposta AI non leggibile"}

    parsed = {
        "from_place": _clean_text(data.get("from_place")),
        "to_place": _clean_text(data.get("to_place")),
        "arrive_by": _clean_dt(data.get("arrive_by")),
        "depart_after": _clean_dt(data.get("depart_after")),
        "max_price": _clean_number(data.get("max_price")),
        "passengers": int(data.get("passengers") or 1),
        "radius_km": _clean_number(data.get("radius_km")) or 5,
        "notes": _clean_text(data.get("notes")),
        "prefs": data.get("prefs") if isinstance(data.get("prefs"), dict) else {},
        "summary": _clean_text(data.get("summary")),
    }
    questions = []
    for raw in (data.get("questions") or [])[:2]:
        if isinstance(raw, dict) and raw.get("key") and raw.get("text"):
            options = [
                {"id": str(o.get("id")), "label": str(o.get("label") or o.get("id"))}
                for o in (raw.get("options") or [])[:4]
                if isinstance(o, dict) and o.get("id")
            ]
            questions.append({"key": str(raw["key"]), "text": str(raw["text"]), "options": options})

    missing = [
        field_name
        for field_name in ("from_place", "to_place", "arrive_by")
        if not parsed.get(field_name)
    ]
    return {
        "ok": True,
        "trip": parsed,
        "missing": missing,
        "questions": questions,
        "model": chat.model,
        "tokens": chat.tokens,
    }


async def interpret_answer(question: dict[str, Any], answer: str) -> dict[str, Any]:
    """Traduce una risposta in preferenze. Prima prova le opzioni, poi l'AI."""
    clean = (answer or "").strip()
    for option in question.get("options") or []:
        if isinstance(option, dict) and option.get("id") == clean and isinstance(option.get("prefs"), dict):
            return {"prefs": option["prefs"], "note": option.get("label")}

    key = str(question.get("key") or "")
    lowered = clean.lower()

    def yes_no(value: str, positive) -> bool | None:
        if any(word in value for word in ("si", "sì", "yes", "ok", "va bene", "certo")):
            return positive
        if any(word in value for word in ("no", "nah", "preferisco no")):
            return not positive
        return None

    if key in ("flexible_days", "flexibility", "day_flexible"):
        digits = [int(ch) for ch in clean if ch.isdigit()]
        if digits:
            return {"prefs": {"flexible_days": min(3, digits[0])}, "note": clean}
        flag = yes_no(lowered, True)
        return {"prefs": {"flexible_days": 1 if flag else 0}, "note": clean}

    if key in ("allow_multi_leg", "multi_leg", "changes"):
        flag = yes_no(lowered, True)
        if flag is not None:
            return {"prefs": {"allow_multi_leg": flag}, "note": clean}

    if key == "max_walk_km":
        digits = [ch for ch in clean.replace(",", ".") if ch.isdigit() or ch == "."]
        if digits:
            try:
                return {"prefs": {"max_walk_km": float("".join(digits))}, "note": clean}
            except ValueError:
                pass

    client = client_from_settings()
    if ai_enabled() and client.available:
        chat = await client.chat(
            [
                {"role": "system", "content": prompts.ANSWER_SYSTEM},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"question": question.get("text"), "options": question.get("options"), "answer": clean},
                        ensure_ascii=False,
                    ),
                },
            ],
            model=client.model_fast,
            json_mode=True,
            # I modelli "reasoning" spendono token sul ragionamento prima
            # della risposta: un limite troppo basso la lascia vuota.
            max_tokens=1200,
            temperature=0.1,
            timeout=20,
        )
        log_call("answer", watch_id=question.get("watch_id"), run_id=None, result=chat)
        data = chat.json() if chat.ok else None
        if data and isinstance(data.get("prefs"), dict):
            return {"prefs": data["prefs"], "note": _clean_text(data.get("note"))}

    return {"prefs": {"notes": clean} if clean else {}, "note": clean}


# --------------------------------------------------------------------------- #
# Helper di normalizzazione
# --------------------------------------------------------------------------- #
def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("null", "none", "n/a", "-"):
        return None
    return text[:200]


def _clean_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _clean_dt(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.isoformat()
