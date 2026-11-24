"""CV upload, onboarding conversation, profile and preferences."""
from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..agents import memory
from ..config import get_settings
from ..database import get_db
from ..models import (AgentRun, Application, BackgroundTask, CandidateFact, CandidateProfile, DiscoverySession, Document,
                      Feedback, JobMatch, OnboardingMessage, Preference, Requirement, SearchStrategy, Swipe, UploadedDocument, User)
from ..schemas import ConsentIn, OnboardingMessageIn, PreferencePatch, ProfilePatch
from ..services import cv_parser, onboarding
from .deps import current_user

router = APIRouter(prefix="/api")


@router.get("/status")
def status(user: User = Depends(current_user)):
    s = get_settings()
    return {"llm_available": s.llm_available, "llm_provider": s.llm_provider, "generation_model": s.generation_model,
            "eval_model": s.eval_model, "demo_mode": s.demo_mode, "user_id": user.id, "onboarding_stage": user.onboarding_stage,
            "llm_consent": user.llm_consent}


@router.post("/consent")
def consent(body: ConsentIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    user = db.get(User, user.id)
    user.llm_consent = body.llm_consent
    db.commit()
    return {"llm_consent": user.llm_consent}


@router.post("/cv/upload")
async def upload_cv(file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    data = await file.read(get_settings().upload_max_bytes + 1)
    try:
        doc = cv_parser.store_upload(db, user.id, file.filename or "cv.txt", data)
    except cv_parser.UploadRejected as e:
        raise HTTPException(422, str(e))
    user = db.get(User, user.id)
    use_llm = get_settings().llm_available and user.llm_consent
    profile, report = cv_parser.build_profile(db, user.id, doc, use_llm)
    if user.onboarding_stage == "upload":
        user.onboarding_stage = "chat"
    db.commit()
    onboarding.ask_next(db, user.id)
    db.commit()
    return {"document_id": doc.id, "profile": profile_payload(db, user), "extraction": report}


@router.get("/profile")
def get_profile(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return profile_payload(db, user)


def profile_payload(db: Session, user: User) -> dict:
    p = cv_parser.latest_profile(db, user.id)
    facts = db.query(CandidateFact).filter_by(profile_id=p.id).all() if p else []
    strat = memory.latest_strategy(db, user.id)
    return {
        "user": {"id": user.id, "onboarding_stage": user.onboarding_stage, "include_uncertain": user.include_uncertain,
                 "llm_consent": user.llm_consent, "scheduled_refresh_opt_in": user.scheduled_refresh_opt_in},
        "profile": ({"id": p.id, "version": p.version, "method": p.extraction_method, "data": p.data} if p else None),
        "facts": [{"id": f.id, "category": f.category, "text": f.text, "source": f.source, "supported": f.supported,
                   "evidence": f.evidence} for f in facts],
        "requirements": memory.get_requirements(db, user.id),
        "preferences": memory.get_soft_preferences(db, user.id),
        "unknowns": memory.unknowns(memory.get_requirements(db, user.id), memory.get_soft_preferences(db, user.id)),
        "strategy": ({"version": strat.version, "titles": strat.titles, "expanded_titles": strat.expanded_titles,
                      "feature_adjustments": strat.feature_adjustments} if strat else None),
    }


@router.patch("/profile")
def patch_profile(body: ProfilePatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Trusted user action: the only way (besides onboarding answers) to change hard requirements."""
    user = db.get(User, user.id)
    for r in body.requirements:
        try:
            memory.set_requirement_trusted(db, user.id, r.key, r.value, source="user_review")
        except ValueError as e:
            raise HTTPException(422, str(e))
    for k, v in body.preferences.items():
        if k in memory.HARD_KEYS:
            raise HTTPException(422, f"{k} is a hard requirement; send it in 'requirements'")
        memory.set_soft_preference(db, user.id, k[:80], v, weight=_pref_weight(k, v), source="user_review")
    if body.target_titles is not None:
        titles = [t.strip()[:80] for t in body.target_titles if t.strip()][:10]
        memory.set_soft_preference(db, user.id, "target_titles", titles, source="user_review")
        strat = memory.latest_strategy(db, user.id)
        if strat and strat.titles != titles:
            db.add(SearchStrategy(user_id=user.id, version=strat.version + 1, titles=titles, expanded_titles=strat.expanded_titles,
                                  weights=strat.weights, feature_adjustments=strat.feature_adjustments, employer_ids=strat.employer_ids,
                                  rationale="You edited your target roles.", changes=[f"Target roles set to: {', '.join(titles)}"],
                                  created_by="user"))
    if body.include_uncertain is not None:
        user.include_uncertain = body.include_uncertain
    if body.complete_onboarding:
        user.onboarding_stage = "done"
    db.commit()
    # profile_updated trigger: re-evaluate stored matches against the new requirements
    _reevaluate(db, user)
    return profile_payload(db, user)


def _pref_weight(k, v) -> float:
    if k == "on_call":
        return {"never": -1.0, "avoid": -0.5, "fine": 0.0}.get(v, 0.0)
    return 0.0


def _reevaluate(db: Session, user: User) -> None:
    from ..models import Job
    from ..services import matching

    reqs = memory.get_requirements(db, user.id)
    for m in db.query(JobMatch).filter_by(user_id=user.id):
        j = db.get(Job, m.job_id)
        e = matching.evaluate_eligibility(matching.job_view(j), reqs)
        m.eligibility = e.status
        m.eligibility_reasons = e.violations + [{"code": "note", "detail": n} for n in e.notes]
        m.uncertain_fields = e.uncertain
    db.commit()


@router.get("/onboarding")
def get_onboarding(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.onboarding_stage == "chat":
        onboarding.ask_next(db, user.id)
        db.commit()
    return {"stage": user.onboarding_stage, "messages": onboarding.transcript(db, user.id)}


@router.post("/onboarding/message")
def onboarding_message(body: OnboardingMessageIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    user = db.get(User, user.id)
    if user.onboarding_stage not in ("chat",):
        raise HTTPException(409, "onboarding conversation is not active")
    res = onboarding.handle_answer(db, user, body.text, body.quick_reply)
    db.commit()
    return {"stage": user.onboarding_stage, "result": res, "messages": onboarding.transcript(db, user.id)}


@router.post("/onboarding/finish-later")
def finish_later(user: User = Depends(current_user), db: Session = Depends(get_db)):
    user = db.get(User, user.id)
    if user.onboarding_stage == "chat":
        user.onboarding_stage = "review"
    db.commit()
    return {"stage": user.onboarding_stage}


# ----------------------------------------------------------------------- preferences / hypotheses
@router.get("/preferences")
def list_preferences(user: User = Depends(current_user), db: Session = Depends(get_db)):
    prefs = db.query(Preference).filter_by(user_id=user.id).order_by(Preference.kind, Preference.updated_at.desc()).all()
    strategies = db.query(SearchStrategy).filter_by(user_id=user.id).order_by(SearchStrategy.version.desc()).limit(10).all()
    return {
        "requirements": memory.get_requirements(db, user.id),
        "preferences": [{"id": p.id, "attribute": p.attribute, "value": p.value, "kind": p.kind, "status": p.status, "weight": p.weight,
                         "confidence": p.confidence, "supporting": p.supporting_feedback_ids, "contradicting": p.contradicting_feedback_ids,
                         "rationale": p.rationale, "question": p.question, "affects_hard_requirement": p.affects_hard_requirement,
                         "updated_at": p.updated_at.isoformat()} for p in prefs],
        "strategies": [{"version": s.version, "titles": s.titles, "expanded_titles": s.expanded_titles,
                        "feature_adjustments": s.feature_adjustments, "changes": s.changes, "rationale": s.rationale,
                        "created_by": s.created_by, "created_at": s.created_at.isoformat()} for s in strategies],
    }


@router.patch("/preferences/{pref_id}")
def patch_preference(pref_id: int, body: PreferencePatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(Preference, pref_id)
    if p is None or p.user_id != user.id:
        raise HTTPException(404, "not found")
    if body.action == "confirm":
        p.status = "confirmed"
        p.confidence = 1.0
        if p.affects_hard_requirement and body.apply_to_requirement:
            _apply_conditional(db, user.id, p)
    elif body.action == "reject":
        p.status = "rejected"
    elif body.action == "correct":
        p.value = body.value
        p.status = "confirmed"
    elif body.action == "remove":
        db.delete(p)
    db.commit()
    return {"ok": True}


def _apply_conditional(db: Session, user_id: int, p: Preference) -> None:
    """Explicit user authorisation to change a hard requirement based on a hypothesis."""
    reqs = memory.get_requirements(db, user_id)
    if p.attribute == "target_salary_floor" and isinstance(p.value, dict):
        cur = reqs.get("min_salary") or {"currency": "GBP"}
        memory.set_requirement_trusted(db, user_id, "min_salary", {**cur, "amount": float(p.value["amount"])}, source="user_confirmed_hypothesis")
    elif p.attribute == "office_days_negotiable_above_salary" and isinstance(p.value, dict):
        # stored as a confirmed conditional rule; the base max_office_days requirement is unchanged
        memory.set_soft_preference(db, user_id, "office_days_exception", p.value, source="user_confirmed_hypothesis")


@router.delete("/profile/data")
def delete_data(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Delete all personal data for this user (DB rows and stored files)."""
    uid = user.id
    for model in (Document, Application, Feedback, Swipe, JobMatch, DiscoverySession, AgentRun, BackgroundTask, Preference,
                  Requirement, OnboardingMessage, SearchStrategy, CandidateFact, CandidateProfile, UploadedDocument):
        db.query(model).filter(model.user_id == uid).delete(synchronize_session=False)
    u = db.get(User, uid)
    u.onboarding_stage, u.llm_consent = "upload", False
    db.commit()
    s = get_settings().storage_path
    for sub in ("uploads", "applications"):
        shutil.rmtree(s / sub / str(int(uid)), ignore_errors=True)
    return {"deleted": True}
