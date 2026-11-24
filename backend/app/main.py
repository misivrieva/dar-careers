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
from .workers.tasks import Worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for noisy in ("httpx", "openai", "anthropic"):
    logging.getLogger(noisy).setLevel(logging.WARNING)  # keep personal data out of logs
worker = Worker()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_engine()
    with SessionLocal() as db:
        sync_registry(db)
    if get_settings().worker_enabled:
        worker.start()
    yield
    worker.stop()


app = FastAPI(title="DAR Careers API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(profile.router)
app.include_router(jobs.router)
app.include_router(system.router)


@app.get("/api/health")
def health():
    s = get_settings()
    return {"ok": True, "llm_available": s.llm_available, "demo_mode": s.demo_mode}
