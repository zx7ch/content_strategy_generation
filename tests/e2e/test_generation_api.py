from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient
import pytest

from app.agents.content_generation_agent import GenerationExecutionResult
from app.config import settings
from app.main import app
from app.memory.session_state import SessionManager
from app.models.session import (
    ContentStrategy,
    GeneratedNote,
    PlatformPreference,
    SessionStage,
)


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "generation-api.db"
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(db_path))
    monkeypatch.setattr(settings, "JOB_POLL_INTERVAL_MS", 10)
    monkeypatch.setattr(settings, "SSE_HEARTBEAT_SECONDS", 1)
    return str(db_path)


def _wait_for_job(client: TestClient, job_id: str, timeout: float = 3.0) -> dict:
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
                positioning="功效护肤",
                target_audience="敏感肌女生",
                content_pillars=["成分", "实测"],
                key_messaging="真实可执行",
                content_types=["图文"],
                posting_strategy="晚间",
                data_source_quality=0.73,
            ),
            platform_preference=PlatformPreference(
                avg_title_length=16,
                popular_tags=["护肤"],
                optimal_posting_times=["20:00"],
                content_patterns=["中等长度文案"],
            ),
            stage=SessionStage.STRATEGY,
        )


async def _fake_generation_execute(self, session_id: str) -> GenerationExecutionResult:
    note = GeneratedNote(
        note_id="g1",
        title="修护精华怎么选",
        content="正文一\n第二段",
        tags=["#护肤"],
        cover_design_prompt="封面提示",
        suggested_update_time="2026-03-18 20:00",
        similarity_check={"max_similarity": 0.1, "status": "safe"},
        generation_params={"proposal_id": "p1", "temperature": 0.7, "slot_id": 0},
    )
    report = {"notes_generated": 1, "failed_count": 0, "similarity_warnings": []}
    async with SessionManager(settings.SQLITE_DB_PATH) as manager:
        await manager.update_session(
            session_id,
            generated_notes=[note],
            similarity_report=report,
            stage=SessionStage.COMPLETED,
        )
    return GenerationExecutionResult(
        success=True,
        status="success",
        notes=[note],
        similarity_report=report,
        message="ok",
        error_code=None,
    )


def test_generate_without_strategy_returns_409(isolated_db):
    with TestClient(app) as client:
        create = client.post(
            "/sessions",
            json={"user_id": "u1", "user_query": "护肤", "platform": "xiaohongshu", "mode": "editing"},
        )
        session_id = create.json()["session_id"]

        generate = client.post(f"/sessions/{session_id}/generate", json={"topic": "护肤"})
        assert generate.status_code == 409


def test_generation_happy_path_runs_through_worker_and_persists_results(isolated_db, monkeypatch):
    monkeypatch.setattr(
        "app.agents.content_generation_agent.ContentGenerationAgent.execute",
        _fake_generation_execute,
    )

    session_id = "session-generation-e2e"
    asyncio.run(_seed_strategy_session(isolated_db, session_id))

    with TestClient(app) as client:
        enqueue = client.post(f"/sessions/{session_id}/generate", json={"topic": "护肤"})
        assert enqueue.status_code == 202
        job_id = enqueue.json()["job_id"]

        job_payload = _wait_for_job(client, job_id)
        assert job_payload["status"] == "succeeded"

        session_payload = client.get(f"/sessions/{session_id}").json()
        assert session_payload["stage"] == "completed"
        assert session_payload["job_status"] == "succeeded"

    async def _assert_generated():
        async with SessionManager(isolated_db) as manager:
            session = await manager.get_session(session_id)
            assert session is not None
            assert session.generated_notes is not None
            assert len(session.generated_notes) == 1
            assert session.similarity_report is not None
            assert session.similarity_report["notes_generated"] == 1

    asyncio.run(_assert_generated())
