from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient
import pytest

from app.agents.content_generation_agent import GenerationExecutionResult
from app.config import settings
from app.main import app
from app.memory.job_store import JobStore
from app.memory.session_state import SessionManager
from app.models.session import (
    ContentStrategy,
    GeneratedNote,
    PlatformPreference,
    SessionStage,
)


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "idempotency-concurrency.db"
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


async def _fake_generation_execute(self, session_id: str, *, progress_callback=None) -> GenerationExecutionResult:
    del progress_callback
    note = GeneratedNote(
        note_id=f"note-{session_id}",
        title=f"{session_id}-标题",
        content="正文一\n第二段",
        tags=["#护肤"],
        cover_design_prompt="封面提示",
        suggested_update_time="2026-03-18 20:00",
        similarity_check={"max_similarity": 0.1, "status": "safe"},
        generation_params={"proposal_id": "p1", "temperature": 0.7, "slot_id": 0},
    )
    async with SessionManager(settings.SQLITE_DB_PATH) as manager:
        await manager.update_session(
            session_id,
            generated_notes=[note],
            similarity_report={"notes_generated": 1, "failed_count": 0, "budget_exceeded": False},
            stage=SessionStage.COMPLETED,
        )
    return GenerationExecutionResult(
        success=True,
        status="success",
        notes=[note],
        similarity_report={"notes_generated": 1, "failed_count": 0, "budget_exceeded": False},
        message="ok",
        error_code=None,
    )


async def _slow_generation_execute(self, session_id: str, *, progress_callback=None) -> GenerationExecutionResult:
    await asyncio.sleep(0.3)
    return await _fake_generation_execute(self, session_id, progress_callback=progress_callback)


def test_generate_idempotency_replays_after_completion_without_creating_duplicate_jobs(isolated_db, monkeypatch):
    monkeypatch.setattr(
        "app.agents.content_generation_agent.ContentGenerationAgent.execute",
        _fake_generation_execute,
    )

    session_id = "session-generate-idempotent"
    asyncio.run(_seed_strategy_session(isolated_db, session_id))

    with TestClient(app) as client:
        first = client.post(
            f"/sessions/{session_id}/generate",
            headers={"Idempotency-Key": "dup-generate"},
        )
        assert first.status_code == 202
        job_id = first.json()["job_id"]

        _wait_for_job(client, job_id)

        replay = client.post(
            f"/sessions/{session_id}/generate",
            headers={"Idempotency-Key": "dup-generate"},
        )

    assert replay.status_code == 202
    assert replay.json()["job_id"] == job_id
    assert replay.json()["stage"] == "completed"

    async def _assert_jobs() -> None:
        async with JobStore(isolated_db) as store:
            assert await store.count_jobs(session_id, "generate") == 1

    asyncio.run(_assert_jobs())


def test_same_session_repeated_generate_does_not_create_double_running_jobs(isolated_db, monkeypatch):
    monkeypatch.setattr(
        "app.agents.content_generation_agent.ContentGenerationAgent.execute",
        _slow_generation_execute,
    )

    session_id = "session-single-running"
    asyncio.run(_seed_strategy_session(isolated_db, session_id))

    with TestClient(app) as client:
        first = client.post(f"/sessions/{session_id}/generate")
        assert first.status_code == 202

        second = client.post(f"/sessions/{session_id}/generate")
        assert second.status_code == 409
        assert second.json()["error_code"] == "INVALID_STAGE"

        _wait_for_job(client, first.json()["job_id"])

    async def _assert_single_job() -> None:
        async with JobStore(isolated_db) as store:
            assert await store.count_jobs(session_id, "generate") == 1

    asyncio.run(_assert_single_job())
