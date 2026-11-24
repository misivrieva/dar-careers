"""DAR Careers API - modular monolith entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .database import SessionLocal, init_engine
from .providers.registry import sync_registry
from .routes import jobs, profile, system
from .security import check_config, guard
from .workers.tasks import Worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for noisy in ("httpx", "openai", "anthropic"):
    logging.getLogger(noisy).setLevel(logging.WARNING)  # keep personal data out of logs
worker = Worker()


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_config()  # refuse to start a deployment that would be open to the internet
    init_engine()
    with SessionLocal() as db:
        sync_registry(db)
    if get_settings().worker_enabled:
        worker.start()
    yield
    worker.stop()


app = FastAPI(title="DAR Careers API", version="0.1.0", lifespan=lifespan)
app.middleware("http")(guard)
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
                   allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
app.include_router(profile.router)
app.include_router(jobs.router)
app.include_router(system.router)


def _mount_frontend() -> None:
    """Serve the built React app from the same origin as the API (single entry point for the tunnel)."""
    from pathlib import Path

    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    from .config import _resolve

    dist = _resolve(get_settings().frontend_dist)
    if not (dist / "index.html").exists():
        return
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            from fastapi import HTTPException

            raise HTTPException(404)
        f = (dist / full_path).resolve()
        if full_path and f.is_file() and dist.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(dist / "index.html")


@app.get("/api/health")
def health():
    s = get_settings()
    return {"ok": True, "llm_available": s.llm_available, "demo_mode": s.demo_mode}


_mount_frontend()  # registered last so /api routes take precedence
