"""Greenhouse Job Board API (public, unauthenticated; no Harvest, no application submission).

GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true
GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}?pay_transparency=true
Verified fields (2026-11): id, title, location.name, absolute_url, updated_at, first_published, content (HTML-escaped),
departments[], offices[], metadata[], company_name; detail endpoint adds pay_input_ranges when published.
"""
from __future__ import annotations

import asyncio

import httpx

from ..services.job_features import clean_html, html_to_text
from .base import FetchResult, JobProvider, ProviderError, get_json, parse_dt, raw_hash
from .normalise import finalise


class GreenhouseProvider(JobProvider):
    name = "greenhouse"
    base_url = "https://boards-api.greenhouse.io/v1/boards"

    async def fetch_board(self, client: httpx.AsyncClient, employer: dict) -> FetchResult:
        url = f"{self.base_url}/{employer['board_id']}/jobs?content=true"
        try:
            data = await get_json(client, url)
        except ProviderError as e:
            return FetchResult(employer_id=employer["id"], ok=False, error=str(e), http_status=e.status)
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            return FetchResult(employer_id=employer["id"], ok=False, error="malformed response: missing jobs[]")
        return await asyncio.to_thread(self._normalise_all, data, employer)

    def _normalise_all(self, data: dict, employer: dict) -> FetchResult:
        res = FetchResult(employer_id=employer["id"], ok=True, raw_hash=raw_hash(data))
        for raw in data["jobs"]:
            try:
                res.jobs.append(self.normalise(raw, employer))
            except Exception:
                res.malformed += 1
        return res

    async def fetch_job(self, client: httpx.AsyncClient, employer: dict, external_id: str):
        url = f"{self.base_url}/{employer['board_id']}/jobs/{external_id}?pay_transparency=true"
        raw = await get_json(client, url)
        return self.normalise(raw, employer)

    def normalise(self, raw: dict, employer: dict):
        if not raw.get("id") or not raw.get("title"):
            raise ValueError("missing id/title")
        prov = {"external_id": "provider"}
        html = clean_html(raw.get("content"))
        text = html_to_text(raw.get("content"))
        location = (raw.get("location") or {}).get("name") or None
        offices = [o.get("name") for o in raw.get("offices") or [] if o.get("name")]
        metadata = {m.get("name"): m.get("value") for m in raw.get("metadata") or [] if isinstance(m, dict) and m.get("name")}
        job = {
            "external_id": str(raw["id"]),
            "provider": "greenhouse",
            "employer_id": employer["id"],
            "employer_name": employer["name"],
            "title": raw["title"].strip(),
            "description_text": text,
            "description_html": html,
            "location_raw": location if not offices or location in offices else f"{location}; offices: {', '.join(offices)}",
            "department": ", ".join(d.get("name") for d in raw.get("departments") or [] if d.get("name")) or None,
            "published_at": parse_dt(raw.get("first_published")),
            "updated_at": parse_dt(raw.get("updated_at")),
            "source_url": raw.get("absolute_url"),
            "application_url": raw.get("absolute_url"),
        }
        # Employer-defined metadata (e.g. "Employment Type", "Workplace Type") - provider facts when present
        for k, v in metadata.items():
            kl = (k or "").lower()
            if isinstance(v, str):
                if "employment" in kl and "type" in kl:
                    from ..services.job_features import parse_employment_type

                    et = parse_employment_type(v)
                    if et:
                        job["employment_type"], prov["employment_type"] = et, "provider"
                if ("workplace" in kl or "remote" in kl or "location type" in kl) and v:
                    vl = v.lower()
                    wp = "remote" if "remote" in vl else "hybrid" if "hybrid" in vl else "onsite" if ("office" in vl or "site" in vl) else None
                    if wp:
                        job["_workplace_hint"], prov["workplace_type"] = wp, "provider"
        # Pay transparency ranges (detail endpoint) - explicit only
        for r in raw.get("pay_input_ranges") or []:
            lo, hi = r.get("min_cents"), r.get("max_cents")
            if lo or hi:
                job.update({
                    "salary_min": lo / 100 if lo else None, "salary_max": hi / 100 if hi else None,
                    "salary_currency": r.get("currency_type"), "salary_period": "year", "salary_disclosed": True,
                })
                for k in ("salary_min", "salary_max", "salary_currency", "salary_period"):
                    prov[k] = "provider"
                break
        return finalise(job, prov, text, offices)
