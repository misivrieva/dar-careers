import os
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="dar_test_"))
os.environ.update({
    "DAR_DATABASE_URL": f"sqlite:///{_TMP / 'boot.db'}",
    "DAR_STORAGE_DIR": str(_TMP),
    "DAR_WORKER_ENABLED": "false",
    "DAR_DEMO_MODE": "false",
    "DAR_LLM_API_KEY": "",  # conventional tests never call a model
    "ANTHROPIC_API_KEY": "",
    "DAR_PROVIDER_MAX_RETRIES": "2",
})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "fixtures"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from app.config import get_settings
    from app import database

    monkeypatch.setenv("DAR_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("DAR_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    get_settings.cache_clear()
    database.init_engine()
    s = database.SessionLocal()
    yield s
    s.close()
    get_settings.cache_clear()


@pytest.fixture()
def client(db):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.providers.registry import sync_registry

    sync_registry(db)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def alex_cv() -> bytes:
    return (FIX / "cvs" / "synthetic_alex_morgan.txt").read_bytes()
