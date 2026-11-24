"""Expand the employer registry to all Nasdaq-listed companies and London-listed companies.

Sources (saved in config/sources/):
- Nasdaq: nasdaqtrader.com symbol directory `nasdaqlisted.txt` (all Nasdaq-listed securities, 2026-11-24).
  ETFs, test issues, warrants, units, rights, notes and preferred shares are removed; one row per company.
- London: FTSE 100, FTSE 250 and FTSE SmallCap constituents (Wikipedia, 2026-11-24), with ICB sectors.
  Optional: `--lse-xlsx PATH` imports the London Stock Exchange's own "Issuer list" spreadsheet for full coverage
  (the LSE website blocks automated downloads, so it has to be downloaded in a browser).

For each company we probe likely Ashby and Greenhouse board slugs and keep a board only when
(1) the public API answers with at least one job and (2) the board's own company name matches the listed company.
Nothing is assumed: companies without a verified board are recorded as unsupported in the coverage report.

Usage: uv run --project backend python scripts/expand_registry.py [--lse-xlsx PATH] [--limit N]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "config" / "sources"
UA = {"User-Agent": "DAR-Careers/0.1 (registry verification; public job board APIs)"}
UK_RX = re.compile(r"\b(UK|United Kingdom|London|England|Scotland|Wales|Manchester|Edinburgh|Cambridge|Bristol|Leeds|Belfast|Oxford|Glasgow|Reading|Birmingham)\b", re.I)
SUFFIX = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|llc|lp|l\.p|n\.v|nv|s\.a|sa|se|ag|ab|asa|oyj|holdings?|group|"
    r"the|class [a-z]|ordinary shares?|common stock|american depositary shares?|ads|adr|shares?|reit|trust|international|intl|"
    r"technologies|technology|tech|systems|enterprises?|industries|global|worldwide|& co)\b\.?", re.I)
_lock = threading.Lock()
_last = [0.0]


GENERIC_EXTRA = {"usa", "us", "uk", "bank", "labs", "lab", "ai", "careers", "jobs", "hq", "plc", "group", "and", "co", "io", "app", "hiring"}


def norm_name(s: str) -> str:
    s = s.lower().replace("&", " and ")
    s = SUFFIX.sub(" ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def name_match(listed: str, board: str | None) -> bool:
    if not board:
        return False
    a, b = norm_name(listed), norm_name(board)
    if not a or not b:
        return False
    if a == b or a.replace(" ", "") == b.replace(" ", ""):
        return True
    ta, tb = set(a.split()), set(b.split())
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    extra = big - small
    # only allow generic extra words, e.g. "DoorDash USA" vs "DoorDash", "Monzo Bank" vs "Monzo"
    return bool(small) and small <= big and max(len(w) for w in small) >= 4 and extra <= GENERIC_EXTRA


def slugs(name: str) -> list[str]:
    n = norm_name(name)
    if not n:
        return []
    words = n.split()
    out = ["".join(words), "-".join(words)]
    if len(words[0]) >= 4:
        out.append(words[0])
    if len(words) >= 2:
        out.append("".join(words[:2]))
    raw = re.sub(r"[^a-z0-9]+", "", name.lower().split(" - ")[0])
    out.append(raw)
    return [s for s in dict.fromkeys(out) if 3 <= len(s) <= 40]


def get(url: str):
    with _lock:  # polite global rate: <= ~25 requests/second
        wait = 0.025 - (time.monotonic() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(e.headers.get("Retry-After") or 2 ** attempt))
                continue
            return e.code, None
        except Exception:
            time.sleep(1 + attempt)
    return 0, None


def _uk(locs: list[str]) -> int:
    return sum(1 for l in locs if UK_RX.search(l or "") and not re.search(r",\s*(MA|PA|AL|NH|CT|NY|CA|TX)\b", l or ""))


def _check(provider: str, slug: str, name: str):
    """Return (verified_name, jobs, uk_jobs) if this slug is a verified board for `name`, else None."""
    q = urllib.parse.quote(slug)
    if provider == "greenhouse":
        s, d = get(f"https://boards-api.greenhouse.io/v1/boards/{q}/jobs")
        if s == 200 and d and d.get("jobs"):
            _, meta = get(f"https://boards-api.greenhouse.io/v1/boards/{q}")
            bn = (meta or {}).get("name")
            if name_match(name, bn):
                return bn, len(d["jobs"]), _uk([(j.get("location") or {}).get("name") for j in d["jobs"]])
    elif provider == "ashby":
        s, d = get(f"https://api.ashbyhq.com/posting-api/job-board/{q}")
        if s == 200 and d and d.get("jobs"):
            org = next((urllib.parse.unquote(m.group(1)) for j in d["jobs"] if (m := re.match(r"https://jobs\.ashbyhq\.com/([^/]+)/", j.get("jobUrl", "")))), None)
            if name_match(name, org) or name_match(name, (org or "").replace("-", " ")):
                return org, len(d["jobs"]), _uk([" ".join([j.get("location") or ""] + [x.get("location", "") for x in j.get("secondaryLocations") or []]) for j in d["jobs"]])
    elif provider == "lever":
        s, d = get(f"https://api.lever.co/v0/postings/{q}?mode=json")
        if s == 200 and isinstance(d, list) and d:
            # Lever exposes no company name: require the company's name in most posting texts
            key = norm_name(name)
            texts = [norm_name((p.get("descriptionPlain") or "") + " " + (p.get("additionalPlain") or "")) for p in d[:10]]
            if key and sum(1 for x in texts if f" {key} " in f" {x} ") >= max(1, len(texts) // 2):
                return name, len(d), _uk([(p.get("categories") or {}).get("location") for p in d])
    elif provider == "workable":
        s, d = get(f"https://apply.workable.com/api/v1/widget/accounts/{q}")
        if s == 200 and d and d.get("jobs") and name_match(name, d.get("name")):
            return d.get("name"), len(d["jobs"]), _uk([f"{j.get('city')}, {j.get('country')}" for j in d["jobs"]])
    elif provider == "smartrecruiters":
        s, d = get(f"https://api.smartrecruiters.com/v1/companies/{q}/postings?limit=1")
        if s == 200 and d and d.get("totalFound"):
            cn = ((d.get("content") or [{}])[0].get("company") or {}).get("name")
            if name_match(name, cn):
                s2, uk = get(f"https://api.smartrecruiters.com/v1/companies/{q}/postings?country=gb&limit=1")
                return cn, d["totalFound"], (uk or {}).get("totalFound", 0)
    elif provider == "recruitee":
        s, d = get(f"https://{q}.recruitee.com/api/offers/")
        if s == 200 and d and d.get("offers") and name_match(name, d["offers"][0].get("company_name")):
            return d["offers"][0].get("company_name"), len(d["offers"]), _uk([o.get("location") for o in d["offers"]])
    return None


ALL_PROVIDERS = ["greenhouse", "ashby", "lever", "workable", "smartrecruiters", "recruitee"]


def probe(company: dict, providers: list[str] | None = None) -> dict:
    res = {**company, "provider": "unsupported", "board_id": None, "verified_name": None, "jobs": 0, "uk_jobs": 0}
    for prov in providers or ALL_PROVIDERS:
        for slug in slugs(company["name"]):
            hit = _check(prov, slug, company["name"])
            if hit:
                res.update(provider=prov, board_id=slug, verified_name=hit[0], jobs=hit[1], uk_jobs=hit[2])
                return res
    return res


def nasdaq_companies() -> list[dict]:
    rows = (SRC / "nasdaqlisted_2026-11-24.txt").read_text().splitlines()[1:-1]
    bad = re.compile(r"\b(warrants?|units?|rights?|notes?|debentures?|preferred|perpetual|subordinated|due 20\d\d|depositary shares,? each representing a 1/)\b", re.I)
    out, seen = [], set()
    for line in rows:
        sym, name, _cat, test, _fin, _lot, etf, *_ = line.split("|")
        if etf == "Y" or test == "Y" or bad.search(name) or re.search(r"\bacquisition (corp|company|co)\b|\bspac\b|capital trust", name, re.I):
            continue
        company = re.split(r"\s+-\s+", name)[0].strip()
        company = re.sub(r"\s+(Class [A-Z]|Common Stock|Ordinary Shares?|American Depositary Shares?).*$", "", company, flags=re.I).strip(" ,.")
        key = norm_name(company)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({"name": company, "ticker": sym, "exchange": "NASDAQ"})
    return out


def lse_companies(xlsx: str | None) -> list[dict]:
    out, seen = [], set()
    for pg, idx in (("FTSE_100_Index", "FTSE 100"), ("FTSE_250_Index", "FTSE 250"), ("FTSE_SmallCap_Index", "FTSE SmallCap")):
        t = (SRC / f"wikipedia_{pg}_2026-11-24.txt").read_text()
        i = t.find('id="constituents"')
        for row in t[i: t.find("|}", i)].split("\n|-")[2:]:
            cells = [c.strip() for c in re.split(r"\n\||\|\|", row) if c.strip()]
            if len(cells) < 2:
                continue
            m = re.search(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", cells[0])
            name = (m.group(1) if m else cells[0]).strip("| ")
            sector = re.sub(r"<ref.*", "", cells[-1]).strip() if len(cells) >= 3 else None
            if sector and ("{{" in sector or len(sector) > 60):
                sector = None
            key = norm_name(name)
            if key and key not in seen:
                seen.add(key)
                out.append({"name": name, "ticker": cells[1].strip("| "), "exchange": "LSE", "index": idx, "sector": sector})
    if xlsx:
        import openpyxl  # optional: only when importing the official LSE issuer list

        wb = openpyxl.load_workbook(xlsx, read_only=True)
        ws = wb.worksheets[0]
        header = None
        for r in ws.iter_rows(values_only=True):
            vals = [str(v).strip() if v is not None else "" for v in r]
            if header is None:
                if "Company Name" in vals:
                    header = vals
                continue
            row = dict(zip(header, vals))
            name, sector = row.get("Company Name", ""), row.get("ICB Super-Sector") or row.get("ICB Industry") or None
            if not name or "closed end" in (sector or "").lower() or re.search(r"\b(investment trust|vct|income fund|fund ltd)\b", name, re.I):
                continue  # investment vehicles have no employees to hire
            pretty = " ".join(w if (len(w) <= 3 and w.isupper() and w not in ("THE", "AND", "OIL", "GAS")) else w.capitalize() for w in name.split())
            key = norm_name(pretty)
            if key and key not in seen:
                seen.add(key)
                out.append({"name": pretty, "ticker": None, "exchange": "LSE", "index": f"LSE {row.get('Market', '').strip().title()}",
                            "sector": sector})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lse-xlsx")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--new-providers-only", action="store_true",
                    help="re-probe already-probed companies only on Lever/Workable/SmartRecruiters/Recruitee")
    a = ap.parse_args()
    reg_path = ROOT / "config" / "employers.yaml"
    reg = yaml.safe_load(reg_path.read_text())
    existing_boards = {(e["provider"], e.get("board_id")) for e in reg["employers"] if e.get("board_id")}
    existing_names = {norm_name(e["name"]) for e in reg["employers"]}
    companies = nasdaq_companies() + lse_companies(a.lse_xlsx)
    if a.limit:
        companies = companies[: a.limit]
    prev = {}
    cov_path = SRC / "coverage_2026-11-24.json"
    if cov_path.exists():
        prev = {(c["exchange"], norm_name(c["name"])): c for c in json.loads(cov_path.read_text())}
    jobs_plan = []
    for c in companies:
        p = prev.get((c["exchange"], norm_name(c["name"])))
        if p and p["provider"] != "unsupported":
            jobs_plan.append((c, None))  # already verified: keep
        elif p and a.new_providers_only:
            jobs_plan.append((c, ["lever", "workable", "smartrecruiters", "recruitee"]))
        else:
            jobs_plan.append((c, ALL_PROVIDERS))
    todo = [(c, pv) for c, pv in jobs_plan if pv]
    print(f"{len(companies)} companies; probing {len(todo)} …", flush=True)
    t0 = time.time()
    results = [{**prev[(c["exchange"], norm_name(c["name"]))], **c} for c, pv in jobs_plan if not pv]
    with cf.ThreadPoolExecutor(16) as ex:
        for i, r in enumerate(ex.map(lambda cp: probe(cp[0], cp[1]), todo), 1):
            results.append(r)
            if i % 250 == 0:
                print(f"  {i}/{len(todo)} · verified {sum(1 for x in results if x['provider'] != 'unsupported')} · {time.time() - t0:.0f}s", flush=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    added = 0
    # mark exchange membership on existing rows
    by_norm = {norm_name(e["name"]): e for e in reg["employers"]}
    for r in results:
        e = by_norm.get(norm_name(r["name"]))
        if e is not None:
            e.setdefault("exchanges", [])
            if r["exchange"] not in e["exchanges"]:
                e["exchanges"].append(r["exchange"])
    for r in results:
        if r["provider"] == "unsupported" or (r["provider"], r["board_id"]) in existing_boards:
            continue
        if norm_name(r["name"]) in existing_names:
            continue
        existing_boards.add((r["provider"], r["board_id"]))
        existing_names.add(norm_name(r["name"]))
        slug = re.sub(r"[^a-z0-9]+", "-", r["name"].lower()).strip("-")[:60]
        reg["employers"].append({
            "id": slug if slug not in by_norm else f"{slug}-{r['exchange'].lower()}", "name": r["name"], "ticker": r.get("ticker"),
            "exchanges": [r["exchange"]], "listing_index": r.get("index"), "nasdaq100": False, "industry": r.get("sector"),
            "provider": r["provider"], "board_id": r["board_id"], "enabled": True, "verification_status": "verified",
            "verified_at": now, "careers_url": (f"https://job-boards.greenhouse.io/{r['board_id']}" if r["provider"] == "greenhouse"
                                                else f"https://jobs.ashbyhq.com/{r['board_id']}"),
            "geographic_focus": "UK" if r["uk_jobs"] >= 10 else None, "uk_jobs_at_verification": r["uk_jobs"],
            "notes": f"Listed on {r['exchange']}; board name '{r['verified_name']}', {r['jobs']} jobs ({r['uk_jobs']} UK) at verification",
        })
        added += 1
    reg["expansion"] = {"run_at": now, "companies_probed": len(results),
                        "nasdaq_companies": sum(1 for r in results if r["exchange"] == "NASDAQ"),
                        "lse_companies": sum(1 for r in results if r["exchange"] == "LSE"),
                        "verified_boards_found": sum(1 for r in results if r["provider"] != "unsupported"),
                        "new_boards_added": added,
                        "lse_source": "FTSE 100/250/SmallCap constituents (Wikipedia)" + (" + LSE issuer list" if a.lse_xlsx else "")}
    reg_path.write_text(yaml.safe_dump(reg, sort_keys=False, allow_unicode=True))
    cov = cov_path
    cov.write_text(json.dumps([{k: r.get(k) for k in ("name", "ticker", "exchange", "provider", "board_id", "verified_name", "jobs", "uk_jobs")}
                               for r in results], indent=1))
    print(json.dumps(reg["expansion"], indent=1))
    print(f"coverage report: {cov.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
