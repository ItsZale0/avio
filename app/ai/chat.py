"""Chiedi all'agente: domande e richieste libere, in linguaggio naturale.

L'utente scrive a parole. L'agente:
- risponde usando i dati REALI della ricerca (prezzi, orari, il piano
  scelto, le note dell'ultimo giro): niente prezzi inventati;
- se la richiesta contiene un CAMBIO (budget, orario, mezzo preferito,
  abbonamento, punto di ritrovo), lo applica e la ricerca riparte.

Senza intelligenza configurata il modulo risponde con i dati reali e
riconosce a mano le richieste piu' comuni.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..services import passes as passes_service
from ..services import questions as questions_service
from ..store import current_plans, get_watch
from ..timeutil import ROME, now_utc
from .groq import ai_enabled, client_from_settings, log_call

log = logging.getLogger("carpool.chat")

# Le chiavi che l'agente puo' modificare con una richiesta libera.
CHIAVI_PATCH = (
    "budget_raise",
    "add_pass",
    "clear_passes",
    "set_from_point",
    "set_to_point",
    "set_depart_after",
    "set_arrive_by",
    "flexible_days",
    "allow_multi_leg",
    "max_walk_km",
    "prefer_modes",
    "prefer_fast",
    "no_changes",
    "arrivo_giorno_prima",
    "luggage",
    "notes",
)

SYSTEM = """Sei l'agente di viaggio di un'app italiana. Rispondi in italiano, breve e concreto, come un bravo travel agent.

Dati che hai davanti (SONO REALI, non inventare altri):
{contesto}

Cosa ti chiede l'utente:
"{richiesta}"

Regole:
- Rispondi solo con i dati forniti: se un prezzo o un orario non c'e', dillo.
- Puoi fare domande di chiarimento, ma non farne a raffica: una domanda sola se serve davvero.
- Se la richiesta contiene un CAMBIO da applicare alla ricerca (budget, orario di partenza o arrivo, mezzo preferito, abbonamento in possesso, punto di ritrovo, preferenze), mettilo in "prefs" con queste chiavi e sole:
  {chiavi}
  - "budget_raise": numero (il nuovo tetto in euro)
  - "set_depart_after" / "set_arrive_by": orario locale "YYYY-MM-DDTHH:MM"
  - "prefer_modes": lista di "treno", "bus", "passaggio"
  - "add_pass": identificatore dell'abbonamento (se lo conosci: {passes})
  - "set_from_point" / "set_to_point": il punto esatto ("Stazione di Trento")
- Se non c'e' nessun cambio da fare, "prefs" resta vuoto.
- Metti in "rerun" true se e' stato cambiato qualcosa che richiede di ricalcolare i piani.

Rispondi SEMPRE e SOLO con un oggetto JSON valido, senza testo attorno:
{{"reply": "la risposta all'utente", "prefs": {{}}, "rerun": false}}
"""


def _fmt(quando: Any) -> str:
    if not quando:
        return "-"
    try:
        return str(quando)[:16].replace("T", " ")
    except Exception:  # noqa: BLE001
        return str(quando)[:16]


def _contesto(watch: dict[str, Any]) -> dict[str, Any]:
    """I dati reali della ricerca, per l'agente."""
    watch_id = watch.get("id")
    plans = current_plans(int(watch_id)) if watch_id else []
    piano = next((p for p in plans if p["kind"] == "cheapest"), None)
    offerte: list[dict[str, Any]] = []
    try:
        from .. import store as store_module

        for offer in (store_module.list_offers(int(watch_id))[:12] if watch_id else []):
            offerte.append(
                {
                    "modo": offer.get("mode"),
                    "operatore": offer.get("operator"),
                    "partenza": _fmt(offer.get("depart_at")),
                    "arrivo": _fmt(offer.get("arrive_at")),
                    "prezzo": offer.get("price"),
                    "costo_effettivo": (offer.get("details") or {}).get("costo_effettivo"),
                    "note": (offer.get("details") or {}).get("pass"),
                }
            )
    except Exception as exc:  # noqa: BLE001
        log.debug("offerte non disponibili: %s", exc)

    prefs = watch.get("prefs") or {}
    if isinstance(prefs, str):
        try:
            prefs = json.loads(prefs)
        except (ValueError, TypeError):
            prefs = {}

    # Gli abbonamenti globali dell'utente (impostazioni): l'agente deve
    # sapere quali ha, altrimenti non puo' rispondere "cosa cambia se...".
    globali: list[str] = []
    try:
        from ..services import settings as app_settings

        for voce in passes_service.normalize_passes(app_settings.get("passes", "[]")):
            globali.append(voce.get("nome") or voce.get("tipo") or "")
    except Exception as exc:  # noqa: BLE001
        log.debug("abbonamenti globali non disponibili: %s", exc)

    return {
        "ricerca": {
            "da": watch.get("from_place"),
            "a": watch.get("to_place"),
            "punto_partenza": watch.get("from_point") or "centro citta'",
            "punto_arrivo": watch.get("to_point") or "centro citta'",
            "arrivo_entro": _fmt(watch.get("arrive_by")),
            "budget": watch.get("max_price"),
            "raggio_km": watch.get("radius_km"),
            "preferenze": prefs,
            "abbonamenti_registrati": globali,
            "abbonamenti_della_ricerca": [
                p.get("nome") or p.get("tipo")
                for p in passes_service.normalize_passes(prefs.get("passes"))
            ],
        },
        "piano": (
            {
                "titolo": piano.get("title"),
                "costo": piano.get("effective_cost") or piano.get("total_price"),
                "prezzo_biglietto": piano.get("total_price"),
                "costo_reale_con_abbonamenti": piano.get("net_total_price"),
                "coperto_da_abbonamento": bool(piano.get("covered")),
                "partenza": _fmt(piano.get("depart_at")),
                "arrivo": _fmt(piano.get("arrive_at")),
                "durata_min": piano.get("total_duration_min"),
                "minuti_a_piedi_alla_partenza": piano.get("walk_out_min"),
                "minuti_a_piedi_all_arrivo": piano.get("walk_in_min"),
                "porta_a_porta_min": piano.get("door_to_door_min"),
                "cambi": piano.get("changes"),
                "abbonamenti": piano.get("passes") or [],
                "spiegazione": piano.get("explanation"),
            }
            if piano
            else None
        ),
        "offerte": offerte,
        "sintesi_ultimo_giro": watch.get("ai_summary"),
    }


def _patch_deterministica(watch: dict[str, Any], testo: str) -> dict[str, Any]:
    """Riconosce a mano le richieste piu' comuni (usata senza intelligenza)."""
    basso = (testo or "").lower()
    patch: dict[str, Any] = {}

    # Budget: "alzo a 15", "fino a 20 euro".
    if re.search(r"budget|spendo|spendere|alzo|alza|fino a", basso):
        numeri = re.findall(r"(\d+(?:[.,]\d+)?)", testo)
        if numeri:
            try:
                import re as _re

                valore = float(_re.findall(r"\d+(?:[.,]\d+)?", testo)[0].replace(",", "."))
                if valore > 0:
                    patch["budget_raise"] = valore
            except (TypeError, ValueError):
                pass

    # Preferenze sui mezzi e sui cambi.
    if re.search(r"(preferisco|voglio|viaggio).{0,20}\b(treno|treni)\b", basso) or re.search(
        r"\bsolo (treno|treni)\b", basso
    ):
        patch["prefer_modes"] = ["treno"]
    if re.search(r"(preferisco|voglio|viaggio).{0,20}\b(bus|autobus)\b", basso) or re.search(
        r"\bsolo (bus|autobus)\b", basso
    ):
        patch["prefer_modes"] = ["bus"]
    if "senza cambi" in basso or "no cambi" in basso or "meno cambi" in basso:
        patch["no_changes"] = True
    if "arrivare prima" in basso or "piu' presto" in basso or "il prima possibile" in basso:
        patch["prefer_fast"] = True

    # Abbonamenti.
    if "abbonament" in basso or "carnet" in basso or "pass" in basso:
        if re.search(r"\b(nessun|niente|non ho)\b", basso):
            patch["clear_passes"] = True
        else:
            trovato = passes_service.trova_per_testo(testo)
            if trovato:
                patch["add_pass"] = trovato

    # Punto di ritrovo.
    if "parto da" in basso or "ritrovo" in basso or "punto di partenza" in basso:
        testo_punto = re.sub(r"^.*?(parto da|ritrovo|punto di partenza)\s+", "", testo, flags=re.I)
        if testo_punto and testo_punto.strip():
            patch["set_from_point"] = testo_punto.strip()[:80]

    return patch


def _risposta_senza_ai(watch: dict[str, Any], testo: str) -> dict[str, Any]:
    """Risposta basata solo sui dati reali (intelligenza non configurata)."""
    contesto = _contesto(watch)
    piano = contesto.get("piano")
    if piano and piano.get("titolo"):
        reply = (
            f"Adesso il piano migliore e' {piano['titolo']}: {piano.get('costo')} EUR, "
            f"partenza {piano.get('partenza')}, arrivo {piano.get('arrivo')}. "
            f"Ci sono {len(contesto.get('offerte') or [])} offerte in lista."
        )
    else:
        reply = (
            "Non c'e' ancora un piano per questa ricerca. "
            f"{contesto.get('sintesi_ultimo_giro') or ''}".strip()
        )
    return {"reply": reply, "prefs": {}, "rerun": False}


async def answer(
    watch_id: int, testo: str, *, user_id: int | None = None
) -> dict[str, Any]:
    """Risponde a una domanda/richiesta e applica le richieste di cambio."""
    watch = get_watch(watch_id, user_id=user_id)
    if not watch:
        return {"ok": False, "error": "Ricerca non trovata"}
    testo = (testo or "").strip()
    if not testo:
        return {"ok": False, "error": "Scrivi la domanda"}

    client = client_from_settings()
    if not (ai_enabled() and client.available):
        risultato = _risposta_senza_ai(watch, testo)
        patch = _patch_deterministica(watch, testo)
    else:
        contesto = _contesto(watch)
        prompt = SYSTEM.format(
            contesto=json.dumps(contesto, ensure_ascii=False, indent=1)[:6000],
            richiesta=testo,
            chiavi=", ".join(CHIAVI_PATCH),
            passes=", ".join(sorted(passes_service.PASS_TYPES)[:40]),
        )
        chat = await client.chat(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": testo},
            ],
            model=client.model_fast,
            json_mode=True,
            max_tokens=600,
            temperature=0.2,
            timeout=45,
        )
        log_call("chat", watch_id=watch_id, run_id=None, result=chat)
        if not chat.ok:
            # L'intelligenza ha fallito: si risponde con i dati reali.
            risultato = _risposta_senza_ai(watch, testo)
            patch = _patch_deterministica(watch, testo)
            risultato["reply"] += f" (L'assistente non ha risposto: {chat.error})"
        else:
            risultato, patch = _leggi_json(chat.text, watch, testo)

    applicato: dict[str, Any] = {}
    if patch:
        try:
            merge = questions_service.merge_prefs(watch, patch)
            applicato = merge.get("changes") or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("patch non applicata: %s", exc)

    return {
        "ok": True,
        "reply": risultato.get("reply") or "",
        "prefs": patch,
        "changes": applicato,
        "rerun": bool(risultato.get("rerun") or applicato),
    }


def _leggi_json(testo: str, watch: dict[str, Any], richiesta: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Legge la risposta JSON del modello (con ripiego)."""
    grezzo = (testo or "").strip()
    if grezzo.startswith("```"):
        grezzo = re.sub(r"^```[a-zA-Z]*\s*", "", grezzo)
        grezzo = re.sub(r"\s*```$", "", grezzo).strip()
    dati: dict[str, Any] = {}
    try:
        dati = json.loads(grezzo)
    except (ValueError, TypeError):
        m = re.search(r"\{.*\}", grezzo, re.S)
        if m:
            try:
                dati = json.loads(m.group(0))
            except (ValueError, TypeError):
                dati = {}
    reply = str(dati.get("reply") or "").strip()
    patch = dati.get("prefs") if isinstance(dati.get("prefs"), dict) else {}
    patch = {k: v for k, v in patch.items() if k in CHIAVI_PATCH and v is not None}
    if not reply:
        reply = _risposta_senza_ai(watch, richiesta)["reply"]
    return {"reply": reply}, patch
