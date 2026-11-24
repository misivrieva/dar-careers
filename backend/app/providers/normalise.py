"""Shared normalisation: provider facts first, then deterministic extraction from text; unknown stays None."""
from __future__ import annotations

from typing import Any

from ..services import job_features as jf
from .base import NormalisedJob


def finalise(job: dict[str, Any], provenance: dict[str, str], text: str, secondary_locations: list[str] | None = None) -> NormalisedJob:
    prov = dict(provenance)
    # Location / workplace
    loc = jf.parse_location(job.get("location_raw"), job.pop("_workplace_hint", None), text)
    for k, v in loc.items():
        if job.get(k) is None and v is not None:
            job[k] = v
            prov.setdefault(k, "extracted")
    if job.get("country") in (None, "Multiple") and secondary_locations:
        if jf.uk_presence(None, secondary_locations):
            job["uk_secondary"] = True
    # Remote eligibility from text, only when explicit
    if job.get("workplace_type") == "remote" and job.get("remote_eligibility") is None:
        import re

        m = re.search(r"remote[^.\n]{0,40}\b(uk|united kingdom|emea|europe|us|united states|anywhere)\b", text, re.I)
        if m:
            val = m.group(1).upper()
            job["remote_eligibility"] = {"UNITED KINGDOM": "UK", "UNITED STATES": "US"}.get(val, val)
            prov["remote_eligibility"] = "extracted"
    if job.get("workplace_type") is None:
        import re

        if re.search(r"\bhybrid\b", text[:3000], re.I):
            job["workplace_type"] = "hybrid"
            prov["workplace_type"] = "extracted"
    # Office days
    od = jf.parse_office_days(text)
    if od is not None:
        job["office_days_per_week"] = od
        prov["office_days_per_week"] = "extracted"
    elif job.get("workplace_type") == "remote":
        job["office_days_per_week"] = 0.0
        prov["office_days_per_week"] = "provider" if prov.get("workplace_type") == "provider" else "extracted"
    elif job.get("workplace_type") == "onsite":
        job["office_days_per_week"] = 5.0
        prov["office_days_per_week"] = "extracted"
    # Salary from text if provider did not supply it
    if not job.get("salary_disclosed"):
        sal = jf.parse_salary_text(text)
        if sal:
            job.update(sal)
            job["salary_disclosed"] = True
            for k in sal:
                prov[k] = "extracted"
    # Employment type
    if job.get("employment_type") is None:
        et = jf.parse_employment_type(None, text)
        if et:
            job["employment_type"] = et
            prov["employment_type"] = "extracted"
    job["seniority"] = jf.parse_seniority(job["title"])
    prov["seniority"] = "extracted"
    feats = jf.compute_features(job["title"], text)
    if job.get("uk_secondary"):
        feats["uk_secondary_location"] = True
    job.pop("uk_secondary", None)
    job["features"] = feats
    job["on_call_required"] = feats["on_call"]
    prov["on_call_required"] = "extracted" if feats["on_call"] is not None else "unknown"
    job["required_skills"] = feats["skills"]
    prov["required_skills"] = "extracted"
    for key in ("salary_min", "country", "office_days_per_week", "employment_type", "remote_eligibility"):
        prov.setdefault(key, "unknown")
    known = sum(1 for k in ("country", "workplace_type", "salary_min", "employment_type", "office_days_per_week") if job.get(k) is not None)
    job["extraction_confidence"] = round(0.3 + 0.14 * known, 2)
    job["field_provenance"] = prov
    return NormalisedJob(**job)
