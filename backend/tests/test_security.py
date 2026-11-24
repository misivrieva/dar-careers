from helpers import key, onboard, seed_catalogue


def test_invalid_uploads_rejected(client):
    assert client.post("/api/cv/upload", files={"file": ("cv.exe", b"MZ....", "application/octet-stream")}).status_code == 422
    assert client.post("/api/cv/upload", files={"file": ("cv.pdf", b"not a pdf at all", "application/pdf")}).status_code == 422
    assert client.post("/api/cv/upload", files={"file": ("cv.docx", b"nope", "application/msword")}).status_code == 422
    assert client.post("/api/cv/upload", files={"file": ("cv.txt", b"", "text/plain")}).status_code == 422
    r = client.post("/api/cv/upload", files={"file": ("cv.txt", b"too short", "text/plain")})
    assert r.status_code == 422 and "plain text" in r.json()["detail"]


def test_upload_size_limit(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "upload_max_bytes", 1000)
    r = client.post("/api/cv/upload", files={"file": ("cv.txt", b"a " * 2000, "text/plain")})
    assert r.status_code == 422


def test_uploaded_filename_never_used_on_disk(client, alex_cv):
    from app.database import SessionLocal
    from app.models import UploadedDocument

    client.post("/api/cv/upload", files={"file": ("../../../../tmp/owned.txt", alex_cv, "text/plain")})
    d = SessionLocal().query(UploadedDocument).one()
    assert "owned" not in d.path and d.original_filename == "owned.txt"


def test_cross_user_document_and_application_access(client, db, alex_cv):
    from app.models import Application, Job
    from app.workers.tasks import drain

    client.post("/api/cv/upload", files={"file": ("cv.txt", alex_cv, "text/plain")})
    seed_catalogue(db)
    onboard(db)
    j = db.query(Job).filter_by(title="Technical Account Manager").one()
    client.post(f"/api/jobs/{j.id}/swipe", json={"decision": "yes", "idempotency_key": key()})
    drain(("generate_application",))
    app = client.get("/api/applications").json()["applications"][0]
    doc_id = app["documents"][0]["id"]
    other = {"X-DAR-User": "2"}
    assert client.get(f"/api/documents/{doc_id}/download", headers=other).status_code == 404
    assert client.get(f"/api/applications/{app['id']}/download", headers=other).status_code == 404
    assert client.patch(f"/api/applications/{app['id']}", json={"app_status": "withdrawn"}, headers=other).status_code == 404
    assert client.get("/api/applications", headers=other).json()["applications"] == []
    assert client.get("/api/profile", headers=other).json()["profile"] is None


def test_document_path_outside_storage_is_refused(client, db, alex_cv):
    from app.models import Document
    from app.workers.tasks import drain
    from app.models import Job

    client.post("/api/cv/upload", files={"file": ("cv.txt", alex_cv, "text/plain")})
    seed_catalogue(db)
    onboard(db)
    j = db.query(Job).filter_by(title="Technical Account Manager").one()
    client.post(f"/api/jobs/{j.id}/swipe", json={"decision": "yes", "idempotency_key": key()})
    drain(("generate_application",))
    d = db.query(Document).first()
    d.path = "/etc/hosts"
    db.commit()
    assert client.get(f"/api/documents/{d.id}/download").status_code == 404


def _ctx(db):
    from app import llm
    from app.agents.tools import RunContext
    from app.models import AgentRun, User

    seed_catalogue(db)
    u = onboard(db)
    run = AgentRun(user_id=u.id, trigger="test", status="running", activity=[], errors=[])
    db.add(run)
    db.flush()
    return RunContext(db=db, user=db.get(User, u.id), run=run, meter=llm.UsageMeter(max_calls=0))


def test_invalid_tool_arguments_and_disallowed_tools(db):
    from app.agents.tools import execute
    from app.models import AgentToolCall

    ctx = _ctx(db)
    assert execute(ctx, "search_local_jobs", {"limit": 500})[0] == "rejected"
    assert execute(ctx, "search_local_jobs", {"unexpected": 1})[0] == "rejected"
    assert execute(ctx, "submit_application", {"job_id": 1})[0] == "rejected"
    assert execute(ctx, "run_shell", {"cmd": "rm -rf /"})[0] == "rejected"
    st, res = execute(ctx, "search_local_jobs", None, parse_error="invalid JSON arguments")
    assert st == "rejected"
    assert db.query(AgentToolCall).filter_by(status="rejected").count() == 5  # every attempt is traced


def test_agent_cannot_change_hard_requirements(db):
    from app.agents import memory
    from app.agents.tools import execute

    ctx = _ctx(db)
    before = memory.get_requirements(db, ctx.user.id)
    assert execute(ctx, "update_search_strategy", {"titles": ["x"], "feature_adjustments": {"max_office_days": 5}, "rationale": "r"})[0] == "rejected"
    assert execute(ctx, "propose_preference_update", {"preference": "min_salary", "value": "0", "points": 1, "evidence": [1, 2]})[0] == "rejected"
    assert execute(ctx, "update_user_profile", {"fields": {"min_salary": "0"}})[0] == "rejected"
    assert memory.get_requirements(db, ctx.user.id) == before


def test_batch_validation_is_enforced_in_code(db):
    from app.agents.tools import execute
    from app.models import Job

    ctx = _ctx(db)
    bad = db.query(Job).filter_by(title="Support Engineer").one()  # below minimum salary
    assert execute(ctx, "create_discovery_batch", {"job_ids": [bad.id]})[0] == "rejected"
    assert execute(ctx, "create_discovery_batch", {"job_ids": list(range(1, 13))})[0] == "rejected"  # > 10
    good = db.query(Job).filter_by(title="Technical Account Manager").one()
    assert execute(ctx, "create_discovery_batch", {"job_ids": [good.id]})[0] == "ok"


def test_external_actions_need_user_authorisation(db):
    from app.agents.tools import execute
    from app.models import Job

    ctx = _ctx(db)
    j = db.query(Job).filter_by(title="Technical Account Manager").one()
    assert execute(ctx, "enqueue_application_generation", {"job_id": j.id})[0] == "rejected"
    assert execute(ctx, "schedule_discovery_refresh", {})[0] == "rejected"


def test_tool_budget_enforced(db, monkeypatch):
    from app.agents.tools import execute
    from app.config import get_settings

    ctx = _ctx(db)
    monkeypatch.setattr(get_settings(), "agent_max_tool_calls", 2)
    assert execute(ctx, "get_confirmed_requirements", {})[0] == "ok"
    assert execute(ctx, "get_confirmed_requirements", {})[0] == "ok"
    st, res = execute(ctx, "get_confirmed_requirements", {})
    assert st == "rejected" and "budget" in res["error"]


def test_html_sanitised():
    from app.services.job_features import clean_html

    out = clean_html('<p onclick="x()">Hi<script>alert(1)</script><a href="javascript:evil()">l</a><img src=x onerror=y></p>')
    assert "script" not in out and "onclick" not in out and "javascript" not in out and "<img" not in out


def test_delete_personal_data(client, alex_cv):
    client.post("/api/cv/upload", files={"file": ("cv.txt", alex_cv, "text/plain")})
    assert client.get("/api/profile").json()["profile"] is not None
    assert client.delete("/api/profile/data").json()["deleted"]
    assert client.get("/api/profile").json()["profile"] is None
