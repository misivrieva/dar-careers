"""Request dependencies.

Local single-user demonstration mode: identity comes from the `X-DAR-User` header (default 1). This is NOT
authentication - it only scopes data so cross-user isolation can be tested. Do not deploy publicly without real auth.
"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Job, JobMatch, User


def current_user(x_dar_user: str | None = Header(default=None), db: Session = Depends(get_db)) -> User:
    try:
        uid = int(x_dar_user or 1)
    except ValueError:
        raise HTTPException(400, "invalid user header")
    if uid < 1 or uid > 1_000_000:
        raise HTTPException(400, "invalid user")
    u = db.get(User, uid)
    if u is None:
        u = User(id=uid)
        db.add(u)
        db.commit()
    return u


def job_card(db: Session, job: Job, user_id: int, full: bool = False) -> dict:
    m = db.query(JobMatch).filter_by(user_id=user_id, job_id=job.id).one_or_none()
    salary = None
    if job.salary_disclosed:
        salary = {"min": job.salary_min, "max": job.salary_max, "currency": job.salary_currency, "period": job.salary_period}
    d = {
        "id": job.id, "title": job.title, "employer": job.employer_name, "employer_id": job.employer_id, "provider": job.provider,
        "location": job.location_raw, "workplace_type": job.workplace_type, "remote_eligibility": job.remote_eligibility,
        "office_days": job.office_days_per_week, "employment_type": job.employment_type, "salary": salary,
        "seniority": job.seniority, "source_url": job.source_url, "application_url": job.application_url,
        "published_at": job.published_at.isoformat() if job.published_at else None,
        "last_seen_at": job.last_seen_at.isoformat() if job.last_seen_at else None,
        "lifecycle": job.lifecycle, "active": job.active, "is_synthetic": job.is_synthetic,
        "role_family": (job.features or {}).get("role_family"), "on_call": job.on_call_required,
        "field_provenance": job.field_provenance,
        "match": None,
    }
    if m:
        d["match"] = {"score": m.score, "eligibility": m.eligibility, "explanation": m.explanation, "concerns": m.concerns,
                      "missing_info": m.missing_info, "uncertain": m.uncertain_fields, "breakdown": m.breakdown,
                      "semantic": m.semantic, "notes": [r["detail"] for r in (m.eligibility_reasons or []) if r.get("code") == "note"]}
    if full:
        d["description_html"] = job.description_html
        d["description_text"] = job.description_text
        d["also_seen_at"] = job.also_seen_at
    return d
