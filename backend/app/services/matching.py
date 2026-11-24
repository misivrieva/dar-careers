"""Hybrid matching engine.

Stage 1 - deterministic eligibility against confirmed hard requirements (eligible / ineligible / uncertain).
Stage 2 - transparent weighted preference scoring (configurable weights + learned feature adjustments).
Stage 3 - optional LLM semantic assessment on a small shortlist (blended, never overrides Stage 1).

All functions operate on plain dicts so evals can exercise the exact production logic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .job_features import UK_TERMS, role_family

DEFAULT_WEIGHTS = {"title": 0.30, "skills": 0.20, "seniority": 0.10, "compensation": 0.15, "flexibility": 0.10,
                   "industry": 0.05, "recency": 0.10}
SENIORITY_ORDER = ["junior", "mid", "senior", "lead", "staff", "principal", "executive"]
UNCERTAIN_PENALTY = 8.0


def job_view(job: Any) -> dict:
    """Normalise a Job ORM row or a fixture dict into the dict shape used by matching."""
    if isinstance(job, dict):
        d = dict(job)
    else:
        d = {c: getattr(job, c) for c in (
            "id", "title", "employer_name", "employer_id", "description_text", "location_raw", "country", "city",
            "workplace_type", "remote_eligibility", "office_days_per_week", "employment_type", "seniority", "salary_min",
            "salary_max", "salary_currency", "salary_period", "salary_disclosed", "on_call_required", "features",
            "active", "lifecycle", "published_at", "required_skills", "is_synthetic")}
    d.setdefault("features", {})
    f = d["features"]
    if "role_family" not in f:
        from .job_features import compute_features

        d["features"] = f = {**compute_features(d.get("title", ""), d.get("description_text") or ""), **f}
    d.setdefault("active", True)
    d.setdefault("lifecycle", "open")
    d.setdefault("salary_disclosed", d.get("salary_min") is not None or d.get("salary_max") is not None)
    d.setdefault("salary_period", "year" if d.get("salary_disclosed") else None)
    if d.get("on_call_required") is None and f.get("on_call") is not None:
        d["on_call_required"] = f["on_call"]
    return d


# ======================================================================== Stage 1: eligibility
@dataclass
class Eligibility:
    status: str = "eligible"  # eligible | ineligible | uncertain
    violations: list[dict] = field(default_factory=list)
    uncertain: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)

    def violate(self, code: str, detail: str):
        self.violations.append({"code": code, "detail": detail})

    def unsure(self, fld: str, detail: str):
        self.uncertain.append({"field": fld, "detail": detail})

    def finalise(self) -> "Eligibility":
        self.status = "ineligible" if self.violations else ("uncertain" if self.uncertain else "eligible")
        return self


def _mentions(text: str | None, term: str) -> bool:
    return bool(text) and re.search(rf"(?<![a-z]){re.escape(term.lower())}(?![a-z])", text.lower()) is not None


def evaluate_eligibility(job: dict, reqs: dict) -> Eligibility:
    e = Eligibility()
    if not job.get("active", True) or job.get("lifecycle") in ("closed",):
        e.violate("vacancy_closed", "This vacancy is no longer open.")
    _check_salary(job, reqs, e)
    _check_location(job, reqs, e)
    _check_employment(job, reqs, e)
    _check_dealbreakers(job, reqs, e)
    return e.finalise()


def _check_salary(job: dict, reqs: dict, e: Eligibility) -> None:
    ms = reqs.get("min_salary")
    if not job.get("salary_disclosed"):
        e.missing_info.append("salary")
    if not ms or not ms.get("amount"):
        return
    minimum, cur = float(ms["amount"]), (ms.get("currency") or "GBP").upper()
    lo, hi = job.get("salary_min"), job.get("salary_max")
    if not job.get("salary_disclosed") or (lo is None and hi is None):
        e.unsure("salary", "Salary not disclosed, so I can't confirm it meets your minimum.")
        return
    if (job.get("salary_period") or "year") != "year":
        e.unsure("salary", f"Salary is quoted per {job.get('salary_period')}, not annually.")
        return
    jcur = (job.get("salary_currency") or "").upper()
    if jcur and jcur != cur:
        e.unsure("salary", f"Salary is in {jcur}; your minimum is in {cur} (no currency conversion applied).")
        return
    top = hi if hi is not None else lo
    bottom = lo if lo is not None else hi
    if top < minimum:
        e.violate("salary_below_minimum", f"Top of range £{top:,.0f} is below your £{minimum:,.0f} minimum.")
    elif bottom < minimum:
        e.notes.append(f"Range £{bottom:,.0f}–£{top:,.0f} overlaps your £{minimum:,.0f} minimum; the offer may land below it.")


def _place_words(raw: str) -> str:
    """Location text with remote/hybrid boilerplate removed (non-empty => names a specific place)."""
    s = re.sub(r"(?i)\b(remote|hybrid|on-?site|office|offices|also|flexible|home[- ]based|wfh|or|and|in)\b", " ", raw or "")
    return re.sub(r"[^A-Za-z]+", " ", s).strip()


def _job_cities(job: dict) -> str:
    return " ".join(filter(None, [job.get("location_raw"), job.get("city")]))


def _check_location(job: dict, reqs: dict, e: Eligibility) -> None:
    loc = reqs.get("locations")
    max_days = reqs.get("max_office_days")
    wt = job.get("workplace_type")
    raw = job.get("location_raw") or ""
    od = job.get("office_days_per_week")
    if wt is None:
        e.missing_info.append("workplace type")
    if od is None and wt != "remote":
        e.missing_info.append("office days")
    if loc:
        remote_regions = [r.upper() for r in loc.get("remote_regions", [])]
        cities = list(dict.fromkeys(loc.get("hybrid_cities", []) + loc.get("onsite_cities", [])))
        uk_remote_ok = "UK" in remote_regions or "UNITED KINGDOM" in remote_regions
        city_hit = None if job.get("country") == "United States" else next((c for c in cities if _mentions(_job_cities(job), c)), None)
        if wt == "remote":
            elig = (job.get("remote_eligibility") or "").upper()
            uk_elig = bool(re.search(r"\bUK\b|UNITED KINGDOM|GREAT BRITAIN|ENGLAND|SCOTLAND|WALES", elig)) or (
                not elig and bool(re.search(r"remote[^;,]{0,12}\(?\b(uk|united kingdom)\b", raw, re.I)))
            if uk_elig and (uk_remote_ok or city_hit or cities):
                pass  # UK remote satisfies a UK remote requirement or a UK city hybrid preference (fewer office days)
            elif uk_elig:
                e.violate("location_unsuitable", "Remote (UK), but you haven't listed UK remote as an option.")
            elif city_hit:
                pass  # remote role listed in one of the user's cities
            elif elig and re.search(r"EMEA|EUROPE|ANYWHERE|WORLDWIDE|GLOBAL", elig):
                e.unsure("location", f"Remote ({elig.title()}) - UK eligibility isn't stated explicitly.")
            elif elig:
                e.violate("remote_outside_uk", f"Remote role restricted to {job.get('remote_eligibility')}.")
            elif UK_TERMS.search(raw):
                e.unsure("location", "Remote role mentioning the UK, but UK eligibility isn't explicit.")
            elif re.search(r"EMEA|EUROPE|ANYWHERE|WORLDWIDE|GLOBAL|INTERNATIONAL", raw, re.I):
                e.unsure("location", f"Remote ({raw}) - UK eligibility isn't stated explicitly.")
            elif _place_words(raw):
                e.violate("remote_outside_uk", f"Remote role tied to {raw}, not the UK.")
            else:
                e.unsure("location", "Remote role, but it doesn't say which countries are eligible.")
        else:
            generic_uk = bool(re.fullmatch(r"\s*(uk|united kingdom|great britain|england)\s*", raw, re.I))
            if city_hit:
                pass
            elif uk_remote_ok and re.search(r"remote[^;,]{0,12}\(?\b(uk|united kingdom)\b|\b(uk|united kingdom)\b[^;,]{0,12}remote", raw, re.I):
                pass  # location string offers UK remote as an option
            elif not raw and not job.get("country"):
                e.unsure("location", "Location not stated.")
            elif generic_uk and wt is None and (uk_remote_ok or cities):
                e.unsure("location", "Listed as 'UK' without a city or remote policy.")
            else:
                e.violate("location_unsuitable", f"Based in {raw or job.get('country')}, outside your locations.")
            if wt == "onsite" and city_hit and city_hit not in loc.get("onsite_cities", []) and max_days is None:
                e.violate("onsite_not_accepted", f"Office-based in {city_hit}; you only want hybrid there.")
    if max_days is not None and wt != "remote":
        days = od if od is not None else (5.0 if wt == "onsite" else None)
        if days is None:
            e.unsure("office_days", "Office attendance isn't stated.")
        elif days > float(max_days):
            e.violate("too_many_office_days", f"Requires {days:g} office days a week; your maximum is {float(max_days):g}.")


def _check_employment(job: dict, reqs: dict, e: Eligibility) -> None:
    allowed = reqs.get("employment_types")
    et = job.get("employment_type")
    if et is None:
        e.missing_info.append("employment type")
        return
    if allowed and et not in allowed:
        e.violate("employment_type", f"{et.replace('_', ' ').title()} role; you're looking for {', '.join(a.replace('_', ' ') for a in allowed)}.")


def _check_dealbreakers(job: dict, reqs: dict, e: Eligibility) -> None:
    f = job.get("features") or {}
    if reqs.get("needs_sponsorship") and f.get("no_visa_sponsorship"):
        e.violate("no_sponsorship", "The vacancy says it can't offer visa sponsorship.")
    db_ = reqs.get("deal_breakers") or {}
    if db_.get("on_call"):
        if job.get("on_call_required") is True:
            e.violate("requires_on_call", "Mentions an on-call rotation, which you've ruled out.")
        elif job.get("on_call_required") is None and f.get("role_family") == "sre_platform":
            e.unsure("on_call", "Infrastructure role; on-call isn't mentioned either way.")
    for emp in db_.get("employers") or []:
        if emp and _mentions(job.get("employer_name"), emp):
            e.violate("excluded_employer", f"You've excluded {job.get('employer_name')}.")
    blob = f"{job.get('title', '')}\n{job.get('description_text') or ''}"
    for kw in db_.get("keywords") or []:
        if kw and len(kw) > 2 and _mentions(blob, kw):
            e.violate("deal_breaker_keyword", f"Mentions '{kw}', one of your deal-breakers.")


# ======================================================================== Stage 2: preference scoring
def _tokens(s: str) -> set[str]:
    stop = {"of", "the", "and", "a", "an", "for", "to", "in", "ii", "iii", "i", "&", "-", "senior", "sr", "staff", "lead",
            "principal", "junior", "uk", "remote", "emea", "london"}
    return {(t[:-1] if len(t) > 4 and t.endswith("s") else t) for t in re.findall(r"[a-z]+", s.lower()) if t not in stop}


GENERIC_TOKENS = {"engineer", "manager", "specialist", "developer", "consultant", "analyst", "associate", "director", "head",
                  "officer", "representative", "partner", "technical", "senior"}


def _tw(tok: str) -> float:
    return 0.25 if tok in GENERIC_TOKENS else 1.0


def title_similarity(job_title: str, targets: list[str]) -> tuple[float, str | None]:
    best, best_t = 0.0, None
    jt = _tokens(job_title)
    jf = role_family(job_title)
    for t in targets:
        tt = _tokens(t)
        if not tt:
            continue
        overlap = sum(_tw(x) for x in jt & tt) / sum(_tw(x) for x in tt)
        fam = 0.7 if (jf != "other" and role_family(t) == jf) else 0.0
        s = max(overlap, fam)
        if s > best:
            best, best_t = s, t
    return min(1.0, best), best_t


def _seniority_score(job_level: str | None, cand_level: str | None) -> float:
    if not job_level or not cand_level or cand_level == "unknown" or job_level not in SENIORITY_ORDER or cand_level not in SENIORITY_ORDER:
        return 0.6
    d = SENIORITY_ORDER.index(job_level) - SENIORITY_ORDER.index(cand_level)
    return {0: 1.0, 1: 0.8, -1: 0.75, 2: 0.4, -2: 0.4}.get(d, 0.15)


def score_job(job: dict, ctx: dict, elig: Eligibility | None = None) -> dict:
    """Transparent weighted score in [0,100] with a per-component breakdown.

    ctx: {titles, expanded_titles, candidate_skills, candidate_seniority, target_salary, remote_preference,
          industries_preferred, industries_avoid, employer_industry, weights, feature_adjustments, on_call_pref}
    """
    w = {**DEFAULT_WEIGHTS, **(ctx.get("weights") or {})}
    f = job.get("features") or {}
    comp: dict[str, float] = {}
    s1, t1 = title_similarity(job["title"], ctx.get("titles") or [])
    s2, t2 = title_similarity(job["title"], ctx.get("expanded_titles") or [])
    comp["title"] = max(s1, 0.85 * s2)
    matched_title = t1 if s1 >= 0.85 * s2 else t2
    jskills = {s.lower() for s in (job.get("required_skills") or f.get("skills") or [])}
    cskills = {s.lower() for s in ctx.get("candidate_skills") or []}
    comp["skills"] = (len(jskills & cskills) / max(4, len(jskills))) if jskills else 0.4
    comp["skills"] = min(1.0, comp["skills"] * 1.5)
    comp["seniority"] = _seniority_score(job.get("seniority"), ctx.get("candidate_seniority"))
    target = (ctx.get("target_salary") or {}).get("amount") if isinstance(ctx.get("target_salary"), dict) else ctx.get("target_salary")
    top = job.get("salary_max") or job.get("salary_min")
    if job.get("salary_disclosed") and top and target and (job.get("salary_currency") or "GBP") == "GBP":
        comp["compensation"] = max(0.0, min(1.0, 0.5 + (top - target) / (2 * target) * 2))
    else:
        comp["compensation"] = 0.45
    rp = ctx.get("remote_preference")
    od = job.get("office_days_per_week")
    if job.get("workplace_type") == "remote":
        comp["flexibility"] = 1.0 if rp in ("fully_remote", "any", None) else 0.8
    elif od is not None:
        comp["flexibility"] = max(0.0, 1.0 - od / 5) if rp == "fully_remote" else max(0.2, 1.0 - od / 6)
    else:
        comp["flexibility"] = 0.4
    ind = (ctx.get("employer_industry") or {}).get(job.get("employer_id")) or ""
    pref_ind = [i.lower() for i in ctx.get("industries_preferred") or []]
    avoid_ind = [i.lower() for i in ctx.get("industries_avoid") or []]
    def _ind_hit(term: str) -> bool:
        return bool(term) and re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", ind.lower()) is not None
    comp["industry"] = 1.0 if any(_ind_hit(p) for p in pref_ind) else (0.0 if any(_ind_hit(a) for a in avoid_ind) else 0.5)
    pub = job.get("published_at")
    if pub:
        from datetime import datetime

        age = (datetime.utcnow() - (pub if not isinstance(pub, str) else datetime.fromisoformat(pub))).days
        comp["recency"] = 1.0 if age <= 14 else 0.7 if age <= 45 else 0.4
    else:
        comp["recency"] = 0.5
    base = sum(w.get(k, 0) * v for k, v in comp.items()) / max(1e-9, sum(w.get(k, 0) for k in comp)) * 100
    # learned and stated feature adjustments (points)
    adjustments: list[dict] = []
    for key, pts in (ctx.get("feature_adjustments") or {}).items():
        val = _feature_value(job, key)
        if val:
            delta = pts * val
            adjustments.append({"feature": key, "points": round(delta, 1)})
    if ctx.get("on_call_pref") == "avoid" and job.get("on_call_required") is True:
        adjustments.append({"feature": "on_call (stated preference: avoid)", "points": -10.0})
    total = base + sum(a["points"] for a in adjustments)
    if elig and elig.status == "uncertain":
        total -= UNCERTAIN_PENALTY
        adjustments.append({"feature": "uncertain eligibility", "points": -UNCERTAIN_PENALTY})
    total = max(0.0, min(100.0, total))
    return {"score": round(total, 1), "components": {k: round(v, 3) for k, v in comp.items()}, "weights": w,
            "adjustments": adjustments, "matched_title": matched_title, "base": round(base, 1)}


def _feature_value(job: dict, key: str) -> float:
    f = job.get("features") or {}
    if key.startswith("role_family:"):
        return 1.0 if f.get("role_family") == key.split(":", 1)[1] else 0.0
    if key == "on_call":
        return 1.0 if job.get("on_call_required") is True else 0.0
    if key in ("customer_facing", "coding_heavy"):
        return float(f.get(key) or 0.0)
    if key == "management":
        return 1.0 if f.get("management") else 0.0
    if key == "travel":
        return min(1.0, (f.get("travel_pct") or 0) / 50)
    if key == "office_days":
        od = job.get("office_days_per_week")
        return (od or 0) / 5
    if key.startswith("employer:"):
        return 1.0 if job.get("employer_id") == key.split(":", 1)[1] else 0.0
    return 0.0


COMPONENT_PHRASES = {
    "title": "closely matches the roles you're targeting",
    "skills": "uses skills from your CV",
    "seniority": "fits your level",
    "compensation": "pays at or above your target",
    "flexibility": "fits how you like to work",
    "industry": "is in an industry you like",
    "recency": "was posted recently",
}


def explain(job: dict, scored: dict, elig: Eligibility, ctx: dict) -> tuple[str, list[str]]:
    comp = scored["components"]
    w = scored["weights"]
    ranked = sorted(comp, key=lambda k: comp[k] * w.get(k, 0), reverse=True)
    good = [COMPONENT_PHRASES[k] for k in ranked if comp[k] >= 0.7][:2]
    parts = []
    if scored.get("matched_title") and comp["title"] >= 0.5:
        parts.append(f"Similar to your target '{scored['matched_title']}'")
    if good:
        parts.append(("it " if parts else "This role ") + " and ".join(good))
    jskills = {s.lower() for s in (job.get("required_skills") or [])}
    shared = sorted(jskills & {s.lower() for s in ctx.get("candidate_skills") or []})[:4]
    if shared:
        parts.append(f"shared skills: {', '.join(shared)}")
    for a in scored["adjustments"]:
        if a["points"] > 0 and not a["feature"].startswith("uncertain"):
            parts.append(f"boosted by what you've liked before ({a['feature'].replace('role_family:', '').replace('_', ' ')})")
            break
    text = "; ".join(parts) + "." if parts else "A partial match on your stated preferences."
    concerns = [v["detail"] for v in elig.uncertain] + list(elig.notes)
    if job.get("on_call_required") is True:
        concerns.append("Mentions on-call duties.")
    f = job.get("features") or {}
    if f.get("travel_pct"):
        concerns.append(f"Travel around {f['travel_pct']}%.")
    if comp.get("seniority", 1) < 0.5:
        concerns.append(f"Seniority ({job.get('seniority')}) may not match your level.")
    return text[0].upper() + text[1:], concerns


def build_context(reqs: dict, prefs: dict, profile_data: dict, strategy: Any | None, employer_industry: dict) -> dict:
    titles = list(prefs.get("target_titles") or (profile_data.get("suggested_target_titles") or [])[:4])
    ind = prefs.get("industries") or {}
    return {
        "titles": (getattr(strategy, "titles", None) or titles),
        "expanded_titles": getattr(strategy, "expanded_titles", None) or [],
        "candidate_skills": (profile_data.get("technical_skills") or []) + (profile_data.get("transferable_skills") or []),
        "candidate_seniority": profile_data.get("seniority"),
        "target_salary": prefs.get("target_salary"),
        "remote_preference": prefs.get("remote_preference"),
        "industries_preferred": ind.get("preferred") if isinstance(ind, dict) else [],
        "industries_avoid": ind.get("avoid") if isinstance(ind, dict) else [],
        "employer_industry": employer_industry,
        "weights": getattr(strategy, "weights", None) or {},
        "feature_adjustments": getattr(strategy, "feature_adjustments", None) or {},
        "on_call_pref": prefs.get("on_call"),
    }


def rank(jobs: list[dict], reqs: dict, ctx: dict, include_uncertain: bool = True, limit: int | None = None) -> list[dict]:
    """Deterministic stages 1+2 over a candidate pool. Ineligible jobs are excluded."""
    out = []
    for j in jobs:
        elig = evaluate_eligibility(j, reqs)
        if elig.status == "ineligible":
            continue
        if elig.status == "uncertain" and not include_uncertain:
            continue
        sc = score_job(j, ctx, elig)
        out.append({"job": j, "eligibility": elig, "scored": sc})
    out.sort(key=lambda r: r["scored"]["score"], reverse=True)
    return out[:limit] if limit else out
