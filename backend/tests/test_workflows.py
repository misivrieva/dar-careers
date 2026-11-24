from helpers import key, no_network, onboard, seed_catalogue


def test_cv_upload_extraction_and_profile_persistence(client, alex_cv):
    r = client.post("/api/cv/upload", files={"file": ("cv.txt", alex_cv, "text/plain")})
    assert r.status_code == 200
    prof = r.json()["profile"]
    roles = prof["profile"]["data"]["roles"]
    assert [x["employer"] for x in roles] == ["Northwind Cloud Ltd", "Fabrikam Telecom", "Contoso Hosting"]
    assert prof["profile"]["method"] == "heuristic"  # no model configured in tests
    again = client.get("/api/profile").json()
    assert again["profile"]["id"] == prof["profile"]["id"]
    assert any(f["category"] == "skill" and "kubernetes" in f["text"].lower() for f in again["facts"])


def test_onboarding_conversation_completes_and_separates_hard_and_soft(client, alex_cv):
    client.post("/api/cv/upload", files={"file": ("cv.txt", alex_cv, "text/plain")})
    answers = ["Technical Account Manager, Solutions Engineer", "£75k", "100k", "UK remote or London hybrid", "4", "hybrid",
               "full-time", "prefer not", "no", "fintech", "none", "yes"]
    for a in answers:
        r = client.post("/api/onboarding/message", json={"text": a})
        assert r.status_code == 200
    for _ in range(3):
        r = client.post("/api/onboarding/message", json={"text": "", "quick_reply": "skip"})
    assert r.json()["stage"] == "review"
    p = client.get("/api/profile").json()
    assert p["requirements"]["min_salary"]["amount"] == 75000            # hard requirement
    assert p["preferences"]["target_salary"]["amount"] == 100000         # soft preference
    assert p["requirements"]["max_office_days"] == 4
    assert "target_salary" not in p["requirements"]
    r = client.patch("/api/profile", json={"complete_onboarding": True})
    assert r.json()["user"]["onboarding_stage"] == "done"


def test_flexible_answer_is_not_a_hard_requirement(client, alex_cv):
    client.post("/api/cv/upload", files={"file": ("cv.txt", alex_cv, "text/plain")})
    client.post("/api/onboarding/message", json={"text": "TAM"})
    client.post("/api/onboarding/message", json={"text": "", "quick_reply": "flexible"})
    p = client.get("/api/profile").json()
    assert "min_salary" not in p["requirements"]


def _start_batch(client, db, monkeypatch):
    from app.workers.tasks import drain

    no_network(monkeypatch)
    seed_catalogue(db)
    onboard(db)
    s = client.post("/api/discovery/start").json()
    assert s["session"]["status"] == "preparing"
    drain(("discovery",))
    return client.get("/api/discovery/current").json()


def test_discovery_batch_excludes_ineligible_and_flags_uncertain(client, db, monkeypatch):
    cur = _start_batch(client, db, monkeypatch)
    titles = {j["title"]: j for j in cur["jobs"]}
    assert cur["session"]["status"] == "active" and 0 < len(cur["jobs"]) <= 10
    assert "Support Engineer" not in titles and "Platform Engineer" not in titles      # salary / US-only
    assert titles["Customer Success Engineer"]["match"]["eligibility"] == "uncertain"
    run = cur["agent_run"]
    assert run["planner"] == "rules" and run["status"] == "completed"
    assert any("unavailable" in a["text"] for a in run["activity"])  # failed boards are reported, not hidden


def test_swipes_persist_undo_and_resume(client, db, monkeypatch):
    cur = _start_batch(client, db, monkeypatch)
    sid = cur["session"]["id"]
    ids = cur["session"]["remaining"]
    r = client.post(f"/api/jobs/{ids[0]}/swipe", json={"decision": "yes", "idempotency_key": (k := key()), "session_id": sid})
    assert r.json()["duplicate"] is False
    assert client.post(f"/api/jobs/{ids[0]}/swipe", json={"decision": "yes", "idempotency_key": k, "session_id": sid}).json()["duplicate"]
    client.post(f"/api/jobs/{ids[1]}/swipe", json={"decision": "no", "idempotency_key": key(), "session_id": sid})
    client.post(f"/api/jobs/{ids[1]}/feedback", json={"reasons": ["too_much_travel"], "comment": "too much travel"})
    # page reload / resume: state comes from the server
    cur2 = client.get("/api/discovery/current").json()
    assert cur2["session"]["decided"] == {str(ids[0]): "yes", str(ids[1]): "no"} or cur2["session"]["decided"] == {ids[0]: "yes", ids[1]: "no"}
    assert client.post("/api/discovery/start").json()["session"]["id"] == sid  # resume, not a new batch
    # undo most recent
    assert client.post("/api/decisions/undo-last").json()["undone"]
    cur3 = client.get("/api/discovery/current").json()
    assert ids[1] in cur3["session"]["remaining"]
    # yes created an application task (not blocking discovery)
    apps = client.get("/api/applications").json()["applications"]
    assert len(apps) == 1 and apps[0]["prep_status"] == "pending"


def test_maybe_and_change_decision_and_review_lists(client, db, monkeypatch):
    cur = _start_batch(client, db, monkeypatch)
    sid, ids = cur["session"]["id"], cur["session"]["remaining"]
    client.post(f"/api/jobs/{ids[0]}/swipe", json={"decision": "maybe", "idempotency_key": key(), "session_id": sid})
    assert [j["id"] for j in client.get("/api/jobs?decision=maybe").json()["jobs"]] == [ids[0]]
    client.post(f"/api/jobs/{ids[0]}/decision", json={"decision": "yes"})
    assert client.get("/api/jobs?decision=maybe").json()["jobs"] == []
    assert [j["id"] for j in client.get("/api/jobs?decision=yes&sort=salary").json()["jobs"]] == [ids[0]]
    # changing Yes -> No cancels pending generation but keeps the record
    client.post(f"/api/jobs/{ids[0]}/decision", json={"decision": "no"})
    app = client.get("/api/applications").json()["applications"][0]
    assert app["prep_status"] == "cancelled"


def test_batch_completion_and_next_batch_excludes_reviewed(client, db, monkeypatch):
    from app.workers.tasks import drain

    cur = _start_batch(client, db, monkeypatch)
    sid, ids = cur["session"]["id"], list(cur["session"]["remaining"])
    for j in ids:
        client.post(f"/api/jobs/{j}/swipe", json={"decision": "no", "idempotency_key": key(), "session_id": sid})
    done = client.get("/api/discovery/current").json()
    assert done["session"]["status"] == "completed" and done["session"]["summary"]["rejected"] == len(ids)
    client.post("/api/discovery/next")
    drain(("discovery",))
    nxt = client.get("/api/discovery/current").json()
    assert not set(nxt["session"]["job_ids"]) & set(ids)
    assert nxt["session"]["status"] in ("empty", "active")


def test_learning_hypotheses_from_feedback(client, db, monkeypatch):
    cur = _start_batch(client, db, monkeypatch)
    sid, ids = cur["session"]["id"], cur["session"]["remaining"]
    for j in ids[:2]:
        client.post(f"/api/jobs/{j}/swipe", json={"decision": "yes", "idempotency_key": key(), "session_id": sid})
        client.post(f"/api/jobs/{j}/feedback", json={"reasons": ["enjoy_customer_work"]})
    prefs = client.get("/api/preferences").json()["preferences"]
    cf = [p for p in prefs if p["attribute"] == "customer_facing"]
    assert cf and cf[0]["status"] == "proposed" and len(cf[0]["supporting"]) == 2
    # user confirms -> confirmed; requirements untouched
    client.patch(f"/api/preferences/{cf[0]['id']}", json={"action": "confirm"})
    assert client.get("/api/preferences").json()["requirements"]["min_salary"]["amount"] == 75000
