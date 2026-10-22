"""Persistenza di ricerche, offerte, avvisi e cronologia."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

from . import db
from .adapters.carsharing import load_providers
from .schemas import DEFAULT_SOURCES, WatchIn, WatchUpdate
from .timeutil import from_iso, iso, now_utc

WATCH_FIELDS = [
    "name",
    "from_place",
    "from_lat",
    "from_lon",
    "from_point",
    "from_point_lat",
    "from_point_lon",
    "to_place",
    "to_lat",
    "to_lon",
    "to_point",
    "to_point_lat",
    "to_point_lon",
    "depart_after",
    "arrive_by",
    "max_price",
    "passengers",
    "radius_km",
    "sources",
    "notify",
    "interval_min",
    "active",
    "prefs",
    "group_id",
]

# Vecchie fonte di default (prima dell'arrivo delle fonti reali aggiuntive).
# Le ricerche che usavano solo queste vengono aggiornate ai default attuali.
LEGACY_SOURCES = {"demo", "blablacar", "flixbus", "carsharing", "transit"}

SELECT_WATCH = """
SELECT w.*,
       (SELECT COUNT(*) FROM offers o WHERE o.watch_id = w.id AND o.active = 1) AS offers_count,
       (SELECT MIN(o.price) FROM offers o
         WHERE o.watch_id = w.id AND o.active = 1 AND o.price IS NOT NULL AND o.price_is_estimate = 0) AS best_price
FROM watches w
"""


def _owner_clause(user_id: int | None, column: str = "w.user_id") -> tuple[str, list[Any]]:
    """Restringe una query al proprietario. Senza utente (uso interno) non filtra."""
    if user_id is None:
        return "", []
    return f" AND {column} = ?", [user_id]


def _owner_clause_plain(user_id: int | None, column: str = "user_id") -> tuple[str, list[Any]]:
    """Come `_owner_clause`, ma senza alias: per UPDATE e DELETE."""
    if user_id is None:
        return "", []
    return f" AND {column} = ?", [user_id]


def _serialize_watch(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    for key in ("depart_after", "arrive_by"):
        if out.get(key):
            out[key] = iso(out[key])
    if out.get("sources") is not None:
        out["sources"] = db.dump_json(out["sources"])
    if out.get("notify") is not None:
        out["notify"] = db.dump_json(out["notify"])
    if out.get("prefs") is None:
        out["prefs"] = db.dump_json({})
    else:
        out["prefs"] = db.dump_json(out["prefs"])
    if out.get("active") is not None:
        out["active"] = 1 if out["active"] else 0
    return out


def watch_to_dict(row) -> dict[str, Any]:
    from .adapters import ADAPTERS

    data = db.row_to_dict(row) or {}
    raw_sources = db.load_json(data.get("sources"), list(DEFAULT_SOURCES))
    legacy_part = set(raw_sources) - {"gtfs"}
    if raw_sources and legacy_part and legacy_part <= LEGACY_SOURCES:
        # Ricerca creata con le vecchie fonti (anche i dati simulati "demo"):
        # la portiamo alle fonti reali complete, cosi' copre anche le nuove.
        data["sources"] = list(DEFAULT_SOURCES)
    else:
        # Fonti rimosse o sconosciute vengono scartate; se non ne resta nessuna
        # torniamo ai default.
        data["sources"] = [s for s in raw_sources if s in ADAPTERS] or list(DEFAULT_SOURCES)
    data["notify"] = db.load_json(data.get("notify"), ["web"])
    data["prefs"] = db.load_json(data.get("prefs"), {})
    data["active"] = bool(data.get("active"))
    data["offers_count"] = int(data.get("offers_count") or 0)
    data["best_price"] = data.get("best_price")
    return data


def migrate_sources() -> int:
    """Aggiunge una sola volta le nuove fonti (GTFS) alle ricerche gia' esistenti."""
    from .services import settings as settings_service

    if settings_service.get("sources_migrated_gtfs") == "1":
        return 0
    changed = 0
    with db.db() as conn:
        for row in conn.execute("SELECT id, sources FROM watches"):
            original = db.load_json(row["sources"], list(DEFAULT_SOURCES))
            sources = list(original)
            legacy_part = set(sources) - {"gtfs"}
            if legacy_part and legacy_part <= LEGACY_SOURCES:
                sources = list(DEFAULT_SOURCES)
            if "gtfs" not in sources:
                sources.append("gtfs")
            if sources != original:
                conn.execute(
                    "UPDATE watches SET sources = ? WHERE id = ?",
                    (db.dump_json(sources), row["id"]),
                )
                changed += 1
    settings_service.update({"sources_migrated_gtfs": "1"})
    return changed


def create_watch(
    payload: WatchIn, resolved: dict | None = None, *, user_id: int | None = None
) -> dict[str, Any]:
    data = payload.model_dump()
    if resolved:
        data.update(resolved)
    serialized = _serialize_watch(data)
    created = now_utc().isoformat()
    columns = ["created_at", "next_run_at"]
    values = [created, created]
    for field in WATCH_FIELDS:
        columns.append(field)
        values.append(serialized.get(field))
    columns.append("user_id")
    values.append(user_id)

    placeholders = ",".join(["?"] * len(columns))
    with db.db() as conn:
        cursor = conn.execute(
            f"INSERT INTO watches ({','.join(columns)}) VALUES ({placeholders})", values
        )
        watch_id = int(cursor.lastrowid)
    return get_watch(watch_id)  # type: ignore[return-value]


def get_watch(watch_id: int, *, user_id: int | None = None) -> dict[str, Any] | None:
    clause, params = _owner_clause(user_id)
    with db.db() as conn:
        row = conn.execute(
            SELECT_WATCH + " WHERE w.id = ?" + clause, [watch_id, *params]
        ).fetchone()
    return watch_to_dict(row) if row else None


def list_watches(user_id: int | None = None) -> list[dict[str, Any]]:
    clause, params = _owner_clause(user_id)
    with db.db() as conn:
        rows = conn.execute(
            SELECT_WATCH + " WHERE 1=1" + clause + " ORDER BY w.active DESC, w.created_at DESC",
            params,
        ).fetchall()
    return [watch_to_dict(row) for row in rows]


# --------------------------------------------------------------------------- #
# Cartelle: i raggruppamenti di ricerche (per viaggio, per contesto)
# --------------------------------------------------------------------------- #
def list_groups(user_id: int | None = None) -> list[dict[str, Any]]:
    """Le cartelle dell'utente con il numero di ricerche dentro."""
    clause, params = _owner_clause_plain(user_id, "g.user_id")
    with db.db() as conn:
        rows = conn.execute(
            "SELECT g.id, g.name, g.position,"
            " (SELECT COUNT(*) FROM watches w WHERE w.group_id = g.id) AS count"
            " FROM watch_groups g WHERE 1=1" + clause + " ORDER BY g.position, g.name",
            params,
        ).fetchall()
    return [db.row_to_dict(row) or {} for row in rows]


def create_group(name: str, *, user_id: int | None = None) -> dict[str, Any]:
    """Crea una cartella. Il nome viene ripulito e reso unico per utente."""
    pulito = " ".join((name or "").split())[:80]
    if not pulito:
        raise ValueError("La cartella ha bisogno di un nome")
    with db.db() as conn:
        esiste = conn.execute(
            "SELECT 1 FROM watch_groups WHERE user_id IS ? AND lower(name) = lower(?)",
            [user_id, pulito],
        ).fetchone()
        if esiste:
            raise ValueError("Esiste già una cartella con questo nome")
        prossima = conn.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 FROM watch_groups WHERE user_id IS ?",
            [user_id],
        ).fetchone()[0]
        cursor = conn.execute(
            "INSERT INTO watch_groups (user_id, name, position, created_at) VALUES (?, ?, ?, ?)",
            [user_id, pulito, prossima, now_utc().isoformat()],
        )
        group_id = int(cursor.lastrowid)
    return get_group(group_id, user_id=user_id) or {}


def get_group(group_id: int, *, user_id: int | None = None) -> dict[str, Any] | None:
    clause, params = _owner_clause_plain(user_id, "g.user_id")
    with db.db() as conn:
        row = conn.execute(
            "SELECT g.id, g.name, g.position,"
            " (SELECT COUNT(*) FROM watches w WHERE w.group_id = g.id) AS count"
            " FROM watch_groups g WHERE g.id = ?" + clause,
            [group_id, *params],
        ).fetchone()
    return db.row_to_dict(row) if row else None


def rename_group(group_id: int, name: str, *, user_id: int | None = None) -> dict[str, Any] | None:
    pulito = " ".join((name or "").split())[:80]
    if not pulito:
        raise ValueError("La cartella ha bisogno di un nome")
    clause, params = _owner_clause_plain(user_id)
    with db.db() as conn:
        duplicato = conn.execute(
            "SELECT 1 FROM watch_groups WHERE user_id IS ? AND lower(name) = lower(?) AND id <> ?",
            [user_id, pulito, group_id],
        ).fetchone()
        if duplicato:
            raise ValueError("Esiste già una cartella con questo nome")
        conn.execute(
            "UPDATE watch_groups SET name = ? WHERE id = ?" + clause, [pulito, group_id, *params]
        )
    return get_group(group_id, user_id=user_id)


def delete_group(group_id: int, *, user_id: int | None = None) -> bool:
    """Elimina la cartella. Le ricerche dentro non si perdono: restano
    semplicemente senza cartella."""
    clause, params = _owner_clause_plain(user_id)
    with db.db() as conn:
        row = conn.execute(
            "SELECT id FROM watch_groups WHERE id = ?" + clause, [group_id, *params]
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE watches SET group_id = NULL WHERE group_id = ?", [group_id]
        )
        conn.execute("DELETE FROM watch_groups WHERE id = ?", [group_id])
    return True


def _merge_prefs(existing: Any, new: Any) -> dict[str, Any]:
    """Unisce le preferenze nuove con quelle gia' imparate.

    Gli abbonamenti sono l'unica eccezione: se vengono reimpostati, la
    lista nuova sostituisce la vecchia (il form e' la fonte di verita').
    """
    base: dict[str, Any] = {}
    if isinstance(existing, str):
        try:
            existing = json.loads(existing)
        except (ValueError, TypeError):
            existing = {}
    if isinstance(existing, dict):
        base = dict(existing)
    if not isinstance(new, dict):
        return base
    if "passes" in new:
        from .services.passes import normalize_passes

        base["passes"] = normalize_passes(new["passes"])
    base.update({k: v for k, v in new.items() if k != "passes"})
    return base


def update_watch(
    watch_id: int, payload: WatchUpdate, *, user_id: int | None = None
) -> dict[str, Any] | None:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return get_watch(watch_id, user_id=user_id)

    if changes.get("sources") is not None:
        changes["sources"] = db.dump_json(changes["sources"])
    if changes.get("notify") is not None:
        changes["notify"] = db.dump_json(changes["notify"])
    if changes.get("prefs") is not None:
        current = get_watch(watch_id, user_id=user_id) or {}
        changes["prefs"] = db.dump_json(
            _merge_prefs(current.get("prefs"), changes["prefs"])
        )
    elif "prefs" in changes:
        changes.pop("prefs")
    if changes.get("active") is not None:
        changes["active"] = 1 if changes["active"] else 0
    for key in ("depart_after", "arrive_by"):
        if changes.get(key) is not None:
            changes[key] = iso(changes[key])

    # Se cambia la finestra o il percorso, riparti subito con una ricerca.
    restart = any(k in changes for k in ("arrive_by", "depart_after", "from_place", "to_place"))
    if restart and "next_run_at" not in changes:
        changes["next_run_at"] = now_utc().isoformat()

    assignments = ", ".join(f"{key} = ?" for key in changes)
    values = list(changes.values()) + [watch_id]
    clause, params = _owner_clause_plain(user_id)
    with db.db() as conn:
        conn.execute(f"UPDATE watches SET {assignments} WHERE id = ?" + clause, values + params)
    return get_watch(watch_id, user_id=user_id)


def delete_watch(watch_id: int, *, user_id: int | None = None) -> bool:
    clause, params = _owner_clause_plain(user_id)
    with db.db() as conn:
        cursor = conn.execute(
            f"DELETE FROM watches WHERE id = ?" + clause, [watch_id, *params]
        )
        return cursor.rowcount > 0


def set_watch_active(
    watch_id: int, active: bool, *, user_id: int | None = None
) -> dict[str, Any] | None:
    clause, params = _owner_clause_plain(user_id)
    with db.db() as conn:
        conn.execute(
            "UPDATE watches SET active = ?, next_run_at = ? WHERE id = ?" + clause,
            (1 if active else 0, now_utc().isoformat() if active else None, watch_id, *params),
        )
    return get_watch(watch_id, user_id=user_id)


def due_watches(limit: int = 5) -> list[dict[str, Any]]:
    """Ricerche da rilanciare. Le esegue lo scheduler: qui non serve filtrare per utente."""
    now = now_utc().isoformat()
    with db.db() as conn:
        rows = conn.execute(
            SELECT_WATCH
            + " WHERE w.active = 1 AND (w.next_run_at IS NULL OR w.next_run_at <= ?)"
            + " ORDER BY w.next_run_at ASC LIMIT ?",
            (now, limit),
        ).fetchall()
    return [watch_to_dict(row) for row in rows]


def list_offers(watch_id: int, include_inactive: bool = False) -> list[dict[str, Any]]:
    query = "SELECT * FROM offers WHERE watch_id = ?"
    if not include_inactive:
        query += " AND active = 1"
    query += " ORDER BY (score IS NULL), score DESC, price ASC"
    with db.db() as conn:
        rows = conn.execute(query, (watch_id,)).fetchall()
    offers = []
    for row in rows:
        item = db.row_to_dict(row)  # type: ignore[assignment]
        item["badges"] = db.load_json(item.get("badges"), [])  # type: ignore[union-attr]
        item["details"] = db.load_json(item.get("details"), {})  # type: ignore[union-attr]
        item["active"] = bool(item.get("active"))  # type: ignore[union-attr]
        item["price_is_estimate"] = bool(item.get("price_is_estimate"))  # type: ignore[union-attr]
        offers.append(item)
    return offers


def list_runs(watch_id: int, limit: int = 20) -> list[dict[str, Any]]:
    with db.db() as conn:
        rows = conn.execute(
            "SELECT * FROM runs WHERE watch_id = ? ORDER BY id DESC LIMIT ?", (watch_id, limit)
        ).fetchall()
    runs = []
    for row in rows:
        item = db.row_to_dict(row)
        item["errors"] = db.load_json(item.get("errors"), [])  # type: ignore[arg-type]
        item["notes"] = db.load_json(item.get("notes"), [])  # type: ignore[arg-type]
        runs.append(item)
    return runs


def _plan_to_dict(row) -> dict[str, Any]:
    item = db.row_to_dict(row) or {}
    item["steps"] = db.load_json(item.get("steps"), [])
    item["watchouts"] = db.load_json(item.get("watchouts"), [])
    item["passes"] = db.load_json(item.get("passes"), [])
    item["confronti"] = db.load_json(item.get("confronti"), [])
    item["is_current"] = bool(item.get("is_current"))
    item["partial"] = bool(item.get("partial"))
    item["ai"] = bool(item.get("ai"))
    item["covered"] = bool(item.get("covered"))
    return item


def save_plans(
    watch_id: int,
    run_id: int | None,
    plan: dict[str, Any] | None,
    alternatives: list[dict[str, Any]] | None = None,
) -> None:
    """Sostituisce il piano corrente della ricerca con quello appena calcolato."""
    timestamp = now_utc().isoformat()
    rows: list[tuple[str, dict[str, Any]]] = []
    if plan:
        rows.append(("cheapest", plan))
    for alternative in alternatives or []:
        rows.append(("alternative", alternative))

    with db.db() as conn:
        conn.execute("UPDATE plans SET is_current = 0 WHERE watch_id = ?", (watch_id,))
        for kind, item in rows:
            conn.execute(
                """
                INSERT INTO plans(watch_id, run_id, kind, title, total_price, total_duration_min,
                    steps, explanation, watchouts, confidence, fingerprint, created_at, is_current,
                    profile, changes, effective_cost, access_cost, depart_at, arrive_at,
                    transfer_km, partial, ai, net_total_price, covered, passes, confronti,
                    door_to_door_min, walk_out_min, walk_in_min, walk_out_km, walk_in_km)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    watch_id,
                    run_id,
                    kind,
                    str(item.get("title") or "")[:200],
                    item.get("total_price"),
                    item.get("total_duration_min"),
                    db.dump_json(item.get("steps") or []),
                    item.get("explanation"),
                    db.dump_json(item.get("watchouts") or []),
                    item.get("confidence"),
                    item.get("fingerprint"),
                    timestamp,
                    item.get("profile"),
                    item.get("changes"),
                    item.get("effective_cost"),
                    item.get("access_cost"),
                    item.get("depart_at"),
                    item.get("arrive_at"),
                    item.get("transfer_km"),
                    1 if item.get("partial") else 0,
                    1 if item.get("ai") else 0,
                    item.get("net_total_price"),
                    1 if item.get("covered") else 0,
                    db.dump_json(item.get("passes") or []),
                    db.dump_json(item.get("confronti") or []),
                    item.get("door_to_door_min"),
                    item.get("walk_out_min"),
                    item.get("walk_in_min"),
                    item.get("walk_out_km"),
                    item.get("walk_in_km"),
                ),
            )


def current_plans(watch_id: int) -> list[dict[str, Any]]:
    with db.db() as conn:
        rows = conn.execute(
            "SELECT * FROM plans WHERE watch_id = ? AND is_current = 1 "
            "ORDER BY (kind = 'cheapest') DESC, total_price ASC",
            (watch_id,),
        ).fetchall()
    return [_plan_to_dict(row) for row in rows]


def clear_plans(watch_id: int) -> None:
    """Nessun piano valido: toglie quello corrente.

    Serve perche' i filtri cambiano: un piano valido ieri (o con un budget
    diverso) non deve restare a schermo come se fosse ancora valido.
    """
    with db.db() as conn:
        conn.execute("UPDATE plans SET is_current = 0 WHERE watch_id = ?", (watch_id,))


def add_chat_message(
    watch_id: int,
    role: str,
    text: str,
    changes: list[str] | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    """Salva un messaggio della conversazione con l'agente."""
    with db.db() as conn:
        cur = conn.execute(
            "INSERT INTO chat_messages(watch_id, user_id, role, text, changes, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                watch_id,
                user_id,
                role,
                text,
                json.dumps(changes or [], ensure_ascii=False),
                now_utc().isoformat(),
            ),
        )
        row = conn.execute(
            "SELECT * FROM chat_messages WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    return _chat_to_dict(row)


def list_chat_messages(watch_id: int, limit: int = 200) -> list[dict[str, Any]]:
    """Gli ultimi messaggi della ricerca, dal piu' vecchio al piu' recente."""
    with db.db() as conn:
        rows = conn.execute(
            "SELECT * FROM (SELECT * FROM chat_messages WHERE watch_id = ? "
            "ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
            (watch_id, limit),
        ).fetchall()
    return [_chat_to_dict(row) for row in rows]


def clear_chat_messages(watch_id: int) -> int:
    with db.db() as conn:
        cur = conn.execute("DELETE FROM chat_messages WHERE watch_id = ?", (watch_id,))
    return int(cur.rowcount or 0)


def _chat_to_dict(row) -> dict[str, Any]:
    item = db.row_to_dict(row) or {}
    item["changes"] = db.load_json(item.get("changes"), [])
    return item


def update_ai_fields(
    watch_id: int,
    *,
    summary: str | None = None,
    fingerprint: str | None = None,
    best_price: float | None = None,
    reset_best: bool = False,
) -> None:
    with db.db() as conn:
        if reset_best:
            # Il piano non c'e' piu': si azzerano prezzo e piano, ma la
            # sintesi va aggiornata lo stesso (altrimenti resta quella
            # vecchia e la dashboard non spiega il vuoto).
            conn.execute(
                "UPDATE watches SET ai_summary = ?, ai_best_price = ?, "
                "ai_plan_fingerprint = ?, ai_updated_at = ? WHERE id = ?",
                (summary, best_price, fingerprint, now_utc().isoformat(), watch_id),
            )
            return
        conn.execute(
            "UPDATE watches SET ai_summary = ?, ai_plan_fingerprint = ?, "
            "ai_best_price = COALESCE(?, ai_best_price), ai_updated_at = ? WHERE id = ?",
            (summary, fingerprint, best_price, now_utc().isoformat(), watch_id),
        )


def list_alerts(
    unread_only: bool = False, limit: int = 50, *, user_id: int | None = None
) -> list[dict[str, Any]]:
    """Avvisi dell'utente. Gli avvisi appartengono a una ricerca, quindi a un utente."""
    where: list[str] = []
    params: list[Any] = []
    if user_id is not None:
        where.append(
            "watch_id IN (SELECT id FROM watches WHERE user_id = ?)"
        )
        params.append(user_id)
    if unread_only:
        where.append("read = 0")
    query = "SELECT * FROM alerts"
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with db.db() as conn:
        rows = conn.execute(query, params).fetchall()
    alerts = []
    for row in rows:
        item = db.row_to_dict(row)
        item["read"] = bool(item.get("read"))  # type: ignore[arg-type]
        alerts.append(item)
    return alerts


def mark_alerts_read(
    alert_ids: list[int] | None = None, *, user_id: int | None = None
) -> int:
    where: list[str] = []
    params: list[Any] = []
    if user_id is not None:
        where.append("watch_id IN (SELECT id FROM watches WHERE user_id = ?)")
        params.append(user_id)
    if alert_ids:
        where.append("id IN (" + ",".join(["?"] * len(alert_ids)) + ")")
        params.extend(alert_ids)
    else:
        where.append("read = 0")
    query = "UPDATE alerts SET read = 1"
    if where:
        query += " WHERE " + " AND ".join(where)
    with db.db() as conn:
        cursor = conn.execute(query, params)
        return cursor.rowcount


def _link_voli(genera, watch: dict[str, Any], momento) -> str:
    """Il link del volo, se la tratta ha aeroporti (altrimenti vuoto).

    Voli e Skyscanner non si possono leggere da un server: il loro valore e'
    proprio il link gia' compilato, che finisce in «Link utili».
    """
    if momento is None:
        return ""
    try:
        return (
            genera(
                watch["from_place"],
                watch["to_place"],
                momento.date(),
                watch.get("passengers", 1),
            )
            or ""
        )
    except Exception:  # noqa: BLE001
        return ""


def links_for_watch(watch: dict[str, Any]) -> list[dict[str, str]]:
    from .adapters.googleflights import link_ricerca as link_google_voli
    from .adapters.skyscanner import link_ricerca as link_skyscanner
    from .timeutil import parse_dt

    arrive = parse_dt(watch["arrive_by"])
    start = parse_dt(watch.get("depart_after")) or arrive
    day = (start or arrive).strftime("%Y-%m-%d") if (start or arrive) else date.today().isoformat()
    day_it = (start or arrive).strftime("%d.%m.%Y") if (start or arrive) else date.today().strftime("%d.%m.%Y")

    from_place = quote(watch["from_place"])
    to_place = quote(watch["to_place"])

    links = [
        {
            "id": "blablacar",
            "label": "BlaBlaCar",
            "url": f"https://www.blablacar.it/search?fn={from_place}&tn={to_place}&db={day}",
            "note": "Cerca passaggi e filtra per ora di arrivo",
        },
        {
            "id": "flixbus",
            "label": "FlixBus",
            "url": (
                f"https://shop.flixbus.it/search?departureCity={from_place}"
                f"&arrivalCity={to_place}&rideDate={day_it}&adult={watch.get('passengers', 1)}"
            ),
            "note": "Autobus low cost",
        },
        {
            "id": "omio",
            "label": "Omio",
            "url": f"https://www.omio.it/search?departure={from_place}&arrival={to_place}&date={day}",
            "note": "Confronto treno + bus",
        },
        {
            "id": "googleflights",
            "label": "Google Voli",
            "url": _link_voli(link_google_voli, watch, start or arrive),
            "note": "Voli: ricerca già compilata",
        },
        {
            "id": "skyscanner",
            "label": "Skyscanner",
            "url": _link_voli(link_skyscanner, watch, start or arrive),
            "note": "Voli: confronto compagnie",
        },
        {
            "id": "trainline",
            "label": "Trainline",
            "url": f"https://www.trainline.it/",
            "note": "Confronto treni e bus",
        },
        {
            "id": "trenitalia",
            "label": "Trenitalia",
            "url": "https://www.lefrecce.it/",
            "note": "Tariffe regionali e Frecce",
        },
        {
            "id": "italo",
            "label": "Italo",
            "url": "https://www.italotreno.it/",
            "note": "Alta velocita'",
        },
        {
            "id": "codici-italo",
            "label": "Codici promo Italo",
            "url": "https://www.italotreno.com/it/offerte-treno/codicepromo",
            "note": "Codici sconto ufficiali Italo",
        },
        {
            "id": "codici-treni",
            "label": "Codici sconto treni",
            "url": (
                "https://www.thetrainline.com/it/compagnie-ferroviarie/trenitalia/codice-sconto"
            ),
            "note": "Codici e promozioni Trenitalia",
        },
    ]

    if watch.get("from_lat") is not None and watch.get("to_lat") is not None:
        links.append(
            {
                "id": "gmaps",
                "label": "Google Maps (mezzi)",
                "url": (
                    "https://www.google.com/maps/dir/?api=1"
                    f"&origin={watch['from_lat']},{watch['from_lon']}"
                    f"&destination={watch['to_lat']},{watch['to_lon']}"
                    "&travelmode=transit"
                ),
                "note": "Orari mezzi pubblici in tempo reale",
            }
        )

    for provider in load_providers():
        links.append(
            {
                "id": provider["id"],
                "label": provider.get("name", provider["id"]),
                "url": provider.get("url", ""),
                "note": provider.get("note", "Carsharing"),
            }
        )
    # Voli: se la tratta non ha aeroporti il link resta vuoto e non si mostra.
    links = [
        collegamento
        for collegamento in links
        if collegamento.get("url")
        or collegamento["id"] not in {"googleflights", "skyscanner"}
    ]
    return links


def neighbor_watches(watch: dict[str, Any], days: int = 1) -> list[dict[str, Any]]:
    """Ricerche sullo stesso percorso in date vicine (utile per flessibilita')."""
    arrive = from_iso(watch["arrive_by"])
    if not arrive:
        return []
    owner, params = _owner_clause(watch.get("user_id"))
    with db.db() as conn:
        rows = conn.execute(
            SELECT_WATCH
            + " WHERE w.id != ? AND lower(w.from_place) = lower(?) AND lower(w.to_place) = lower(?)"
            + " AND w.arrive_by BETWEEN ? AND ?"
            + owner,
            [
                watch["id"],
                watch["from_place"],
                watch["to_place"],
                (arrive - timedelta(days=days)).isoformat(),
                (arrive + timedelta(days=days)).isoformat(),
                *params,
            ],
        ).fetchall()
    return [watch_to_dict(row) for row in rows]


def cleanup_old_data(days: int = 90) -> None:
    cutoff = (now_utc() - timedelta(days=days)).isoformat()
    with db.db() as conn:
        conn.execute("DELETE FROM offers WHERE active = 0 AND last_seen < ?", (cutoff,))
        conn.execute("DELETE FROM runs WHERE started_at < ?", (cutoff,))
        conn.execute("DELETE FROM alerts WHERE created_at < ?", (cutoff,))
