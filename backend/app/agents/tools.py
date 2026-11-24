"""Explicit, schema-validated agent tools.

- Arguments are validated with Pydantic in application code, independent of the model.
- Permissions and hard constraints are enforced here, not by prompting.
- There is no tool for shell, filesystem, arbitrary HTTP, application submission, email, or hard-requirement edits.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import llm
from ..config import get_settings
from ..models import AgentRun, AgentToolCall, Application, Employer, Feedback, Job, JobMatch, Preference, Swipe, User, now
from . import memory


class ToolRejected(Exception):
    """Raised when a call is refused by a permission or constraint check."""


@dataclass
class RunContext:
    db: Session
    user: User
    run: AgentRun
    meter: llm.UsageMeter
    started: float = field(default_factory=time.monotonic)
    tool_calls: int = 0
    boards_refreshed: int = 0
    strategy_id: int | None = None
    shortlist: list[dict] = field(default_factory=list)
    batch_session_id: int | None = None
    batch_job_ids: list[int] = field(default_factory=list)
    clarifications: list[str] = field(default_factory=list)
    allowed: set[str] = field(default_factory=set)
    session: Any = None  # DiscoverySession being prepared

    def activity(self, text: str, kind: str = "info") -> None:
        self.run.activity = (self.run.activity or []) + [{"t": now().isoformat(timespec="seconds"), "text": text, "kind": kind}]
        self.db.flush()
        self.db.commit()  # make progress visible to polling clients immediately


@dataclass
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    fn: Callable[[RunContext, Any], dict]
    group: str


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, args: type[BaseModel], group: str):
    def deco(fn):
        REGISTRY[name] = Tool(name, description, args, fn, group)
        return fn
    return deco


class NoArgs(BaseModel):
    model_config = {"extra": "forbid"}


# =============================================================== profile tools
@tool("get_user_profile", "Get the candidate's verified CV facts (roles, skills, education) and AI interpretations.", NoArgs, "profile")
def _get_profile(ctx: RunContext, a: NoArgs) -> dict:
    from ..services.cv_parser import latest_profile

    p = latest_profile(ctx.db, ctx.user.id)
    if not p:
        return {"profile": None}
    d = p.data or {}
    return {"roles": [{"id": r["id"], "title": r["title"], "employer": r["employer"], "start": r.get("start"), "end": r.get("end"),
                       "highlights": [b["text"] for b in r.get("bullets", [])[:4]]} for r in d.get("roles", [])],
            "skills": d.get("technical_skills", [])[:40], "transferable_skills_ai": d.get("transferable_skills", []),
            "seniority_ai": d.get("seniority"), "suggested_titles_ai": d.get("suggested_target_titles", [])}


class AnalyseCVArgs(BaseModel):
    model_config = {"extra": "forbid"}
    document_id: int


@tool("analyse_cv", "Re-run structured extraction on one of the user's uploaded CV documents.", AnalyseCVArgs, "profile")
def _analyse_cv(ctx: RunContext, a: AnalyseCVArgs) -> dict:
    from ..models import UploadedDocument
    from ..services.cv_parser import build_profile

    doc = ctx.db.get(UploadedDocument, a.document_id)
    if doc is None or doc.user_id != ctx.user.id:
        raise ToolRejected("document not found for this user")
    p, rep = build_profile(ctx.db, ctx.user.id, doc, use_llm=get_settings().llm_available and ctx.user.llm_consent)
    return {"profile_version": p.version, "method": rep["method"], "rejected_items": len(rep["rejected"])}


class UpdateProfileArgs(BaseModel):
    model_config = {"extra": "forbid"}
    fields: dict[Literal["agent_notes", "suggested_target_titles"], list[str] | str]


@tool("update_user_profile", "Update agent-owned profile annotations (notes, suggested titles). Cannot change CV facts or requirements.",
      UpdateProfileArgs, "profile")
def _update_profile(ctx: RunContext, a: UpdateProfileArgs) -> dict:
    from ..services.cv_parser import latest_profile

    p = latest_profile(ctx.db, ctx.user.id)
    if not p:
        raise ToolRejected("no profile")
    d = dict(p.data or {})
    for k, v in a.fields.items():
        d[k] = v
    p.data = d
    return {"updated": list(a.fields)}


@tool("get_confirmed_requirements", "Get the user's confirmed hard requirements (must never be violated) and stated soft preferences.",
      NoArgs, "profile")
def _get_reqs(ctx: RunContext, a: NoArgs) -> dict:
    return {"hard_requirements": memory.get_requirements(ctx.db, ctx.user.id),
            "soft_preferences": memory.get_soft_preferences(ctx.db, ctx.user.id),
            "include_uncertain_jobs": ctx.user.include_uncertain}


@tool("get_preference_hypotheses", "Get inferred preference hypotheses with evidence, confidence and confirmation status.", NoArgs, "profile")
def _get_hyp(ctx: RunContext, a: NoArgs) -> dict:
    return {"hypotheses": [{"attribute": h.attribute, "value": h.value, "points": h.weight, "confidence": h.confidence,
                            "status": h.status, "supporting": len(h.supporting_feedback_ids or []),
                            "contradicting": len(h.contradicting_feedback_ids or []), "rationale": h.rationale}
                           for h in memory.get_hypotheses(ctx.db, ctx.user.id)]}


class ClarifyArgs(BaseModel):
    model_config = {"extra": "forbid"}
    question: str = Field(min_length=5, max_length=300)


@tool("request_user_clarification", "Ask the user one short question (shown on their dashboard). Does not pause discovery.", ClarifyArgs, "profile")
def _clarify(ctx: RunContext, a: ClarifyArgs) -> dict:
    if len(ctx.clarifications) >= 1:
        raise ToolRejected("only one clarification question per run")
    ctx.clarifications.append(a.question)
    ctx.activity(f"Asked you: {a.question}", "question")
    return {"queued": True}


# =============================================================== discovery tools
class ListBoardsArgs(BaseModel):
    model_config = {"extra": "forbid"}
    filters: dict[Literal["industry", "nasdaq100", "only_unrefreshed"], str | bool] = Field(default_factory=dict)
    limit: int = Field(30, ge=1, le=100)


@tool("list_employer_boards", "List enabled employer boards ranked by relevance to the user (UK volume, preferred industries).",
      ListBoardsArgs, "discovery")
def _list_boards(ctx: RunContext, a: ListBoardsArgs) -> dict:
    from ..services.discovery import rank_employers

    emps = rank_employers(ctx.db, ctx.user.id, limit=200)
    out = []
    for e in emps:
        if "industry" in a.filters and str(a.filters["industry"]).lower() not in (e.industry or "").lower():
            continue
        if a.filters.get("nasdaq100") is True and not e.nasdaq100:
            continue
        if a.filters.get("only_unrefreshed") is True and e.last_success_at is not None:
            continue
        out.append({"employer_id": e.id, "name": e.name, "provider": e.provider, "industry": e.industry, "nasdaq100": e.nasdaq100,
                    "last_job_count": e.last_job_count, "last_error": e.last_error,
                    "last_success_at": e.last_success_at.isoformat() if e.last_success_at else None})
        if len(out) >= a.limit:
            break
    total = ctx.db.query(Employer).filter(Employer.enabled.is_(True)).count()
    return {"boards": out, "enabled_total": total}


class RefreshOneArgs(BaseModel):
    model_config = {"extra": "forbid"}
    employer_id: str = Field(max_length=80)


class RefreshManyArgs(BaseModel):
    model_config = {"extra": "forbid"}
    employer_ids: list[str] = Field(min_length=1, max_length=40)
    force: bool = False


def _refresh(ctx: RunContext, ids: list[str], force: bool) -> dict:
    from ..services.catalogue import refresh_boards

    s = get_settings()
    known = {e.id for e in ctx.db.scalars(select(Employer).where(Employer.id.in_(ids), Employer.enabled.is_(True)))}
    unknown = [i for i in ids if i not in known]
    ids = [i for i in ids if i in known]
    room = s.agent_max_boards - ctx.boards_refreshed
    if room <= 0:
        raise ToolRejected(f"board budget exhausted ({s.agent_max_boards} per run)")
    ids = ids[:room]
    ctx.activity(f"Searching {len(ids)} employer boards on Ashby and Greenhouse…")
    summary = refresh_boards(ctx.db, ids, force=force)
    ctx.boards_refreshed += len(ids)
    failed = summary["failed"]
    total_live = ctx.db.query(Job).filter(Job.employer_id.in_(ids), Job.active.is_(True)).count()
    msg = (f"Searched {summary['fetched'] + summary['cached']} boards: {total_live} live vacancies"
           f"{f' ({summary['cached']} boards refreshed recently, reused from cache)' if summary['cached'] else ''}"
           f"{f', {summary['new_jobs']} new' if summary['new_jobs'] else ''}"
           f"{f', {summary['duplicates_merged']} duplicates merged' if summary['duplicates_merged'] else ''}"
           f"{f'; {len(failed)} board(s) unavailable' if failed else ''}.")
    ctx.activity(msg)
    return {**summary, "unknown_employer_ids": unknown, "failed": failed[:10]}


@tool("refresh_employer_board", "Retrieve live vacancies from one employer board.", RefreshOneArgs, "discovery")
def _refresh_one(ctx: RunContext, a: RefreshOneArgs) -> dict:
    return _refresh(ctx, [a.employer_id], False)


@tool("refresh_multiple_boards", "Retrieve live vacancies from several employer boards concurrently (cached boards are reused).",
      RefreshManyArgs, "discovery")
def _refresh_many(ctx: RunContext, a: RefreshManyArgs) -> dict:
    return _refresh(ctx, a.employer_ids, a.force)


class SearchArgs(BaseModel):
    model_config = {"extra": "forbid"}
    keywords: list[str] = Field(default_factory=list, max_length=20, description="Optional title keywords to narrow the pool")
    employer_ids: list[str] = Field(default_factory=list, max_length=100)
    limit: int = Field(25, ge=1, le=40)


@tool("search_local_jobs", "Filter the local vacancy catalogue with hard requirements, then rank by the current search strategy. "
      "Excludes ineligible, closed, duplicate and already-reviewed jobs. Returns a ranked shortlist.", SearchArgs, "discovery")
def _search(ctx: RunContext, a: SearchArgs) -> dict:
    import re

    from ..services.discovery import candidate_pool, rank_pool

    pool = candidate_pool(ctx.db, ctx.user.id, a.employer_ids or None)
    if a.keywords:
        rx = re.compile("|".join(re.escape(k) for k in a.keywords), re.I)
        pool = [j for j in pool if rx.search(j.title)]
    strategy = memory.latest_strategy(ctx.db, ctx.user.id)
    ctx.activity(f"Evaluating {len(pool)} potential opportunities against your requirements…")
    res = rank_pool(ctx.db, ctx.user, strategy, pool, limit=a.limit)
    ex = res["excluded"]
    top_v = sorted(res["violation_counts"].items(), key=lambda kv: -kv[1])[:3]
    ctx.activity(f"{res['eligible_or_flagged']} fit your hard requirements; excluded {ex['ineligible']} that didn't"
                 + (f" (mostly: {', '.join(k.replace('_', ' ') for k, _ in top_v)})" if top_v else "") + ".")
    ctx.shortlist = res["shortlist"]
    return res


class JobIdArgs(BaseModel):
    model_config = {"extra": "forbid"}
    job_id: int


@tool("get_job_details", "Get the normalised details of one vacancy (description truncated).", JobIdArgs, "discovery")
def _job_details(ctx: RunContext, a: JobIdArgs) -> dict:
    j = ctx.db.get(Job, a.job_id)
    if not j:
        raise ToolRejected("job not found")
    return {"id": j.id, "title": j.title, "employer": j.employer_name, "location": j.location_raw, "workplace_type": j.workplace_type,
            "office_days": j.office_days_per_week, "salary": [j.salary_min, j.salary_max, j.salary_currency] if j.salary_disclosed else None,
            "employment_type": j.employment_type, "on_call": j.on_call_required, "features": j.features,
            "description_excerpt_untrusted": (j.description_text or "")[:1500], "lifecycle": j.lifecycle}


class EvalArgs(BaseModel):
    model_config = {"extra": "forbid"}
    job_ids: list[int] = Field(min_length=1, max_length=15)
    profile_id: int | None = None


@tool("evaluate_job_match", "Run the semantic (LLM) assessment on up to 15 shortlisted jobs: responsibilities, transferable skills, "
      "gaps and concerns. Only jobs that already passed eligibility are scored.", EvalArgs, "discovery")
def _evaluate(ctx: RunContext, a: EvalArgs) -> dict:
    from ..services import semantic
    from ..services.cv_parser import latest_profile
    from ..services.discovery import apply_semantic
    from ..services.matching import job_view

    allowed = {m.job_id for m in ctx.db.query(JobMatch).filter(JobMatch.user_id == ctx.user.id, JobMatch.job_id.in_(a.job_ids))}
    ids = [i for i in a.job_ids if i in allowed]
    if not ids:
        raise ToolRejected("none of these jobs are on the eligible shortlist")
    if not (get_settings().llm_available and ctx.user.llm_consent):
        return {"skipped": True, "reason": "AI assessment unavailable (no API key or no consent); deterministic ranking kept."}
    jobs = [job_view(ctx.db.get(Job, i)) for i in ids]
    prof = latest_profile(ctx.db, ctx.user.id)
    strategy = memory.latest_strategy(ctx.db, ctx.user.id)
    ctx.activity(f"Reading {len(ids)} job descriptions in depth to check real responsibilities and fit…")
    res = semantic.assess(jobs, prof.data if prof else {}, memory.get_soft_preferences(ctx.db, ctx.user.id),
                          (strategy.titles + strategy.expanded_titles) if strategy else [], meter=ctx.meter)
    n = apply_semantic(ctx.db, ctx.user.id, res)
    inj = [r for r, x in res.items() if x.get("injection_detected")]
    if inj:
        ctx.activity(f"Ignored embedded AI instructions found in {len(inj)} listing(s).", "security")
    return {"assessed": n, "results": [{"job_id": r, "fit": x["fit_score"], "adjacent": x["adjacent_opportunity"],
                                        "concerns": x["concerns"][:2], "injection_detected": x["injection_detected"]}
                                       for r, x in res.items()]}


class BatchArgs(BaseModel):
    model_config = {"extra": "forbid"}
    job_ids: list[int] = Field(min_length=0, max_length=10)


@tool("create_discovery_batch", "Create the user's next discovery batch (at most 10 jobs, best first). Every job is re-validated "
      "against hard requirements in code. Fewer than 10 is fine if fewer are suitable.", BatchArgs, "discovery")
def _create_batch(ctx: RunContext, a: BatchArgs) -> dict:
    from ..services.discovery import BatchRejected, create_batch

    if ctx.batch_session_id is not None:
        raise ToolRejected("a batch was already created in this run")
    try:
        s = create_batch(ctx.db, ctx.user, a.job_ids, ctx.run.id, ctx.strategy_id, session=ctx.session)
    except BatchRejected as e:
        raise ToolRejected(str(e)) from e
    ctx.batch_session_id = s.id
    ctx.batch_job_ids = list(s.job_ids)
    ctx.activity(f"Prepared {len(s.job_ids)} opportunities for you to review." if s.job_ids else
                 "No new suitable opportunities right now - I won't pad the list with poor matches.", "result")
    return {"session_id": s.id, "jobs": len(s.job_ids)}


# =============================================================== learning tools
@tool("get_swipe_history", "Get the user's recent decisions (yes/no/maybe) with role families and feedback reasons.", NoArgs, "learning")
def _history(ctx: RunContext, a: NoArgs) -> dict:
    rows = (ctx.db.query(Swipe, Job).join(Job, Job.id == Swipe.job_id)
            .filter(Swipe.user_id == ctx.user.id, Swipe.undone.is_(False), Swipe.superseded.is_(False))
            .order_by(Swipe.id.desc()).limit(60).all())
    fb = {f.job_id: f for f in ctx.db.query(Feedback).filter_by(user_id=ctx.user.id)}
    return {"decisions": [{"job_id": j.id, "title": j.title, "employer": j.employer_name, "decision": s.decision,
                           "family": (j.features or {}).get("role_family"), "on_call": j.on_call_required,
                           "reasons": (fb[j.id].reasons if j.id in fb else []),
                           "comment_untrusted": (fb[j.id].comment[:200] if j.id in fb and fb[j.id].comment else None)}
                          for s, j in rows]}


@tool("get_job_feedback", "Get structured feedback and comments for one job.", JobIdArgs, "learning")
def _job_fb(ctx: RunContext, a: JobIdArgs) -> dict:
    return {"feedback": [{"reasons": f.reasons, "comment_untrusted": f.comment} for f in
                         ctx.db.query(Feedback).filter_by(user_id=ctx.user.id, job_id=a.job_id)]}


@tool("analyse_feedback", "Analyse all persisted decisions and feedback into evidence-backed preference hypotheses, and store them. "
      "Returns each hypothesis with support/contradiction counts and whether it is strong enough to act on.", NoArgs, "learning")
def _analyse(ctx: RunContext, a: NoArgs) -> dict:
    from ..services.feedback import analyse, persist_hypotheses

    an = analyse(ctx.db, ctx.user.id)
    persist_hypotheses(ctx.db, ctx.user.id, an)
    if an.decisions:
        ctx.activity(f"Reviewed {an.decisions} of your decisions and updated {len(an.hypotheses)} preference hypotheses.")
    return {"decisions": an.decisions, "accepted_families": an.accepted_families, "rejected_families": an.rejected_families,
            "hypotheses": [{"attribute": h.attribute, "value": h.value, "points": h.points, "confidence": h.confidence,
                            "support": len(h.supporting), "contradict": len(h.contradicting), "actionable": h.actionable,
                            "affects_hard_requirement": h.affects_hard_requirement, "rationale": h.rationale,
                            "question": h.question} for h in an.hypotheses]}


ALLOWED_PREF_ATTRS = ("on_call", "customer_facing", "coding_heavy", "travel", "management", "office_days")


class ProposePrefArgs(BaseModel):
    model_config = {"extra": "forbid"}
    preference: str = Field(max_length=80, description="e.g. 'customer_facing', 'on_call', 'role_family:solutions_presales'")
    value: str = Field(max_length=80)
    points: float = Field(ge=-20, le=20)
    evidence: list[int] = Field(min_length=1, max_length=50, description="Swipe ids supporting this")

    @field_validator("preference")
    @classmethod
    def _attr(cls, v: str) -> str:
        if v in memory.HARD_KEYS or v.startswith(("min_salary", "location", "max_office", "employment", "deal_breaker", "needs_")):
            raise ValueError("hard requirements cannot be changed by the agent")
        if not (v in ALLOWED_PREF_ATTRS or v.startswith(("role_family:", "employer:"))):
            raise ValueError(f"unknown preference attribute; allowed: {ALLOWED_PREF_ATTRS} or role_family:*/employer:*")
        return v


@tool("propose_preference_update", "Record an inferred soft-preference hypothesis backed by specific decisions. It stays 'proposed' "
      "until the user confirms it. Hard requirements cannot be proposed here.", ProposePrefArgs, "learning")
def _propose(ctx: RunContext, a: ProposePrefArgs) -> dict:
    own = {s.id for s in ctx.db.query(Swipe).filter(Swipe.user_id == ctx.user.id, Swipe.id.in_(a.evidence), Swipe.undone.is_(False))}
    if len(own) < 2:
        raise ToolRejected("at least two of the user's own current decisions are required as evidence")
    p = ctx.db.query(Preference).filter_by(user_id=ctx.user.id, kind="inferred", attribute=a.preference).one_or_none()
    if p and p.status == "rejected":
        raise ToolRejected("the user rejected this hypothesis previously")
    if p is None:
        p = Preference(user_id=ctx.user.id, kind="inferred", attribute=a.preference, status="proposed")
        ctx.db.add(p)
    if p.status != "confirmed":
        p.value, p.weight = a.value, a.points
    p.supporting_feedback_ids = sorted(own)
    p.confidence = round(len(own) / (len(own) + 1), 2)
    p.rationale = p.rationale or f"Proposed by the agent from {len(own)} decisions."
    ctx.activity(f"Updated preference hypothesis: {a.preference.replace('role_family:', '').replace('_', ' ')} → {a.value}.")
    return {"preference_id": p.id if p.id else None, "status": p.status}


class StrategyArgs(BaseModel):
    model_config = {"extra": "forbid"}
    titles: list[str] = Field(min_length=1, max_length=12)
    expanded_titles: list[str] = Field(default_factory=list, max_length=12)
    feature_adjustments: dict[str, float] = Field(default_factory=dict)
    rationale: str = Field(max_length=600)

    @field_validator("feature_adjustments")
    @classmethod
    def _adj(cls, v: dict[str, float]) -> dict[str, float]:
        for k, x in v.items():
            if k in memory.HARD_KEYS or k.startswith(("min_salary", "location", "max_office", "employment", "deal_breaker", "needs_")):
                raise ValueError("hard requirements cannot be relaxed or changed through strategy")
            if not (k in ALLOWED_PREF_ATTRS or k.startswith(("role_family:", "employer:"))):
                raise ValueError(f"unknown adjustment {k}")
            if not -20 <= x <= 20:
                raise ValueError("adjustments must be within ±20 points")
        return v


@tool("update_search_strategy", "Save a new search strategy version: target titles, adjacent titles to explore and soft ranking "
      "adjustments. Cannot alter hard requirements. Unchanged strategies are not re-saved.", StrategyArgs, "learning")
def _update_strategy(ctx: RunContext, a: StrategyArgs) -> dict:
    from ..services.feedback import apply_strategy, confirmed_adjustments

    prev = memory.latest_strategy(ctx.db, ctx.user.id)
    adj = {**a.feature_adjustments, **confirmed_adjustments(ctx.db, ctx.user.id)}  # user-confirmed always win
    for k, v in adj.items():
        h = ctx.db.query(Preference).filter_by(user_id=ctx.user.id, kind="inferred", attribute=k, status="rejected").first()
        if h:
            raise ToolRejected(f"the user rejected the '{k}' hypothesis")
    changes = []
    prev_adj = prev.feature_adjustments if prev else {}
    for k, v in adj.items():
        if prev_adj.get(k) != v:
            changes.append(f"{'Increased' if v > 0 else 'Reduced'} weighting for {k.replace('role_family:', '').replace('_', ' ')} ({v:+.0f} pts)")
    for t in a.expanded_titles:
        if not prev or t not in prev.expanded_titles:
            changes.append(f"Expanded search to '{t}'")
    for t in a.titles:
        if not prev or t not in prev.titles:
            changes.append(f"Added target title '{t}'")
    s = apply_strategy(ctx.db, ctx.user.id, {"titles": a.titles, "expanded_titles": a.expanded_titles,
                                             "feature_adjustments": adj, "changes": changes}, "agent", a.rationale)
    if s is None:
        return {"changed": False}
    ctx.strategy_id = s.id
    if changes:
        ctx.activity("Adapted search strategy: " + "; ".join(changes[:4]) + ".", "adaptation")
    return {"changed": True, "version": s.version, "changes": changes}


# =============================================================== application tools
@tool("enqueue_application_generation", "Queue tailored CV and cover letter preparation for a job the USER marked Yes.", JobIdArgs, "application")
def _enqueue(ctx: RunContext, a: JobIdArgs) -> dict:
    from ..services.applications import enqueue

    yes = ctx.db.query(Swipe).filter_by(user_id=ctx.user.id, job_id=a.job_id, decision="yes", undone=False, superseded=False).first()
    if not yes:
        raise ToolRejected("applications are only prepared for jobs the user marked Yes")
    app = enqueue(ctx.db, ctx.user.id, a.job_id)
    return {"application_id": app.id, "prep_status": app.prep_status}


@tool("get_application_status", "Get preparation and submission status for one job's application.", JobIdArgs, "application")
def _app_status(ctx: RunContext, a: JobIdArgs) -> dict:
    app = ctx.db.query(Application).filter_by(user_id=ctx.user.id, job_id=a.job_id).one_or_none()
    return {"exists": bool(app), "prep_status": app.prep_status if app else None, "app_status": app.app_status if app else None}


# =============================================================== progress tools
@tool("get_shortlisted_jobs", "Jobs the user marked Yes.", NoArgs, "progress")
def _shortlisted(ctx: RunContext, a: NoArgs) -> dict:
    return {"jobs": [s.job_id for s in ctx.db.query(Swipe).filter_by(user_id=ctx.user.id, decision="yes", undone=False, superseded=False)]}


@tool("get_pending_applications", "Applications not yet ready for review.", NoArgs, "progress")
def _pending(ctx: RunContext, a: NoArgs) -> dict:
    return {"applications": [{"job_id": x.job_id, "prep_status": x.prep_status} for x in
                             ctx.db.query(Application).filter(Application.user_id == ctx.user.id,
                                                              Application.prep_status.in_(["pending", "generating", "failed", "needs_correction"]))]}


@tool("get_jobs_to_revisit", "Jobs the user marked Maybe.", NoArgs, "progress")
def _revisit(ctx: RunContext, a: NoArgs) -> dict:
    return {"jobs": [s.job_id for s in ctx.db.query(Swipe).filter_by(user_id=ctx.user.id, decision="maybe", undone=False, superseded=False)]}


@tool("recommend_next_action", "Compute the single most useful next action for the user.", NoArgs, "progress")
def _next_action(ctx: RunContext, a: NoArgs) -> dict:
    from ..services.progress import next_action

    return next_action(ctx.db, ctx.user)


@tool("schedule_discovery_refresh", "Schedule a background discovery refresh. Requires the user's opt-in to scheduled activity.",
      NoArgs, "progress")
def _schedule(ctx: RunContext, a: NoArgs) -> dict:
    if not ctx.user.scheduled_refresh_opt_in:
        raise ToolRejected("the user has not opted in to scheduled activity")
    from ..workers.tasks import enqueue_task

    t = enqueue_task(ctx.db, ctx.user.id, "refresh", {}, idem=f"refresh-{ctx.user.id}-{now():%Y%m%d}")
    return {"task_id": t.id}


# =============================================================== executor
def sanitise_args(args: dict | None) -> dict:
    if not isinstance(args, dict):
        return {}
    out = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 300:
            v = v[:300] + "…"
        if isinstance(v, list) and len(v) > 30:
            v = v[:30] + ["…"]
        out[k] = v
    return out


def execute(ctx: RunContext, name: str, raw_args: dict | None, parse_error: str | None = None) -> tuple[str, dict]:
    """Validate and run one tool call; every call (including rejections) is persisted to agent_tool_calls."""
    s = get_settings()
    t0 = time.monotonic()
    status, result = "ok", {}
    try:
        if ctx.tool_calls >= s.agent_max_tool_calls:
            raise ToolRejected(f"tool-call budget exhausted ({s.agent_max_tool_calls})")
        if time.monotonic() - ctx.started > s.agent_timeout_s:
            raise ToolRejected("run time budget exhausted")
        ctx.tool_calls += 1
        tool_ = REGISTRY.get(name)
        if tool_ is None or (ctx.allowed and name not in ctx.allowed):
            raise ToolRejected(f"tool '{name}' is not available")
        if parse_error:
            raise ToolRejected(parse_error)
        try:
            args = tool_.args.model_validate(raw_args or {})
        except ValidationError as e:
            raise ToolRejected("invalid arguments: " + "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:4]))
        result = tool_.fn(ctx, args)
    except ToolRejected as e:
        status, result = "rejected", {"error": str(e)}
    except llm.BudgetExceeded as e:
        status, result = "rejected", {"error": str(e)}
    except Exception as e:  # tool failure is reported to the agent, not hidden
        status, result = "error", {"error": f"{type(e).__name__}: {str(e)[:300]}"}
    ctx.db.add(AgentToolCall(run_id=ctx.run.id, tool=name, arguments=sanitise_args(raw_args), status=status,
                             result_summary=_summarise(result), duration_ms=int((time.monotonic() - t0) * 1000)))
    ctx.db.flush()
    return status, result


def _summarise(result: dict) -> str:
    import json

    s = json.dumps(result, default=str)
    return s[:600]


def tool_definitions(names: set[str]) -> list[dict]:
    out = []
    for n in sorted(names):
        t = REGISTRY[n]
        schema = t.args.model_json_schema()
        schema.pop("title", None)
        out.append({"name": t.name, "description": t.description, "parameters": schema})
    return out


DISCOVERY_TOOLS = {
    "get_user_profile", "get_confirmed_requirements", "get_preference_hypotheses", "request_user_clarification",
    "list_employer_boards", "refresh_employer_board", "refresh_multiple_boards", "search_local_jobs", "get_job_details",
    "evaluate_job_match", "create_discovery_batch", "get_swipe_history", "get_job_feedback", "analyse_feedback",
    "propose_preference_update", "update_search_strategy", "get_application_status", "recommend_next_action",
}
