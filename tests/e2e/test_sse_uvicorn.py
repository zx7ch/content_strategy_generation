from __future__ import annotations

import asyncio
from contextlib import contextmanager
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from app.agents.content_strategy_agent import StrategyResult
from app.config import settings
from app.main import create_app
from app.memory.job_store import JobStore
from app.memory.session_state import SessionManager
from app.models.session import ContentStrategy, PlatformPreference, SessionStage, SpiderNote


def _reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", 0))
        except PermissionError as exc:  # pragma: no cover - env specific
            pytest.skip(f"socket bind unavailable in current environment: {exc}")
        return int(sock.getsockname()[1])


@contextmanager
def _run_uvicorn() -> str:
    port = _reserve_port()
    config = uvicorn.Config(
        create_app(),
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="on",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base_url}/health", timeout=0.2)
            if response.status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    else:  # pragma: no cover - startup failure
        server.should_exit = True
        thread.join(timeout=5)
        raise AssertionError("uvicorn server did not start in time")

    try:
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=5)


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


def _wait_for_job(client: httpx.Client, base_url: str, job_id: str, timeout: float = 4.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.get(f"{base_url}/jobs/{job_id}").json()
        if payload["status"] in {"succeeded", "failed"}:
            return
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in time")


def _collect_sse_events(response: httpx.Response, *, stop_when) -> list[str]:
    events: list[str] = []
    buffer: list[str] = []
    for line in response.iter_lines():
        if line == "":
            if not buffer:
                continue
            event = "\n".join(buffer)
            events.append(event)
            buffer = []
            if stop_when(events):
                break
            continue
        buffer.append(line)
    return events


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "sse-uvicorn.db"
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(db_path))
    monkeypatch.setattr(settings, "JOB_POLL_INTERVAL_MS", 10)
    monkeypatch.setattr(settings, "SSE_HEARTBEAT_SECONDS", 0.2)
    monkeypatch.setattr(
        "app.agents.content_strategy_agent.ContentStrategyAgent.execute",
        _fake_strategy_execute,
    )
    return str(db_path)


def test_sse_over_uvicorn_keeps_connection_open_and_delivers_live_events(isolated_db):
    del isolated_db
    with _run_uvicorn() as base_url:
        with httpx.Client(timeout=httpx.Timeout(5.0, read=3.0)) as client:
            create = client.post(
                f"{base_url}/sessions",
                json={"user_id": "u1", "user_query": "轻运动", "platform": "xiaohongshu", "mode": "editing"},
            )
            session_id = create.json()["session_id"]

            enqueue = client.post(f"{base_url}/sessions/{session_id}/strategy", json={"foo": "bar"})
            job_id = enqueue.json()["job_id"]
            _wait_for_job(client, base_url, job_id)

            def _append_live_event() -> None:
                time.sleep(0.35)

                async def _write() -> None:
                    async with JobStore(settings.SQLITE_DB_PATH) as store:
                        await store.append_session_event(
                            session_id=session_id,
                            event_name="task_progress",
                            stage="strategy",
                            payload={
                                "message": "late live event",
                                "progress": 80,
                                "error_code": None,
                                "details": {"source": "uvicorn_e2e"},
                            },
                        )

                asyncio.run(_write())

            writer = threading.Thread(target=_append_live_event, daemon=True)
            writer.start()

            with client.stream(
                "GET",
                f"{base_url}/sessions/{session_id}/events",
                headers={"Last-Event-ID": "1"},
            ) as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                events = _collect_sse_events(
                    response,
                    stop_when=lambda seen: (
                        sum("event: heartbeat" in event for event in seen) >= 2
                        and any("late live event" in event for event in seen)
                    ),
                )

            writer.join(timeout=2)

    body = "\n\n".join(events)
    assert "event: heartbeat" in body
    assert body.count("event: heartbeat") >= 2
    assert "late live event" in body
    assert "id: 1" not in body
