"""Career progress: dashboard aggregates and the single recommended next action."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import AgentRun, Application, DiscoverySession, Preference, Swipe, User


def counts(db: Session, user_id: int) -> dict:
    sw = db.query(Swipe).filter_by(user_id=user_id, undone=False, superseded=False).all()
    apps = db.query(Application).filter_by(user_id=user_id).all()
    return {
        "interested": sum(1 for s in sw if s.decision == "yes"),
        "maybe": sum(1 for s in sw if s.decision == "maybe"),
        "passed": sum(1 for s in sw if s.decision == "no"),
        "applications_ready": sum(1 for a in apps if a.prep_status == "ready_for_review"),
        "applications_needing_correction": sum(1 for a in apps if a.prep_status == "needs_correction"),
        "applications_in_progress": sum(1 for a in apps if a.prep_status in ("pending", "generating")),
        "applications_failed": sum(1 for a in apps if a.prep_status == "failed"),
        "applied": sum(1 for a in apps if a.app_status != "not_applied"),
    }


def next_action(db: Session, user: User) -> dict:
    if user.onboarding_stage != "done":
        return {"action": "finish_onboarding", "label": "Finish setting up your profile", "route": "/onboarding"}
    c = counts(db, user.id)
    active = db.query(DiscoverySession).filter_by(user_id=user.id, status="active").order_by(DiscoverySession.id.desc()).first()
    if active:
        from .discovery import session_progress

        if session_progress(db, active)["remaining"]:
            return {"action": "continue_discovery", "label": "Continue your current batch", "route": "/discover"}
    q = db.query(Preference).filter(Preference.user_id == user.id, Preference.kind == "inferred", Preference.status == "proposed",
                                    Preference.question.isnot(None)).first()
    if q:
        return {"action": "answer_question", "label": q.question, "route": "/preferences"}
    if c["applications_ready"]:
        return {"action": "review_applications", "label": f"Review {c['applications_ready']} prepared application(s)", "route": "/jobs?tab=interested"}
    if c["maybe"]:
        return {"action": "revisit_maybes", "label": f"Revisit {c['maybe']} job(s) you marked Maybe", "route": "/jobs?tab=maybe"}
    return {"action": "discover", "label": "Discover new opportunities", "route": "/discover"}


def recent_activity(db: Session, user_id: int, limit: int = 5) -> list[dict]:
    runs = db.query(AgentRun).filter_by(user_id=user_id).order_by(AgentRun.id.desc()).limit(limit).all()
    return [{"id": r.id, "trigger": r.trigger, "status": r.status, "planner": r.planner, "explanation": r.explanation,
             "started_at": r.started_at.isoformat(), "activity": (r.activity or [])[-6:]} for r in runs]
