from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient
import pytest

from app.agents.content_generation_agent import GenerationExecutionResult
from app.agents.content_strategy_agent import StrategyResult
from app.config import settings
from app.main import app
from app.memory.job_store import JobStore
from app.memory.session_state import SessionManager
from app.models.session import (
    ContentStrategy,
    GeneratedNote,
    PlatformPreference,
    SessionStage,
    SpiderNote,
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


async def _fake_strategy_execute(self, session_id: str) -> StrategyResult:
    async with SessionManager(settings.SQLITE_DB_PATH) as manager:
        strategy = ContentStrategy(
            positioning=f"定位-{session_id}",
            target_audience="城市女生",
            content_pillars=["教程"],
            key_messaging="真实可执行",
            content_types=["图文"],
            posting_strategy="晚间",
            data_source_quality=0.67,
        )
        preference = PlatformPreference(
            avg_title_length=14,
            popular_tags=[session_id],
            optimal_posting_times=["20:00"],
            content_patterns=["中等长度文案"],
        )
        await manager.update_session(
            session_id,
            spider_notes=[SpiderNote(note_id="n1", title="标题", content="内容", tags=["tag"])],
            content_strategy=strategy,
            platform_preference=preference,
            quality_score=0.67,
            used_fallback=False,
            stage=SessionStage.STRATEGY,
        )
    return StrategyResult(
        success=True,
        message="ok",
        quality_score=0.67,
        content_strategy=strategy,
        platform_preference=preference,
        used_fallback=False,
    )


async def _fake_generation_execute(self, session_id: str) -> GenerationExecutionResult:
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


async def _slow_generation_execute(self, session_id: str) -> GenerationExecutionResult:
    await asyncio.sleep(0.3)
    return await _fake_generation_execute(self, session_id)


def test_strategy_idempotency_replays_existing_job_and_multi_session_data_stays_isolated(isolated_db, monkeypatch):
    monkeypatch.setattr(
        "app.agents.content_strategy_agent.ContentStrategyAgent.execute",
        _fake_strategy_execute,
    )

    with TestClient(app) as client:
        first = client.post(
            "/sessions",
            json={"user_id": "u1", "user_query": "轻运动", "platform": "xiaohongshu", "mode": "editing"},
        )
        second = client.post(
            "/sessions",
            json={"user_id": "u2", "user_query": "收纳", "platform": "xiaohongshu", "mode": "editing"},
        )
        first_session = first.json()["session_id"]
        second_session = second.json()["session_id"]

        first_job = client.post(
            f"/sessions/{first_session}/strategy",
            headers={"Idempotency-Key": "dup-strategy"},
        ).json()["job_id"]
        replay = client.post(
            f"/sessions/{first_session}/strategy",
            headers={"Idempotency-Key": "dup-strategy"},
        )
        second_job = client.post(
            f"/sessions/{second_session}/strategy",
            headers={"Idempotency-Key": "dup-strategy"},
        ).json()["job_id"]

        assert replay.status_code == 202
        assert replay.json()["job_id"] == first_job

        _wait_for_job(client, first_job)
        _wait_for_job(client, second_job)

    async def _assert_state() -> None:
        async with JobStore(isolated_db) as store:
            assert await store.count_jobs(first_session, "strategy") == 1
            assert await store.count_jobs(second_session, "strategy") == 1

        async with SessionManager(isolated_db) as manager:
            session_one = await manager.get_session(first_session)
            session_two = await manager.get_session(second_session)
            assert session_one is not None
            assert session_two is not None
            assert session_one.content_strategy is not None
            assert session_two.content_strategy is not None
            assert session_one.content_strategy.positioning != session_two.content_strategy.positioning
            assert session_one.platform_preference is not None
            assert session_two.platform_preference is not None
            assert session_one.platform_preference.popular_tags != session_two.platform_preference.popular_tags

    asyncio.run(_assert_state())


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
