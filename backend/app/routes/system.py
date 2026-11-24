"""Dashboard, agent activity, employer registry status and evaluation results."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import ROOT, get_settings
from ..database import get_db
from ..models import AgentRun, DiscoverySession, Employer, EvalResult, EvalRun, Job, Preference, User
from ..services import progress
from .deps import current_user
from .jobs import _run_payload

router = APIRouter(prefix="/api")


@router.get("/dashboard")
def dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = progress.counts(db, user.id)
    last = db.query(DiscoverySession).filter_by(user_id=user.id).order_by(DiscoverySession.id.desc()).first()
    new_opps = 0
    if last and last.status == "active":
        from ..services.discovery import session_progress

        new_opps = len(session_progress(db, last)["remaining"])
    questions = [{"id": p.id, "question": p.question, "rationale": p.rationale} for p in
                 db.query(Preference).filter(Preference.user_id == user.id, Preference.kind == "inferred",
                                             Preference.status == "proposed", Preference.question.isnot(None))]
    pending_q = None
    last_run = db.query(AgentRun).filter_by(user_id=user.id).order_by(AgentRun.id.desc()).first()
    if last_run and (last_run.result_counts or {}).get("pending_question"):
        pending_q = last_run.result_counts["pending_question"]
    return {"counts": {**c, "new_opportunities": new_opps}, "next_action": progress.next_action(db, user),
            "activity": progress.recent_activity(db, user.id), "questions": questions, "agent_question": pending_q,
            "last_session": {"id": last.id, "status": last.status} if last else None}


@router.get("/agent/runs")
def list_runs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    runs = db.query(AgentRun).filter_by(user_id=user.id).order_by(AgentRun.id.desc()).limit(20).all()
    return {"runs": [_run_payload(db, r) for r in runs]}


@router.get("/agent/runs/{run_id}")
def get_run(run_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = db.get(AgentRun, run_id)
    if r is None or r.user_id != user.id:
        raise HTTPException(404, "not found")
    return _run_payload(db, r)


@router.get("/employers")
def employers(db: Session = Depends(get_db)):
    rows = db.query(Employer).order_by(Employer.nasdaq100.desc(), Employer.name).all()
    from sqlalchemy import func

    counts = dict(db.query(Job.employer_id, func.count(Job.id)).filter(Job.active.is_(True)).group_by(Job.employer_id).all())
    uk = dict(db.query(Job.employer_id, func.count(Job.id)).filter(Job.active.is_(True), Job.country == "United Kingdom")
              .group_by(Job.employer_id).all())
    return {"employers": [{"id": e.id, "name": e.name, "provider": e.provider, "board_id": e.board_id, "nasdaq100": e.nasdaq100,
                           "industry": e.industry, "enabled": e.enabled, "verification_status": e.verification_status,
                           "verified_at": e.verified_at.isoformat() if e.verified_at else None,
                           "last_success_at": e.last_success_at.isoformat() if e.last_success_at else None,
                           "last_error": e.last_error, "active_jobs": counts.get(e.id, 0), "uk_jobs": uk.get(e.id, 0),
                           "careers_url": e.careers_url, "notes": e.notes} for e in rows],
            "summary": {"total": len(rows), "verified": sum(1 for e in rows if e.verification_status == "verified"),
                        "nasdaq100": sum(1 for e in rows if e.nasdaq100),
                        "nasdaq100_supported": sum(1 for e in rows if e.nasdaq100 and e.enabled),
                        "jobs_in_catalogue": sum(counts.values())}}


def _report_files() -> list:
    d = ROOT / "evals" / "reports"
    return sorted(d.glob("eval_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


@router.get("/evals/latest")
def evals_latest():
    """Reads genuine report files produced by `make eval` / `make eval-live`. Never synthesises results."""
    files = _report_files()
    if not files:
        return {"available": False, "message": "No evaluation has been run yet. Run `make eval` or `make eval-live`."}
    latest_by_mode: dict[str, dict] = {}
    history = []
    for f in files:
        try:
            r = json.loads(f.read_text())
        except Exception:
            continue
        history.append({k: r.get(k) for k in ("run_id", "mode", "timestamp", "totals", "model", "judge_model", "git_commit")})
        latest_by_mode.setdefault(r.get("mode", "offline"), r)
    return {"available": True, "latest": latest_by_mode, "history": history[:20]}


@router.get("/evals/report/{run_id}")
def eval_report(run_id: str):
    for f in _report_files():
        r = json.loads(f.read_text())
        if r.get("run_id") == run_id:
            return r
    raise HTTPException(404, "not found")
