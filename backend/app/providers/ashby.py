"""Ashby public job posting API.

GET https://api.ashbyhq.com/posting-api/job-board/{board_name}?includeCompensation=true
Verified response fields (2026-11): id, title, department, team, employmentType, location, secondaryLocations[],
isListed, isRemote, workplaceType, address.postalAddress, jobUrl, applyUrl, descriptionHtml, descriptionPlain,
publishedAt, compensation{compensationTiers[{components[{compensationType, interval, currencyCode, minValue, maxValue}]}]}.
"""
from __future__ import annotations

import asyncio
import hashlib

import httpx

from ..services.job_features import clean_html, html_to_text
from .base import FetchResult, JobProvider, ProviderError, get_json, parse_dt, raw_hash
from .normalise import finalise

COUNTRY_ALIASES = {"UK": "United Kingdom", "GB": "United Kingdom", "Great Britain": "United Kingdom", "USA": "United States",
                   "US": "United States"}


class AshbyProvider(JobProvider):
    name = "ashby"
    base_url = "https://api.ashbyhq.com/posting-api/job-board"

    async def fetch_board(self, client: httpx.AsyncClient, employer: dict) -> FetchResult:
        url = f"{self.base_url}/{employer['board_id']}?includeCompensation=true"
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
                if raw.get("isListed") is False:
                    res.skipped_unlisted += 1
                    continue
                res.jobs.append(self.normalise(raw, employer))
            except Exception:
                res.malformed += 1
        return res

    def normalise(self, raw: dict, employer: dict):
        if not raw.get("title"):
            raise ValueError("missing title")
        prov: dict[str, str] = {}
        ext_id = raw.get("id")
        if ext_id:
            prov["external_id"] = "provider"
        else:  # derive a stable key from verified source info, never invent
            basis = raw.get("jobUrl") or f"{raw.get('title')}|{raw.get('location')}|{raw.get('publishedAt')}"
            ext_id = "derived-" + hashlib.sha256(basis.encode()).hexdigest()[:20]
            prov["external_id"] = "derived"
        html = raw.get("descriptionHtml")
        text = raw.get("descriptionPlain") or html_to_text(html)
        addr = ((raw.get("address") or {}).get("postalAddress") or {})
        country = addr.get("addressCountry")
        country = COUNTRY_ALIASES.get(country, country) if country else None
        secondary = [s.get("location", "") for s in raw.get("secondaryLocations") or [] if isinstance(s, dict)]
        secondary_countries = {
            COUNTRY_ALIASES.get(c, c)
            for c in (((s.get("address") or {}).get("postalAddress") or {}).get("addressCountry") for s in raw.get("secondaryLocations") or [] if isinstance(s, dict))
            if c
        }
        location_raw = raw.get("location") or None
        if secondary:
            location_raw = f"{location_raw or ''}; also: {', '.join(secondary)}".strip("; ")
        wp = (raw.get("workplaceType") or "").lower() or None
        if wp == "onsite":
            wp = "onsite"
        if raw.get("isRemote") and not wp:
            wp = "remote"
        job = {
            "external_id": str(ext_id),
            "provider": "ashby",
            "employer_id": employer["id"],
            "employer_name": employer["name"],
            "title": raw["title"].strip(),
            "description_text": text,
            "description_html": clean_html(html),
            "location_raw": location_raw,
            "country": country,
            "department": raw.get("department"),
            "employment_type": {"FullTime": "full_time", "PartTime": "part_time", "Intern": "internship",
                                "Contract": "contract", "Temporary": "temporary"}.get(raw.get("employmentType") or "", None),
            "published_at": parse_dt(raw.get("publishedAt")),
            "updated_at": parse_dt(raw.get("updatedAt") or raw.get("publishedAt")),
            "source_url": raw.get("jobUrl"),
            "application_url": raw.get("applyUrl") or raw.get("jobUrl"),
            "_workplace_hint": wp,
        }
        if country:
            prov["country"] = "provider"
        if wp:
            job["workplace_type"] = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite"}.get(wp)
            prov["workplace_type"] = "provider"
            if job["workplace_type"] == "remote":
                countries = ({country} if country else set()) | secondary_countries
                if countries:
                    job["remote_eligibility"] = ", ".join(sorted(countries))
                    prov["remote_eligibility"] = "provider"
        if job["employment_type"]:
            prov["employment_type"] = "provider"
        if raw.get("team"):
            job["department"] = f"{raw.get('department') or ''} / {raw['team']}".strip(" /")
        # Compensation: base salary component only
        comp = raw.get("compensation") or {}
        for tier in comp.get("compensationTiers") or []:
            for c in tier.get("components") or []:
                if c.get("compensationType") == "Salary" and (c.get("minValue") or c.get("maxValue")):
                    interval = (c.get("interval") or "").upper()
                    job["salary_min"] = float(c["minValue"]) if c.get("minValue") else None
                    job["salary_max"] = float(c["maxValue"]) if c.get("maxValue") else job["salary_min"]
                    job["salary_currency"] = c.get("currencyCode")
                    job["salary_period"] = "year" if "YEAR" in interval else ("hour" if "HOUR" in interval else interval.lower() or None)
                    job["salary_disclosed"] = True
                    for k in ("salary_min", "salary_max", "salary_currency", "salary_period"):
                        prov[k] = "provider"
                    break
            if job.get("salary_disclosed"):
                break
        return finalise(job, prov, text or "", secondary)
