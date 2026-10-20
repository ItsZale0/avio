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
from ..services import passes as passes_service
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
    # Le priorita' dell'utente: un travel agent ragiona su questo, non solo
    # sul prezzo piu' basso.
    if prefs.get("prefer_fast"):
        parts.append(
            "Priorita' dell'utente: ARRIVARE PRIMA. Se un viaggio piu' veloce "
            "costa qualcosa in piu', sceglilo. Minimizza i cambi."
        )
    if prefs.get("no_changes"):
        parts.append("Priorita' dell'utente: VIAGGIARE SENZA CAMBI.")
    if prefs.get("max_walk_km"):
        parts.append(f"Massimo {prefs['max_walk_km']} km a piedi per il cambio.")
    if prefs.get("luggage"):
        parts.append(f"Bagagli: {prefs['luggage']}")
    if prefs.get("prefer_modes"):
        modes = prefs.get("prefer_modes")
        if isinstance(modes, list) and modes:
            parts.append("Mezzi preferiti: " + ", ".join(str(m) for m in modes))
    pass_summary = passes_service.summary(
        passes_service.normalize_passes(prefs.get("passes"))
    )
    if pass_summary:
        parts.append(
            f"Abbonamenti in corso: {pass_summary}. Le tratte coperte costano 0 euro "
            "(il prezzo del biglietto resta, ma non lo paghi)."
        )
    if prefs:
        parts.append("Preferenze apprese: " + json.dumps(prefs, ensure_ascii=False))
    return "\n".join(parts)


def _human_duration(minutes: int | None) -> str | None:
    if not minutes:
        return None
    hours, rest = divmod(int(minutes), 60)
    return f"{hours}h {rest:02d}m" if hours else f"{rest} min"


def _candidates_payload(candidates: list[Candidate]) -> list[dict[str, Any]]:
    # Riferimenti per far capire all'AI il trade-off: il prezzo minimo e la
    # durata minima tra i candidati.
    cheapest = min((c.effective_cost for c in candidates), default=None)
    fastest = min(
        (c.total_duration_min for c in candidates if c.total_duration_min), default=None
    )
    payload = []
    for candidate in candidates[:MAX_CANDIDATES_IN_PROMPT]:
        legs = []
        for leg in candidate.legs:
            leg_info = {
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
            if leg.details.get("spezzatura"):
                # Due biglietti: l'AI deve poterlo spiegare.
                leg_info["spezzatura"] = True
                leg_info["risparmio"] = leg.details.get("risparmio")
                leg_info["prezzo_diretto"] = leg.details.get("prezzo_diretto")
            legs.append(leg_info)
        duration = candidate.total_duration_min
        payload.append(
            {
                "id": candidate.id,
                "kind": candidate.kind,
                "total_price": round(candidate.total_price, 2),
                "net_total_price": round(candidate.net_total, 2),
                "covered": candidate.covered,
                "passes": list(candidate.passes),
                "effective_cost": candidate.effective_cost,
                "depart_local": human_rome(candidate.depart_at, "%d/%m %H:%M")
                if candidate.depart_at
                else None,
                "arrive_local": human_rome(candidate.arrive_at, "%d/%m %H:%M")
                if candidate.arrive_at
                else None,
                "duration_min": duration,
                "duration": _human_duration(duration),
                "changes": candidate.changes,
                "legs_count": len(candidate.legs),
                "partial": candidate.partial,
                "transfer_km": round(candidate.transfer_km, 1)
                if candidate.transfer_km is not None
                else None,
                # Confronto: quanto costa in piu' rispetto al piu' economico e
                # quanto si guadagna in tempo rispetto al piu' veloce.
                "extra_cost_vs_cheapest": round(candidate.effective_cost - cheapest, 2)
                if cheapest is not None
                else None,
                "faster_than_slowest_min": (fastest - duration)
                if (fastest is not None and duration is not None)
                else None,
                "legs": legs,
            }
        )
    return payload


def _templated_explanation(candidate: Candidate, watch: dict[str, Any]) -> str:
    """Spiegazione deterministica: cosa costa, quanto tempo ci mette, quanti cambi."""
    partial = (
        " Il totale è parziale: una tratta non ha prezzo, verifica la tariffa ufficiale."
        if candidate.partial
        else ""
    )
    durata = _human_duration(candidate.total_duration_min)
    quantita = []
    if durata:
        quantita.append(f"durata {durata}")
    if candidate.changes:
        quantita.append(
            f"{candidate.changes} cambi" if candidate.changes > 1 else "1 cambio"
        )
    else:
        quantita.append("nessun cambio")
    quadro = ", ".join(quantita)
    # Se e' coperto da un abbonamento, e' la notte piu' importante.
    coperto = ""
    if candidate.covered:
        nomi = ", ".join(candidate.passes)
        coperto = (
            f" Il viaggio e' coperto da {nomi}: non paghi il biglietto "
            f"({candidate.net_total:.2f} EUR effettivi invece di "
            f"{candidate.total_price:.2f} EUR)."
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
            f"Opzione più economica: {''.join(parts)}. "
            f"Totale {candidate.total_price:.2f} EUR ({quadro}), "
            f"arrivo {human_rome(candidate.arrive_at, '%a %d %b, %H:%M')}.{coperto}{partial}"
        )
    leg = candidate.legs[0]
    if leg.details.get("spezzatura"):
        # Il caso piu' interessante: due biglietti invece di uno.
        risparmio = leg.details.get("risparmio")
        diretto = leg.details.get("prezzo_diretto")
        tappe = leg.details.get("tappe") or []
        percorso = " + ".join(
            f"{step.get('da')}→{step.get('a')}" for step in tappe if isinstance(step, dict)
        )
        return (
            f"Si compra con due biglietti: {percorso}. "
            f"Totale {candidate.total_price:.2f} EUR invece di {diretto:.2f} EUR "
            f"del biglietto unico: risparmi {risparmio:.2f} EUR. "
            f"{quadro}, arrivo {human_rome(candidate.arrive_at, '%a %d %b, %H:%M')}.{coperto}"
        )
    return (
        f"Opzione più economica: {leg.operator or leg.source} "
        f"({human_rome(leg.depart_at, '%H:%M')} → {human_rome(leg.arrive_at, '%H:%M')}) "
        f"a {candidate.total_price:.2f} EUR. {quadro}, "
        f"arrivo {human_rome(candidate.arrive_at, '%a %d %b, %H:%M')}.{coperto}"
    )


def _transfer_note(previous: Offer, following: Offer) -> str:
    """Nota sul cambio tra due tratte (con la distanza, se nota)."""
    km = _transfer_km(previous, following)
    if km is None:
        return ""
    return f"cambio a {previous.dropoff_name} (~{km:.1f} km),"


def _profile_of(candidate: Candidate, all_candidates: list[Candidate]) -> str:
    """Etichetta il profilo di un piano: economico, veloce, comodo."""
    others = [c for c in all_candidates if c.id != candidate.id]
    if not others:
        return "proposta"
    cheapest = min(c.effective_cost for c in others)
    fastest = min((c.total_duration_min or 10**6) for c in others)
    # Solo il piu' economico in assoluto e' "economico": se i costi sono pari
    # (per esempio con un abbonamento che copre tutto), conta altro.
    if candidate.effective_cost < cheapest - 0.01:
        return "economico"
    if candidate.total_duration_min and candidate.total_duration_min <= fastest:
        return "veloce"
    if candidate.changes <= min(c.changes for c in others):
        return "comodo"
    if candidate.effective_cost <= cheapest + 0.01:
        return "economico"
    return "alternativa"


def _plan_from_candidate(
    candidate: Candidate,
    ranked: dict[str, dict] | None,
    *,
    title: str | None = None,
    explanation: str | None = None,
    confidence: float | None = None,
    watchouts: list[str] | None = None,
    ai: bool = False,
    profile: str | None = None,
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
    plan["profile"] = profile
    plan["fingerprint"] = _fingerprint(plan)
    return plan


def _fingerprint(plan: dict[str, Any]) -> str:
    ids = ",".join(str(step.get("offer_id")) for step in plan.get("steps", []))
    return f"{plan.get('total_price')}|{ids}"


def _pass_question(prefs: dict[str, Any]) -> list[dict]:
    """Chiede quali abbonamenti ha l'utente, se non li ha gia' dichiarati.

    E' la domanda che spesso cambia tutto: con un abbonamento regionale il
    viaggio da 6 ore costa 0 euro e diventa il piano migliore.
    """
    if passes_service.normalize_passes(prefs.get("passes")):
        return []
    return [
        {
            "key": "abbonamenti",
            "text": (
                "Hai qualche abbonamento o carnet in corso (Trenitalia, bus/metro "
                "cittadino, carnet da 10 biglietti)? Se la tratta e' coperta il "
                "biglietto non lo paghi: il piano migliore puo' cambiare del tutto."
            ),
            "options": [
                {
                    "id": "trenitalia_regionale",
                    "label": "Abbonamento Trenitalia (regionale)",
                    "prefs": {"add_pass": "trenitalia_regionale"},
                },
                {
                    "id": "trenitalia_tutti",
                    "label": "Abbonamento Trenitalia (tutti i treni)",
                    "prefs": {"add_pass": "trenitalia_tutti"},
                },
                {
                    "id": "carnet_regionale",
                    "label": "Carnet 10 biglietti regionali",
                    "prefs": {"add_pass": "carnet_regionale"},
                },
                {
                    "id": "urbano",
                    "label": "Abbonamento bus/metro cittadino",
                    "prefs": {"add_pass": "urbano"},
                },
                {
                    "id": "none",
                    "label": "Nessuno, pago ogni biglietto",
                    "prefs": {"clear_passes": True},
                },
            ],
        }
    ]


def _relax_questions(
    offers: list[Offer], ctx: SearchContext, prefs: dict[str, Any]
) -> list[dict]:
    """Domande mirate quando non c'e' nessuna combinazione valida."""
    latest = ctx.arrive_by + timedelta(minutes=int(prefs.get("flexible_days", 0)) * 1440)
    # Offerte che arrivano nel giorno richiesta (ed entro il limite): sono
    # le uniche che interessano per il prezzo.
    in_window = [
        o
        for o in offers
        if o.arrive_at is None
        or (
            ctx.arrival_day_start <= o.arrive_at <= latest
            or (prefs.get("flexible_days", 0) and o.arrive_at > latest)
        )
    ]
    late = [
        o
        for o in offers
        if o.arrive_at
        and o.arrive_at > latest
        and _reaches_destination(o, ctx)
        and (o.price is None or ctx.max_price is None or o.price <= ctx.max_price)
    ]    # Nella finestra di tempo ma piu' care del budget: e' il prezzo reale
    # della soluzione che l'utente deve conoscere per decidere.
    over_budget = [
        o
        for o in in_window
        if ctx.max_price is not None
        and o.price is not None
        and o.price > ctx.max_price
    ]
    # Offerte che arrivano la sera prima: servono solo se l'utente le accetta.
    giorno_prima = [
        o
        for o in offers
        if o.arrive_at
        and ctx.arrival_day_start <= o.arrive_at < ctx.arrival_day_start + timedelta(days=1)
        and o.arrive_at > ctx.arrival_day_start
    ]
    # La sera primavera' rispetto al giorno richiesto.
    sera_prima = [
        o
        for o in offers
        if o.arrive_at
        and (ctx.arrival_day_start - timedelta(days=1)) <= o.arrive_at < ctx.arrival_day_start
    ]
    # Ordine di priorita' delle domande (al massimo due vanno avanti):
    # 1. gli abbonamenti: se la tratta e' coperta, il budget non e' un problema;
    # 2. il budget: il prezzo reale della soluzione piu' economica;
    # 3. la sera prima e il giorno dopo.
    ordinate: list[dict] = []
    ordinate.extend(_pass_question(prefs))
    if over_budget and ctx.max_price:
        cheapest = min(over_budget, key=lambda o: o.price or 0)
        suggested = round((cheapest.price or 0) * 1.1, 2)
        ordinate.append(
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
                        "label": f"Alzo a {suggested:.0f} EUR",
                        "prefs": {"budget_raise": suggested},
                    },
                ],
            }
        )
    if sera_prima and not prefs.get("arrivo_giorno_prima"):
        presto = max(sera_prima, key=lambda o: o.arrive_at or ctx.arrival_day_start)
        ordinate.append(
            {
                "key": "arrivo_giorno_prima",
                "text": (
                    "Nessuna corsa arriva in tempo per l'ora che hai chiesto. "
                    f"Il piu' vicino arriva {human_rome(presto.arrive_at, '%a %d %b alle %H:%M')}, "
                    "cioe' la sera prima: va bene o preferisci aspettare il giorno dopo?"
                ),
                "options": [
                    {
                        "id": "no",
                        "label": "No, voglio arrivare quel giorno",
                        "prefs": {"arrivo_giorno_prima": False},
                    },
                    {
                        "id": "yes",
                        "label": "Si, arrivo la sera prima",
                        "prefs": {"arrivo_giorno_prima": True},
                    },
                ],
            }
        )
    if late and not prefs.get("flexible_days"):
        soonest = min(late, key=lambda o: o.arrive_at or latest)
        ordinate.append(
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
    return ordinate[:2]


def _priority_question(candidates: list[Candidate]) -> list[dict]:
    """Chiede la priorita' quando c'e' un trade-off reale fra tempo e prezzo.

    E' la domanda che fa un travel agent: "il piu' economico ci mette 6 ore,
    il piu' veloce costa 5 euro in piu': cosa preferisci?".
    """
    usable = [
        c
        for c in candidates
        if c.total_duration_min and not c.partial and c.effective_cost > 0
    ]
    if len(usable) < 2:
        return []
    cheapest = min(usable, key=lambda c: c.effective_cost)
    fastest = min(usable, key=lambda c: (c.total_duration_min or 10**6, c.effective_cost))
    if fastest is cheapest:
        return []
    # Quanto tempo si guadagna scegliendo il piu' veloce (e quanto costa).
    gain = (cheapest.total_duration_min or 0) - (fastest.total_duration_min or 0)
    extra = fastest.effective_cost - cheapest.effective_cost
    if gain < 60 or extra < 1:
        return []
    return [
        {
            "key": "priorita",
            "text": (
                f"Ci sono due ottimi viaggi: il piu' economico costa "
                f"{cheapest.effective_cost:.2f} EUR e ci mette "
                f"{_human_duration(cheapest.total_duration_min)}; il piu' veloce costa "
                f"{fastest.effective_cost:.2f} EUR e ci mette "
                f"{_human_duration(fastest.total_duration_min)}. Cosa preferisci?"
            ),
            "options": [
                {
                    "id": "economico",
                    "label": (
                        f"Preferisco spendere meno "
                        f"({cheapest.effective_cost:.2f} EUR, "
                        f"{_human_duration(cheapest.total_duration_min)})"
                    ),
                    "prefs": {"prefer_fast": False},
                },
                {
                    "id": "veloce",
                    "label": (
                        f"Preferisco arrivare prima "
                        f"({_human_duration(fastest.total_duration_min)}, "
                        f"{fastest.effective_cost:.2f} EUR)"
                    ),
                    "prefs": {"prefer_fast": True},
                },
            ],
        }
    ]


def _pick_alternatives(candidates: list[Candidate], chosen: Candidate) -> list[Candidate]:
    """Sceglie le alternative con un PROFILO diverso dal piano scelto.

    Un travel agent non mostra "le altre tre piu' economiche": mostra
    "il piu' veloce", "il piu' comodo (meno cambi)", "il piu' economico".
    """
    rest = [c for c in candidates if c.id != chosen.id]
    if not rest:
        return []
    picked: list[Candidate] = []
    seen: set[str] = set()

    def _add(candidate: Candidate | None) -> None:
        if candidate is None or candidate.id in seen:
            return
        seen.add(candidate.id)
        picked.append(candidate)

    priced = [c for c in rest if c.total_duration_min]
    _add(min(priced, key=lambda c: c.total_duration_min or 10**6) if priced else None)
    _add(min(rest, key=lambda c: (c.changes, c.effective_cost)))
    _add(min(rest, key=lambda c: c.effective_cost))
    for candidate in rest:
        if len(picked) >= 3:
            break
        _add(candidate)
    return picked


def _merge_alternatives(
    candidates: list[Candidate], chosen: Candidate, ai_ids: Any
) -> list[Candidate]:
    """Alternativhe: prima quelle indicate dall'AI, poi i profili diversi."""
    by_id = {c.id: c for c in candidates}
    picked: list[Candidate] = []
    seen: set[str] = set()

    if isinstance(ai_ids, list):
        for raw in ai_ids[:3]:
            candidate = by_id.get(str(raw))
            if candidate is not None and candidate.id != chosen.id:
                seen.add(candidate.id)
                picked.append(candidate)
    for candidate in _pick_alternatives(candidates, chosen):
        if len(picked) >= 3:
            break
        if candidate.id in seen:
            continue
        seen.add(candidate.id)
        picked.append(candidate)
    return picked


def _ai_options_with_prefs(key: str, options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Alle opzioni dell'AI sulle priorita' aggancia le preferenze.

    Cosi' il click sul pulsante e' deterministico: non serve che l'AI
    interpreti la risposta una seconda volta.
    """
    if key not in ("priorita", "priority", "prefer_fast", "travel_time", "speed"):
        return options
    enriched: list[dict[str, Any]] = []
    for option in options:
        label = str(option.get("label") or "").lower()
        if any(word in label for word in ("veloc", "prima", "tempo", "arrivare")):
            enriched.append({**option, "prefs": {"prefer_fast": True}})
        elif any(word in label for word in ("econom", "meno", "risparm", "soldi")):
            enriched.append({**option, "prefs": {"prefer_fast": False}})
        else:
            enriched.append(option)
    return enriched


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
    alternatives = _pick_alternatives(candidates, chosen)
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
            alternatives = _merge_alternatives(
                candidates, chosen, data.get("alternatives")
            )
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
                            "options": _ai_options_with_prefs(
                                str(raw["key"]), options
                            ),
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
        profile=_profile_of(chosen, candidates),
    )
    result.plan = plan
    result.alternatives = [
        _plan_from_candidate(
            candidate,
            ranked,
            ai=result.used_ai,
            profile=_profile_of(candidate, candidates),
        )
        for candidate in alternatives[:3]
    ]
    # Le domande dell'AI hanno la precedenza, ma la domanda sugli
    # abbonamenti resta: se la tratta e' coperta cambia il piano.
    if ai_questions:
        result.questions = (ai_questions + _pass_question(prefs))[:2]
    else:
        result.questions = (_pass_question(prefs) + _priority_question(candidates))[:2]
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

    if key in ("budget_flex", "budget", "budget_increase", "budget_raise"):
        # "alzo a 15", "15 euro", "fino a 20" -> il nuovo tetto.
        cifre = [ch for ch in clean if ch.isdigit() or ch in ".,"]
        if cifre:
            try:
                import re

                numeri = re.findall(r"\d+(?:[.,]\d+)?", clean)
                if numeri:
                    amount = float(numeri[0].replace(",", "."))
                    if amount > 0:
                        return {"prefs": {"budget_raise": round(amount, 2)}, "note": clean}
            except (TypeError, ValueError):
                pass
        flag = yes_no(lowered, True)
        if flag is not None:
            return {"prefs": {"notes": clean}, "note": clean}

    if key in ("arrivo_giorno_prima", "day_before", "arrivo"):
        flag = yes_no(lowered, True)
        if flag is not None:
            return {"prefs": {"arrivo_giorno_prima": flag}, "note": clean}

    if key in ("allow_multi_leg", "multi_leg", "changes"):
        flag = yes_no(lowered, True)
        if flag is not None:
            return {"prefs": {"allow_multi_leg": flag}, "note": clean}

    if key in ("abbonamenti", "passes", "pass"):
        # Testo libero: "ho l'abbonamento Trenitalia" -> il tipo giusto.
        testo = lowered
        if any(word in testo for word in ("nessun", "niente", "no abbonament", "pagho ogni")):
            return {"prefs": {"clear_passes": True}, "note": clean}
        if "freccia" in testo or "tutti i treni" in testo or "annuale" in testo:
            return {"prefs": {"add_pass": "trenitalia_tutti"}, "note": clean}
        if "carnet" in testo and "bus" in testo:
            return {"prefs": {"add_pass": "carnet_bus"}, "note": clean}
        if "carnet" in testo:
            return {"prefs": {"add_pass": "carnet_regionale"}, "note": clean}
        if "trenitalia" in testo or "treno" in testo or "regionale" in testo:
            return {"prefs": {"add_pass": "trenitalia_regionale"}, "note": clean}
        if any(word in testo for word in ("bus", "metro", "urbano", "citta", "città", "tram")):
            return {"prefs": {"add_pass": "urbano"}, "note": clean}

    if key in ("priorita", "priority", "prefer_fast", "travel_time", "speed"):
        if clean.strip().lower() in ("yes", "si", "sì", "ok", "true", "1"):
            return {"prefs": {"prefer_fast": True}, "note": clean}
        if clean.strip().lower() in ("no", "false", "0"):
            return {"prefs": {"prefer_fast": False}, "note": clean}
        if any(word in lowered for word in ("veloc", "prima", "tempo", "arrivare", "veloce")):
            return {"prefs": {"prefer_fast": True}, "note": clean}
        if any(word in lowered for word in ("econom", "meno", "spend", "risparm", "soldi")):
            return {"prefs": {"prefer_fast": False}, "note": clean}

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
