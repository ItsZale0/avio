"""Scheduler: ogni 20 secondi avvia le ricerche scadute."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .. import db, store
from ..timeutil import now_utc
from . import search
from . import telegram_bot

scheduler = AsyncIOScheduler(timezone="Europe/Rome")
_tasks: set[asyncio.Task] = set()


def spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def tick() -> None:
    for watch in store.due_watches(limit=5):
        if watch["id"] in search.RUNNING:
            continue
        # Prenota subito il prossimo giro: evita doppi avvii se il run e' lento.
        next_at = (now_utc() + timedelta(minutes=watch["interval_min"])).isoformat()
        with db.db() as conn:
            conn.execute("UPDATE watches SET next_run_at=? WHERE id=?", (next_at, watch["id"]))
        spawn(search.execute_run(watch["id"], "schedule"))


async def cleanup() -> None:
    await asyncio.to_thread(store.cleanup_old_data, 90)
    # Sessioni e token di reset scaduti: non serve tenerli in giro.
    from .. import auth

    await asyncio.to_thread(auth.purge_expired_sessions)
    await asyncio.to_thread(auth.purge_expired_resets)


def start() -> None:
    if scheduler.running:
        return
    scheduler.add_job(tick, "interval", seconds=20, id="tick", max_instances=1, coalesce=True)
    scheduler.add_job(
        telegram_bot.poll, "interval", seconds=45, id="telegram_poll", max_instances=1, coalesce=True
    )
    scheduler.add_job(cleanup, "cron", hour=4, minute=10, id="cleanup", max_instances=1)
    scheduler.start()


def shutdown() -> None:
    for task in list(_tasks):
        task.cancel()
    if scheduler.running:
        scheduler.shutdown(wait=False)


def info() -> dict:
    jobs = []
    if scheduler.running:
        for job in scheduler.get_jobs():
            jobs.append({"id": job.id, "next_run": job.next_run_time.isoformat() if job.next_run_time else None})
    return {"running": scheduler.running, "jobs": jobs, "active_runs": sorted(search.RUNNING)}
