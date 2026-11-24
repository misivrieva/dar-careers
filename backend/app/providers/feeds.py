"""Aggregator feeds and search APIs (one registry entry per feed; each job keeps the real employer name).

Public, no key (verified 2026-11-24):
- Remotive  https://remotive.com/api/remote-jobs            (remote roles; attribution required; <= a few calls/day)
- Jobicy    https://jobicy.com/api/v2/remote-jobs?geo=uk    (remote roles open to the UK; attribution required)
- Himalayas https://himalayas.app/jobs/api                  (remote roles; filtered to UK/EU/worldwide eligibility)
- Arbeitnow https://www.arbeitnow.com/api/job-board-api     (mostly Germany/EU; kept only if remote or UK)
Free key required (enabled automatically when the key is in .env):
- Reed      https://www.reed.co.uk/api/1.0/search           REED_API_KEY
- Adzuna    https://api.adzuna.com/v1/api/jobs/gb/search/N  ADZUNA_APP_ID + ADZUNA_APP_KEY
Predicted/estimated salaries (Adzuna `salary_is_predicted`) are never treated as disclosed.
"""
from __future__ import annotations

import asyncio
import base64
import os
import re
from datetime import datetime, timezone

import httpx

from ..services.job_features import clean_html, html_to_text
from .base import FetchResult, JobProvider, ProviderError, get_json, parse_dt, raw_hash
from .normalise import finalise

UKISH = re.compile(r"\b(uk|united kingdom|great britain|england|scotland|wales|europe|emea|worldwide|anywhere|global)\b", re.I)


def _ts(v) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(v), tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError):
        return parse_dt(v)


def _key_or_none(*names: str) -> list[str] | None:
    vals = [os.environ.get(n) or _dotenv(n) for n in names]
    return vals if all(vals) else None


def _dotenv(name: str) -> str | None:
    from ..config import ROOT

    try:
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip() or None
    except OSError:
        return None
    return None


class _Feed(JobProvider):
    attribution = ""

    def job(self, employer, ext_id, company, title, text, html, prov_extra=None, **kw):
        job = {"external_id": str(ext_id), "provider": self.name, "employer_id": employer["id"],
               "employer_name": (company or "Unknown employer").strip()[:200], "title": title.strip(), "description_text": text,
               "description_html": clean_html(html) if html else None}
        prov = {"external_id": "provider", "source": f"aggregator:{self.name}"}
        for k, v in kw.items():
            if v is not None:
                job[k] = v
        prov.update(prov_extra or {})
        return finalise(job, prov, text)

    def wrap(self, employer, data, jobs, bad=0):
        r = FetchResult(employer_id=employer["id"], ok=True, raw_hash=raw_hash(data), malformed=bad)
        r.jobs = jobs
        return r


class RemotiveFeed(_Feed):
    name = "remotive"

    async def fetch_board(self, client, employer):
        try:
            data = await get_json(client, "https://remotive.com/api/remote-jobs")
        except ProviderError as e:
            return FetchResult(employer_id=employer["id"], ok=False, error=str(e), http_status=e.status)
        return await asyncio.to_thread(self._all, data, employer)

    def _all(self, data, employer):
        jobs, bad = [], 0
        for p in data.get("jobs", []):
            try:
                loc = p.get("candidate_required_location") or ""
                if loc and not UKISH.search(loc):
                    continue  # e.g. "USA only": not usable for a UK candidate
                jobs.append(self.job(employer, p["id"], p.get("company_name"), p["title"], html_to_text(p.get("description")), p.get("description"),
                                     {"workplace_type": "provider", "remote_eligibility": "provider" if loc else "unknown"},
                                     location_raw=f"Remote ({loc})" if loc else "Remote", _workplace_hint="remote",
                                     remote_eligibility=("UK" if re.search(r"\buk\b|united kingdom", loc, re.I) else loc.upper() or None),
                                     source_url=p.get("url"), application_url=p.get("url"), published_at=parse_dt(p.get("publication_date"))))
            except Exception:
                bad += 1
        return self.wrap(employer, data, jobs, bad)


class JobicyFeed(_Feed):
    name = "jobicy"

    async def fetch_board(self, client, employer):
        try:
            data = await get_json(client, "https://jobicy.com/api/v2/remote-jobs?count=100&geo=uk")
        except ProviderError as e:
            return FetchResult(employer_id=employer["id"], ok=False, error=str(e), http_status=e.status)
        return await asyncio.to_thread(self._all, data, employer)

    def _all(self, data, employer):
        jobs, bad = [], 0
        for p in data.get("jobs", []):
            try:
                geo = p.get("jobGeo") or ""
                kw = {}
                if p.get("salaryMin") or p.get("salaryMax"):
                    per = (p.get("salaryPeriod") or "").lower()
                    if per in ("year", "yearly", "annual"):
                        kw = dict(salary_min=p.get("salaryMin"), salary_max=p.get("salaryMax"), salary_currency=p.get("salaryCurrency"),
                                  salary_period="year", salary_disclosed=True)
                types = p.get("jobType") or []
                jobs.append(self.job(employer, p["id"], p.get("companyName"), p["jobTitle"], html_to_text(p.get("jobDescription")), p.get("jobDescription"),
                                     {"workplace_type": "provider"}, location_raw=f"Remote ({geo})", _workplace_hint="remote",
                                     remote_eligibility=("UK" if re.search(r"\buk\b|united kingdom", geo, re.I) else geo.upper() or None),
                                     employment_type="full_time" if any("full" in t.lower() for t in types) else None,
                                     source_url=p.get("url"), application_url=p.get("url"), published_at=parse_dt(p.get("pubDate")), **kw))
            except Exception:
                bad += 1
        return self.wrap(employer, data, jobs, bad)


class HimalayasFeed(_Feed):
    name = "himalayas"

    async def fetch_board(self, client, employer):
        pages, cursor = [], None
        try:
            for _ in range(3):
                url = "https://himalayas.app/jobs/api?limit=100" + (f"&cursor={cursor}" if cursor else "")
                d = await get_json(client, url)
                pages.append(d)
                cursor = d.get("nextCursor")
                if not cursor:
                    break
        except ProviderError as e:
            if not pages:
                return FetchResult(employer_id=employer["id"], ok=False, error=str(e), http_status=e.status)
        data = {"jobs": [j for p in pages for j in p.get("jobs", [])]}
        return await asyncio.to_thread(self._all, data, employer)

    def _all(self, data, employer):
        jobs, bad = [], 0
        for p in data["jobs"]:
            try:
                restr = p.get("locationRestrictions") or []
                if restr and not any(UKISH.search(r) for r in restr):
                    continue
                elig = "UK" if any(re.search(r"united kingdom|\buk\b", r, re.I) for r in restr) else (", ".join(restr) if restr else None)
                kw = {}
                if (p.get("minSalary") or p.get("maxSalary")) and (p.get("salaryPeriod") or "") in ("annual", "year"):
                    kw = dict(salary_min=p.get("minSalary"), salary_max=p.get("maxSalary"), salary_currency=p.get("currency"),
                              salary_period="year", salary_disclosed=True)
                jobs.append(self.job(employer, p.get("guid") or p.get("applicationLink"), p.get("companyName"), p["title"],
                                     html_to_text(p.get("description")), p.get("description"), {"workplace_type": "provider"},
                                     location_raw=f"Remote ({', '.join(restr) if restr else 'anywhere'})", _workplace_hint="remote",
                                     remote_eligibility=elig, employment_type={"full time": "full_time", "part time": "part_time",
                                                                               "contractor": "contract"}.get((p.get("employmentType") or "").lower()),
                                     source_url=p.get("applicationLink"), application_url=p.get("applicationLink"), published_at=_ts(p.get("pubDate")), **kw))
            except Exception:
                bad += 1
        return self.wrap(employer, data, jobs, bad)


class ArbeitnowFeed(_Feed):
    name = "arbeitnow"

    async def fetch_board(self, client, employer):
        pages = []
        try:
            for n in (1, 2):
                pages.append(await get_json(client, f"https://www.arbeitnow.com/api/job-board-api?page={n}"))
        except ProviderError as e:
            if not pages:
                return FetchResult(employer_id=employer["id"], ok=False, error=str(e), http_status=e.status)
        data = {"data": [j for p in pages for j in p.get("data", [])]}
        return await asyncio.to_thread(self._all, data, employer)

    def _all(self, data, employer):
        jobs, bad = [], 0
        for p in data["data"]:
            try:
                remote = str(p.get("remote")).lower() == "true"
                loc = p.get("location") or ""
                if not remote and not re.search(r"\b(uk|united kingdom|london|england)\b", loc, re.I):
                    continue
                jobs.append(self.job(employer, p.get("slug"), p.get("company_name"), p["title"], html_to_text(p.get("description")), p.get("description"),
                                     location_raw=(f"Remote ({loc})" if remote and loc and loc.lower() != "remote" else loc or None),
                                     _workplace_hint="remote" if remote else None, source_url=p.get("url"), application_url=p.get("url"),
                                     published_at=_ts(p.get("created_at"))))
            except Exception:
                bad += 1
        return self.wrap(employer, data, jobs, bad)


class ReedSearch(_Feed):
    """UK job search. Needs a free key from reed.co.uk/developers (REED_API_KEY)."""
    name = "reed"
    MAX_DETAILS = 40

    async def fetch_board(self, client, employer):
        key = _key_or_none("REED_API_KEY")
        if not key:
            return FetchResult(employer_id=employer["id"], ok=False, error="REED_API_KEY not configured")
        auth = {"Authorization": "Basic " + base64.b64encode(f"{key[0]}:".encode()).decode()}
        results = []
        for kw in (employer.get("keywords") or ["technical account manager"])[:6]:
            r = await client.get("https://www.reed.co.uk/api/1.0/search", params={"keywords": kw, "resultsToTake": 50}, headers=auth)
            if r.status_code != 200:
                return FetchResult(employer_id=employer["id"], ok=False, error=f"Reed HTTP {r.status_code}", http_status=r.status_code)
            results += r.json().get("results", [])
        seen, details = set(), []
        for p in results:
            if p["jobId"] in seen:
                continue
            seen.add(p["jobId"])
            if len(details) < self.MAX_DETAILS:
                d = await client.get(f"https://www.reed.co.uk/api/1.0/jobs/{p['jobId']}", headers=auth)
                details.append({**p, **(d.json() if d.status_code == 200 else {})})
        data = {"results": details}
        return await asyncio.to_thread(self._all, data, employer)

    def _all(self, data, employer):
        jobs, bad = [], 0
        for p in data["results"]:
            try:
                kw = {}
                if (p.get("minimumSalary") or p.get("maximumSalary")) and (p.get("salaryType") or "per annum").lower().startswith("per annum"):
                    kw = dict(salary_min=p.get("minimumSalary"), salary_max=p.get("maximumSalary"), salary_currency=p.get("currency") or "GBP",
                              salary_period="year", salary_disclosed=True)
                html = p.get("jobDescription") or ""
                jobs.append(self.job(employer, p["jobId"], p.get("employerName"), p["jobTitle"], html_to_text(html), html,
                                     {"salary_min": "provider"} if kw else None, location_raw=p.get("locationName"), country="United Kingdom",
                                     employment_type="full_time" if p.get("fullTime") else ("part_time" if p.get("partTime") else None),
                                     source_url=p.get("jobUrl") or p.get("externalUrl"), application_url=p.get("externalUrl") or p.get("jobUrl"),
                                     published_at=parse_dt(p.get("datePosted") or p.get("date")), **kw))
            except Exception:
                bad += 1
        return self.wrap(employer, data, jobs, bad)


class AdzunaSearch(_Feed):
    """UK job search. Needs free credentials from developer.adzuna.com (ADZUNA_APP_ID, ADZUNA_APP_KEY)."""
    name = "adzuna"

    async def fetch_board(self, client, employer):
        key = _key_or_none("ADZUNA_APP_ID", "ADZUNA_APP_KEY")
        if not key:
            return FetchResult(employer_id=employer["id"], ok=False, error="ADZUNA_APP_ID/ADZUNA_APP_KEY not configured")
        results = []
        try:
            for kw in (employer.get("keywords") or ["technical account manager"])[:6]:
                d = await get_json(client, "https://api.adzuna.com/v1/api/jobs/gb/search/1?" + httpx.QueryParams(
                    {"app_id": key[0], "app_key": key[1], "what": kw, "results_per_page": 50, "content-type": "application/json"}).__str__())
                results += d.get("results", [])
        except ProviderError as e:
            return FetchResult(employer_id=employer["id"], ok=False, error=str(e), http_status=e.status)
        data = {"results": results}
        return await asyncio.to_thread(self._all, data, employer)

    def _all(self, data, employer):
        jobs, bad, seen = [], 0, set()
        for p in data["results"]:
            try:
                if p["id"] in seen:
                    continue
                seen.add(p["id"])
                kw = {}
                if str(p.get("salary_is_predicted")) != "1" and (p.get("salary_min") or p.get("salary_max")):
                    kw = dict(salary_min=p.get("salary_min"), salary_max=p.get("salary_max"), salary_currency="GBP", salary_period="year", salary_disclosed=True)
                text = p.get("description") or ""  # Adzuna provides a snippet; the full text is on the source page
                jobs.append(self.job(employer, p["id"], (p.get("company") or {}).get("display_name"), p["title"], text, None,
                                     location_raw=(p.get("location") or {}).get("display_name"), country="United Kingdom",
                                     employment_type={"full_time": "full_time", "part_time": "part_time"}.get(p.get("contract_time") or ""),
                                     source_url=p.get("redirect_url"), application_url=p.get("redirect_url"), published_at=parse_dt(p.get("created")), **kw))
            except Exception:
                bad += 1
        return self.wrap(employer, data, jobs, bad)
