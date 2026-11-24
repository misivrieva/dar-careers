"""Export every company DAR knows about to config/companies_master.csv.

One row per company with its source list, exchange, sector, whether a readable job board was found, and which.
Rerun after `make expand-registry` or `make discover-boards` to refresh it.

Usage: uv run --project backend --with openpyxl python scripts/export_companies.py
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import expand_registry as er  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config" / "companies_master.csv"
SOURCE = {
    "NASDAQ": "Nasdaq Trader symbol directory (nasdaqlisted.txt, 2026-11-24)",
    "FTSE": "Wikipedia FTSE 100/250/SmallCap constituents (2026-11-24)",
    "LSE": "London Stock Exchange Issuer list (as at 30 Sep 2026)",
}
COLS = ["company", "ticker", "exchange", "source_list", "index_or_market", "sector", "nasdaq100", "status", "ats_provider",
        "board_id", "verified_board_name", "jobs_at_check", "uk_jobs_at_check", "careers_url", "in_registry"]


def main() -> None:
    reg = yaml.safe_load((ROOT / "config" / "employers.yaml").read_text())["employers"]
    by_board = {(e["provider"], (e.get("board_id") or "").lower()): e for e in reg if e.get("board_id")}
    by_name = {er.norm_name(e["name"]): e for e in reg}
    cov_path = er.SRC / "coverage_2026-11-24.json"
    cov = {(c["exchange"], er.norm_name(c["name"])): c for c in json.loads(cov_path.read_text())} if cov_path.exists() else {}
    xlsx = ROOT / "Issuer list.xlsx"
    companies = er.nasdaq_companies() + er.lse_companies(str(xlsx) if xlsx.exists() else None)
    rows, seen = [], set()
    for c in companies:
        key = (c["exchange"], er.norm_name(c["name"]))
        if key in seen:
            continue
        seen.add(key)
        p = cov.get(key)
        reg_row = by_board.get((p["provider"], (p.get("board_id") or "").lower())) if p and p.get("board_id") else by_name.get(key[1])
        if p and p["provider"] != "unsupported":
            status = "readable_board_verified"
        elif p:
            status = "no_readable_board_found"
        else:
            status = "not_checked_yet"
        if reg_row and reg_row.get("provider") not in (None, "unsupported") and status != "readable_board_verified":
            status = "readable_board_verified"
            p = {"provider": reg_row["provider"], "board_id": reg_row.get("board_id"), "verified_name": reg_row["name"],
                 "jobs": None, "uk_jobs": reg_row.get("uk_jobs_at_verification")}
        idx = c.get("index") or ""
        src = SOURCE["FTSE"] if idx.startswith("FTSE") else SOURCE[c["exchange"]]
        rows.append({
            "company": c["name"], "ticker": c.get("ticker") or "", "exchange": c["exchange"], "source_list": src,
            "index_or_market": idx, "sector": c.get("sector") or (reg_row or {}).get("industry") or "",
            "nasdaq100": bool((reg_row or {}).get("nasdaq100")), "status": status,
            "ats_provider": (p or {}).get("provider", "") if status == "readable_board_verified" else "",
            "board_id": (p or {}).get("board_id") or "", "verified_board_name": (p or {}).get("verified_name") or "",
            "jobs_at_check": (p or {}).get("jobs") if p and p.get("jobs") is not None else "",
            "uk_jobs_at_check": (p or {}).get("uk_jobs") if p and p.get("uk_jobs") is not None else "",
            "careers_url": (reg_row or {}).get("careers_url") or "", "in_registry": bool(reg_row and reg_row.get("enabled")),
        })
    # Nasdaq-100 rows from the original registry that aren't in the directory parse, plus supplementary employers and feeds
    listed = {er.norm_name(r["company"]) for r in rows}
    for e in reg:
        if er.norm_name(e["name"]) in listed:
            continue
        kind = "aggregator" if e.get("kind") == "aggregator" else ("nasdaq100" if e.get("nasdaq100") else "supplementary")
        rows.append({
            "company": e["name"], "ticker": e.get("ticker") or "", "exchange": "NASDAQ" if e.get("nasdaq100") else "",
            "source_list": {"aggregator": "Job feed / search API", "nasdaq100": "Wikipedia List of NASDAQ-100 companies (2026-11-24)",
                            "supplementary": "Supplementary UK tech employer (hand-picked, verified)"}[kind],
            "index_or_market": "Nasdaq-100" if e.get("nasdaq100") else "", "sector": e.get("industry") or "", "nasdaq100": bool(e.get("nasdaq100")),
            "status": "readable_board_verified" if e.get("provider") not in (None, "unsupported") else "no_readable_board_found",
            "ats_provider": e.get("provider") if e.get("provider") != "unsupported" else "", "board_id": e.get("board_id") or "",
            "verified_board_name": e["name"], "jobs_at_check": "", "uk_jobs_at_check": e.get("uk_jobs_at_verification") or "",
            "careers_url": e.get("careers_url") or "", "in_registry": bool(e.get("enabled")),
        })
    rows.sort(key=lambda r: (r["status"] != "readable_board_verified", r["exchange"], r["company"].lower()))
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    from collections import Counter

    print(f"wrote {len(rows)} rows to {OUT.relative_to(ROOT)}")
    print(Counter((r["exchange"] or "other", r["status"]) for r in rows))


if __name__ == "__main__":
    main()
