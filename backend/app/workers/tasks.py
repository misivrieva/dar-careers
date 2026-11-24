"""Durable background tasks stored in SQLite, executed by an in-process worker thread.

- Tasks survive restarts: anything left 'running' at startup is re-queued (interrupted work is retried).
- Idempotency keys prevent duplicate tasks.
- Discovery runs and application generation execute on separate threads so neither blocks the other.
"""
from __future__ import annotations

import logging
import threading
import time
import traceback

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import SessionLocal
from ..models import BackgroundTask, now

log = logging.getLogger("dar.worker")
MAX_TASK_ATTEMPTS = 3


def enqueue_task(db: Session, user_id: int | None, kind: str, payload: dict, idem: str | None = None) -> BackgroundTask:
    if idem:
        existing = db.query(BackgroundTask).filter_by(idempotency_key=idem).one_or_none()
        if existing:
            return existing
    t = BackgroundTask(user_id=user_id, kind=kind, payload=payload, status="queued", idempotency_key=idem)
    db.add(t)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return db.query(BackgroundTask).filter_by(idempotency_key=idem).one()
    return t


def recover_interrupted() -> int:
    with SessionLocal() as db:
        n = db.execute(update(BackgroundTask).where(BackgroundTask.status == "running").values(status="queued")).rowcount
        from ..models import Application, DiscoverySession

        db.query(Application).filter(Application.prep_status == "generating").update({Application.prep_status: "pending"})
        db.commit()
        return n


def _claim(kinds: tuple[str, ...]) -> int | None:
    with SessionLocal() as db:
        t = (db.query(BackgroundTask).filter(BackgroundTask.status == "queued", BackgroundTask.kind.in_(kinds))
             .order_by(BackgroundTask.id).first())
        if not t:
            return None
        res = db.execute(update(BackgroundTask).where(BackgroundTask.id == t.id, BackgroundTask.status == "queued")
                         .values(status="running", attempts=BackgroundTask.attempts + 1, updated_at=now()))
        db.commit()
        return t.id if res.rowcount == 1 else None


def run_task(task_id: int) -> None:
    db = SessionLocal()
    try:
        t = db.get(BackgroundTask, task_id)
        if t.kind == "generate_application":
            from ..services.applications import generate

            generate(db, t.payload["application_id"])
        elif t.kind == "discovery":
            from ..agents.orchestrator import run_discovery

            run_discovery(db, t.user_id, t.payload.get("trigger", "more_jobs_requested"), t.payload.get("session_id"))
        elif t.kind == "refresh":
            from ..services.catalogue import refresh_boards
            from ..services.discovery import rank_employers

            refresh_boards(db, [e.id for e in rank_employers(db, t.user_id, get_settings().agent_max_boards)])
        db.commit()
        t = db.get(BackgroundTask, task_id)
        t.status = "done"
        t.error = None
        db.commit()
    except Exception as e:
        db.rollback()
        log.error("task %s failed: %s", task_id, traceback.format_exc())
        t = db.get(BackgroundTask, task_id)
        t.error = f"{type(e).__name__}: {str(e)[:500]}"
        t.status = "queued" if t.attempts < MAX_TASK_ATTEMPTS else "failed"
        if t.status == "failed" and t.kind == "generate_application":
            from ..models import Application

            app = db.get(Application, t.payload["application_id"])
            if app:
                app.prep_status, app.error = "failed", t.error
        elif t.kind == "generate_application":
            from ..models import Application

            app = db.get(Application, t.payload["application_id"])
            if app and app.prep_status == "generating":
                app.prep_status = "pending"
        db.commit()
        time.sleep(1.0)
    finally:
        db.close()


class Worker:
    def __init__(self):
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        recover_interrupted()
        for name, kinds in (("apps", ("generate_application",)), ("agent", ("discovery", "refresh"))):
            th = threading.Thread(target=self._loop, args=(kinds,), name=f"dar-worker-{name}", daemon=True)
            th.start()
            self._threads.append(th)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self, kinds: tuple[str, ...]) -> None:
        poll = get_settings().worker_poll_s
        while not self._stop.is_set():
            try:
                tid = _claim(kinds)
            except Exception:
                log.exception("claim failed")
                tid = None
            if tid is None:
                self._stop.wait(poll)
                continue
            run_task(tid)


def drain(kinds: tuple[str, ...] = ("generate_application", "discovery", "refresh"), max_tasks: int = 50) -> int:
    """Synchronously run queued tasks (tests and CLI)."""
    n = 0
    while n < max_tasks:
        tid = _claim(kinds)
        if tid is None:
            break
        run_task(tid)
        n += 1
    return n
