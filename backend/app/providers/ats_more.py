"""Additional employer ATS providers (public, unauthenticated posting APIs; verified 2026-11-24).

- Lever:           GET https://api.lever.co/v0/postings/{site}?mode=json
- Workable:        GET https://apply.workable.com/api/v1/widget/accounts/{account}?details=true
- SmartRecruiters: GET https://api.smartrecruiters.com/v1/companies/{id}/postings?country=gb  (+ per-posting detail)
- Recruitee:       GET https://{slug}.recruitee.com/api/offers/

Same contract as Ashby/Greenhouse: provider facts first, deterministic extraction second, unknown stays None.
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone

import httpx

from ..services.job_features import clean_html, html_to_text
from .base import FetchResult, JobProvider, ProviderError, get_json, parse_dt, raw_hash
from .normalise import finalise

COUNTRY = {"GB": "United Kingdom", "UK": "United Kingdom", "US": "United States", "IE": "Ireland", "DE": "Germany", "FR": "France",
           "NL": "Netherlands", "ES": "Spain", "PL": "Poland", "IN": "India", "CA": "Canada", "AU": "Australia", "SG": "Singapore"}
EMP = {"full-time": "full_time", "full time": "full_time", "fulltime": "full_time", "permanent": "full_time", "part-time": "part_time",
       "part time": "part_time", "contract": "contract", "contractor": "contract", "temporary": "temporary", "internship": "internship",
       "intern": "internship", "fulltime_permanent": "full_time", "fulltime_fixed_term": "contract", "parttime_permanent": "part_time"}


def _emp(v: str | None) -> str | None:
    return EMP.get((v or "").strip().lower()) if v else None


def _from_ms(ms) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


class _Base(JobProvider):
    def _base(self, employer: dict, ext_id, title: str, text: str, html: str | None, **kw) -> dict:
        prov = {"external_id": "provider"}
        if not ext_id:
            ext_id = "derived-" + hashlib.sha256(f"{title}|{kw.get('location_raw')}|{kw.get('source_url')}".encode()).hexdigest()[:20]
            prov["external_id"] = "derived"
        job = {"external_id": str(ext_id), "provider": self.name, "employer_id": employer["id"], "employer_name": employer["name"],
               "title": title.strip(), "description_text": text, "description_html": clean_html(html) if html else None}
        for k, v in kw.items():
            if v is not None:
                job[k] = v
                if k in ("country", "employment_type", "workplace_type", "salary_min"):
                    prov[k] = "provider"
        return job, prov

    async def _done(self, employer, data, jobs, malformed=0) -> FetchResult:
        res = FetchResult(employer_id=employer["id"], ok=True, raw_hash=raw_hash(data), malformed=malformed)
        res.jobs = jobs
        return res


class LeverProvider(_Base):
    name = "lever"

    async def fetch_board(self, client: httpx.AsyncClient, employer: dict) -> FetchResult:
        try:
            data = await get_json(client, f"https://api.lever.co/v0/postings/{employer['board_id']}?mode=json")
        except ProviderError as e:
            return FetchResult(employer_id=employer["id"], ok=False, error=str(e), http_status=e.status)
        if not isinstance(data, list):
            return FetchResult(employer_id=employer["id"], ok=False, error="malformed response: expected a list")
        return await asyncio.to_thread(self._all, data, employer)

    def _all(self, data, employer):
        jobs, bad = [], 0
        for p in data:
            try:
                cat = p.get("categories") or {}
                html = (p.get("description") or "") + "".join(f"<h3>{l.get('text')}</h3>{l.get('content')}" for l in p.get("lists") or []) + (p.get("additional") or "")
                text = html_to_text(html) or p.get("descriptionPlain") or ""
                locs = cat.get("allLocations") or [cat.get("location")]
                wp = (p.get("workplaceType") or "").lower() or None
                job, prov = self._base(employer, p.get("id"), p["text"], text, html, location_raw="; ".join(l for l in locs if l) or None,
                                       country=COUNTRY.get((p.get("country") or "").upper()),
                                       employment_type=_emp(cat.get("commitment")), department=cat.get("team") or cat.get("department"),
                                       published_at=_from_ms(p.get("createdAt")), source_url=p.get("hostedUrl"),
                                       application_url=p.get("applyUrl") or p.get("hostedUrl"),
                                       _workplace_hint={"onsite": "onsite", "on-site": "onsite"}.get(wp, wp))
                sr = p.get("salaryRange") or {}
                if sr.get("min") or sr.get("max"):
                    iv = (sr.get("interval") or "").lower()
                    job.update(salary_min=sr.get("min"), salary_max=sr.get("max"), salary_currency=sr.get("currency"),
                               salary_period="year" if "year" in iv or "annual" in iv else iv or None, salary_disclosed=True)
                    prov.update({k: "provider" for k in ("salary_min", "salary_max", "salary_currency")})
                jobs.append(finalise(job, prov, text))
            except Exception:
                bad += 1
        res = FetchResult(employer_id=employer["id"], ok=True, raw_hash=raw_hash(data), malformed=bad)
        res.jobs = jobs
        return res


class WorkableProvider(_Base):
    name = "workable"

    async def fetch_board(self, client, employer) -> FetchResult:
        try:
            data = await get_json(client, f"https://apply.workable.com/api/v1/widget/accounts/{employer['board_id']}?details=true")
        except ProviderError as e:
            return FetchResult(employer_id=employer["id"], ok=False, error=str(e), http_status=e.status)
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            return FetchResult(employer_id=employer["id"], ok=False, error="malformed response: missing jobs[]")
        return await asyncio.to_thread(self._all, data, employer)

    def _all(self, data, employer):
        jobs, bad = [], 0
        for p in data["jobs"]:
            try:
                html = p.get("description") or ""
                text = html_to_text(html)
                locs = p.get("locations") or [{"city": p.get("city"), "country": p.get("country")}]
                loc_raw = "; ".join(", ".join(x for x in (l.get("city"), l.get("region"), l.get("country")) if x) for l in locs) or None
                countries = {l.get("country") for l in locs if l.get("country")}
                remote = str(p.get("telecommuting")).lower() == "true"
                job, prov = self._base(employer, p.get("shortcode"), p["title"], text, html, location_raw=loc_raw,
                                       country=("United Kingdom" if countries == {"United Kingdom"} else (next(iter(countries)) if len(countries) == 1 else None)),
                                       employment_type=_emp(p.get("employment_type")), department=p.get("department"),
                                       published_at=parse_dt(p.get("published_on")), source_url=p.get("url") or p.get("shortlink"),
                                       application_url=p.get("application_url"), _workplace_hint="remote" if remote else None)
                if remote and countries:
                    job["remote_eligibility"] = ", ".join(sorted(countries))
                    prov["remote_eligibility"] = "provider"
                jobs.append(finalise(job, prov, text))
            except Exception:
                bad += 1
        res = FetchResult(employer_id=employer["id"], ok=True, raw_hash=raw_hash(data), malformed=bad)
        res.jobs = jobs
        return res


class SmartRecruitersProvider(_Base):
    """Lists UK postings only (country=gb) and fetches each posting's detail for the description (capped)."""
    name = "smartrecruiters"
    MAX_DETAILS = 60

    async def fetch_board(self, client, employer) -> FetchResult:
        base = f"https://api.smartrecruiters.com/v1/companies/{employer['board_id']}/postings"
        try:
            data = await get_json(client, f"{base}?country=gb&limit=100")
        except ProviderError as e:
            return FetchResult(employer_id=employer["id"], ok=False, error=str(e), http_status=e.status)
        if not isinstance(data, dict) or not isinstance(data.get("content"), list):
            return FetchResult(employer_id=employer["id"], ok=False, error="malformed response: missing content[]")
        details = []
        for p in data["content"][: self.MAX_DETAILS]:
            try:
                details.append(await get_json(client, f"{base}/{p['id']}"))
            except ProviderError:
                details.append(p)  # keep the listing even if the detail call fails
        return await asyncio.to_thread(self._all, data, details, employer)

    def _all(self, data, details, employer):
        jobs, bad = [], 0
        for p in details:
            try:
                secs = ((p.get("jobAd") or {}).get("sections") or {})
                html = "".join(f"<h3>{s.get('title', '')}</h3>{s.get('text', '')}" for s in secs.values() if isinstance(s, dict))
                text = html_to_text(html)
                loc = p.get("location") or {}
                loc_raw = loc.get("fullLocation") or ", ".join(x for x in (loc.get("city"), loc.get("region"), (loc.get("country") or "").upper()) if x)
                wp = "remote" if loc.get("remote") else "hybrid" if loc.get("hybrid") else None
                job, prov = self._base(employer, p.get("id"), p["name"], text, html, location_raw=loc_raw,
                                       country=COUNTRY.get((loc.get("country") or "").upper()),
                                       employment_type=_emp((p.get("typeOfEmployment") or {}).get("label")),
                                       department=(p.get("department") or {}).get("label"), published_at=parse_dt(p.get("releasedDate")),
                                       source_url=p.get("postingUrl") or f"https://jobs.smartrecruiters.com/{employer['board_id']}/{p.get('id')}",
                                       application_url=p.get("applyUrl"), _workplace_hint=wp)
                if wp == "remote" and job.get("country"):
                    job["remote_eligibility"] = job["country"]
                    prov["remote_eligibility"] = "provider"
                jobs.append(finalise(job, prov, text))
            except Exception:
                bad += 1
        res = FetchResult(employer_id=employer["id"], ok=True, raw_hash=raw_hash(data), malformed=bad)
        res.jobs = jobs
        return res


class RecruiteeProvider(_Base):
    name = "recruitee"

    async def fetch_board(self, client, employer) -> FetchResult:
        try:
            data = await get_json(client, f"https://{employer['board_id']}.recruitee.com/api/offers/")
        except ProviderError as e:
            return FetchResult(employer_id=employer["id"], ok=False, error=str(e), http_status=e.status)
        if not isinstance(data, dict) or not isinstance(data.get("offers"), list):
            return FetchResult(employer_id=employer["id"], ok=False, error="malformed response: missing offers[]")
        return await asyncio.to_thread(self._all, data, employer)

    def _all(self, data, employer):
        jobs, bad = [], 0
        for p in data["offers"]:
            try:
                html = (p.get("description") or "") + (p.get("requirements") or "")
                text = html_to_text(html)
                remote = str(p.get("remote")).lower() == "true"
                hybrid = str(p.get("hybrid")).lower() == "true"
                country = COUNTRY.get((p.get("country_code") or "").upper()) or p.get("country")
                job, prov = self._base(employer, p.get("id"), p["title"], text, html, location_raw=p.get("location"), country=country,
                                       employment_type=_emp(p.get("employment_type_code")), department=p.get("department"),
                                       published_at=parse_dt((p.get("published_at") or "").replace(" UTC", "+00:00")),
                                       source_url=p.get("careers_url"), application_url=p.get("careers_apply_url"),
                                       _workplace_hint="remote" if remote else "hybrid" if hybrid else ("onsite" if str(p.get("on_site")).lower() == "true" else None))
                sal = p.get("salary") or {}
                if (sal.get("min") or sal.get("max")) and (sal.get("period") in ("year", "annual")):
                    job.update(salary_min=float(sal["min"]) if sal.get("min") else None, salary_max=float(sal["max"]) if sal.get("max") else None,
                               salary_currency=sal.get("currency"), salary_period="year", salary_disclosed=True)
                    prov.update({k: "provider" for k in ("salary_min", "salary_max", "salary_currency")})
                jobs.append(finalise(job, prov, text))
            except Exception:
                bad += 1
        res = FetchResult(employer_id=employer["id"], ok=True, raw_hash=raw_hash(data), malformed=bad)
        res.jobs = jobs
        return res
