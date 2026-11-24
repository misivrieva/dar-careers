import asyncio
import json

import httpx
import pytest

from conftest import FIX

ASHBY = json.loads((FIX / "providers" / "ashby_board.json").read_text())
GH = json.loads((FIX / "providers" / "greenhouse_board.json").read_text())
EMP_A = {"id": "acme-a", "name": "Acme", "board_id": "acme", "provider": "ashby"}
EMP_G = {"id": "acme-g", "name": "Acme", "board_id": "acme", "provider": "greenhouse"}


def _fetch(provider, emp, handler):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await provider.fetch_board(c, emp)
    return asyncio.run(go())


def test_valid_ashby_board():
    from app.providers.ashby import AshbyProvider

    res = _fetch(AshbyProvider(), EMP_A, lambda r: httpx.Response(200, json=ASHBY))
    assert res.ok and res.skipped_unlisted == 1
    by = {j.title: j for j in res.jobs}
    tam = by["Technical Account Manager"]
    assert (tam.salary_min, tam.salary_max, tam.salary_currency, tam.salary_period) == (90000, 110000, "GBP", "year")
    assert tam.country == "United Kingdom" and tam.workplace_type == "hybrid" and tam.office_days_per_week == 3
    assert tam.field_provenance["salary_min"] == "provider" and tam.field_provenance["office_days_per_week"] == "extracted"
    assert tam.source_url == "https://jobs.ashbyhq.com/acme/a1" and tam.application_url.endswith("/application")
    se = by["Solutions Engineer"]
    assert se.salary_min is None and se.salary_disclosed is False  # missing salary is None, never 0
    assert se.remote_eligibility == "United Kingdom"
    noid = by["No Id Role"]
    assert noid.external_id.startswith("derived-") and noid.field_provenance["external_id"] == "derived"
    assert noid.location_raw is None and noid.country is None  # missing location stays unknown


def test_valid_greenhouse_board():
    from app.providers.greenhouse import GreenhouseProvider

    res = _fetch(GreenhouseProvider(), EMP_G, lambda r: httpx.Response(200, json=GH))
    assert res.ok and res.malformed == 1
    by = {j.external_id: j for j in res.jobs}
    cse = by["101"]
    assert (cse.salary_min, cse.salary_max, cse.salary_currency) == (85000, 95000, "GBP")
    assert cse.field_provenance["salary_min"] == "extracted"
    assert by["102"].on_call_required is True and by["102"].employment_type == "full_time"
    assert by["102"].field_provenance["employment_type"] == "provider"
    assert "<script" not in (cse.description_html or "")


def test_invalid_board_404():
    from app.providers.greenhouse import GreenhouseProvider

    res = _fetch(GreenhouseProvider(), EMP_G, lambda r: httpx.Response(404, json={"status": 404}))
    assert not res.ok and "404" in res.error and res.jobs == []


def test_timeout_is_reported_not_faked():
    from app.providers.ashby import AshbyProvider

    def boom(r):
        raise httpx.ReadTimeout("slow", request=r)

    res = _fetch(AshbyProvider(), EMP_A, boom)
    assert not res.ok and "network error" in res.error and res.jobs == []


def test_429_respects_retry_after_then_succeeds(monkeypatch):
    from app.providers import base
    from app.providers.ashby import AshbyProvider

    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(base.asyncio, "sleep", fake_sleep)
    calls = {"n": 0}

    def handler(r):
        calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "3"}) if calls["n"] == 1 else httpx.Response(200, json=ASHBY)

    res = _fetch(AshbyProvider(), EMP_A, handler)
    assert res.ok and calls["n"] == 2 and sleeps == [3.0]


def test_5xx_bounded_retries_with_backoff(monkeypatch):
    from app.providers import base
    from app.providers.ashby import AshbyProvider

    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(base.asyncio, "sleep", fake_sleep)
    res = _fetch(AshbyProvider(), EMP_A, lambda r: httpx.Response(503))
    assert not res.ok and len(sleeps) == 2 and sleeps[1] > sleeps[0]  # exponential


def test_malformed_response():
    from app.providers.greenhouse import GreenhouseProvider

    res = _fetch(GreenhouseProvider(), EMP_G, lambda r: httpx.Response(200, content=b"<html>oops"))
    assert not res.ok and "malformed" in res.error
    res = _fetch(GreenhouseProvider(), EMP_G, lambda r: httpx.Response(200, json={"unexpected": True}))
    assert not res.ok and "missing jobs" in res.error


def _seed_employers(db):
    from app.models import Employer

    for e in (EMP_A, EMP_G):
        db.add(Employer(id=e["id"], name=e["name"], provider=e["provider"], board_id=e["board_id"], enabled=True))
    db.commit()


def test_cross_provider_duplicates_merged_with_provenance(db):
    from app.models import Employer, Job
    from app.providers.ashby import AshbyProvider
    from app.providers.greenhouse import GreenhouseProvider
    from app.services.catalogue import upsert_jobs

    _seed_employers(db)
    a = _fetch(AshbyProvider(), EMP_A, lambda r: httpx.Response(200, json=ASHBY))
    g = _fetch(GreenhouseProvider(), EMP_G, lambda r: httpx.Response(200, json=GH))
    upsert_jobs(db, db.get(Employer, "acme-a"), a.jobs)
    st = upsert_jobs(db, db.get(Employer, "acme-g"), g.jobs)
    db.commit()
    assert st["duplicates_merged"] == 1
    dup = db.query(Job).filter(Job.duplicate_of.isnot(None)).one()
    canon = db.get(Job, dup.duplicate_of)
    assert canon.provider == "ashby" and canon.also_seen_at[0]["provider"] == "greenhouse"
    # same title, different content/location: not merged
    assert db.query(Job).filter(Job.title == "Solutions Engineer").count() == 1


def test_closed_vacancy_lifecycle_and_failed_fetch_does_not_close(db, monkeypatch):
    from app.models import Employer, Job
    from app.providers.ashby import AshbyProvider
    from app.services import catalogue

    _seed_employers(db)
    emp = db.get(Employer, "acme-a")
    first = _fetch(AshbyProvider(), EMP_A, lambda r: httpx.Response(200, json=ASHBY))
    catalogue.upsert_jobs(db, emp, first.jobs)
    db.commit()
    fewer = {"jobs": [j for j in ASHBY["jobs"] if j.get("id") != "a2"]}
    second = _fetch(AshbyProvider(), EMP_A, lambda r: httpx.Response(200, json=fewer))
    catalogue.upsert_jobs(db, emp, second.jobs)
    db.commit()
    j = db.query(Job).filter_by(external_id="a2").one()
    assert j.lifecycle == "potentially_closed" and j.active  # not closed after one miss
    # a failed retrieval must not advance the lifecycle
    async def failing(employers):
        from app.providers.base import FetchResult
        return [FetchResult(employer_id="acme-a", ok=False, error="timeout")]
    monkeypatch.setattr(catalogue, "_fetch_all", failing)
    summary = catalogue.refresh_boards(db, ["acme-a"], force=True)
    assert summary["failed"] and db.query(Job).filter_by(external_id="a2").one().lifecycle == "potentially_closed"
    catalogue.upsert_jobs(db, emp, second.jobs)
    db.commit()
    j = db.query(Job).filter_by(external_id="a2").one()
    assert j.lifecycle == "closed" and not j.active


def test_partial_provider_failure(db, monkeypatch):
    from app.models import Job
    from app.providers.base import FetchResult
    from app.services import catalogue

    _seed_employers(db)
    from app.providers.ashby import AshbyProvider

    good = _fetch(AshbyProvider(), EMP_A, lambda r: httpx.Response(200, json=ASHBY))

    async def mixed(employers):
        return [good if e["id"] == "acme-a" else FetchResult(employer_id=e["id"], ok=False, error="HTTP 500 after 3 attempts")
                for e in employers]

    monkeypatch.setattr(catalogue, "_fetch_all", mixed)
    s = catalogue.refresh_boards(db, ["acme-a", "acme-g"], force=True)
    assert s["fetched"] == 1 and len(s["failed"]) == 1 and s["new_jobs"] == 3
    assert db.query(Job).count() == 3


def test_registry_yaml_loads_and_distinguishes_nasdaq(db):
    from app.models import Employer
    from app.providers.registry import load_registry, sync_registry

    rows = load_registry()
    assert any(r["nasdaq100"] and r["provider"] == "unsupported" for r in rows)
    assert all(r["board_id"] for r in rows if r["provider"] in ("ashby", "greenhouse"))
    sync_registry(db)
    assert db.query(Employer).filter_by(provider="unsupported", enabled=True).count() == 0
