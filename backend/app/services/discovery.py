"""Discovery: employer selection, candidate pool, persisted ranking, and validated batch creation."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agents import memory
from ..models import DiscoverySession, Employer, Job, JobMatch, SearchStrategy, Swipe, User, now
from . import matching
from .catalogue import discoverable_jobs_query
from .cv_parser import latest_profile

BATCH_MAX = 10


class BatchRejected(ValueError):
    pass


def ensure_initial_strategy(db: Session, user_id: int) -> SearchStrategy:
    s = memory.latest_strategy(db, user_id)
    if s:
        return s
    prefs = memory.get_soft_preferences(db, user_id)
    prof = latest_profile(db, user_id)
    titles = list(prefs.get("target_titles") or ((prof.data or {}).get("suggested_target_titles") if prof else []) or [])[:8]
    s = SearchStrategy(user_id=user_id, version=1, titles=titles, expanded_titles=[], weights={}, feature_adjustments={},
                       employer_ids=[], rationale="Initial strategy from your onboarding answers and CV.",
                       changes=[f"Searching for: {', '.join(titles)}"] if titles else [], created_by="system")
    db.add(s)
    db.flush()
    return s


def rank_employers(db: Session, user_id: int, limit: int, exclude: set[str] | None = None) -> list[Employer]:
    """Prioritise boards by known UK vacancy volume, preferred industries and recent success."""
    prefs = memory.get_soft_preferences(db, user_id)
    pref_ind = [i.lower() for i in ((prefs.get("industries") or {}).get("preferred") or [])] if isinstance(prefs.get("industries"), dict) else []
    avoid_ind = [i.lower() for i in ((prefs.get("industries") or {}).get("avoid") or [])] if isinstance(prefs.get("industries"), dict) else []
    emps = db.scalars(select(Employer).where(Employer.enabled.is_(True))).all()
    uk_counts: dict[str, int] = {}
    for eid, cnt in db.execute(
        select(Job.employer_id, Job.id).where(Job.active.is_(True), Job.country == "United Kingdom")
    ).all():
        uk_counts[eid] = uk_counts.get(eid, 0) + 1

    def score(e: Employer) -> float:
        s = 0.0
        ind = (e.industry or "").lower()
        if any(p in ind for p in pref_ind if p):
            s += 50
        if any(a in ind for a in avoid_ind if a):
            s -= 100
        s += min(40, uk_counts.get(e.id, e.uk_jobs_hint or 0))
        if e.last_error:
            s -= 20
        if e.last_success_at is None:
            s += 5  # unexplored
        return s

    emps = [e for e in emps if not exclude or e.id not in exclude]
    return sorted(emps, key=score, reverse=True)[:limit]


def reviewed_job_ids(db: Session, user_id: int) -> set[int]:
    return {jid for (jid,) in db.query(Swipe.job_id).filter(Swipe.user_id == user_id, Swipe.undone.is_(False))}


def candidate_pool(db: Session, user_id: int, employer_ids: list[str] | None = None) -> list[Job]:
    q = discoverable_jobs_query()
    if employer_ids:
        q = q.where(Job.employer_id.in_(employer_ids))
    reviewed = reviewed_job_ids(db, user_id)
    return [j for j in db.scalars(q).all() if j.id not in reviewed]


def matching_context(db: Session, user_id: int, strategy: SearchStrategy | None) -> tuple[dict, dict, dict]:
    reqs = memory.get_requirements(db, user_id)
    prefs = memory.get_soft_preferences(db, user_id)
    prof = latest_profile(db, user_id)
    emp_ind = {e.id: e.industry or "" for e in db.scalars(select(Employer))}
    ctx = matching.build_context(reqs, prefs, (prof.data if prof else {}) or {}, strategy, emp_ind)
    return reqs, prefs, ctx


def rank_pool(db: Session, user: User, strategy: SearchStrategy, jobs: list[Job], limit: int = 40) -> dict:
    reqs, prefs, ctx = matching_context(db, user.id, strategy)
    views = [matching.job_view(j) for j in jobs]
    excluded = {"ineligible": 0, "uncertain_hidden": 0}
    counts: dict[str, int] = {}
    ranked = []
    for v in views:
        e = matching.evaluate_eligibility(v, reqs)
        if e.status == "ineligible":
            excluded["ineligible"] += 1
            for viol in e.violations:
                counts[viol["code"]] = counts.get(viol["code"], 0) + 1
            continue
        if e.status == "uncertain" and not user.include_uncertain:
            excluded["uncertain_hidden"] += 1
            continue
        ranked.append((v, e, matching.score_job(v, ctx, e)))
    ranked.sort(key=lambda r: r[2]["score"], reverse=True)
    top = ranked[:limit]
    for v, e, sc in top:
        _persist_match(db, user.id, v, e, sc, strategy.id, ctx)
    db.flush()
    return {"evaluated": len(views), "eligible_or_flagged": len(ranked), "excluded": excluded, "violation_counts": counts,
            "shortlist": [{"job_id": v["id"], "title": v["title"], "employer": v["employer_name"], "score": sc["score"],
                           "eligibility": e.status, "family": v["features"].get("role_family")} for v, e, sc in top]}


def _persist_match(db: Session, user_id: int, v: dict, e: matching.Eligibility, sc: dict, strategy_id: int, ctx: dict) -> JobMatch:
    m = db.query(JobMatch).filter_by(user_id=user_id, job_id=v["id"]).one_or_none()
    if m is None:
        m = JobMatch(user_id=user_id, job_id=v["id"])
        db.add(m)
    explanation, concerns = matching.explain(v, sc, e, ctx)
    m.strategy_id = strategy_id
    m.eligibility = e.status
    m.eligibility_reasons = e.violations + [{"code": "note", "detail": n} for n in e.notes]
    m.uncertain_fields = e.uncertain
    m.score = sc["score"]
    m.breakdown = sc
    m.explanation = explanation
    m.concerns = concerns
    m.missing_info = sorted(set(e.missing_info))
    m.semantic = None
    m.updated_at = now()
    return m


def apply_semantic(db: Session, user_id: int, assessments: dict[str, dict]) -> int:
    from .semantic import blend

    n = 0
    for ref, a in assessments.items():
        try:
            jid = int(ref)
        except ValueError:
            continue
        m = db.query(JobMatch).filter_by(user_id=user_id, job_id=jid).one_or_none()
        if m is None:
            continue  # the model can only annotate jobs that passed deterministic eligibility
        m.semantic = a
        bd = dict(m.breakdown or {})
        bd["semantic_fit"] = a.get("fit_score")
        bd["deterministic_score"] = bd.get("score", m.score)
        m.score = blend(bd["deterministic_score"], a.get("fit_score"))
        bd["score"] = m.score
        m.breakdown = bd
        if a.get("why_relevant"):
            m.explanation = a["why_relevant"]
        extra = [c for c in (a.get("concerns") or []) + [f"Gap: {g}" for g in a.get("experience_gaps") or []] if c not in (m.concerns or [])]
        m.concerns = (m.concerns or []) + extra[:4]
        if a.get("injection_detected"):
            m.concerns = m.concerns + ["This listing contains text aimed at AI systems; it was ignored."]
        n += 1
    db.flush()
    return n


def create_batch(db: Session, user: User, job_ids: list[int], run_id: int | None, strategy_id: int | None,
                 session: DiscoverySession | None = None) -> DiscoverySession:
    """Validated in application code: the agent cannot put ineligible, closed, duplicate or reviewed jobs in a batch."""
    ids = list(dict.fromkeys(int(i) for i in job_ids))
    if len(ids) > BATCH_MAX:
        raise BatchRejected(f"a batch may contain at most {BATCH_MAX} jobs")
    reviewed = reviewed_job_ids(db, user.id)
    reqs = memory.get_requirements(db, user.id)
    for jid in ids:
        j = db.get(Job, jid)
        if j is None:
            raise BatchRejected(f"job {jid} does not exist")
        if not j.active or j.lifecycle != "open" or j.duplicate_of is not None:
            raise BatchRejected(f"job {jid} is not open or is a duplicate")
        if jid in reviewed:
            raise BatchRejected(f"job {jid} was already reviewed")
        e = matching.evaluate_eligibility(matching.job_view(j), reqs)  # re-checked here, not trusted from the caller
        if e.status == "ineligible":
            raise BatchRejected(f"job {jid} violates a confirmed requirement: {e.violations[0]['code']}")
        if e.status == "uncertain" and not user.include_uncertain:
            raise BatchRejected(f"job {jid} has uncertain eligibility and you chose to hide such jobs")
    ids.sort(key=lambda i: -(db.query(JobMatch.score).filter_by(user_id=user.id, job_id=i).scalar() or 0))
    if session is None:
        session = DiscoverySession(user_id=user.id)
        db.add(session)
    session.job_ids = ids
    session.status = "active" if ids else "empty"
    session.agent_run_id = run_id
    session.strategy_id = strategy_id
    db.flush()
    return session


def session_progress(db: Session, s: DiscoverySession) -> dict:
    decided = {}
    for sw in db.query(Swipe).filter(Swipe.session_id == s.id, Swipe.undone.is_(False), Swipe.superseded.is_(False)):
        decided[sw.job_id] = sw.decision
    remaining = [j for j in s.job_ids if j not in decided]
    return {"total": len(s.job_ids), "decided": decided, "remaining": remaining}
