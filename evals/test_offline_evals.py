"""Pytest entry point for the offline (deterministic) evaluation cases. Live cases run via `make eval-live`."""
import pytest

from evals import runner


@pytest.fixture(scope="module")
def ctx():
    runner._setup_env("offline")
    from app.database import init_engine

    init_engine()
    c = runner.Ctx("offline", 1)
    c.metrics = {}
    return c


@pytest.mark.parametrize("fn", ["matching_dev", "matching_held_out", "learning", "applications", "security"])
def test_offline_group(ctx, fn):
    start = len(ctx.results)
    {"matching_dev": lambda: runner.run_matching(ctx, "development"),
     "matching_held_out": lambda: runner.run_matching(ctx, "held_out"),
     "learning": lambda: runner.run_learning(ctx),
     "applications": lambda: runner.run_application_offline(ctx),
     "security": lambda: runner.run_security_offline(ctx)}[fn]()
    failed = [(c["id"], c["failure"]) for c in ctx.results[start:] if c["status"] == "failed"]
    assert not failed, failed
