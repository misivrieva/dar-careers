"""Vacancy catalogue: concurrent board refresh, upsert, deduplication and lifecycle tracking."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Iterable

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Employer, Job, now
from ..providers.base import FetchResult, NormalisedJob
from ..providers.registry import get_provider

log = logging.getLogger("dar.catalogue")

JOB_FIELDS = [
    "employer_name", "title", "description_text", "description_html", "location_raw", "country", "city", "workplace_type",
    "remote_eligibility", "office_days_per_week", "employment_type", "department", "seniority", "salary_min", "salary_max",
    "salary_currency", "salary_period", "salary_disclosed", "published_at", "updated_at", "source_url", "application_url",
    "required_skills", "preferred_skills", "on_call_required", "features", "extraction_confidence", "field_provenance",
    "is_synthetic",
]


def _emp_dict(e: Employer) -> dict:
    return {"id": e.id, "name": e.name, "provider": e.provider, "board_id": e.board_id}


async def _fetch_all(employers: list[dict]) -> list[FetchResult]:
    s = get_settings()
    sem = asyncio.Semaphore(max(1, s.provider_concurrency))
    timeout = httpx.Timeout(s.provider_timeout_s, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async def one(emp: dict) -> FetchResult:
            prov = get_provider(emp["provider"])
            if prov is None:
                return FetchResult(employer_id=emp["id"], ok=False, error=f"unsupported provider {emp['provider']}")
            async with sem:
                try:
                    return await prov.fetch_board(client, emp)
                except Exception as e:  # never let one board break the batch
                    return FetchResult(employer_id=emp["id"], ok=False, error=f"{type(e).__name__}: {e}"[:300])

        return await asyncio.gather(*(one(e) for e in employers))


def refresh_boards(db: Session, employer_ids: Iterable[str], force: bool = False) -> dict:
    """Refresh boards; returns a summary with per-board outcomes. Partial failures are reported, not hidden."""
    s = get_settings()
    ids = list(dict.fromkeys(employer_ids))
    emps = [db.get(Employer, i) for i in ids]
    emps = [e for e in emps if e is not None and e.enabled]
    ttl = timedelta(seconds=s.provider_cache_ttl_s)
    to_fetch, cached = [], []
    for e in emps:
        if not force and e.last_success_at and now() - e.last_success_at < ttl:
            cached.append(e.id)
        else:
            to_fetch.append(e)
    results = asyncio.run(_fetch_all([_emp_dict(e) for e in to_fetch])) if to_fetch else []
    summary = {"requested": len(ids), "fetched": 0, "cached": len(cached), "failed": [], "new_jobs": 0, "updated_jobs": 0,
               "closed_candidates": 0, "unchanged_boards": 0, "jobs_seen": 0, "duplicates_merged": 0}
    for res in results:
        emp = db.get(Employer, res.employer_id)
        emp.last_attempt_at = now()
        if not res.ok:
            emp.last_error = res.error
            summary["failed"].append({"employer_id": emp.id, "employer": emp.name, "error": res.error})
            continue  # lifecycle untouched on failure: never close jobs because a provider timed out
        summary["fetched"] += 1
        emp.last_success_at = now()
        emp.last_error = None
        emp.last_job_count = len(res.jobs)
        unchanged = emp.content_hash is not None and emp.content_hash == res.raw_hash
        emp.content_hash = res.raw_hash
        summary["jobs_seen"] += len(res.jobs)
        if unchanged:
            summary["unchanged_boards"] += 1
            db.query(Job).filter(Job.employer_id == emp.id, Job.lifecycle != "closed").update({Job.last_seen_at: now()})
            continue
        st = upsert_jobs(db, emp, res.jobs)
        for k in ("new_jobs", "updated_jobs", "closed_candidates", "duplicates_merged"):
            summary[k] += st[k]
    db.commit()
    return summary


def upsert_jobs(db: Session, emp: Employer, jobs: list[NormalisedJob]) -> dict:
    st = {"new_jobs": 0, "updated_jobs": 0, "closed_candidates": 0, "duplicates_merged": 0}
    with db.no_autoflush:
        existing = {(j.provider, j.external_id): j for j in db.scalars(select(Job).where(Job.employer_id == emp.id))}
        seen = set()
        new_rows: list[Job] = []
        for nj in jobs:
            key = (nj.provider, nj.external_id)
            if key in seen:
                continue
            seen.add(key)
            h = nj.content_hash()
            row = existing.get(key)
            if row is None:
                row = Job(external_id=nj.external_id, provider=nj.provider, employer_id=emp.id, content_hash=h)
                for f in JOB_FIELDS:
                    setattr(row, f, getattr(nj, f))
                db.add(row)
                new_rows.append(row)
                st["new_jobs"] += 1
            else:
                if row.content_hash != h:
                    for f in JOB_FIELDS:
                        setattr(row, f, getattr(nj, f))
                    row.content_hash = h
                    st["updated_jobs"] += 1
                row.last_seen_at = now()
                row.missing_count = 0
                row.lifecycle = "open"
                row.active = True
        # Lifecycle: jobs missing from a SUCCESSFUL retrieval
        for key, row in existing.items():
            if key in seen or row.lifecycle == "closed":
                continue
            row.missing_count += 1
            if row.missing_count == 1:
                row.lifecycle = "potentially_closed"
                st["closed_candidates"] += 1
            else:
                row.lifecycle = "closed"
                row.active = False
    db.flush()
    if new_rows:
        titles = {r.title for r in new_rows}
        index: dict[str, list[Job]] = {}
        for c in db.scalars(select(Job).where(Job.title.in_(titles), Job.duplicate_of.is_(None), Job.active.is_(True),
                                              Job.employer_id != emp.id)):
            index.setdefault(c.title, []).append(c)
        for r in new_rows:
            if _dedupe(r, index.get(r.title, [])):
                st["duplicates_merged"] += 1
    return st


def _norm(s: str | None) -> str:
    s = (s or "").lower()
    s = re.sub(r"\b(inc|ltd|limited|plc|llc|corp|corporation|technologies|group)\b", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _similarity(a: str | None, b: str | None) -> float:
    ta = set(re.findall(r"[a-z0-9]{3,}", (a or "").lower()[:6000]))
    tb = set(re.findall(r"[a-z0-9]{3,}", (b or "").lower()[:6000]))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _dedupe(row: Job, cands: list[Job]) -> bool:
    """Cross-source duplicate detection. Title equality alone is never sufficient."""
    for c in cands:
        if c.id == row.id:
            continue
        if _norm(c.employer_name) != _norm(row.employer_name):
            continue
        if (c.provider, c.employer_id) == (row.provider, row.employer_id):
            continue  # same board: distinct postings (e.g. multiple locations) are kept
        same_url = bool(c.source_url and c.source_url == row.source_url)
        same_loc = _norm(c.location_raw) == _norm(row.location_raw) or (c.city and c.city == row.city)
        if same_url or (same_loc and _similarity(c.description_text, row.description_text) >= 0.85):
            row.duplicate_of = c.id
            c.also_seen_at = (c.also_seen_at or []) + [{"provider": row.provider, "employer_id": row.employer_id,
                                                         "external_id": row.external_id, "source_url": row.source_url}]
            return True
    return False


def discoverable_jobs_query():
    return select(Job).where(Job.active.is_(True), Job.lifecycle == "open", Job.duplicate_of.is_(None))
