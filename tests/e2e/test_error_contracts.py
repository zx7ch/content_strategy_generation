from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import time

from fastapi.testclient import TestClient
import pytest

from app.agents.content_generation_agent import GenerationExecutionResult
from app.agents.content_strategy_agent import StrategyResult
from app.config import settings
from app.main import app
from app.memory.session_state import SessionManager
from app.models.session import ContentStrategy, PlatformPreference, SessionLifecycleState, SessionStage


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "error-contracts.db"
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(db_path))
    monkeypatch.setattr(settings, "JOB_POLL_INTERVAL_MS", 10)
    monkeypatch.setattr(settings, "SSE_HEARTBEAT_SECONDS", 1)
    return str(db_path)


def _wait_for_job(client: TestClient, job_id: str, timeout: float = 4.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.get(f"/jobs/{job_id}").json()
        if payload["status"] in {"succeeded", "failed"}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in time")


async def _seed_strategy_session(db_path: str, session_id: str) -> None:
    async with SessionManager(db_path) as manager:
        await manager.create_session(session_id, "u1", "护肤")
        await manager.update_session(
            session_id,
            content_strategy=ContentStrategy(
                positioning="修护",
                target_audience="敏感肌女生",
                content_pillars=["成分", "实测"],
                key_messaging="真实可执行",
                content_types=["图文"],
                posting_strategy="晚间",
                data_source_quality=0.8,
            ),
            platform_preference=PlatformPreference(
                avg_title_length=16,
                popular_tags=["护肤"],
                optimal_posting_times=["20:00"],
                content_patterns=["中等长度文案"],
            ),
            stage=SessionStage.STRATEGY,
        )


async def _set_session_state(db_path: str, session_id: str, **fields) -> None:
    async with SessionManager(db_path) as manager:
        await manager.update_session(session_id, **fields)


async def _fake_budget_exceeded(self, session_id: str) -> GenerationExecutionResult:
    return GenerationExecutionResult(
        success=False,
        status="failed",
        notes=[],
        similarity_report={"notes_generated": 0, "budget_exceeded": True},
        message=f"budget exceeded for {session_id}",
        error_code="BUDGET_EXCEEDED",
    )


async def _fake_retryable_strategy_failure(self, session_id: str) -> StrategyResult:
    return StrategyResult(
        success=False,
        message=f"spider unavailable for {session_id}",
        error_code="SPIDER_SERVICE_UNAVAILABLE",
    )


def test_http_error_contracts_cover_404_409_410_423_and_429(isolated_db):
    with TestClient(app) as client:
        create = client.post(
            "/sessions",
            json={"user_id": "u1", "user_query": "护肤", "platform": "xiaohongshu", "mode": "editing"},
        )
        session_id = create.json()["session_id"]

        missing_session = client.get("/sessions/session-missing")
        missing_job = client.get("/jobs/job-missing")
        invalid_stage = client.post(f"/sessions/{session_id}/generate")

    asyncio.run(
        _set_session_state(
            isolated_db,
            session_id,
            last_user_activity_at=(datetime.utcnow() - timedelta(days=2)).isoformat(),
        )
    )

    with TestClient(app) as client:
        frozen = client.post(f"/sessions/{session_id}/strategy")

    asyncio.run(
        _set_session_state(
            isolated_db,
            session_id,
            spider_cooldown_until=(datetime.utcnow() + timedelta(minutes=10)).isoformat(),
            last_user_activity_at=datetime.utcnow().isoformat(),
        )
    )

    with TestClient(app) as client:
        cooldown = client.post(f"/sessions/{session_id}/strategy")

    asyncio.run(
        _set_session_state(
            isolated_db,
            session_id,
            lifecycle_state=SessionLifecycleState.PURGED,
            purged_at=datetime.utcnow().isoformat(),
        )
    )

    with TestClient(app) as client:
        purged = client.get(f"/sessions/{session_id}")

    assert missing_session.status_code == 404
    assert missing_session.json()["error_code"] == "SESSION_NOT_FOUND"
    assert missing_session.json()["error_message"]
    assert missing_session.json()["suggested_action"]

    assert missing_job.status_code == 404
    assert missing_job.json()["error_code"] == "JOB_NOT_FOUND"
    assert missing_job.json()["error_message"]

    assert invalid_stage.status_code == 409
    assert invalid_stage.json()["error_code"] == "INVALID_STAGE"
    assert invalid_stage.json()["error_details"]["current_stage"] == "init"

    assert frozen.status_code == 423
    assert frozen.json()["error_code"] == "SESSION_FROZEN"

    assert cooldown.status_code == 429
    assert cooldown.json()["error_code"] == "SPIDER_COOLDOWN_ACTIVE"
    assert cooldown.json()["retryable"] is True

    assert purged.status_code == 410
    assert purged.json()["error_code"] == "SESSION_PURGED"


def test_async_job_failures_expose_budget_and_max_retry_error_codes(isolated_db, monkeypatch):
    monkeypatch.setattr(
        "app.agents.content_generation_agent.ContentGenerationAgent.execute",
        _fake_budget_exceeded,
    )
    monkeypatch.setattr(
        "app.agents.content_strategy_agent.ContentStrategyAgent.execute",
        _fake_retryable_strategy_failure,
    )
    monkeypatch.setattr(settings, "JOB_MAX_RETRIES", 1)

    generate_session = "session-budget"
    asyncio.run(_seed_strategy_session(isolated_db, generate_session))

    with TestClient(app) as client:
        generation = client.post(f"/sessions/{generate_session}/generate")
        assert generation.status_code == 202
        generation_job = _wait_for_job(client, generation.json()["job_id"])

        create = client.post(
            "/sessions",
            json={"user_id": "u2", "user_query": "轻运动", "platform": "xiaohongshu", "mode": "editing"},
        )
        strategy_session = create.json()["session_id"]
        strategy = client.post(f"/sessions/{strategy_session}/strategy")
        assert strategy.status_code == 202
        strategy_job = _wait_for_job(client, strategy.json()["job_id"])

    assert generation_job["status"] == "failed"
    assert generation_job["last_error_code"] == "BUDGET_EXCEEDED"
    assert "budget exceeded" in generation_job["last_error_message"]

    async def _assert_generate_session_failed():
        async with SessionManager(isolated_db) as manager:
            session = await manager.get_session(generate_session)
            assert session is not None
            assert session.stage == SessionStage.FAILED
            assert session.error is not None
            assert session.error.code == "BUDGET_EXCEEDED"

    asyncio.run(_assert_generate_session_failed())

    assert strategy_job["status"] == "failed"
    assert strategy_job["last_error_code"] == "JOB_MAX_RETRIES_EXCEEDED"
    assert "spider unavailable" in strategy_job["last_error_message"]
