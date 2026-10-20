"""API FastAPI + servizio della dashboard web."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, access, auth, db, deps, geo, notify, store
from . import gtfs as gtfs_service
from .adapters import sources_meta
from .ai import intelligence
from .ai.groq import ai_enabled, client_from_settings, log_call
from .config import APP_NAME, CORS_ORIGINS, HOST, PORT, WEB_DIR
from .schemas import (
    AccessIn,
    AnswerIn,
    AskIn,
    OfferOut,
    ParseRequest,
    PushSubscribeIn,
    PushUnsubscribeIn,
    RunOut,
    SettingsIn,
    UnlockIn,
    WatchIn,
    WatchOut,
    WatchUpdate,
)
from .services import promos as promos_service
from .services import push as push_service
from .services import questions as questions_service
from .services import scheduler
from .services import search
from .services import settings as app_settings
from .services import telegram_chats as telegram_chats_service
from .timeutil import iso, now_utc, parse_dt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("carpool")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    store.migrate_sources()
    scheduler.start()
    log.info("%s pronto su http://%s:%s", APP_NAME, HOST, PORT)
    yield
    scheduler.shutdown()


app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan)

if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Blocca le API che richiedono l'accesso, salvo quelle pubbliche.

    Con `auth_enabled = 0` (impostazione predefinita) non viene chiesto
    nessun accesso: l'app si apre direttamente e le ricerche sono del
    proprietario implicito.
    """
    path = request.url.path
    # Le richieste preflight CORS (OPTIONS) non hanno cookie: vanno lasciate
    # passare, altrimenti il browser blocca la chiamata cross-origin.
    if request.method == "OPTIONS":
        return await call_next(request)
    if path.startswith("/api/") and path not in deps.PUBLIC_PATHS:
        if deps.auth_enabled():
            user = deps.current_user(request)
            if not user:
                return JSONResponse(
                    {"detail": "Accesso richiesto", "code": "unauthorized"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Session"},
                )
            request.state.user = user
    return await call_next(request)


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
        **(await _resolve_points(payload)),
    }


async def _resolve_points(payload: WatchIn) -> dict:
    """Geocodifica i punti di ritrovo esatti (quartiere, piazza, stazione).

    Servono per calcolare il tempo a piedi: il punto di ritrovo scelto
    dall'autista puo' essere lontano da casa, e 20 minuti a piedi cambiano
    il piano.
    """
    resolved: dict[str, float] = {}
    for prefix, place, lat, lon in (
        ("from", payload.from_point, payload.from_point_lat, payload.from_point_lon),
        ("to", payload.to_point, payload.to_point_lat, payload.to_point_lon),
    ):
        if not place:
            continue
        if lat is not None and lon is not None:
            resolved[f"{prefix}_point_lat"] = lat
            resolved[f"{prefix}_point_lon"] = lon
            continue
        found = await geo.resolve_place(place)
        if found and found[0] is not None:
            resolved[f"{prefix}_point_lat"] = found[0]
            resolved[f"{prefix}_point_lon"] = found[1]
    return resolved


# --------------------------------------------------------------------------- #
# Accesso: una password (o una chiave segreta nell'URL)
# --------------------------------------------------------------------------- #
@app.get("/api/auth/status")
async def auth_status(request: Request):
    """Stato dell'accesso: serve al frontend per sapere se chiedere la password."""
    locked = deps.auth_enabled()
    user = deps.current_user(request)
    owner = auth.implicit_owner()
    return {
        "authenticated": bool(user) or not locked,
        "locked": locked,
        "unlocked": bool(user),
        "has_password": access.has_password(),
        "has_key": access.has_key(),
        "user": auth.public_user(user or owner) if (user or owner) else None,
    }


@app.post("/api/auth/unlock")
async def unlock(payload: UnlockIn, request: Request, response: Response):
    """Sblocca l'app con la password (o con la chiave segreta)."""
    token, error = access.unlock(
        payload.password, user_agent=request.headers.get("user-agent")
    )
    if error:
        raise HTTPException(status_code=401, detail=error)
    if token:
        deps.set_session_cookie(response, token, secure=deps.is_secure_request(request))
    return {"ok": True}


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    """Chiude la sessione corrente."""
    token = request.cookies.get(auth.COOKIE_NAME)
    if token:
        auth.revoke_session(token)
    deps.clear_session_cookie(response)
    return {"ok": True}


@app.get("/api/auth/me")
async def me(request: Request):
    user = deps.require_user(request)
    return {"user": auth.public_user(user)}


@app.post("/api/auth/access")
async def set_access(request: Request, payload: AccessIn):
    """Imposta o toglie la password d'accesso.

    - `password` non vuota: imposta la nuova password e chiude le sessioni;
    - `password` vuota: toglie la password, l'app si riapre liberamente.
    """
    deps.require_user(request)
    try:
        changed = access.set_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if changed:
        return {
            "ok": True,
            "locked": True,
            "message": "Password impostata. Alla prossima apertura ti verra' chiesta.",
        }
    return {
        "ok": True,
        "locked": False,
        "message": "Password rimossa: l'app si apre senza accesso.",
    }


@app.post("/api/auth/key")
async def rotate_key(request: Request):
    """Genera una chiave segreta nuova.

    La chiave si vede una volta sola (nel database c'e' solo l'hash):
    usala nell'URL per entrare con un tocco, es.
    `https://.../?key=XXXX`.
    """
    deps.require_user(request)
    key = access.new_key()
    return {
        "ok": True,
        "key": key,
        "message": "Copia la chiave: si vede solo adesso.",
    }


@app.delete("/api/auth/key")
async def delete_key(request: Request):
    """Toglie la chiave segreta."""
    deps.require_user(request)
    access.clear_key()
    return {"ok": True, "message": "Chiave segreta rimossa."}


@app.get("/api/auth/users")
async def list_users(request: Request):
    """Elenco degli account. Riservato agli amministratori."""
    deps.require_admin(request)
    return {
        "users": [auth.public_user(u) for u in auth.list_users()],
        "pending": auth.count_users(auth.STATUS_PENDING),
    }


@app.post("/api/auth/telegram/link")
async def link_telegram(request: Request):
    """Collega la chat Telegram all'utente loggato.

    Il bot riceve un messaggio con il codice; l'utente lo incolla qui e la
    chat viene associata all'account, cosi' le notifiche arrivano nel posto
    giusto e non a chiunque.
    """
    user = deps.require_user(request)
    token = auth.secrets.token_urlsafe(6).upper()
    with db.db() as conn:
        conn.execute(
            "INSERT INTO telegram_links(code, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (
                token,
                int(user["id"]),
                now_utc().isoformat(),
                (now_utc() + timedelta(minutes=15)).isoformat(),
            ),
        )
    # Se l'utente ha gia' un bot configurato, gli mandiamo il codice li'.
    sent = False
    if app_settings.get_user(int(user["id"]), "telegram_token"):
        ok, _ = await notify.send_telegram_message(
            f"Il tuo codice per collegare la chat: {token}\n\n"
            "Rispondi /link CODICE per confermare.",
            user_id=int(user["id"]),
        )
        sent = ok
    return {
        "ok": True,
        "code": token,
        "message": (
            "Codice generato. Inoltralo al bot (/link CODICE) e poi premi Â«CollegaÂ»."
            if not sent
            else "Codice generato e inviato in chat: torna qui e premi Â«CollegaÂ»."
        ),
    }


@app.post("/api/auth/telegram/confirm")
async def confirm_telegram(request: Request):
    """Conferma il collegamento della chat Telegram con il codice ricevuto."""
    user = deps.require_user(request)
    body = await request.json()
    code = str(body.get("code") or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="Codice mancante")
    with db.db() as conn:
        row = conn.execute(
            "SELECT * FROM telegram_links WHERE code = ? AND user_id = ?",
            (code, int(user["id"])),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="Codice non valido")
        if str(row["expires_at"] or "") < now_utc().isoformat():
            conn.execute("DELETE FROM telegram_links WHERE id = ?", (row["id"],))
            raise HTTPException(status_code=400, detail="Codice scaduto")
        chat_id = str(row["chat_id"] or "").strip()
        conn.execute("DELETE FROM telegram_links WHERE id = ?", (row["id"],))
    if not chat_id:
        raise HTTPException(status_code=400, detail="Il bot non ha ancora inviato il codice")
    auth.set_telegram_chat(int(user["id"]), chat_id)
    return {"ok": True, "telegram_chat_id": chat_id}


# --------------------------------------------------------------------------- #
# Chat Telegram: privati, gruppi e canali
# --------------------------------------------------------------------------- #
@app.get("/api/telegram/chats")
async def telegram_chats(request: Request):
    """Le chat Telegram registrate, e il link per aggiungere il bot."""
    user = deps.require_user(request)
    uid = int(user["id"])
    bot = await notify.telegram_bot_info()
    return {
        "chats": telegram_chats_service.list_chats(uid),
        "bot": bot,
        "add_url": f"https://t.me/{bot['username']}" if bot.get("username") else "",
    }


@app.post("/api/telegram/chats/{chat_id}/default")
async def telegram_set_default(chat_id: str, request: Request):
    """Imposta la chat predefinita (dove arrivano gli avvisi)."""
    user = deps.require_user(request)
    if not telegram_chats_service.set_default(int(user["id"]), chat_id):
        raise HTTPException(status_code=404, detail="Chat non trovata")
    return {"ok": True, "chats": telegram_chats_service.list_chats(int(user["id"]))}


@app.delete("/api/telegram/chats/{chat_id}")
async def telegram_delete_chat(chat_id: str, request: Request):
    """Toglie una chat dalla lista."""
    user = deps.require_user(request)
    removed = telegram_chats_service.remove_chat(int(user["id"]), chat_id)
    return {"ok": removed, "chats": telegram_chats_service.list_chats(int(user["id"]))}


# --------------------------------------------------------------------------- #
# Stato e fonti
# --------------------------------------------------------------------------- #
@app.get("/api/status")
async def status(request: Request):
    user = deps.require_user(request)
    uid = int(user["id"])
    watches = store.list_watches(uid)
    return {
        "app": APP_NAME,
        "version": __version__,
        "time": now_utc().isoformat(),
        "watches": len(watches),
        "active_watches": sum(1 for w in watches if w["active"]),
        "unread_alerts": len(store.list_alerts(unread_only=True, limit=200, user_id=uid)),
        "pending_questions": questions_service.count_pending(user_id=uid),
        "scheduler": scheduler.info(),
        "sources": sources_meta(),
        "ai": _ai_status(),
        "user": auth.public_user(user),
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
async def list_watches(request: Request):
    user = deps.require_user(request)
    return store.list_watches(int(user["id"]))


@app.post("/api/watches", response_model=WatchOut, status_code=201)
async def create_watch(payload: WatchIn, request: Request):
    user = deps.require_user(request)
    resolved = await _resolve_places(payload)
    watch = store.create_watch(payload, resolved, user_id=int(user["id"]))
    _queue_run(watch["id"], "created", delay=0.4)
    return store.get_watch(watch["id"], user_id=int(user["id"]))


@app.get("/api/watches/{watch_id}", response_model=WatchOut)
async def get_watch(watch_id: int, request: Request):
    user = deps.require_user(request)
    watch = store.get_watch(watch_id, user_id=int(user["id"]))
    if not watch:
        raise HTTPException(status_code=404, detail="Ricerca non trovata")
    return watch


@app.patch("/api/watches/{watch_id}", response_model=WatchOut)
async def update_watch(watch_id: int, payload: WatchUpdate, request: Request):
    user = deps.require_user(request)
    uid = int(user["id"])
    watch = store.get_watch(watch_id, user_id=uid)
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
    # Punti di ritrovo: se il nome cambia, le coordinate vanno ricalcolate.
    for prefix in ("from", "to"):
        key = f"{prefix}_point"
        if key in changes and changes[key]:
            lat, lon, _ = await geo.resolve_place(str(changes[key]))
            if lat is not None:
                changes[f"{key}_lat"] = lat
                changes[f"{key}_lon"] = lon
        elif key in changes and not changes[key]:
            changes[f"{key}_lat"] = None
            changes[f"{key}_lon"] = None

    # Confronto sempre in UTC: la patch puo' arrivare con orari senza fuso
    # (es. "2026-10-22T07:21") e il confronto misto andava in errore 500.
    arrive_by = (
        parse_dt(changes["arrive_by"]) if "arrive_by" in changes else parse_dt(watch["arrive_by"])
    )
    depart_after = (
        parse_dt(changes["depart_after"])
        if "depart_after" in changes
        else parse_dt(watch.get("depart_after"))
    )
    if arrive_by and depart_after and depart_after >= arrive_by:
        raise HTTPException(status_code=400, detail="La partenza minima deve precedere l'arrivo massimo")

    updated = store.update_watch(watch_id, WatchUpdate(**changes), user_id=uid)
    if any(k in changes for k in ("sources", "arrive_by", "depart_after", "from_place", "to_place", "max_price")):
        _queue_run(watch_id, "updated")
    return updated


@app.delete("/api/watches/{watch_id}", status_code=204)
async def delete_watch(watch_id: int, request: Request):
    user = deps.require_user(request)
    if not store.delete_watch(watch_id, user_id=int(user["id"])):
        raise HTTPException(status_code=404, detail="Ricerca non trovata")
    return Response(status_code=204)


@app.post("/api/watches/{watch_id}/run")
async def run_now(watch_id: int, request: Request):
    user = deps.require_user(request)
    if not store.get_watch(watch_id, user_id=int(user["id"])):
        raise HTTPException(status_code=404, detail="Ricerca non trovata")
    _queue_run(watch_id, "manual", delay=0.05)
    return {"status": "queued"}


@app.post("/api/watches/{watch_id}/toggle", response_model=WatchOut)
async def toggle_watch(watch_id: int, request: Request):
    user = deps.require_user(request)
    uid = int(user["id"])
    watch = store.get_watch(watch_id, user_id=uid)
    if not watch:
        raise HTTPException(status_code=404, detail="Ricerca non trovata")
    updated = store.set_watch_active(watch_id, not watch["active"], user_id=uid)
    if updated and updated["active"]:
        _queue_run(watch_id, "resumed")
    return updated


@app.get("/api/watches/{watch_id}/offers", response_model=list[OfferOut])
async def watch_offers(watch_id: int, request: Request, include_inactive: bool = False):
    user = deps.require_user(request)
    if not store.get_watch(watch_id, user_id=int(user["id"])):
        raise HTTPException(status_code=404, detail="Ricerca non trovata")
    return store.list_offers(watch_id, include_inactive=include_inactive)


@app.get("/api/watches/{watch_id}/runs", response_model=list[RunOut])
async def watch_runs(watch_id: int, request: Request):
    user = deps.require_user(request)
    if not store.get_watch(watch_id, user_id=int(user["id"])):
        raise HTTPException(status_code=404, detail="Ricerca non trovata")
    return store.list_runs(watch_id)


@app.get("/api/watches/{watch_id}/links")
async def watch_links(watch_id: int, request: Request):
    user = deps.require_user(request)
    watch = store.get_watch(watch_id, user_id=int(user["id"]))
    if not watch:
        raise HTTPException(status_code=404, detail="Ricerca non trovata")
    return {"links": store.links_for_watch(watch)}


# --------------------------------------------------------------------------- #
# Avvisi
# --------------------------------------------------------------------------- #
@app.get("/api/alerts")
async def alerts(request: Request, unread: bool = False, limit: int = Query(50, ge=1, le=200)):
    user = deps.require_user(request)
    return store.list_alerts(unread_only=unread, limit=limit, user_id=int(user["id"]))


@app.post("/api/alerts/read")
async def read_alerts(request: Request, payload: dict | None = None):
    user = deps.require_user(request)
    ids = None
    if payload and isinstance(payload.get("ids"), list):
        ids = [int(i) for i in payload["ids"]]
    return {"updated": store.mark_alerts_read(ids, user_id=int(user["id"]))}


# --------------------------------------------------------------------------- #
# Offerte e codici sconto (pagine ufficiali)
# --------------------------------------------------------------------------- #
@app.get("/api/promos")
async def promos(refresh: bool = False):
    """Offerte del momento e codici sconto dalle pagine ufficiali dei vettori."""
    return await promos_service.get_promos(force=refresh)


@app.get("/api/carsharing/zones")
async def carsharing_zones():
    """CittÃ  coperte dal carsharing, con coordinate, per la mappa."""
    from .adapters.carsharing import load_providers

    zones = []
    for provider in load_providers():
        for city in provider.get("cities", []):
            key = str(city).strip().lower()
            coords = geo.CITIES.get(key)
            if not coords:
                continue
            zones.append(
                {
                    "provider": provider.get("name") or provider.get("id"),
                    "city": key.title(),
                    "lat": coords[0],
                    "lon": coords[1],
                    "url": provider.get("url"),
                    "note": provider.get("note", ""),
                    "intercity": bool(provider.get("intercity_one_way")),
                }
            )
    return {"zones": zones}


# --------------------------------------------------------------------------- #
# Orari GTFS (treni e bus): stato e aggiornamento dei feed aperti
# --------------------------------------------------------------------------- #
_gtfs_state: dict = {"running": False, "log": [], "results": []}


def _gtfs_run(feed_ids):
    _gtfs_state["running"] = True
    try:
        def log_line(message: str) -> None:
            _gtfs_state["log"].append(message)

        _gtfs_state["results"] = gtfs_service.update(feed_ids, force=True, progress=log_line)
    except Exception as exc:  # noqa: BLE001
        _gtfs_state["log"].append(f"Errore: {type(exc).__name__}: {exc}")
        _gtfs_state["results"] = [{"feed": feed_ids or "tutti", "status": "errore", "error": str(exc)}]
    finally:
        _gtfs_state["running"] = False


@app.get("/api/gtfs")
async def gtfs_status_endpoint():
    """Stato dei feed GTFS: quali sono importati e quanti orari contengono."""
    return {
        "enabled": app_settings.get("gtfs_enabled", "1") == "1",
        "running": bool(_gtfs_state["running"]),
        "log": list(_gtfs_state["log"])[-6:],
        "results": list(_gtfs_state["results"]),
        "feeds": gtfs_service.status(),
    }


@app.post("/api/gtfs/update")
async def gtfs_update_endpoint(feed: list[str] | None = Query(None)):
    """Scarica e importa gli orari (in background). Senza `feed`, aggiorna tutti."""
    if _gtfs_state["running"]:
        return {"status": "running"}
    _gtfs_state["log"] = []
    _gtfs_state["results"] = []
    asyncio.create_task(asyncio.to_thread(_gtfs_run, feed))
    return {"status": "started"}


# --------------------------------------------------------------------------- #
# Impostazioni
# --------------------------------------------------------------------------- #
@app.get("/api/passes")
async def list_passes(request: Request):
    """Il catalogo degli abbonamenti conosciuti (per il selettore)."""
    deps.require_user(request)
    from .services import passes as passes_service

    catalogo = []
    for chiave, dati in passes_service.PASS_TYPES.items():
        catalogo.append(
            {
                "id": chiave,
                "nome": dati.get("nome", chiave),
                "tipo": dati.get("tipo", "altro"),
                "nota": dati.get("nota", ""),
                "fonte": dati.get("fonte", ""),
            }
        )
    catalogo.sort(key=lambda item: (item["tipo"], item["nome"]))
    return {"passes": catalogo}


@app.get("/api/settings")
async def get_settings(request: Request):
    """Impostazioni: quelle comuni piu' quelle personali dell'utente."""
    user = deps.require_user(request)
    values = app_settings.get_all(mask=True)
    values.update(app_settings.get_user_all(int(user["id"]), mask=True))
    return values


@app.put("/api/settings")
async def put_settings(payload: SettingsIn, request: Request):
    """Salva le impostazioni. Quelle globali richiedono il ruolo admin."""
    user = deps.require_user(request)
    values = payload.model_dump(exclude_unset=True)
    personal = {k: v for k, v in values.items() if k in app_settings.PER_USER_KEYS}
    shared = {k: v for k, v in values.items() if k in app_settings.GLOBAL_KEYS}

    if shared:
        if not user.get("is_admin"):
            raise HTTPException(
                status_code=403, detail="Le impostazioni comuni si modificano solo da un amministratore"
            )
        app_settings.update(shared)
    if personal:
        app_settings.update_user(int(user["id"]), personal)

    merged = app_settings.get_all(mask=True)
    merged.update(app_settings.get_user_all(int(user["id"]), mask=True))
    return merged


@app.post("/api/settings/test-telegram")
async def test_telegram(request: Request):
    user = deps.require_user(request)
    ok, error = await notify.send_telegram(
        f"âœ… {APP_NAME}: notifiche Telegram configurate correttamente.",
        user_id=int(user["id"]),
    )
    return {"ok": ok, "error": error}


@app.post("/api/settings/test-email")
async def test_email(request: Request):
    user = deps.require_user(request)
    ok, error = await notify.send_email(
        f"{APP_NAME} - test", "âœ… Le notifiche email sono configurate correttamente.",
        user_id=int(user["id"]),
    )
    return {"ok": ok, "error": error}


# --------------------------------------------------------------------------- #
# Notifiche push per la PWA (Web Push + VAPID)
# --------------------------------------------------------------------------- #
@app.get("/api/push/status")
async def push_status(request: Request):
    user = deps.require_user(request)
    return {
        "available": push_service.available(),
        "public_key": push_service.public_key(),
        "subscriptions": push_service.count(user_id=int(user["id"])),
    }


@app.post("/api/push/subscribe")
async def push_subscribe(payload: PushSubscribeIn, request: Request):
    user = deps.require_user(request)
    if not push_service.available():
        raise HTTPException(503, "Notifiche push non disponibili: manca la libreria pywebpush")
    try:
        total = push_service.subscribe(
            {"endpoint": payload.endpoint, "keys": payload.keys.model_dump(), "label": payload.label},
            user_agent=request.headers.get("user-agent"),
            user_id=int(user["id"]),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "subscriptions": total}


@app.post("/api/push/unsubscribe")
async def push_unsubscribe(payload: PushUnsubscribeIn, request: Request):
    user = deps.require_user(request)
    return {
        "ok": True,
        "subscriptions": push_service.unsubscribe(payload.endpoint, user_id=int(user["id"])),
    }


@app.post("/api/push/test")
async def push_test(request: Request):
    user = deps.require_user(request)
    outcomes = await push_service.send(
        f"{APP_NAME} - prova",
        "âœ… Se vedi questa notifica, le push funzionano. Buon viaggio!",
        "/",
        user_id=int(user["id"]),
    )
    ok = any(item["ok"] for item in outcomes)
    error = "" if ok else "; ".join(item["error"] for item in outcomes if item.get("error"))
    return {"ok": ok, "error": error, "devices": push_service.count(user_id=int(user["id"]))}


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
async def ai_status(request: Request):
    user = deps.require_user(request)
    info = _ai_status()
    with db.db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(tokens),0) AS tokens FROM ai_log"
        ).fetchone()
    info["calls"] = int(row["calls"]) if row else 0
    info["tokens"] = int(row["tokens"]) if row else 0
    info["pending_questions"] = questions_service.count_pending(user_id=int(user["id"]))
    return info


@app.get("/api/ai/models")
async def ai_models():
    client = client_from_settings()
    if not client.available:
        raise HTTPException(status_code=400, detail="Chiave Groq non configurata")
    detailed = await client.list_models_detailed()
    return {
        "models": [model["id"] for model in detailed],
        "details": detailed,
        "model_smart": client.model_smart,
        "model_fast": client.model_fast,
    }


@app.post("/api/ai/parse")
async def ai_parse(payload: ParseRequest):
    result = await intelligence.parse_trip_text(payload.text)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Interpretazione non riuscita")
    return result


@app.get("/api/watches/{watch_id}/chat")
async def chat_history(watch_id: int, request: Request):
    """La conversazione salvata con l'agente per questa ricerca."""
    user = deps.require_user(request)
    uid = int(user["id"])
    if not store.get_watch(watch_id, user_id=uid):
        raise HTTPException(status_code=404, detail="Ricerca non trovata")
    return {"messages": store.list_chat_messages(watch_id)}


@app.delete("/api/watches/{watch_id}/chat")
async def chat_clear(watch_id: int, request: Request):
    """Azzera la conversazione (i messaggi della ricerca, non la ricerca)."""
    user = deps.require_user(request)
    uid = int(user["id"])
    if not store.get_watch(watch_id, user_id=uid):
        raise HTTPException(status_code=404, detail="Ricerca non trovata")
    return {"ok": True, "removed": store.clear_chat_messages(watch_id)}


@app.post("/api/watches/{watch_id}/ask")
async def ask_agent(watch_id: int, payload: AskIn, request: Request):
    """Domanda o richiesta libera all'agente, con i dati reali della ricerca.

    Se la richiesta cambia qualcosa (budget, orario, mezzo, abbonamento),
    il cambiamento viene applicato e la ricerca riparte.
    """
    user = deps.require_user(request)
    uid = int(user["id"])
    if not store.get_watch(watch_id, user_id=uid):
        raise HTTPException(status_code=404, detail="Ricerca non trovata")
    from .ai import chat as chat_service

    result = await chat_service.answer(watch_id, payload.text, user_id=uid)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Richiesta non riuscita")
    if result.get("rerun"):
        _queue_run(watch_id, "answer", delay=0.4)
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
        # I modelli "reasoning" (gpt-oss, qwen) consumano token per il
        # ragionamento prima della risposta: con pochi token il testo
        # arriva vuoto.
        max_tokens=400,
        temperature=0,
        timeout=40,
    )
    log_call("test", watch_id=None, run_id=None, result=chat)
    return {"ok": chat.ok, "model": chat.model, "reply": chat.text.strip()[:120], "error": chat.error}


@app.get("/api/watches/{watch_id}/plan")
async def watch_plan(watch_id: int, request: Request):
    user = deps.require_user(request)
    uid = int(user["id"])
    watch = store.get_watch(watch_id, user_id=uid)
    if not watch:
        raise HTTPException(status_code=404, detail="Ricerca non trovata")
    plans = store.current_plans(watch_id)
    current = next((p for p in plans if p["kind"] == "cheapest"), None)
    return {
        "plan": current,
        "alternatives": [p for p in plans if p["kind"] != "cheapest"],
        "summary": watch.get("ai_summary"),
        "best_price": watch.get("ai_best_price"),
        "questions": questions_service.list_for_watch(watch_id, status="pending", user_id=uid),
        "ai": _ai_status(),
    }


@app.get("/api/watches/{watch_id}/questions")
async def watch_questions(watch_id: int, request: Request, status: str | None = None):
    user = deps.require_user(request)
    uid = int(user["id"])
    if not store.get_watch(watch_id, user_id=uid):
        raise HTTPException(status_code=404, detail="Ricerca non trovata")
    return questions_service.list_for_watch(watch_id, status=status, user_id=uid)


@app.get("/api/questions")
async def all_questions(request: Request, status: str = "pending"):
    user = deps.require_user(request)
    uid = int(user["id"])
    if status == "pending":
        return questions_service.pending(limit=50, user_id=uid)
    return questions_service.list_all(status=status, user_id=uid)


@app.post("/api/questions/{question_id}/answer")
async def answer_question(question_id: int, payload: AnswerIn, request: Request):
    user = deps.require_user(request)
    result = await questions_service.answer(
        question_id, payload.answer.strip(), source=payload.source, user_id=int(user["id"])
    )
    if not result:
        raise HTTPException(status_code=404, detail="Domanda non trovata")
    watch_id = int(result["question"]["watch_id"])
    _queue_run(watch_id, "answer", delay=0.4)
    return result


@app.post("/api/questions/{question_id}/skip")
async def skip_question(question_id: int, request: Request):
    user = deps.require_user(request)
    result = questions_service.skip(question_id, user_id=int(user["id"]))
    if not result:
        raise HTTPException(status_code=404, detail="Domanda non trovata")
    return result



# --------------------------------------------------------------------------- #
# Frontend statico (dashboard + PWA)
# --------------------------------------------------------------------------- #
def _build_stamp() -> str:
    """Versione degli asset: l'hash del commit, se noto, altrimenti la data.

    Serve per il cache-busting: GitHub Pages tiene in cache i file per 10
    minuti, quindi un URL senza versione puo' servire una versione vecchia.
    """
    from .timeutil import now_utc

    return os.getenv("CARPOOL_BUILD_STAMP") or now_utc().strftime("%Y%m%d%H%M%S")


def _web_file(name: str, media_type: str | None = None, cache: str = "no-store"):
    path = WEB_DIR / name
    if not path.exists():
        return JSONResponse({"detail": f"{name} non trovato"}, status_code=404)
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": cache})


@app.get("/")
async def index():
    """La dashboard, con il placeholder __BUILD__ sostituito dalla versione."""
    path = WEB_DIR / "index.html"
    if not path.exists():
        return JSONResponse({"detail": "index.html non trovato"}, status_code=404)
    html = path.read_text(encoding="utf-8").replace("__BUILD__", _build_stamp())
    return Response(
        html,
        media_type="text/html",
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


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


class _WebStatic(StaticFiles):
    """File statici senza cache aggressiva.

    L'app cambia spesso: meglio che il browser (e il service worker) prendano
    sempre la versione corrente di HTML, JS e CSS. Le icone, invece, possono
    restare in cache per un po'.
    """

    async def get_response(self, path: str, scope):  # type: ignore[override]
        response = await super().get_response(path, scope)
        if path.endswith((".html", ".js", ".css", ".webmanifest")) or path in ("", "/"):
            response.headers["Cache-Control"] = "no-cache"
        return response


# I file statici stanno nella radice del frontend (layout GitHub Pages).
# Il mount va registrato IN FONDO, cosi' le route /api e /config.json
# dichiarate sopra hanno la precedenza.
if WEB_DIR.exists():
    app.mount("/", _WebStatic(directory=WEB_DIR, html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
