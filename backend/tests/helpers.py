"""Test helpers: seed an onboarded user and a small job catalogue without network access."""
import uuid

from app.agents import memory
from app.models import Employer, User, now
from app.providers.normalise import finalise
from app.services.catalogue import upsert_jobs

REQS = {"min_salary": {"amount": 75000, "currency": "GBP"},
        "locations": {"remote_regions": ["UK"], "hybrid_cities": ["London"], "onsite_cities": []},
        "max_office_days": 4, "employment_types": ["full_time"]}

JOBS = [
    ("j1", "Technical Account Manager", "Remote (UK)", "remote", (95000, 110000), "Own enterprise customer relationships. AWS, Kubernetes."),
    ("j2", "Solutions Engineer", "London, United Kingdom", "hybrid", (90000, 100000), "Customer demos and workshops. 2 days a week in the office."),
    ("j3", "Support Engineer", "Remote (UK)", "remote", (45000, 50000), "Support tickets."),
    ("j4", "Platform Engineer", "Remote (US)", "remote", (150000, 170000), "Kubernetes; join the on-call rotation."),
    ("j5", "Customer Success Engineer", "Remote (UK)", "remote", None, "Help customers adopt our platform."),
]


def seed_catalogue(db, emp_id="test-emp"):
    if db.get(Employer, emp_id) is None:
        db.add(Employer(id=emp_id, name="Testco", provider="greenhouse", board_id="testco", enabled=True,
                        verification_status="verified", last_success_at=now()))
        db.flush()
    jobs = []
    for ext, title, loc, wp, sal, desc in JOBS:
        raw = {"external_id": ext, "provider": "greenhouse", "employer_id": emp_id, "employer_name": "Testco", "title": title,
               "description_text": desc, "location_raw": loc, "_workplace_hint": wp, "employment_type": "full_time",
               "source_url": f"https://example.test/{ext}"}
        if sal:
            raw.update(salary_min=sal[0], salary_max=sal[1], salary_currency="GBP", salary_period="year", salary_disclosed=True)
        jobs.append(finalise(raw, {}, desc))
    upsert_jobs(db, db.get(Employer, emp_id), jobs)
    db.commit()


def onboard(db, uid=1, consent=False):
    u = db.get(User, uid) or User(id=uid)
    u.onboarding_stage, u.llm_consent = "done", consent
    db.add(u)
    for k, v in REQS.items():
        memory.set_requirement_trusted(db, uid, k, v)
    memory.set_soft_preference(db, uid, "target_titles", ["Technical Account Manager", "Solutions Engineer", "Customer Success Engineer"])
    db.commit()
    return u


def key():
    return uuid.uuid4().hex


def no_network(monkeypatch):
    from app.providers.base import FetchResult
    from app.services import catalogue

    async def offline(employers):
        return [FetchResult(employer_id=e["id"], ok=False, error="network disabled in tests") for e in employers]

    monkeypatch.setattr(catalogue, "_fetch_all", offline)
