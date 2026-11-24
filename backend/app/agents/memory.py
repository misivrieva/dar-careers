"""Shared agent memory: structured relational records (requirements, preferences, hypotheses, history).

Hard requirements can only be written through `set_requirement_trusted`, which is called exclusively from
user-initiated API routes (onboarding answers, profile review, explicit preference confirmation). Agent tools
have no code path to it.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models import Preference, Requirement, SearchStrategy, now

HARD_KEYS = {"min_salary", "locations", "max_office_days", "employment_types", "needs_sponsorship", "deal_breakers"}


def get_requirements(db: Session, user_id: int) -> dict[str, Any]:
    return {r.key: r.value for r in db.query(Requirement).filter_by(user_id=user_id, confirmed=True)}


def set_requirement_trusted(db: Session, user_id: int, key: str, value: Any, source: str = "user") -> Requirement | None:
    """Trusted user action only. `value=None` removes the requirement."""
    if key not in HARD_KEYS:
        raise ValueError(f"unknown requirement key {key}")
    r = db.query(Requirement).filter_by(user_id=user_id, key=key).one_or_none()
    if value is None:
        if r:
            db.delete(r)
        return None
    if r is None:
        r = Requirement(user_id=user_id, key=key)
        db.add(r)
    r.value = value
    r.source = source
    r.confirmed = True
    r.updated_at = now()
    return r


def get_soft_preferences(db: Session, user_id: int) -> dict[str, Any]:
    out = {}
    for p in db.query(Preference).filter_by(user_id=user_id, kind="soft", status="confirmed"):
        out[p.attribute] = p.value
    return out


def set_soft_preference(db: Session, user_id: int, attribute: str, value: Any, weight: float = 0.0, source="user") -> Preference:
    p = db.query(Preference).filter_by(user_id=user_id, kind="soft", attribute=attribute).one_or_none()
    if p is None:
        p = Preference(user_id=user_id, kind="soft", attribute=attribute)
        db.add(p)
    p.value, p.weight, p.status, p.confidence, p.rationale = value, weight, "confirmed", 1.0, f"Stated by user ({source})"
    p.updated_at = now()
    return p


def get_hypotheses(db: Session, user_id: int, include_rejected: bool = False) -> list[Preference]:
    q = db.query(Preference).filter_by(user_id=user_id, kind="inferred")
    if not include_rejected:
        q = q.filter(Preference.status.in_(["proposed", "confirmed"]))
    return q.order_by(Preference.updated_at.desc()).all()


def latest_strategy(db: Session, user_id: int) -> SearchStrategy | None:
    return db.query(SearchStrategy).filter_by(user_id=user_id).order_by(SearchStrategy.version.desc()).first()


def unknowns(reqs: dict, prefs: dict) -> list[str]:
    missing = []
    for k, label in [("min_salary", "minimum salary"), ("locations", "where you can work"),
                     ("employment_types", "employment type"), ("max_office_days", "maximum office days")]:
        if k not in reqs:
            missing.append(label)
    if "target_titles" not in prefs:
        missing.append("target roles")
    return missing
