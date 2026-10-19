"""API FastAPI + servizio della dashboard web."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, db, geo, notify, store
from .adapters import sources_meta
from .ai import intelligence
from .ai.groq import ai_enabled, client_from_settings, log_call
from .config import APP_NAME, CORS_ORIGINS, HOST, PORT, WEB_DIR
from .schemas import (
    AnswerIn,
    OfferOut,
    ParseRequest,
    RunOut,
    SettingsIn,
    WatchIn,
    WatchOut,
    WatchUpdate,
)
from .services import questions as questions_service
from .services import scheduler
from .services import search
from .services import settings as app_settings
from .timeutil import iso, now_utc, parse_dt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("carpool")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    scheduler.start()
    log.info("%s pronto su http://%s:%s", APP_NAME, HOST, PORT)
    yield
    scheduler.shutdown()


app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan)

if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# --------------------------------------------------------------------------- #
# Utility
# --------------------------------------------------------------------------- #
def _queue_run(watch_id: int, trigger: str = "manual", delay: float = 0.2) -> None:
    with db.db() as conn:
        conn.execute(
            "UPDATE watches SET next_run_at=? WHERE id=?",
            ((now_utc() + timedelta(minutes=1)).isoformat(), watch_id),
        )

    async def runner():
        await asyncio.sleep(delay)
        await search.execute_run(watch_id, trigger)

    scheduler.spawn(runner())


async def _resolve_places(payload: WatchIn) -> dict:
    from_lat, from_lon, from_name = await geo.resolve_place(
        payload.from_place, payload.from_lat, payload.from_lon
    )
    to_lat, to_lon, to_name = await geo.resolve_place(payload.to_place, payload.to_lat, payload.to_lon)

    if from_lat is None or from_lon is None:
        raise HTTPException(status_code=400, detail=f"Localita' di partenza non trovata: {payload.from_place}")
    if to_lat is None or to_lon is None:
        raise HTTPException(status_code=400, detail=f"Localita' di arrivo non trovata: {payload.to_place}")

    return {
        "from_lat": from_lat,
        "from_lon": from_lon,
        "to_lat": to_lat,
        "to_lon": to_lon,
    }


# --------------------------------------------------------------------------- #
# Stato e fonti
# --------------------------------------------------------------------------- #
@app.get("/api/status")
async def status():
    watches = store.list_watches()
    return {
        "app": APP_NAME,
        "version": __version__,
        "time": now_utc().isoformat(),
        "watches": len(watches),
        "active_watches": sum(1 for w in watches if w["active"]),
        "unread_alerts": len(store.list_alerts(unread_only=True, limit=200)),
        "pending_questions": questions_service.count_pending(),
        "scheduler": scheduler.info(),
        "sources": sources_meta(),
        "ai": _ai_status(),
    }


@app.get("/api/sources")
async def sources():
    return {"sources": sources_meta()}


@app.get("/api/geocode")
async def geocode_endpoint(q: str = Query(..., min_length=2)):
    found = await geo.geocode(q)
    if not found:
        return {"found": False, "query": q}
    return {"found": True, "query": q, "lat": found[0], "lon": found[1], "name": found[2]}


# --------------------------------------------------------------------------- #
# Ricerche
# --------------------------------------------------------------------------- #
@app.get("/api/watches", response_model=list[WatchOut])
async def list_watches():
    return store.list_watches()


@app.post("/api/watches", response_model=WatchOut, status_code=201)
async def create_watch(payload: WatchIn):
    resolved = await _resolve_places(payload)
    watch = store.create_watch(payload, resolved)
    _queue_run(watch["id"], "created", delay=0.4)
    return store.get_watch(watch["id"])


@app.get("/api/watches/{watch_id}", response_model=WatchOut)
async def get_watch(watch_id: int):
    watch = store.get_watch(watch_id)
    if not watch:
        raise HTTPException(status_code=404, detail="Ricerca non trovata")
    return watch


@app.patch("/api/watches/{watch_id}", response_model=WatchOut)
async def update_watch(watch_id: int, payload: WatchUpdate):
    watch = store.get_watch(watch_id)
    if not watch:
        raise HTTPException(status_code=404, detail="Ricerca non trovata")

    changes = payload.model_dump(exclude_unset=True)
    from_place = changes.get("from_place", watch["from_place"])
    to_place = changes.get("to_place", watch["to_place"])
    if "from_place" in changes and "from_lat" not in changes:
        lat, lon, _ = await geo.resolve_place(from_place)
        if lat is None:
            raise HTTPException(status_code=400, detail=f"Localita' non trovata: {from_place}")
        changes.update(from_lat=lat, from_lon=lon)
    if "to_place" in changes and "to_lat" not in changes:
        lat, lon, _ = await geo.resolve_place(to_place)
        if lat is None:
            raise HTTPException(status_code=400, detail=f"Localita' non trovata: {to_place}")
        changes.update(to_lat=lat, to_lon=lon)

    arrive_by = changes.get("arrive_by", parse_dt(watch["arrive_by"]))
    depart_after = changes.get("depart_after", parse_dt(watch.get("depart_after")))
    if arrive_by and depart_after and depart_after >= arrive_by:
        raise HTTPException(status_code=400, detail="La partenza minima deve precedere l'arrivo massimo")

    updated = store.update_watch(watch_id, WatchUpdate(**changes))
    if any(k in changes for k in ("sources", "arrive_by", "depart_after", "from_place", "to_place", "max_price")):
        _queue_run(watch_id, "updated")
    return updated


@app.delete("/api/watches/{watch_id}", status_code=204)
async def delete_watch(watch_id: int):
    if not store.delete_watch(watch_id):
        raise HTTPException(status_code=404, detail="Ricerca non trovata")
    return Response(status_code=204)


@app.post("/api/watches/{watch_id}/run")
async def run_now(watch_id: int):
    if not store.get_watch(watch_id):
        raise HTTPException(status_code=404, detail="Ricerca non trovata")
    _queue_run(watch_id, "manual", delay=0.05)
    return {"status": "queued"}


@app.post("/api/watches/{watch_id}/toggle", response_model=WatchOut)
async def toggle_watch(watch_id: int):
    watch = store.get_watch(watch_id)
    if not watch:
        raise HTTPException(status_code=404, detail="Ricerca non trovata")
    updated = store.set_watch_active(watch_id, not watch["active"])
    if updated and updated["active"]:
        _queue_run(watch_id, "resumed")
    return updated


@app.get("/api/watches/{watch_id}/offers", response_model=list[OfferOut])
async def watch_offers(watch_id: int, include_inactive: bool = False):
    if not store.get_watch(watch_id):
        raise HTTPException(status_code=404, detail="Ricerca non trovata")
    return store.list_offers(watch_id, include_inactive=include_inactive)


@app.get("/api/watches/{watch_id}/runs", response_model=list[RunOut])
async def watch_runs(watch_id: int):
    return store.list_runs(watch_id)


@app.get("/api/watches/{watch_id}/links")
async def watch_links(watch_id: int):
    watch = store.get_watch(watch_id)
    if not watch:
        raise HTTPException(status_code=404, detail="Ricerca non trovata")
    return {"links": store.links_for_watch(watch)}


# --------------------------------------------------------------------------- #
# Avvisi
# --------------------------------------------------------------------------- #
@app.get("/api/alerts")
async def alerts(unread: bool = False, limit: int = Query(50, ge=1, le=200)):
    return store.list_alerts(unread_only=unread, limit=limit)


@app.post("/api/alerts/read")
async def read_alerts(payload: dict | None = None):
    ids = None
    if payload and isinstance(payload.get("ids"), list):
        ids = [int(i) for i in payload["ids"]]
    return {"updated": store.mark_alerts_read(ids)}


# --------------------------------------------------------------------------- #
# Impostazioni
# --------------------------------------------------------------------------- #
@app.get("/api/settings")
async def get_settings():
    return app_settings.get_all(mask=True)


@app.put("/api/settings")
async def put_settings(payload: SettingsIn):
    values = payload.model_dump(exclude_unset=True)
    return app_settings.update(values)


@app.post("/api/settings/test-telegram")
async def test_telegram():
    ok, error = await notify.send_telegram(
        f"✅ {APP_NAME}: notifiche Telegram configurate correttamente."
    )
    return {"ok": ok, "error": error}


@app.post("/api/settings/test-email")
async def test_email():
    ok, error = await notify.send_email(
        f"{APP_NAME} - test", "✅ Le notifiche email sono configurate correttamente."
    )
    return {"ok": ok, "error": error}


# --------------------------------------------------------------------------- #
# Intelligenza (Groq) e domande
# --------------------------------------------------------------------------- #
def _ai_status() -> dict:
    client = client_from_settings()
    return {
        "configured": client.available,
        "enabled": ai_enabled(),
        "model_smart": client.model_smart,
        "model_fast": client.model_fast,
        "active": bool(client.available and ai_enabled()),
    }


@app.get("/api/ai/status")
async def ai_status():
    info = _ai_status()
    with db.db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(tokens),0) AS tokens FROM ai_log"
        ).fetchone()
    info["calls"] = int(row["calls"]) if row else 0
    info["tokens"] = int(row["tokens"]) if row else 0
    info["pending_questions"] = questions_service.count_pending()
    return info


@app.get("/api/ai/models")
async def ai_models():
    client = client_from_settings()
    if not client.available:
        raise HTTPException(status_code=400, detail="Chiave Groq non configurata")
    return {
        "models": await client.list_models(),
        "model_smart": client.model_smart,
        "model_fast": client.model_fast,
    }


@app.post("/api/ai/parse")
async def ai_parse(payload: ParseRequest):
    result = await intelligence.parse_trip_text(payload.text)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Interpretazione non riuscita")
    return result


@app.post("/api/ai/test")
async def ai_test():
    client = client_from_settings()
    if not client.available:
        raise HTTPException(status_code=400, detail="Chiave Groq non configurata")
    chat = await client.chat(
        [
            {"role": "system", "content": "Rispondi in una riga, in italiano."},
            {"role": "user", "content": "Di' soltanto: pronto."},
        ],
        model=client.model_fast,
        max_tokens=20,
        temperature=0,
        timeout=20,
    )
    log_call("test", watch_id=None, run_id=None, result=chat)
    return {"ok": chat.ok, "model": chat.model, "reply": chat.text.strip()[:120], "error": chat.error}


@app.get("/api/watches/{watch_id}/plan")
async def watch_plan(watch_id: int):
    watch = store.get_watch(watch_id)
    if not watch:
        raise HTTPException(status_code=404, detail="Ricerca non trovata")
    plans = store.current_plans(watch_id)
    current = next((p for p in plans if p["kind"] == "cheapest"), None)
    return {
        "plan": current,
        "alternatives": [p for p in plans if p["kind"] != "cheapest"],
        "summary": watch.get("ai_summary"),
        "best_price": watch.get("ai_best_price"),
        "questions": questions_service.list_for_watch(watch_id, status="pending"),
        "ai": _ai_status(),
    }


@app.get("/api/watches/{watch_id}/questions")
async def watch_questions(watch_id: int, status: str | None = None):
    if not store.get_watch(watch_id):
        raise HTTPException(status_code=404, detail="Ricerca non trovata")
    return questions_service.list_for_watch(watch_id, status=status)


@app.get("/api/questions")
async def all_questions(status: str = "pending"):
    if status == "pending":
        return questions_service.pending(limit=50)
    return questions_service.list_all(status=status)


@app.post("/api/questions/{question_id}/answer")
async def answer_question(question_id: int, payload: AnswerIn):
    result = await questions_service.answer(question_id, payload.answer.strip(), source=payload.source)
    if not result:
        raise HTTPException(status_code=404, detail="Domanda non trovata")
    watch_id = int(result["question"]["watch_id"])
    _queue_run(watch_id, "answer", delay=0.4)
    return result


@app.post("/api/questions/{question_id}/skip")
async def skip_question(question_id: int):
    result = questions_service.skip(question_id)
    if not result:
        raise HTTPException(status_code=404, detail="Domanda non trovata")
    return result



# --------------------------------------------------------------------------- #
# Frontend statico (dashboard + PWA)
# --------------------------------------------------------------------------- #
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


def _web_file(name: str, media_type: str | None = None, cache: str = "no-store"):
    path = WEB_DIR / name
    if not path.exists():
        return JSONResponse({"detail": f"{name} non trovato"}, status_code=404)
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": cache})


@app.get("/")
async def index():
    return _web_file("index.html")


@app.get("/config.json")
async def runtime_config():
    """Config runtime: il frontend puo' puntare a un'API remota (tunnel/Pages)."""
    return JSONResponse(
        {"apiBase": os.getenv("CARPOOL_API_BASE", ""), "app": APP_NAME},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/manifest.webmanifest")
async def web_manifest():
    return _web_file("manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker():
    # Deve stare sulla radice per controllare tutta la pagina.
    return _web_file("sw.js", media_type="application/javascript")


@app.get("/favicon.ico")
async def favicon():
    path = WEB_DIR / "favicon.svg"
    if path.exists():
        return FileResponse(path)
    return Response(status_code=204)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
