"""Discovery batches, swipes, feedback, review dashboard, applications and document downloads."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from ..agents.orchestrator import learn_from_decision
from ..config import get_settings
from ..database import get_db
from ..models import AgentRun, Application, DiscoverySession, Document, Feedback, Job, JobMatch, Swipe, User, now
from ..schemas import ApplicationPatch, DecisionChange, FeedbackIn, SwipeIn
from ..services import applications as appsvc
from ..services import document_generator as dg
from ..services.discovery import session_progress
from ..workers.tasks import enqueue_task
from .deps import current_user, job_card

router = APIRouter(prefix="/api")


# ----------------------------------------------------------------------- discovery
def _session_payload(db: Session, s: DiscoverySession | None, user_id: int) -> dict:
    if s is None:
        return {"session": None}
    prog = session_progress(db, s)
    run = db.get(AgentRun, s.agent_run_id) if s.agent_run_id else None
    jobs = [job_card(db, db.get(Job, jid), user_id, full=True) for jid in s.job_ids]
    apps = {a.job_id: a.prep_status for a in db.query(Application).filter(Application.user_id == user_id,
                                                                          Application.job_id.in_(s.job_ids or [-1]))}
    summary = {
        "reviewed": len(prog["decided"]),
        "interested": sum(1 for d in prog["decided"].values() if d == "yes"),
        "maybe": sum(1 for d in prog["decided"].values() if d == "maybe"),
        "rejected": sum(1 for d in prog["decided"].values() if d == "no"),
        "applications_prepared": sum(1 for v in apps.values() if v in ("ready_for_review", "needs_correction")),
        "applications_generating": sum(1 for v in apps.values() if v in ("pending", "generating")),
    }
    return {"session": {"id": s.id, "status": s.status, "note": s.note, "created_at": s.created_at.isoformat(),
                        "job_ids": s.job_ids, "decided": prog["decided"], "remaining": prog["remaining"], "summary": summary},
            "jobs": jobs, "agent_run": _run_payload(db, run) if run else None}


def _run_payload(db: Session, run: AgentRun) -> dict:
    from ..models import AgentToolCall

    calls = db.query(AgentToolCall).filter_by(run_id=run.id).order_by(AgentToolCall.id).all()
    return {"id": run.id, "trigger": run.trigger, "status": run.status, "planner": run.planner, "model": run.model,
            "prompt_version": run.prompt_version, "activity": run.activity, "explanation": run.explanation,
            "strategy_summary": run.strategy_summary, "result_counts": run.result_counts, "errors": run.errors,
            "llm_calls": run.llm_calls, "input_tokens": run.input_tokens, "output_tokens": run.output_tokens,
            "estimated_cost_usd": run.estimated_cost_usd, "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "tool_calls": [{"tool": c.tool, "status": c.status, "arguments": c.arguments, "result": c.result_summary,
                            "duration_ms": c.duration_ms} for c in calls]}


def _start(db: Session, user: User, trigger: str) -> DiscoverySession:
    s = DiscoverySession(user_id=user.id, status="preparing")
    db.add(s)
    db.flush()
    enqueue_task(db, user.id, "discovery", {"trigger": trigger, "session_id": s.id}, idem=f"disc-{user.id}-{s.id}")
    db.commit()
    return s


@router.post("/discovery/start")
def discovery_start(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.onboarding_stage != "done":
        raise HTTPException(409, "Finish onboarding first.")
    cur = db.query(DiscoverySession).filter(DiscoverySession.user_id == user.id,
                                           DiscoverySession.status.in_(["preparing", "active"])).order_by(DiscoverySession.id.desc()).first()
    if cur and (cur.status == "preparing" or session_progress(db, cur)["remaining"]):
        return _session_payload(db, cur, user.id)
    first = db.query(DiscoverySession).filter_by(user_id=user.id).count() == 0
    return _session_payload(db, _start(db, user, "onboarding_completed" if first else "more_jobs_requested"), user.id)


@router.post("/discovery/next")
def discovery_next(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.onboarding_stage != "done":
        raise HTTPException(409, "Finish onboarding first.")
    busy = db.query(DiscoverySession).filter_by(user_id=user.id, status="preparing").first()
    if busy:
        return _session_payload(db, busy, user.id)
    for s in db.query(DiscoverySession).filter_by(user_id=user.id, status="active"):
        s.status, s.completed_at = "completed", now()
    return _session_payload(db, _start(db, user, "more_jobs_requested"), user.id)


@router.get("/discovery/current")
def discovery_current(user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = db.query(DiscoverySession).filter_by(user_id=user.id).order_by(DiscoverySession.id.desc()).first()
    return _session_payload(db, s, user.id)


@router.post("/discovery/{session_id}/finish")
def discovery_finish(session_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(DiscoverySession, session_id)
    if s is None or s.user_id != user.id:
        raise HTTPException(404, "not found")
    s.status, s.completed_at = "completed", now()
    db.commit()
    return _session_payload(db, s, user.id)


# ----------------------------------------------------------------------- swipes & feedback
def _get_job(db: Session, job_id: int) -> Job:
    j = db.get(Job, job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    return j


def _record(db: Session, user: User, job: Job, decision: str, key: str, session_id: int | None) -> Swipe:
    prev = db.query(Swipe).filter_by(user_id=user.id, job_id=job.id, undone=False, superseded=False).order_by(Swipe.id.desc()).first()
    if session_id is not None:
        sess = db.get(DiscoverySession, session_id)
        if sess is None or sess.user_id != user.id:
            raise HTTPException(404, "session not found")
    sw = Swipe(user_id=user.id, job_id=job.id, session_id=session_id, decision=decision, idempotency_key=key)
    db.add(sw)
    if prev:
        prev.superseded = True
    if decision == "yes":
        appsvc.enqueue(db, user.id, job.id)  # right swipe enqueues preparation; discovery isn't blocked
    elif prev and prev.decision == "yes":
        appsvc.cancel(db, user.id, job.id)  # generated documents are kept
    db.flush()
    return sw


def _complete_if_done(db: Session, session_id: int | None) -> None:
    if session_id is None:
        return
    s = db.get(DiscoverySession, session_id)
    if s and s.status == "active" and not session_progress(db, s)["remaining"]:
        s.status, s.completed_at = "completed", now()


@router.post("/jobs/{job_id}/swipe")
def swipe(job_id: int, body: SwipeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    existing = db.query(Swipe).filter_by(idempotency_key=body.idempotency_key).one_or_none()
    if existing:
        if existing.user_id != user.id:
            raise HTTPException(409, "idempotency key conflict")
        return {"swipe_id": existing.id, "decision": existing.decision, "duplicate": True}
    job = _get_job(db, job_id)
    sw = _record(db, user, job, body.decision, body.idempotency_key, body.session_id)
    _complete_if_done(db, body.session_id)
    db.commit()
    learn_from_decision(db, user.id, f"job_{'accepted' if body.decision == 'yes' else 'rejected' if body.decision == 'no' else 'maybe'}")
    db.commit()
    return {"swipe_id": sw.id, "decision": sw.decision, "duplicate": False}


@router.post("/jobs/{job_id}/feedback")
def feedback(job_id: int, body: FeedbackIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _get_job(db, job_id)
    sw = db.query(Swipe).filter_by(user_id=user.id, job_id=job_id, undone=False, superseded=False).order_by(Swipe.id.desc()).first()
    fb = db.query(Feedback).filter_by(user_id=user.id, job_id=job_id).one_or_none()
    if fb is None:
        fb = Feedback(user_id=user.id, job_id=job_id)
        db.add(fb)
    fb.swipe_id = sw.id if sw else None
    fb.reasons = sorted(set(body.reasons))
    fb.comment = body.comment
    db.commit()
    learn_from_decision(db, user.id, "feedback")
    db.commit()
    return {"ok": True, "feedback_id": fb.id}


@router.post("/decisions/undo-last")
def undo_last(user: User = Depends(current_user), db: Session = Depends(get_db)):
    latest = db.query(Swipe).filter_by(user_id=user.id, undone=False).order_by(Swipe.id.desc()).first()
    if latest is None:
        raise HTTPException(404, "nothing to undo")
    return undo(latest.job_id, user, db)


@router.post("/jobs/{job_id}/undo")
def undo(job_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    sw = db.query(Swipe).filter_by(user_id=user.id, job_id=job_id, undone=False, superseded=False).order_by(Swipe.id.desc()).first()
    if sw is None:
        raise HTTPException(404, "nothing to undo")
    latest = db.query(Swipe).filter_by(user_id=user.id, undone=False).order_by(Swipe.id.desc()).first()
    if latest.id != sw.id:
        raise HTTPException(409, "only your most recent decision can be undone; change older decisions from the review screen")
    sw.undone = True
    prev = db.query(Swipe).filter(Swipe.user_id == user.id, Swipe.job_id == job_id, Swipe.superseded.is_(True),
                                  Swipe.undone.is_(False)).order_by(Swipe.id.desc()).first()
    if prev:
        prev.superseded = False
    if sw.decision == "yes" and not (prev and prev.decision == "yes"):
        appsvc.cancel(db, user.id, job_id)
    if sw.session_id:
        s = db.get(DiscoverySession, sw.session_id)
        if s and s.status == "completed":
            s.status, s.completed_at = "active", None
    db.commit()
    learn_from_decision(db, user.id, "undo")
    db.commit()
    return {"undone": sw.id, "restored": prev.decision if prev else None}


@router.post("/jobs/{job_id}/decision")
def change_decision(job_id: int, body: DecisionChange, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = _get_job(db, job_id)
    import uuid

    sw = _record(db, user, job, body.decision, f"change-{uuid.uuid4().hex}", None)
    db.commit()
    learn_from_decision(db, user.id, "decision_changed")
    db.commit()
    return {"swipe_id": sw.id, "decision": sw.decision}


# ----------------------------------------------------------------------- job review
@router.get("/jobs/{job_id}")
def get_job(job_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = _get_job(db, job_id)
    card = job_card(db, job, user.id, full=True)
    sw = db.query(Swipe).filter_by(user_id=user.id, job_id=job_id, undone=False, superseded=False).order_by(Swipe.id.desc()).first()
    fb = db.query(Feedback).filter_by(user_id=user.id, job_id=job_id).one_or_none()
    app = db.query(Application).filter_by(user_id=user.id, job_id=job_id).one_or_none()
    card.update(decision=sw.decision if sw else None, feedback={"reasons": fb.reasons, "comment": fb.comment} if fb else None,
                application=_app_payload(db, app, job) if app else None)
    return card


@router.get("/jobs")
def list_jobs(decision: str = Query("yes", pattern="^(yes|no|maybe)$"), sort: str = Query("relevance", pattern="^(relevance|salary|date)$"),
              user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = (db.query(Swipe, Job).join(Job, Job.id == Swipe.job_id)
            .filter(Swipe.user_id == user.id, Swipe.decision == decision, Swipe.undone.is_(False), Swipe.superseded.is_(False)).all())
    fbs = {f.job_id: f for f in db.query(Feedback).filter_by(user_id=user.id)}
    apps = {a.job_id: a for a in db.query(Application).filter_by(user_id=user.id)}
    out = []
    for s, j in rows:
        c = job_card(db, j, user.id)
        c["decided_at"] = s.created_at.isoformat()
        c["feedback"] = {"reasons": fbs[j.id].reasons, "comment": fbs[j.id].comment} if j.id in fbs else None
        c["application"] = _app_payload(db, apps[j.id], j) if j.id in apps else None
        out.append(c)
    key = {"relevance": lambda c: -((c["match"] or {}).get("score") or 0),
           "salary": lambda c: -(((c["salary"] or {}).get("max") or (c["salary"] or {}).get("min") or 0)),
           "date": lambda c: c["decided_at"]}[sort]
    out.sort(key=key, reverse=(sort == "date"))
    return {"jobs": out}


# ----------------------------------------------------------------------- applications
def _app_payload(db: Session, app: Application, job: Job) -> dict:
    docs = appsvc.latest_documents(db, app)
    return {"id": app.id, "job_id": app.job_id, "prep_status": app.prep_status, "app_status": app.app_status, "comment": app.comment,
            "version": app.version, "generator": app.generator, "error": app.error, "validation": app.validation,
            "requirement_map": app.requirement_map, "updated_at": app.updated_at.isoformat(),
            "folder": _rel_folder(app.folder),
            "documents": [{"id": d.id, "kind": d.kind, "version": d.version, "filename": Path(d.path).name,
                           "preview": d.preview} for d in docs]}


def _rel_folder(folder: str | None) -> str | None:
    if not folder:
        return None
    base = get_settings().storage_path
    try:
        return str(Path(folder).resolve().relative_to(base.resolve()))
    except ValueError:
        return None


@router.get("/applications")
def list_applications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    out = []
    for a in db.query(Application).filter_by(user_id=user.id).order_by(Application.updated_at.desc()):
        j = db.get(Job, a.job_id)
        out.append({**_app_payload(db, a, j), "job": job_card(db, j, user.id)})
    return {"applications": out}


def _own_app(db: Session, app_id: int, user: User) -> Application:
    a = db.get(Application, app_id)
    if a is None or a.user_id != user.id:
        raise HTTPException(404, "not found")
    return a


@router.patch("/applications/{app_id}")
def patch_application(app_id: int, body: ApplicationPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = _own_app(db, app_id, user)
    if body.app_status:
        a.app_status = body.app_status  # manual tracking only; DAR never submits applications
    if body.comment is not None:
        a.comment = body.comment
    db.commit()
    return _app_payload(db, a, db.get(Job, a.job_id))


@router.post("/applications/{app_id}/regenerate")
def regenerate(app_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = _own_app(db, app_id, user)
    a.input_hash = None  # explicit user request: allow a new version even if inputs are unchanged
    a.attempts = 0
    appsvc.enqueue(db, user.id, a.job_id, force=True)
    db.commit()
    return _app_payload(db, a, db.get(Job, a.job_id))


@router.get("/applications/{app_id}/download")
def download_package(app_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = _own_app(db, app_id, user)
    docs = appsvc.latest_documents(db, a)
    if not docs:
        raise HTTPException(404, "no documents yet")
    base = get_settings().storage_path / "applications"
    files = [dg.ensure_within(Path(d.path), base) for d in docs]
    j = db.get(Job, a.job_id)
    data = dg.zip_folder(files, f"{j.employer_name}_{j.title}_{j.id}")
    name = dg.safe_component(f"DAR_{j.employer_name}_{j.title}") + ".zip"
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/documents/{doc_id}/download")
def download_document(doc_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = db.get(Document, doc_id)
    if d is None or d.user_id != user.id:
        raise HTTPException(404, "not found")  # same response for other users' documents: no existence leak
    p = dg.ensure_within(Path(d.path), get_settings().storage_path / "applications")
    if not p.exists():
        raise HTTPException(404, "file missing")
    return FileResponse(str(p), filename=p.name)
