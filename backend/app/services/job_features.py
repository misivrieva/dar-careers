"""Deterministic extraction of vacancy facts from provider fields and description text.

Everything here is rule-based and reproducible. Values that cannot be determined stay None.
"""
from __future__ import annotations

import html as htmllib
import re
from typing import Any

import nh3

UK_TERMS = re.compile(
    r"\b(united kingdom|uk|u\.k\.|great britain|england|scotland|wales|northern ireland|london|manchester|edinburgh|"
    r"glasgow|bristol|cambridge|oxford|leeds|birmingham|belfast|cardiff|reading|newcastle|brighton|nottingham|sheffield|"
    r"liverpool|milton keynes|guildford)\b",
    re.I,
)
UK_CITIES = {
    "london", "manchester", "edinburgh", "glasgow", "bristol", "cambridge", "oxford", "leeds", "birmingham", "belfast",
    "cardiff", "reading", "newcastle", "brighton", "nottingham", "sheffield", "liverpool", "milton keynes", "guildford",
}
COUNTRY_TERMS = [
    ("United Kingdom", UK_TERMS),
    ("United States", re.compile(r"\b(united states|usa|u\.s\.|us-remote|us remote|remote[ ,-]+us|new york|san francisco|seattle|austin|boston|chicago|denver|los angeles|california|texas|washington,? d\.?c|atlanta|nyc|sf bay|bay area|\bUS\b)", re.I)),
    ("Canada", re.compile(r"\b(canada|toronto|vancouver|montreal)\b", re.I)),
    ("Ireland", re.compile(r"\b(ireland|dublin)\b", re.I)),
    ("Germany", re.compile(r"\b(germany|berlin|munich|hamburg)\b", re.I)),
    ("France", re.compile(r"\b(france|paris)\b", re.I)),
    ("Netherlands", re.compile(r"\b(netherlands|amsterdam)\b", re.I)),
    ("Spain", re.compile(r"\b(spain|madrid|barcelona)\b", re.I)),
    ("Poland", re.compile(r"\b(poland|warsaw|krakow)\b", re.I)),
    ("India", re.compile(r"\b(india|bangalore|bengaluru|hyderabad|pune|mumbai|delhi)\b", re.I)),
    ("Singapore", re.compile(r"\bsingapore\b", re.I)),
    ("Australia", re.compile(r"\b(australia|sydney|melbourne)\b", re.I)),
    ("Japan", re.compile(r"\b(japan|tokyo)\b", re.I)),
    ("Israel", re.compile(r"\b(israel|tel aviv)\b", re.I)),
    ("Brazil", re.compile(r"\b(brazil|são paulo|sao paulo)\b", re.I)),
]
REGION_TERMS = re.compile(r"\b(emea|europe|eu|european union|worldwide|global|anywhere)\b", re.I)


def clean_html(raw: str | None) -> str | None:
    if not raw:
        return None
    unescaped = htmllib.unescape(raw)
    return nh3.clean(unescaped, tags={"p", "br", "ul", "ol", "li", "strong", "b", "em", "i", "h1", "h2", "h3", "h4", "a", "span", "div"},
                     attributes={"a": {"href"}}, url_schemes={"https", "http", "mailto"}, link_rel="noopener noreferrer nofollow")


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    t = htmllib.unescape(raw)
    t = re.sub(r"(?i)<\s*(br|/p|/li|/h\d|/div)\s*/?>", "\n", t)
    t = re.sub(r"(?i)<li[^>]*>", "\n- ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = htmllib.unescape(t)
    t = re.sub(r"[ \t\xa0]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n\n", t)
    return t.strip()


# ---------------------------------------------------------------- location
def parse_location(location_raw: str | None, workplace_hint: str | None = None, text: str = "") -> dict[str, Any]:
    loc = (location_raw or "").strip()
    out: dict[str, Any] = {"country": None, "city": None, "workplace_type": None, "remote_eligibility": None}
    low = loc.lower()
    # workplace type
    wp = (workplace_hint or "").lower()
    if wp in ("remote",):
        out["workplace_type"] = "remote"
    elif wp in ("hybrid",):
        out["workplace_type"] = "hybrid"
    elif wp in ("onsite", "on-site", "office", "inoffice", "in office"):
        out["workplace_type"] = "onsite"
    elif "hybrid" in low:
        out["workplace_type"] = "hybrid"
    elif "remote" in low:
        out["workplace_type"] = "remote"
    countries = [name for name, rx in COUNTRY_TERMS if rx.search(loc)]
    if len(countries) == 1:
        out["country"] = countries[0]
    elif len(countries) > 1:
        out["country"] = "Multiple"
    for city in UK_CITIES:
        if re.search(rf"\b{re.escape(city)}\b", low):
            out["city"] = city.title()
            break
    if out["city"] is None:
        m = re.match(r"^\s*([A-Z][A-Za-z .'-]+?)(,|\s-|\s\(|$)", loc)
        if m and m.group(1).lower() not in ("remote", "hybrid", "united kingdom", "united states", "uk", "us", "emea", "europe"):
            out["city"] = m.group(1).strip()
    if out["workplace_type"] == "remote":
        if out["country"] and out["country"] != "Multiple":
            out["remote_eligibility"] = out["country"]
        elif out["country"] == "Multiple":
            out["remote_eligibility"] = ", ".join(countries)
        else:
            r = REGION_TERMS.search(loc)
            out["remote_eligibility"] = r.group(1).upper() if r else None  # unknown stays None
    return out


def uk_presence(location_raw: str | None, secondary: list[str] | None = None) -> bool:
    blob = " | ".join([location_raw or ""] + (secondary or []))
    return bool(UK_TERMS.search(blob))


# ---------------------------------------------------------------- office days
WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


def parse_office_days(text: str) -> float | None:
    t = text.lower()
    m = re.search(
        r"\b(\d|one|two|three|four|five)(?:\s*(?:-|to)\s*(\d|one|two|three|four|five))?\s*(?:days?|x)\s*(?:a|per|each)?\s*(?:week|wk)?\s*(?:in|at|from)?\s*(?:the|our|one of our)?\s*(?:[a-z]+\s+){0,2}(?:office|offices|hub|onsite|on-site|in person|in-person)",
        t,
    )
    if not m:
        m = re.search(r"(?:office|on-?site|in[- ]person)[^.\n]{0,40}?\b(\d|one|two|three|four|five)\s*days?\s*(?:a|per)\s*week", t)
        if not m:
            return None
        v = m.group(1)
        return float(WORD_NUM.get(v, v))
    lo = m.group(1)
    hi = m.group(2) or lo
    return float(WORD_NUM.get(hi, hi))  # conservative: the higher end


# ---------------------------------------------------------------- salary
CURRENCY_SYMBOLS = {"£": "GBP", "$": "USD", "€": "EUR"}


def _num(s: str, k: bool) -> float:
    v = float(s.replace(",", ""))
    return v * 1000 if k or v < 1000 else v


def parse_salary_text(text: str) -> dict[str, Any] | None:
    """Find an explicit annual salary range in free text. Returns None if absent/ambiguous."""
    pat = re.compile(
        r"(?P<cur>£|\$|€|GBP|USD|EUR)\s?(?P<a>\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d+)?)\s?(?P<ak>k)?\s*(?:-|–|—|to)\s*(?:(?P<cur2>£|\$|€|GBP|USD|EUR)\s?)?(?P<b>\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d+)?)\s?(?P<bk>k)?",
        re.I,
    )
    for m in pat.finditer(text):
        cur = m.group("cur").upper()
        cur = CURRENCY_SYMBOLS.get(cur, cur)
        a = _num(m.group("a"), bool(m.group("ak") or m.group("bk")))
        b = _num(m.group("b"), bool(m.group("bk") or m.group("ak")))
        window = text[max(0, m.start() - 80): m.end() + 60].lower()
        if any(w in window for w in ("per hour", "/hr", "hourly", "per day", "day rate", "/day", "per month")):
            continue
        if any(w in window for w in ("ote", "on-target", "on target", "total compensation", "funding", "raised", "revenue", "arr", "valuation", "million", "billion")):
            if "base" not in window:
                continue
        if 15_000 <= a <= 1_000_000 and a <= b <= 1_500_000:
            return {"salary_min": a, "salary_max": b, "salary_currency": cur, "salary_period": "year"}
    return None


# ---------------------------------------------------------------- employment / seniority
def parse_employment_type(raw: str | None, text: str = "") -> str | None:
    r = (raw or "").lower().replace("_", "").replace("-", "").replace(" ", "")
    mapping = {"fulltime": "full_time", "parttime": "part_time", "contract": "contract", "intern": "internship",
               "internship": "internship", "temporary": "temporary", "contractor": "contract"}
    if r in mapping:
        return mapping[r]
    # Only explicit statements count; otherwise unknown.
    t = (raw or "") + " " + text[:6000]
    if re.search(r"\b(this is a|this role is a|on a)\s+(\d+[- ]month\s+)?(fixed[- ]term|contract|temporary)\b|\b\d+[- ]month (fixed[- ]term )?contract\b", t, re.I):
        return "contract"
    if re.search(r"\b(this is a|this role is a|this is a permanent,?)\s+part[- ]time\b|\bpart[- ]time (role|position)\b", t, re.I):
        return "part_time"
    if re.search(r"\bintern(ship)?\b", raw or "", re.I):
        return "internship"
    if re.search(r"\bfull[- ]time\b|\bpermanent (role|position|contract)\b", t, re.I):
        return "full_time"
    return None


def parse_seniority(title: str) -> str | None:
    t = title.lower()
    for level, rx in [
        ("executive", r"\b(chief|vp|vice president|head of|director)\b"),
        ("principal", r"\b(principal|distinguished|fellow)\b"),
        ("staff", r"\bstaff\b"),
        ("lead", r"\b(lead|manager)\b"),
        ("senior", r"\b(senior|sr\.?|snr)\b"),
        ("junior", r"\b(junior|jr\.?|graduate|entry|associate|intern)\b"),
    ]:
        if re.search(rx, t):
            return level
    return "mid"


# ---------------------------------------------------------------- role features
ROLE_FAMILIES: list[tuple[str, str]] = [
    ("technical_account_management", r"technical account manager|\btam\b|technical customer success|customer success engineer|customer engineer"),
    ("solutions_presales", r"solutions? (engineer|consultant|architect)|sales engineer|pre-?sales|forward deployed|field engineer|implementation (engineer|consultant)|deployment (engineer|strategist)"),
    ("customer_success", r"customer success|account manager|client success|customer experience"),
    ("support", r"support engineer|technical support|support specialist|customer support|helpdesk|service desk"),
    ("sre_platform", r"site reliability|\bsre\b|platform engineer|devops|infrastructure engineer|cloud engineer|production engineer|systems engineer|network engineer"),
    ("software_engineering", r"software engineer|developer|backend|front-?end|full[- ]?stack|mobile engineer|ios|android|engineer, "),
    ("data_ml", r"data (scientist|engineer|analyst)|machine learning|\bml\b|research (scientist|engineer)|analytics"),
    ("security", r"security|secops|appsec|threat"),
    ("product", r"product manager|product owner|product lead"),
    ("design", r"designer|\bux\b|\bui\b"),
    ("sales", r"account executive|\bsdr\b|\bbdr\b|business development|sales (manager|director|representative)|partnerships"),
    ("marketing", r"marketing|growth|content|brand|communications"),
    ("operations", r"operations|ops manager|program manager|project manager|chief of staff"),
    ("people_finance_legal", r"recruit|talent|people|hr\b|finance|accountant|legal|counsel|payroll"),
]

CUSTOMER_TERMS = r"\b(customers?|clients?|stakeholders?|pre-?sales|post-?sales|customer-facing|client-facing|account|onboarding|demos?|workshops?|enterprise customers|partners)\b"
CODING_TERMS = r"\b(write (production )?code|coding|software development|build (and ship|features)|python|golang|go\b|java\b|typescript|rust|c\+\+|kotlin|scala|ruby|codebase|pull requests|algorithms)\b"
ONCALL_TERMS = re.compile(r"on[- ]call|pager ?duty rotation|out[- ]of[- ]hours (support|rota)|24/7 (support|rota)|incident rotation|participate in (an|the) (on-call|rotation)", re.I)
NO_ONCALL = re.compile(r"no on[- ]call|without on[- ]call|not (be )?(required to be|expected to be) on[- ]call", re.I)
TRAVEL_TERMS = re.compile(r"(\d{1,2})\s*%\s*travel|travel (up to|of up to)\s*(\d{1,2})\s*%|(frequent|regular|extensive) travel", re.I)
MGMT_TERMS = re.compile(r"\b(manage a team|line manage|people manager|direct reports|lead a team of|managing a team)\b", re.I)
SPONSOR_NO = re.compile(r"(unable to|cannot|can't|do not|does not|will not|won't|not able to) (offer|provide|sponsor)[^.\n]{0,40}(visa|sponsorship)|no (visa )?sponsorship", re.I)

SKILL_VOCAB = [
    "aws", "azure", "gcp", "google cloud", "kubernetes", "docker", "terraform", "ansible", "linux", "networking", "tcp/ip", "dns",
    "bgp", "python", "go", "java", "javascript", "typescript", "sql", "postgresql", "mysql", "mongodb", "redis", "kafka",
    "spark", "react", "node.js", "rest apis", "apis", "graphql", "ci/cd", "jenkins", "github actions", "prometheus", "grafana",
    "datadog", "splunk", "observability", "monitoring", "incident management", "security", "iam", "sso", "saml", "oauth",
    "salesforce", "zendesk", "jira", "customer success", "pre-sales", "solution design", "stakeholder management",
    "technical writing", "project management", "agile", "scrum", "itil", "saas", "cloud infrastructure", "microservices",
    "machine learning", "llms", "data analysis", "excel", "presentations", "account management", "troubleshooting",
    "bash", "powershell", "vmware", "cisco", "firewalls", "load balancing", "cdn", "helm", "openshift", "serverless",
]


_SKILL_RX = re.compile(
    r"(?<![a-z0-9])(" + "|".join(re.escape(s) for s in sorted(SKILL_VOCAB, key=len, reverse=True)) + r")(?![a-z0-9])")


def detect_skills(text: str) -> list[str]:
    hits = set(_SKILL_RX.findall(text.lower()))
    return [s for s in SKILL_VOCAB if s in hits]


def role_family(title: str) -> str:
    t = title.lower()
    for fam, rx in ROLE_FAMILIES:
        if re.search(rx, t):
            return fam
    return "other"


def compute_features(title: str, text: str) -> dict[str, Any]:
    low = text.lower()
    cust_hits = len(re.findall(CUSTOMER_TERMS, low))
    code_hits = len(re.findall(CODING_TERMS, low))
    words = max(len(low.split()), 1)
    fam = role_family(title)
    on_call = None
    if NO_ONCALL.search(text):
        on_call = False
    elif ONCALL_TERMS.search(text) or re.search(r"\bon[- ]call\b", title, re.I):
        on_call = True
    travel = None
    tm = TRAVEL_TERMS.search(text)
    if tm:
        pct = next((g for g in tm.groups() if g and g.isdigit()), None)
        travel = int(pct) if pct else 30
    customer_facing = min(1.0, cust_hits / max(6.0, words / 120)) if words else 0.0
    if fam in ("technical_account_management", "solutions_presales", "customer_success", "support", "sales"):
        customer_facing = max(customer_facing, 0.8)
    coding_heavy = min(1.0, code_hits / max(5.0, words / 150))
    if fam in ("software_engineering",):
        coding_heavy = max(coding_heavy, 0.8)
    return {
        "role_family": fam,
        "customer_facing": round(customer_facing, 2),
        "coding_heavy": round(coding_heavy, 2),
        "on_call": on_call,
        "travel_pct": travel,
        "management": bool(MGMT_TERMS.search(text)),
        "no_visa_sponsorship": bool(SPONSOR_NO.search(text)),
        "skills": detect_skills(title + "\n" + text),
    }
