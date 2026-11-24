import io
import zipfile
from pathlib import Path

from helpers import key, onboard, seed_catalogue


def _yes(client, db, alex_cv, title="Technical Account Manager"):
    from app.models import Job

    client.post("/api/cv/upload", files={"file": ("cv.txt", alex_cv, "text/plain")})
    seed_catalogue(db)
    onboard(db)
    j = db.query(Job).filter_by(title=title).one()
    client.post(f"/api/jobs/{j.id}/swipe", json={"decision": "yes", "idempotency_key": key()})
    return j


def test_background_generation_creates_docx_package(client, db, alex_cv):
    from app.models import BackgroundTask
    from app.workers.tasks import drain

    j = _yes(client, db, alex_cv)
    assert db.query(BackgroundTask).filter_by(kind="generate_application").count() == 1
    drain(("generate_application",))
    app = client.get("/api/applications").json()["applications"][0]
    assert app["prep_status"] in ("ready_for_review", "needs_correction")
    assert app["generator"].startswith("template")  # no model in tests: labelled template draft
    names = sorted(d["filename"] for d in app["documents"])
    assert names == ["CV.docx", "Cover_Letter.docx", "Job_Description.txt"]
    assert app["folder"].startswith("applications/1/Testco/Technical_Account_Manager_")
    # individual download
    cv = next(d for d in app["documents"] if d["kind"] == "cv")
    r = client.get(f"/api/documents/{cv['id']}/download")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    import docx

    text = "\n".join(p.text for p in docx.Document(io.BytesIO(r.content)).paragraphs)
    assert "Northwind Cloud Ltd" in text and "Fabrikam Telecom" in text and "Contoso Hosting" in text
    # zip package
    z = client.get(f"/api/applications/{app['id']}/download")
    assert z.status_code == 200
    assert sorted(Path(n).name for n in zipfile.ZipFile(io.BytesIO(z.content)).namelist()) == names


def test_duplicate_generation_prevented_and_regenerate_versions(client, db, alex_cv):
    from app.models import Application, Document
    from app.services import applications as appsvc
    from app.workers.tasks import drain

    j = _yes(client, db, alex_cv)
    drain(("generate_application",))
    app = db.query(Application).one()
    v1 = app.version
    appsvc.generate(db, app.id)  # unchanged inputs -> no new documents
    db.commit()
    assert db.query(Document).count() == 3 and db.get(Application, app.id).version == v1
    client.post(f"/api/applications/{app.id}/regenerate")
    drain(("generate_application",))
    db.expire_all()
    app = db.get(Application, app.id)
    assert app.version == v1 + 1 and db.query(Document).count() == 6
    files = {d["filename"] for d in client.get("/api/applications").json()["applications"][0]["documents"]}
    assert files == {"CV_v2.docx", "Cover_Letter_v2.docx", "Job_Description_v2.txt"}


def test_failed_generation_then_retry(client, db, alex_cv, monkeypatch):
    from app.models import Application
    from app.services import applications as appsvc
    from app.workers import tasks

    _yes(client, db, alex_cv)
    monkeypatch.setattr(tasks, "MAX_TASK_ATTEMPTS", 1)
    real = appsvc._template_generate
    monkeypatch.setattr(appsvc, "_template_generate", lambda *a: (_ for _ in ()).throw(RuntimeError("provider down")))
    monkeypatch.setattr(tasks.time, "sleep", lambda s: None)
    tasks.drain(("generate_application",))
    db.expire_all()
    app = db.query(Application).one()
    assert app.prep_status == "failed" and "provider down" in app.error
    monkeypatch.setattr(appsvc, "_template_generate", real)
    client.post(f"/api/applications/{app.id}/regenerate")
    tasks.drain(("generate_application",))
    db.expire_all()
    assert db.query(Application).one().prep_status in ("ready_for_review", "needs_correction")


def test_mark_applied_is_manual_and_separate_from_preparation(client, db, alex_cv):
    from app.workers.tasks import drain

    _yes(client, db, alex_cv)
    drain(("generate_application",))
    app = client.get("/api/applications").json()["applications"][0]
    r = client.patch(f"/api/applications/{app['id']}", json={"app_status": "applied", "comment": "Applied via site"})
    assert r.json()["app_status"] == "applied" and r.json()["prep_status"] == app["prep_status"]


def test_path_sanitisation():
    from app.services.document_generator import application_folder, safe_component

    assert safe_component("../../etc/passwd") == "etc_passwd"
    assert safe_component("A/B\\C: D?") == "A_B_C_D"
    assert safe_component("") == "unnamed"
    f = application_folder(1, "../../evil", "..\\..\\x", 7)
    assert "evil" in f.parts[-2] and ".." not in f.parts
