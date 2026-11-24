"""Build config/employers.yaml by verifying Ashby/Greenhouse boards against the live public APIs.

Nasdaq-100 constituents: config/sources/nasdaq100_2026-11-24.json, parsed from Wikipedia
"List of NASDAQ-100 companies" (retrieved 2026-11-24). Each candidate board is verified by a successful API
response and (for Greenhouse) the board's own company name. Unverified employers are recorded as unsupported.

Usage: uv run --project backend python scripts/build_registry.py
"""
import json, re, sys, urllib.request, concurrent.futures as cf
from datetime import datetime, timezone
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
UA = {"User-Agent": "DAR-Careers/0.1 (registry verification)"}

# Nasdaq-100 boards verified by slug probe + board name check (ticker -> (provider, board, verified name))
N100_BOARDS = {
    "ABNB": ("greenhouse", "airbnb"), "ALNY": ("greenhouse", "alnylampharmaceuticals"), "APP": ("greenhouse", "applovin"),
    "ALAB": ("greenhouse", "asteralabs"), "AXON": ("greenhouse", "axon"), "CRWV": ("greenhouse", "coreweave"),
    "DDOG": ("greenhouse", "datadog"), "NBIS": ("greenhouse", "nebius"), "RKLB": ("greenhouse", "rocketlab"),
    "SPCX": ("greenhouse", "spacex"), "DASH": ("greenhouse", "doordashusa"), "TTWO": ("greenhouse", "taketwo"),
}
INDUSTRY = {
    "ABNB": "Travel tech", "ALNY": "Biotech", "APP": "Adtech", "ALAB": "Semiconductors", "AXON": "Public safety tech",
    "CRWV": "Cloud infrastructure", "DDOG": "Observability SaaS", "NBIS": "AI cloud infrastructure", "RKLB": "Aerospace",
    "SPCX": "Aerospace", "DASH": "Marketplace", "TTWO": "Gaming",
}
# Supplementary (non-Nasdaq-100) technology employers with UK hiring, verified by probe
SUPPLEMENTARY = [
    ("greenhouse", "monzo", "Fintech"), ("greenhouse", "stripe", "Fintech"), ("greenhouse", "gitlab", "Developer tools"),
    ("greenhouse", "twilio", "Communications SaaS"), ("greenhouse", "okta", "Identity security"), ("greenhouse", "elastic", "Search & observability"),
    ("greenhouse", "mongodb", "Databases"), ("greenhouse", "anthropic", "AI"), ("ashby", "openai", "AI"),
    ("ashby", "synthesia", "AI video"), ("ashby", "elevenlabs", "AI audio"), ("ashby", "vanta", "Security compliance"),
    ("ashby", "posthog", "Product analytics"), ("ashby", "snyk", "Developer security"), ("greenhouse", "deliveroo", "Marketplace"),
    ("ashby", "multiverse", "Edtech"), ("ashby", "cohere", "AI"), ("ashby", "harvey", "Legal AI"), ("ashby", "wayve", "Autonomous driving"),
    ("greenhouse", "polyai", "Conversational AI"), ("greenhouse", "tide", "Fintech"), ("ashby", "paddle", "Payments"),
    ("greenhouse", "cleo", "Fintech"), ("greenhouse", "gocardless", "Payments"), ("greenhouse", "figma", "Design tools"),
    ("ashby", "notion", "Productivity SaaS"), ("ashby", "plaid", "Fintech"), ("greenhouse", "intercom", "Customer service SaaS"),
    ("greenhouse", "contentful", "Content platform"), ("ashby", "amplitude", "Product analytics"), ("greenhouse", "mixpanel", "Product analytics"),
    ("greenhouse", "pagerduty", "Incident management"), ("greenhouse", "databricks", "Data & AI platform"), ("ashby", "clickhouse", "Databases"),
    ("ashby", "supabase", "Developer platform"), ("greenhouse", "vercel", "Developer platform"), ("greenhouse", "grafanalabs", "Observability"),
    ("ashby", "temporal", "Developer platform"), ("greenhouse", "fivetran", "Data integration"), ("greenhouse", "scaleai", "AI data"),
    ("ashby", "perplexity", "AI"), ("ashby", "sierra", "Conversational AI"), ("ashby", "writer", "AI"), ("greenhouse", "sumup", "Payments"),
    ("ashby", "oaknorth", "Fintech"), ("ashby", "trainline", "Travel tech"), ("ashby", "motorway", "Marketplace"), ("ashby", "pleo", "Fintech"),
    ("greenhouse", "graphcore", "Semiconductors"), ("ashby", "quantexa", "Data analytics"), ("ashby", "incident", "Incident management"),
    ("greenhouse", "zoominfo", "Sales intelligence"), ("greenhouse", "coinbase", "Crypto"), ("greenhouse", "reddit", "Social media"),
    ("greenhouse", "dropbox", "Productivity SaaS"), ("greenhouse", "hubspotjobs", "CRM SaaS"), ("greenhouse", "pinterest", "Social media"),
    ("greenhouse", "samsara", "IoT SaaS"), ("greenhouse", "launchdarkly", "Developer tools"), ("ashby", "sanity", "Content platform"),
    ("greenhouse", "braze", "Customer engagement"), ("greenhouse", "6sense", "Sales intelligence"), ("greenhouse", "algolia", "Search SaaS"),
    ("greenhouse", "cockroachlabs", "Databases"), ("greenhouse", "jfrog", "Developer tools"), ("ashby", "docker", "Developer tools"),
]

NAME_OVERRIDES = {"incident": "incident.io", "temporal": "Temporal Technologies", "docker": "Docker", "notion": "Notion",
    "multiverse": "Multiverse", "paddle": "Paddle", "sanity": "Sanity", "motorway": "Motorway", "plaid": "Plaid",
    "cohere": "Cohere", "harvey": "Harvey", "quantexa": "Quantexa", "pleo": "Pleo", "trainline": "Trainline",
    "oaknorth": "OakNorth", "clickhouse": "ClickHouse", "supabase": "Supabase", "amplitude": "Amplitude", "vanta": "Vanta",
    "synthesia": "Synthesia", "wayve": "Wayve", "openai": "OpenAI", "writer": "WRITER", "hubspotjobs": "HubSpot",
    "doordashusa": "DoorDash"}

UK_RX = re.compile(r"\b(UK|United Kingdom|London|England|Scotland|Wales|Manchester|Edinburgh|Cambridge|Bristol|Leeds|Belfast|Oxford|Glasgow)\b", re.I)

def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
            return r.status, json.loads(r.read())
    except Exception as e:
        return getattr(e, "code", 0), None

def verify(provider, board, _tries=3):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if provider == "greenhouse":
        s, d = get(f"https://boards-api.greenhouse.io/v1/boards/{board}")
        s2, j = get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs")
        if s == 200 and d and s2 == 200:
            uk = sum(1 for x in j.get("jobs", []) if UK_RX.search((x.get("location") or {}).get("name") or ""))
            return {"verified_name": d.get("name"), "jobs": len(j.get("jobs", [])), "uk_jobs": uk, "verified_at": now,
                    "careers_url": f"https://job-boards.greenhouse.io/{board}"}
    else:
        s, d = get(f"https://api.ashbyhq.com/posting-api/job-board/{board}")
        if s == 200 and d and "jobs" in d:
            name = None
            for x in d["jobs"]:
                m = re.match(r"https://jobs\.ashbyhq\.com/([^/]+)/", x.get("jobUrl", ""))
                if m:
                    name = urllib.request.unquote(m.group(1)); break
            uk = sum(1 for x in d["jobs"] if UK_RX.search(" ".join([x.get("location") or ""] + [s.get("location", "") for s in x.get("secondaryLocations") or []])))
            return {"verified_name": name, "jobs": len(d["jobs"]), "uk_jobs": uk, "verified_at": now,
                    "careers_url": f"https://jobs.ashbyhq.com/{board}"}
    if _tries > 1:
        import time; time.sleep(1.5)
        return verify(provider, board, _tries - 1)
    return None

def main():
    n100 = json.loads((ROOT / "config/sources/nasdaq100_2026-11-24.json").read_text())
    seen, rows = set(), []
    tasks = []
    for c in n100:
        if c["ticker"] in ("GOOG",):  # duplicate share class
            continue
        tasks.append(("n100", c))
    for prov, board, ind in SUPPLEMENTARY:
        tasks.append(("supp", (prov, board, ind)))
    def work(t):
        kind, c = t
        if kind == "n100":
            pb = N100_BOARDS.get(c["ticker"])
            v = verify(*pb) if pb else None
            return kind, c, pb, v
        prov, board, ind = c
        return kind, c, (prov, board), verify(prov, board)
    with cf.ThreadPoolExecutor(12) as ex:
        results = list(ex.map(work, tasks))
    for kind, c, pb, v in results:
        if kind == "n100":
            slug = re.sub(r"[^a-z0-9]+", "-", c["name"].lower()).strip("-")
            row = {"id": slug, "name": c["name"], "ticker": c["ticker"], "nasdaq100": True,
                   "industry": INDUSTRY.get(c["ticker"]), "geographic_focus": None}
            if pb and v:
                row.update({"provider": pb[0], "board_id": pb[1], "enabled": True, "verification_status": "verified",
                            "verified_at": v["verified_at"], "careers_url": v["careers_url"],
                            "uk_jobs_at_verification": v["uk_jobs"],
                            "notes": f"Board name '{v['verified_name']}', {v['jobs']} jobs ({v['uk_jobs']} UK) at verification"})
            else:
                row.update({"provider": "unsupported", "board_id": None, "enabled": False,
                            "verification_status": "no_supported_board_found", "detected_ats": None,
                            "notes": "No Ashby or Greenhouse board found by slug probe; ATS not verified."})
        else:
            prov, board, ind = c
            if not v:
                print("FAILED supplementary", board, file=sys.stderr); continue
            name = NAME_OVERRIDES.get(board) or v["verified_name"] or board.title()
            row = {"id": board, "name": name, "nasdaq100": False, "industry": ind, "provider": prov, "board_id": board,
                   "enabled": True, "verification_status": "verified", "verified_at": v["verified_at"],
                   "careers_url": v["careers_url"], "geographic_focus": "UK" if v["uk_jobs"] >= 10 else None,
                   "uk_jobs_at_verification": v["uk_jobs"],
                   "notes": f"Supplementary employer; {v['jobs']} jobs ({v['uk_jobs']} UK) at verification"}
        if row["id"] in seen:
            row["id"] += "-2"
        seen.add(row["id"])
        rows.append(row)
    out = {"version": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
           "nasdaq100_source": "Wikipedia 'List of NASDAQ-100 companies', retrieved 2026-11-24",
           "employers": rows}
    (ROOT / "config/employers.yaml").write_text(yaml.safe_dump(out, sort_keys=False, allow_unicode=True))
    v = sum(1 for r in rows if r["verification_status"] == "verified")
    print(f"{len(rows)} employers, {v} verified boards ({sum(1 for r in rows if r['nasdaq100'] and r['enabled'])} Nasdaq-100)")

if __name__ == "__main__":
    main()
