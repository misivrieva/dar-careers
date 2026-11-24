"""Demo provider: SYNTHETIC vacancies from fixtures/demo_jobs.json. Only active when DAR_DEMO_MODE=true.

Every job it produces carries is_synthetic=True and a "[DEMO]" employer label so it can never be mistaken for live data.
"""
from __future__ import annotations

import json

import httpx

from ..config import ROOT
from .base import FetchResult, JobProvider
from .normalise import finalise

FIXTURE = ROOT / "fixtures" / "demo_jobs.json"


class DemoProvider(JobProvider):
    name = "demo"

    @staticmethod
    def registry_rows() -> list[dict]:
        if not FIXTURE.exists():
            return []
        data = json.loads(FIXTURE.read_text())
        return [
            {"id": e["id"], "name": f"[DEMO] {e['name']}", "provider": "demo", "board_id": e["id"], "enabled": True,
             "verification_status": "synthetic_fixture", "industry": e.get("industry"), "nasdaq100": False,
             "notes": "Synthetic demo employer - not a real vacancy source"}
            for e in data["employers"]
        ]

    async def fetch_board(self, client: httpx.AsyncClient, employer: dict) -> FetchResult:
        data = json.loads(FIXTURE.read_text())
        res = FetchResult(employer_id=employer["id"], ok=True)
        for j in data["jobs"]:
            if j["employer"] != employer["board_id"]:
                continue
            text = j["description"]
            job = {
                "external_id": j["id"], "provider": "demo", "employer_id": employer["id"], "employer_name": employer["name"],
                "title": j["title"], "description_text": text, "description_html": None, "location_raw": j["location"],
                "_workplace_hint": j.get("workplace"), "employment_type": j.get("employment_type", "full_time"),
                "source_url": None, "application_url": None, "is_synthetic": True,
            }
            prov = {"external_id": "synthetic"}
            if j.get("salary"):
                lo, hi, cur = j["salary"]
                job.update({"salary_min": lo, "salary_max": hi, "salary_currency": cur, "salary_period": "year", "salary_disclosed": True})
                prov.update({k: "synthetic" for k in ("salary_min", "salary_max", "salary_currency")})
            res.jobs.append(finalise(job, prov, text))
        return res
