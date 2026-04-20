from __future__ import annotations

import time

from fastapi.testclient import TestClient
import pytest

from app.agents.content_strategy_agent import StrategyResult
from app.config import settings
from app.main import app
from app.memory.session_state import SessionManager
from app.models.session import ContentStrategy, PlatformPreference, SessionStage, SpiderNote


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "strategy-api.db"
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


async def _fake_strategy_execute(self, session_id: str) -> StrategyResult:
    async with SessionManager(settings.SQLITE_DB_PATH) as manager:
        strategy = ContentStrategy(
            positioning="轻运动",
            target_audience="城市女生",
            content_pillars=["教程", "穿搭"],
            key_messaging="真实可执行",
            content_types=["图文"],
            posting_strategy="晚间",
            data_source_quality=0.67,
        )
        preference = PlatformPreference(
            avg_title_length=14,
            popular_tags=["教程"],
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


def test_strategy_happy_path_executes_through_worker(isolated_db, monkeypatch):
    monkeypatch.setattr(
        "app.agents.content_strategy_agent.ContentStrategyAgent.execute",
        _fake_strategy_execute,
    )

    with TestClient(app) as client:
        create = client.post(
            "/sessions",
            json={"user_id": "u1", "user_query": "轻运动", "platform": "xiaohongshu", "mode": "editing"},
        )
        session_id = create.json()["session_id"]

        enqueue = client.post(f"/sessions/{session_id}/strategy", json={"foo": "bar"})
        assert enqueue.status_code == 202
        job_id = enqueue.json()["job_id"]

        job_payload = _wait_for_job(client, job_id)
        assert job_payload["status"] == "succeeded"

        session_payload = client.get(f"/sessions/{session_id}").json()
        assert session_payload["stage"] == "strategy"
        assert session_payload["job_status"] == "succeeded"

    import asyncio

    async def _assert_session_data():
        async with SessionManager(isolated_db) as manager:
            session = await manager.get_session(session_id)
            assert session is not None
            assert session.content_strategy is not None
            assert session.platform_preference is not None
            assert session.quality_score == pytest.approx(0.67)

    asyncio.run(_assert_session_data())
