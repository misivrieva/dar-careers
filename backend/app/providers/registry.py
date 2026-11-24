"""Employer registry (YAML -> DB) and provider lookup. Employers can be added without code changes."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Employer
from .ashby import AshbyProvider
from .base import JobProvider
from .demo import DemoProvider
from .greenhouse import GreenhouseProvider

PROVIDERS: dict[str, JobProvider] = {
    "ashby": AshbyProvider(),
    "greenhouse": GreenhouseProvider(),
    "demo": DemoProvider(),
}


def get_provider(name: str) -> JobProvider | None:
    return PROVIDERS.get(name)


def load_registry(path: Path | None = None) -> list[dict]:
    path = path or get_settings().registry_path
    data = yaml.safe_load(path.read_text()) or {}
    return data.get("employers", [])


def _dt(v) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v.replace(tzinfo=None)
    return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None)


def sync_registry(db: Session, path: Path | None = None) -> int:
    """Upsert registry entries. Runtime fetch status columns are preserved."""
    rows = load_registry(path)
    if get_settings().demo_mode:
        rows = rows + DemoProvider.registry_rows()
    for r in rows:
        e = db.get(Employer, r["id"])
        if e is None:
            e = Employer(id=r["id"])
            db.add(e)
        e.name = r["name"]
        e.provider = r.get("provider") or "unsupported"
        e.board_id = r.get("board_id")
        e.careers_url = r.get("careers_url")
        e.industry = r.get("industry")
        e.geographic_focus = r.get("geographic_focus")
        e.nasdaq100 = bool(r.get("nasdaq100"))
        e.detected_ats = r.get("detected_ats")
        e.enabled = bool(r.get("enabled", True)) and e.provider in PROVIDERS
        if get_settings().demo_mode:
            e.enabled = e.provider == "demo"  # demo mode never mixes synthetic and live vacancies
        e.verification_status = r.get("verification_status", "unverified")
        e.verified_at = _dt(r.get("verified_at"))
        e.notes = r.get("notes")
        e.uk_jobs_hint = r.get("uk_jobs_at_verification")
    db.commit()
    return len(rows)
