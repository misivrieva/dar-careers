"""Find job boards for listed companies whose board slug can't be guessed from their name, using Tavily web search.

Needs TAVILY_API_KEY in .env. For each company recorded as `unsupported` in config/sources/coverage_2026-11-24.json, search
the six supported ATS domains, pull a candidate board id out of any result URL, then verify it with the same public-API
name check used by expand_registry.py. Only verified boards are added to config/employers.yaml.

Usage: uv run --project backend python scripts/discover_boards_tavily.py [--limit 200] [--exchange LSE]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import expand_registry as er  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9._-]+)")),
    ("lever", re.compile(r"jobs\.lever\.co/([A-Za-z0-9._-]+)")),
    ("workable", re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)")),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)")),
    ("recruitee", re.compile(r"https?://([A-Za-z0-9-]+)\.recruitee\.com")),
]
DOMAINS = ["greenhouse.io", "ashbyhq.com", "lever.co", "workable.com", "smartrecruiters.com", "recruitee.com"]


def tavily_key() -> str | None:
    if os.environ.get("TAVILY_API_KEY"):
        return os.environ["TAVILY_API_KEY"]
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("TAVILY_API_KEY="):
            return line.split("=", 1)[1].strip() or None
    return None


def search(key: str, company: str) -> list[str]:
    body = json.dumps({"api_key": key, "query": f"{company} careers jobs", "include_domains": DOMAINS, "max_results": 5}).encode()
    req = urllib.request.Request("https://api.tavily.com/search", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return [x.get("url", "") for x in json.loads(r.read()).get("results", [])]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--exchange", choices=["LSE", "NASDAQ"], default=None)
    a = ap.parse_args()
    key = tavily_key()
    if not key:
        print("TAVILY_API_KEY is not set in .env - nothing to do.")
        return
    cov_path = er.SRC / "coverage_2026-11-24.json"
    cov = json.loads(cov_path.read_text())
    todo = [c for c in cov if c["provider"] == "unsupported" and (not a.exchange or c["exchange"] == a.exchange)]
    todo.sort(key=lambda c: (c["exchange"] != "LSE",))  # UK-listed first
    reg_path = ROOT / "config" / "employers.yaml"
    reg = yaml.safe_load(reg_path.read_text())
    have = {(e["provider"], (e.get("board_id") or "").lower()) for e in reg["employers"]}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    found = 0
    for c in todo[: a.limit]:
        try:
            urls = search(key, c["name"])
        except Exception as e:
            print("search failed:", c["name"], e)
            continue
        for url in urls:
            for prov, rx in PATTERNS:
                m = rx.search(url)
                if not m or (prov, m.group(1).lower()) in have:
                    continue
                hit = er._check(prov, m.group(1), c["name"])
                if hit:
                    c.update(provider=prov, board_id=m.group(1), verified_name=hit[0], jobs=hit[1], uk_jobs=hit[2])
                    have.add((prov, m.group(1).lower()))
                    reg["employers"].append({
                        "id": re.sub(r"[^a-z0-9]+", "-", c["name"].lower()).strip("-")[:60] + "-t", "name": c["name"], "ticker": c.get("ticker"),
                        "exchanges": [c["exchange"]], "nasdaq100": False, "industry": None, "provider": prov, "board_id": m.group(1),
                        "enabled": True, "verification_status": "verified", "verified_at": now, "careers_url": url,
                        "geographic_focus": "UK" if hit[2] >= 10 else None, "uk_jobs_at_verification": hit[2],
                        "notes": f"Found via Tavily search, verified via {prov} public API ('{hit[0]}', {hit[1]} jobs, {hit[2]} UK)"})
                    found += 1
                    print(f"+ {c['name']} -> {prov}:{m.group(1)} ({hit[1]} jobs, {hit[2]} UK)")
                    break
            if c["provider"] != "unsupported":
                break
    reg_path.write_text(yaml.safe_dump(reg, sort_keys=False, allow_unicode=True))
    cov_path.write_text(json.dumps(cov, indent=1))
    print(f"searched {min(a.limit, len(todo))} companies, added {found} verified boards")


if __name__ == "__main__":
    main()
