"""Feedback interpretation and adaptive preference learning.

Learning is recomputed from persisted decisions every time (undo and decision changes are respected), so the
preference state is always explainable from evidence. Hypotheses never modify hard requirements: a hypothesis that
touches one is stored with a clarification question and waits for an explicit user confirmation.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ..agents import memory
from ..models import Feedback, Job, Preference, SearchStrategy, Swipe, now

MIN_SUPPORT = 2  # never generalise from a single decision
MAX_POINTS = 20.0

ADJACENT_TITLES = {
    "solutions_presales": ["Pre-Sales Engineer", "Solutions Consultant", "Sales Engineer", "Technical Pre-Sales Consultant"],
    "technical_account_management": ["Customer Success Engineer", "Technical Customer Success Manager", "Customer Engineer"],
    "customer_success": ["Customer Success Manager", "Technical Customer Success Manager"],
    "support": ["Technical Support Engineer", "Customer Support Engineer"],
    "sre_platform": ["Cloud Infrastructure Engineer", "DevOps Engineer", "Platform Engineer"],
    "software_engineering": ["Backend Engineer", "Software Engineer"],
    "data_ml": ["Data Engineer", "Analytics Engineer"],
    "security": ["Security Engineer", "Cloud Security Engineer"],
    "product": ["Technical Product Manager", "Product Manager"],
}
CUSTOMER_TECH_TITLES = ["Implementation Consultant", "Forward Deployed Engineer", "Solutions Consultant"]
FAMILY_LABEL = {
    "solutions_presales": "solutions / pre-sales", "technical_account_management": "technical account management",
    "customer_success": "customer success", "support": "technical support", "sre_platform": "SRE / platform",
    "software_engineering": "software engineering", "data_ml": "data / ML", "security": "security", "product": "product",
    "sales": "sales", "marketing": "marketing", "operations": "operations", "design": "design", "other": "other roles",
    "people_finance_legal": "people / finance / legal",
}
POSITIVE_REASONS = {"enjoy_customer_work", "good_tech_customer_mix", "great_salary", "great_flexibility"}


@dataclass
class Hypothesis:
    attribute: str
    value: Any
    points: float  # feature adjustment (ranking points); 0 for informational hypotheses
    confidence: float
    supporting: list[int] = field(default_factory=list)  # swipe ids
    contradicting: list[int] = field(default_factory=list)
    rationale: str = ""
    question: str | None = None
    affects_hard_requirement: str | None = None

    @property
    def actionable(self) -> bool:
        return self.points != 0 and len(self.supporting) >= MIN_SUPPORT and len(self.supporting) > 2 * len(self.contradicting)


@dataclass
class Analysis:
    decisions: int
    hypotheses: list[Hypothesis]
    accepted_families: dict[str, int]
    rejected_families: dict[str, int]


def _decisions(db: Session, user_id: int) -> list[dict]:
    rows = (
        db.query(Swipe, Job)
        .join(Job, Job.id == Swipe.job_id)
        .filter(Swipe.user_id == user_id, Swipe.undone.is_(False), Swipe.superseded.is_(False))
        .order_by(Swipe.id)
        .all()
    )
    fb = defaultdict(list)
    for f in db.query(Feedback).filter_by(user_id=user_id):
        fb[f.job_id].append(f)
    out = []
    for s, j in rows:
        reasons = set()
        comments = []
        for f in fb.get(j.id, []):
            reasons.update(f.reasons or [])
            if f.comment:
                comments.append(f.comment)
        out.append({"swipe_id": s.id, "decision": s.decision, "job": j, "features": j.features or {}, "reasons": reasons,
                    "comments": comments})
    return out


def analyse_decisions(decisions: list[dict]) -> Analysis:
    """Pure function over decision records (used by production code and evals)."""
    hyps: list[Hypothesis] = []
    yes = [d for d in decisions if d["decision"] == "yes"]
    no = [d for d in decisions if d["decision"] == "no"]
    acc_fam: dict[str, int] = defaultdict(int)
    rej_fam: dict[str, int] = defaultdict(int)
    for d in yes:
        acc_fam[d["features"].get("role_family", "other")] += 1
    for d in no:
        rej_fam[d["features"].get("role_family", "other")] += 1

    # --- on-call
    oc_no = [d["swipe_id"] for d in no if "requires_on_call" in d["reasons"] or (d["job"].on_call_required and not d["reasons"])]
    oc_no_explicit = [d["swipe_id"] for d in no if "requires_on_call" in d["reasons"]]
    oc_yes = [d["swipe_id"] for d in yes if d["job"].on_call_required]
    if oc_no_explicit or len(oc_no) >= MIN_SUPPORT:
        sup = sorted(set(oc_no_explicit) | set(oc_no))
        hyps.append(Hypothesis("on_call", "avoid", -min(MAX_POINTS, 8.0 * len(sup)), _conf(sup, oc_yes), sup, oc_yes,
                               f"Passed on {len(sup)} role(s) involving on-call"
                               + (f", but liked {len(oc_yes)} that had it" if oc_yes else "") + "."))

    # --- customer-facing work
    cf_yes = [d["swipe_id"] for d in yes if (d["features"].get("customer_facing") or 0) >= 0.6
              or d["reasons"] & {"enjoy_customer_work", "good_tech_customer_mix"}]
    cf_no = [d["swipe_id"] for d in no if (d["features"].get("customer_facing") or 0) >= 0.6 and "responsibilities_unsuitable" in d["reasons"]]
    if cf_yes:
        hyps.append(Hypothesis("customer_facing", "prefer", min(MAX_POINTS, 6.0 * len(cf_yes)), _conf(cf_yes, cf_no), cf_yes, cf_no,
                               f"Liked {len(cf_yes)} customer-facing technical role(s)."))

    # --- coding-heavy
    code_no = [d["swipe_id"] for d in no if "too_much_coding" in d["reasons"]]
    code_yes = [d["swipe_id"] for d in yes if (d["features"].get("coding_heavy") or 0) >= 0.6]
    if code_no:
        hyps.append(Hypothesis("coding_heavy", "avoid", -min(MAX_POINTS, 7.0 * len(code_no)), _conf(code_no, code_yes), code_no, code_yes,
                               f"Said 'too much coding' {len(code_no)} time(s)."))

    # --- travel
    tr_no = [d["swipe_id"] for d in no if "too_much_travel" in d["reasons"]]
    if tr_no:
        hyps.append(Hypothesis("travel", "avoid", -min(MAX_POINTS, 6.0 * len(tr_no)), _conf(tr_no, []), tr_no, [],
                               f"Passed on {len(tr_no)} role(s) because of travel."))

    # --- role families (boost liked families; mild, contradiction-aware penalty for repeatedly rejected ones)
    for fam, n in acc_fam.items():
        sup = [d["swipe_id"] for d in yes if d["features"].get("role_family") == fam]
        con = [d["swipe_id"] for d in no if d["features"].get("role_family") == fam]
        if fam != "other":
            hyps.append(Hypothesis(f"role_family:{fam}", "prefer", min(MAX_POINTS, 5.0 * len(sup)), _conf(sup, con), sup, con,
                                   f"Liked {len(sup)} {FAMILY_LABEL.get(fam, fam)} role(s)" + (f", passed on {len(con)}" if con else "") + "."))
    for fam, n in rej_fam.items():
        if fam in acc_fam or fam == "other":
            continue
        sup = [d["swipe_id"] for d in no if d["features"].get("role_family") == fam]
        # if the rejections are explained by a specific reason (e.g. on-call), don't blame the whole family
        explained = [d["swipe_id"] for d in no if d["features"].get("role_family") == fam and d["reasons"] & {"requires_on_call", "salary_too_low", "location_unsuitable", "too_many_office_days"}]
        if len(sup) >= 3 and len(explained) < len(sup):
            hyps.append(Hypothesis(f"role_family:{fam}", "less_interested", -min(10.0, 3.0 * (len(sup) - len(explained))), _conf(sup, []), sup, [],
                                   f"Passed on {len(sup)} {FAMILY_LABEL.get(fam, fam)} role(s)."))
        elif sup:
            hyps.append(Hypothesis(f"role_family:{fam}", "rejections_explained_by_other_reasons", 0.0, 0.3, sup, [],
                                   f"Passed on {len(sup)} {FAMILY_LABEL.get(fam, fam)} role(s), but for specific reasons "
                                   "(e.g. on-call) - not excluding the whole category."))

    # --- employers
    emp_no = defaultdict(list)
    for d in no:
        if "not_interested_in_employer" in d["reasons"]:
            emp_no[d["job"].employer_id].append(d["swipe_id"])
    for emp, sup in emp_no.items():
        hyps.append(Hypothesis(f"employer:{emp}", "avoid", -15.0, _conf(sup, []), sup, [],
                               f"Not interested in this employer ({len(sup)} time(s)).") if len(sup) >= 1 else None)
    hyps = [h for h in hyps if h]
    for h in hyps:
        if h.attribute.startswith("employer:"):
            h.supporting = h.supporting  # single explicit employer rejection is a direct statement, still needs confirmation

    # --- office days vs compensation (conditional, affects a hard requirement -> question only)
    od_no = [d for d in no if "too_many_office_days" in d["reasons"] and d["job"].office_days_per_week]
    for d_no in od_no:
        days = d_no["job"].office_days_per_week
        sal_no = d_no["job"].salary_max or d_no["job"].salary_min
        for d_yes in yes:
            jy = d_yes["job"]
            sal_yes = jy.salary_max or jy.salary_min
            if jy.office_days_per_week and jy.office_days_per_week >= days and sal_no and sal_yes and sal_yes > sal_no:
                threshold = round((sal_no + sal_yes) / 2 / 5000) * 5000
                hyps.append(Hypothesis(
                    "office_days_negotiable_above_salary", {"office_days": days, "salary_above": threshold}, 0.0,
                    0.5, [d_no["swipe_id"], d_yes["swipe_id"]], [],
                    f"Passed on a £{sal_no:,.0f} role for its {days:g} office days but liked a £{sal_yes:,.0f} role with the same.",
                    question=f"Would you consider {days:g} office days for roles paying above £{threshold:,.0f}?",
                    affects_hard_requirement="max_office_days"))
                break
    # --- salary signals (soft target, never the hard minimum)
    sal_no = [d for d in no if "salary_too_low" in d["reasons"] and (d["job"].salary_max or d["job"].salary_min)]
    if len(sal_no) >= MIN_SUPPORT:
        top = max(d["job"].salary_max or d["job"].salary_min for d in sal_no)
        hyps.append(Hypothesis("target_salary_floor", {"amount": top}, 0.0, _conf([d["swipe_id"] for d in sal_no], []),
                               [d["swipe_id"] for d in sal_no], [],
                               f"Said 'salary too low' for roles paying up to £{top:,.0f}.",
                               question=f"Should I raise your minimum salary above £{top:,.0f}?",
                               affects_hard_requirement="min_salary"))
    return Analysis(len(decisions), hyps, dict(acc_fam), dict(rej_fam))


def _conf(sup: list, con: list) -> float:
    return round(len(sup) / (len(sup) + len(con) + 1), 2)


def analyse(db: Session, user_id: int) -> Analysis:
    return analyse_decisions(_decisions(db, user_id))


def persist_hypotheses(db: Session, user_id: int, analysis: Analysis) -> list[Preference]:
    """Upsert inferred preferences. User-confirmed or rejected decisions are never overwritten."""
    existing = {p.attribute: p for p in db.query(Preference).filter_by(user_id=user_id, kind="inferred")}
    out = []
    seen = set()
    for h in analysis.hypotheses:
        seen.add(h.attribute)
        p = existing.get(h.attribute)
        if p and p.status in ("rejected",):
            continue
        if p is None:
            p = Preference(user_id=user_id, kind="inferred", attribute=h.attribute, status="proposed")
            db.add(p)
        if p.status != "confirmed":
            p.value = h.value
            p.weight = h.points
        p.confidence = h.confidence
        p.supporting_feedback_ids = h.supporting
        p.contradicting_feedback_ids = h.contradicting
        p.rationale = h.rationale
        p.question = h.question
        p.affects_hard_requirement = h.affects_hard_requirement
        p.updated_at = now()
        out.append(p)
    for attr, p in existing.items():  # evidence disappeared (e.g. undo) -> supersede unconfirmed hypotheses
        if attr not in seen and p.status == "proposed":
            p.status = "superseded"
    db.flush()
    return out


def propose_strategy(prev: SearchStrategy | None, analysis: Analysis, base_titles: list[str],
                     confirmed: dict[str, float]) -> dict:
    """Compute the next strategy (pure). `confirmed` = user-confirmed inferred adjustments (always applied)."""
    adjustments: dict[str, float] = {}
    for h in analysis.hypotheses:
        if h.actionable and h.affects_hard_requirement is None:
            adjustments[h.attribute] = round(h.points, 1)
    adjustments.update(confirmed)
    prev_titles = list(prev.titles) if prev else list(base_titles)
    prev_expanded = list(prev.expanded_titles) if prev else []
    expanded = list(prev_expanded)
    reasons = []
    for fam, n in sorted(analysis.accepted_families.items(), key=lambda kv: -kv[1]):
        if n >= MIN_SUPPORT or (n >= 1 and len(analysis.accepted_families) >= 2):
            for t in ADJACENT_TITLES.get(fam, []):
                if t not in expanded and t not in prev_titles:
                    expanded.append(t)
                    reasons.append(f"Added '{t}' (adjacent to {FAMILY_LABEL.get(fam, fam)} roles you liked)")
    if adjustments.get("customer_facing", 0) > 0:
        for t in CUSTOMER_TECH_TITLES:
            if t not in expanded and t not in prev_titles:
                expanded.append(t)
                reasons.append(f"Added '{t}' (customer-facing technical work you've liked)")
    expanded = expanded[:12]
    changes = []
    prev_adj = dict(prev.feature_adjustments) if prev else {}
    for k, v in adjustments.items():
        if prev_adj.get(k) != v:
            label = k.replace("role_family:", "").replace("_", " ")
            changes.append(f"{'Increased' if v > 0 else 'Reduced'} weighting for {label} ({v:+.0f} pts)")
    for k in prev_adj:
        if k not in adjustments:
            changes.append(f"Removed adjustment for {k.replace('_', ' ')} (evidence changed)")
    changes += reasons
    return {"titles": prev_titles, "expanded_titles": expanded, "feature_adjustments": adjustments, "changes": changes}


def apply_strategy(db: Session, user_id: int, proposal: dict, created_by: str, rationale: str | None = None) -> SearchStrategy | None:
    prev = memory.latest_strategy(db, user_id)
    if prev and prev.titles == proposal["titles"] and prev.expanded_titles == proposal["expanded_titles"] and \
            prev.feature_adjustments == proposal["feature_adjustments"]:
        return None  # nothing changed
    s = SearchStrategy(user_id=user_id, version=(prev.version + 1) if prev else 1, titles=proposal["titles"],
                       expanded_titles=proposal["expanded_titles"], keywords=proposal.get("keywords", []),
                       employer_ids=proposal.get("employer_ids", prev.employer_ids if prev else []),
                       weights=proposal.get("weights", prev.weights if prev else {}),
                       feature_adjustments=proposal["feature_adjustments"],
                       rationale=rationale or "; ".join(proposal["changes"][:4]), changes=proposal["changes"],
                       created_by=created_by)
    db.add(s)
    db.flush()
    return s


def confirmed_adjustments(db: Session, user_id: int) -> dict[str, float]:
    return {p.attribute: p.weight for p in db.query(Preference).filter_by(user_id=user_id, kind="inferred", status="confirmed")
            if p.weight and not p.affects_hard_requirement}
