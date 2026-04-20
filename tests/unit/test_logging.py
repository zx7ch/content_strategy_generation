"""Unit tests for structured logging module.

Tests cover JSON output format, correlation ID propagation,
log level filtering, and logger field binding.
"""

import json
import logging
import sys
import uuid
from datetime import datetime, timedelta
from io import StringIO
from typing import Generator
from unittest.mock import AsyncMock, patch
import asyncio

import pytest
import structlog
from fastapi.testclient import TestClient

from app.agents.content_generation_agent import ContentGenerationAgent
from app.api.routes.router import _event_stream, app
from app.core.context import (
    clear_correlation_id,
    get_correlation_id,
    set_correlation_id,
)
from app.core.logging import configure_logging, get_logger
from app.config import settings
from app.llm.client import LLMClient
from app.memory.job_store import JobStore
from app.memory.session_state import SessionManager
from app.models.session import (
    ContentStrategy,
    GeneratedNote,
    PlatformPreference,
    Proposal,
)


@pytest.fixture(autouse=True)
def reset_logging() -> Generator[None, None, None]:
    """Reset logging configuration and context before each test."""
    # Reset logging state
    from app.core.logging import _logging_configured
    import app.core.logging as logging_module
    logging_module._logging_configured = False

    clear_correlation_id()
    yield
    clear_correlation_id()


class TestStructuredLogOutput:
    """Tests for JSON structured log output format."""

    def test_json_format_output(self) -> None:
        """Verify logs are output in valid JSON format with required fields."""
        # Capture stdout
        captured = StringIO()
        original_stdout = sys.stdout
        sys.stdout = captured

        try:
            # Configure logging with JSON output (force to override previous config)
            configure_logging(log_level="INFO", json_format=True, force=True)

            logger = get_logger("test.json.output")
            logger.info("Test message", operation="test_op", duration_ms=100)

            # Parse the log output
            log_output = captured.getvalue().strip()
            log_entry = json.loads(log_output)

            # Verify required fields
            assert "timestamp" in log_entry
            assert "level" in log_entry
            assert "logger" in log_entry
            assert "event" in log_entry
            assert log_entry["event"] == "Test message"
            assert log_entry["operation"] == "test_op"
            assert log_entry["duration_ms"] == 100
        finally:
            sys.stdout = original_stdout

    def test_timestamp_iso_format(self) -> None:
        """Verify timestamp is in ISO 8601 format."""
        from datetime import datetime

        captured = StringIO()
        original_stdout = sys.stdout
        sys.stdout = captured

        try:
            configure_logging(log_level="INFO", force=True)

            logger = get_logger("test.timestamp")
            logger.info("Timestamp test")

            log_output = captured.getvalue().strip()
            log_entry = json.loads(log_output)

            # Verify timestamp can be parsed
            timestamp = log_entry["timestamp"]
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            assert parsed is not None
        finally:
            sys.stdout = original_stdout


class TestCorrelationIdPropagation:
    """Tests for correlation ID context propagation."""

    def test_correlation_id_in_log_output(self) -> None:
        """Verify correlation_id is automatically injected into log entries."""
        captured = StringIO()
        original_stdout = sys.stdout
        sys.stdout = captured

        try:
            configure_logging(log_level="INFO", force=True)

            test_correlation_id = "sess_abc123"
            set_correlation_id(test_correlation_id)

            logger = get_logger("test.correlation")
            logger.info("Session created", stage="init")

            log_output = captured.getvalue().strip()
            log_entry = json.loads(log_output)

            assert log_entry["correlation_id"] == test_correlation_id
        finally:
            sys.stdout = original_stdout

    def test_no_correlation_id_when_not_set(self) -> None:
        """Verify correlation_id is not present when not set in context."""
        captured = StringIO()
        original_stdout = sys.stdout
        sys.stdout = captured

        try:
            configure_logging(log_level="INFO", force=True)

            # Ensure no correlation_id is set
            clear_correlation_id()

            logger = get_logger("test.no_correlation")
            logger.info("No correlation test")

            log_output = captured.getvalue().strip()
            log_entry = json.loads(log_output)

            assert "correlation_id" not in log_entry
        finally:
            sys.stdout = original_stdout

    def test_correlation_id_context_isolation(self) -> None:
        """Verify correlation_id context is properly isolated."""
        captured = StringIO()
        original_stdout = sys.stdout
        sys.stdout = captured

        try:
            configure_logging(log_level="INFO", force=True)

            # Set correlation ID
            set_correlation_id("sess_first")
            assert get_correlation_id() == "sess_first"

            # Clear and set new one
            clear_correlation_id()
            set_correlation_id("sess_second")
            assert get_correlation_id() == "sess_second"

            logger = get_logger("test.context_isolation")
            logger.info("Context test")

            log_output = captured.getvalue().strip()
            log_entry = json.loads(log_output)

            assert log_entry["correlation_id"] == "sess_second"
        finally:
            sys.stdout = original_stdout


class TestLogLevelFiltering:
    """Tests for log level filtering behavior."""

    def test_debug_filtered_when_level_is_info(self) -> None:
        """Verify DEBUG logs are filtered when level is INFO."""
        captured = StringIO()
        original_stdout = sys.stdout
        sys.stdout = captured

        try:
            configure_logging(log_level="INFO", force=True)

            logger = get_logger("test.level_filtering")
            logger.debug("This should be filtered")
            logger.info("This should appear")

            log_output = captured.getvalue().strip()
            assert "This should be filtered" not in log_output
            assert "This should appear" in log_output
        finally:
            sys.stdout = original_stdout

    def test_warning_and_error_pass_through(self) -> None:
        """Verify WARNING and ERROR logs pass through at INFO level."""
        captured = StringIO()
        original_stdout = sys.stdout
        sys.stdout = captured

        try:
            configure_logging(log_level="INFO", force=True)

            logger = get_logger("test.levels")
            logger.warning("Warning message")
            logger.error("Error message")

            log_output = captured.getvalue().strip()
            lines = [line for line in log_output.split("\n") if line.strip()]

            assert len(lines) == 2

            warning_entry = json.loads(lines[0])
            error_entry = json.loads(lines[1])

            assert warning_entry["level"] == "WARNING"
            assert error_entry["level"] == "ERROR"
        finally:
            sys.stdout = original_stdout

    def test_log_level_from_environment(self) -> None:
        """Verify log level can be set from environment variable."""
        captured = StringIO()
        original_stdout = sys.stdout
        sys.stdout = captured

        try:
            with patch.dict("os.environ", {"LOG_LEVEL": "WARNING"}):
                configure_logging(force=True)

                logger = get_logger("test.env_level")
                logger.info("Should be filtered")
                logger.warning("Should appear")

                log_output = captured.getvalue().strip()
                assert "Should be filtered" not in log_output
                lines = [line for line in log_output.split("\n") if line.strip()]
                assert len(lines) == 1
                entry = json.loads(lines[0])
                assert entry["level"] == "WARNING"
        finally:
            sys.stdout = original_stdout


class TestLoggerBinding:
    """Tests for logger field binding capabilities."""

    def test_bind_additional_fields(self) -> None:
        """Verify logger can bind additional fields that appear in output."""
        captured = StringIO()
        original_stdout = sys.stdout
        sys.stdout = captured

        try:
            configure_logging(log_level="INFO", force=True)

            logger = get_logger("test.binding")
            bound_logger = logger.bind(session_id="sess_001", user_id="user_123")

            bound_logger.info("Bound message")

            log_output = captured.getvalue().strip()
            log_entry = json.loads(log_output)

            assert log_entry["session_id"] == "sess_001"
            assert log_entry["user_id"] == "user_123"
            assert log_entry["event"] == "Bound message"
        finally:
            sys.stdout = original_stdout

    def test_bind_preserves_context(self) -> None:
        """Verify bound logger preserves correlation_id from context."""
        captured = StringIO()
        original_stdout = sys.stdout
        sys.stdout = captured

        try:
            configure_logging(log_level="INFO", force=True)

            set_correlation_id("sess_bound_123")

            logger = get_logger("test.bind_context")
            bound_logger = logger.bind(operation="rag_query")

            bound_logger.info("Query completed", results_count=5)

            log_output = captured.getvalue().strip()
            log_entry = json.loads(log_output)

            assert log_entry["correlation_id"] == "sess_bound_123"
            assert log_entry["operation"] == "rag_query"
            assert log_entry["results_count"] == 5
        finally:
            sys.stdout = original_stdout

    def test_multiple_log_entries_with_binding(self) -> None:
        """Verify multiple log entries with bound logger."""
        captured = StringIO()
        original_stdout = sys.stdout
        sys.stdout = captured

        try:
            configure_logging(log_level="INFO", force=True)

            logger = get_logger("test.multiple")
            bound_logger = logger.bind(service="rag_service")

            bound_logger.info("Start operation", step=1)
            bound_logger.info("Continue operation", step=2)
            bound_logger.info("Complete operation", step=3)

            log_output = captured.getvalue().strip()
            lines = [line for line in log_output.split("\n") if line.strip()]

            assert len(lines) == 3

            for i, line in enumerate(lines, 1):
                entry = json.loads(line)
                assert entry["service"] == "rag_service"
                assert entry["step"] == i
        finally:
            sys.stdout = original_stdout


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "logging.db"
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(db_path))
    monkeypatch.setattr(settings, "SSE_HEARTBEAT_SECONDS", 0.01)
    return str(db_path)


async def _seed_generation_session(db_path: str, session_id: str) -> None:
    async with SessionManager(db_path) as manager:
        await manager.create_session(session_id, "u1", "护肤")
        await manager.update_session(
            session_id,
            content_strategy=ContentStrategy(
                positioning="实验室风",
                target_audience="成分党",
                content_pillars=["护肤", "修护"],
                key_messaging="真实可执行",
                content_types=["图文"],
                posting_strategy="晚间",
                data_source_quality=0.8,
            ),
            platform_preference=PlatformPreference(
                avg_title_length=16,
                popular_tags=["护肤"],
                optimal_posting_times=["20:00"],
                content_patterns=["清单式"],
            ),
            stage="strategy",
        )


class _NeverDisconnectRequest:
    async def is_disconnected(self) -> bool:
        return False


def _create_session_via_api(client: TestClient) -> str:
    response = client.post(
        "/sessions",
        json={"user_id": "u1", "user_query": "护肤", "platform": "xiaohongshu", "mode": "editing"},
    )
    assert response.status_code == 201
    return response.json()["session_id"]


def _proposal(index: int) -> Proposal:
    return Proposal(
        proposal_id=f"p{index}",
        angle=f"angle-{index}",
        hook=f"hook-{index}",
        outline=f"outline-{index}",
        target_emotion="trust",
        content_pillars=["护肤"],
        suggested_tags=["护肤"],
        score=1.0 - index * 0.1,
    )


def test_router_paths_emit_session_stage_resume_and_heartbeat_logs(isolated_db):
    with patch("app.api.routes.router.log_event") as mocked_log_event:
        with TestClient(app) as client:
            session_id = _create_session_via_api(client)
            strategy_response = client.post(f"/sessions/{session_id}/strategy", json={"foo": "bar"})
            assert strategy_response.status_code == 202

        async def _pause_and_get_last_event_id() -> int:
            async with JobStore(isolated_db) as store:
                await store.pause_session_jobs(session_id)
                await store.append_session_event(
                    session_id=session_id,
                    event_name="task_progress",
                    stage="strategy",
                    payload={"message": "seed", "progress": 1, "error_code": None, "details": {}},
                )
                events = await store.list_session_events(session_id)
                return events[-1].event_id

        last_event_id = asyncio.run(_pause_and_get_last_event_id())

        with TestClient(app) as client:
            resume_response = client.post(f"/sessions/{session_id}/resume")
            assert resume_response.status_code == 200

        async def _latest_event_id() -> int:
            async with JobStore(isolated_db) as store:
                events = await store.list_session_events(session_id)
                return events[-1].event_id

        last_event_id = asyncio.run(_latest_event_id())

        async def _collect_heartbeat() -> str:
            stream = _event_stream(
                _NeverDisconnectRequest(),
                session_id=session_id,
                last_event_id=last_event_id,
            )
            chunk = await asyncio.wait_for(stream.__anext__(), timeout=0.2)
            await stream.aclose()
            return chunk

        heartbeat_chunk = asyncio.run(_collect_heartbeat())
        assert "event: heartbeat" in heartbeat_chunk

        event_names = [call.kwargs["event_name"] for call in mocked_log_event.call_args_list]
        assert "session_created" in event_names
        assert "stage_changed" in event_names
        assert "session_resumed" in event_names
        assert "sse_heartbeat" in event_names


@pytest.mark.asyncio
async def test_session_lifecycle_paths_emit_frozen_and_purged_logs(isolated_db):
    session_id = str(uuid.uuid4())
    async with SessionManager(isolated_db) as manager:
        await manager.create_session(session_id, "u1", "护肤")

        with patch("app.memory.session_state.log_event") as mocked_log_event:
            frozen_ts = (datetime.utcnow() - timedelta(hours=25)).isoformat()
            await manager._conn.execute(
                "UPDATE sessions SET last_user_activity_at = ?, last_activity_at = ? WHERE session_id = ?",
                (frozen_ts, frozen_ts, session_id),
            )
            await manager._conn.commit()
            await manager.refresh_lifecycle_state(session_id)

            purged_ts = (datetime.utcnow() - timedelta(days=11)).isoformat()
            await manager._conn.execute(
                "UPDATE sessions SET last_user_activity_at = ?, last_activity_at = ? WHERE session_id = ?",
                (purged_ts, purged_ts, session_id),
            )
            await manager._conn.commit()
            await manager.refresh_lifecycle_state(session_id)

        event_names = [call.kwargs["event_name"] for call in mocked_log_event.call_args_list]
        assert "session_frozen" in event_names
        assert "session_purged" in event_names


@pytest.mark.asyncio
async def test_generation_execute_emits_budget_logs(isolated_db, monkeypatch):
    session_id = str(uuid.uuid4())
    await _seed_generation_session(isolated_db, session_id)
    monkeypatch.setattr(settings, "SESSION_TOKEN_BUDGET", 300)
    monkeypatch.setattr(settings, "GENERATION_PARALLEL_SLOTS", 5)
    monkeypatch.setattr(settings, "GENERATION_DEGRADED_SLOTS", 2)

    agent = ContentGenerationAgent(session_manager=SessionManager(isolated_db))

    async def fake_generate_proposals(**kwargs):
        del kwargs
        return [_proposal(i) for i in range(5)]

    async def fake_parallel_generate(**kwargs):
        budget = kwargs["budget"]
        note = GeneratedNote(
            note_id="note-1",
            title="标题1",
            content="正文一\n第二段",
            tags=["#护肤"],
            cover_design_prompt="封面",
            suggested_update_time="2026-03-21 20:00",
            similarity_check={"max_similarity": 0.0, "status": "safe"},
            generation_params={"proposal_id": "p0", "temperature": 0.3, "slot_id": 0},
        )
        try:
            await budget.consume("x" * 2000)
        except Exception:
            pass
        return [note]

    monkeypatch.setattr(agent, "generate_proposals", fake_generate_proposals)
    monkeypatch.setattr(agent, "_parallel_generate", fake_parallel_generate)

    with patch("app.agents.content_generation_agent.log_event") as mocked_log_event:
        result = await agent.execute(session_id)

    assert result.status == "partial"
    event_names = [call.kwargs["event_name"] for call in mocked_log_event.call_args_list]
    assert "budget_degrade_applied" in event_names
    assert "budget_exceeded" in event_names


@pytest.mark.asyncio
async def test_llm_client_chat_emits_completed_and_failed_logs():
    success_response = type(
        "Response",
        (),
        {
            "choices": [
                type(
                    "Choice",
                    (),
                    {"message": type("Message", (), {"content": "ok"})()},
                )()
            ]
        },
    )()

    with patch("app.llm.client.AsyncOpenAI") as mock_openai:
        client_instance = mock_openai.return_value
        client_instance.chat.completions.create = AsyncMock(return_value=success_response)
        client = LLMClient(provider="deepseek", model="deepseek-chat")

        with patch("app.llm.client.log_event") as mocked_log_event:
            result = await client.chat(system="sys", user="user", max_tokens=64, temperature=0.2)

        assert result == "ok"
        event_names = [call.kwargs["event_name"] for call in mocked_log_event.call_args_list]
        assert "llm_call_completed" in event_names

        client_instance.chat.completions.create = AsyncMock(side_effect=RuntimeError("timeout"))
        with patch("app.llm.client.log_event") as mocked_log_event:
            with pytest.raises(RuntimeError):
                await client.chat(system="sys", user="user", max_tokens=64, temperature=0.2)

        event_names = [call.kwargs["event_name"] for call in mocked_log_event.call_args_list]
        assert "llm_call_failed" in event_names

def test_unbound_logger_not_affected() -> None:
    """Verify original logger is not affected by bound logger."""
    captured = StringIO()
    original_stdout = sys.stdout
    sys.stdout = captured

    try:
        configure_logging(log_level="INFO", force=True)

        logger = get_logger("test.unbound")
        _ = logger.bind(extra_field="value")

        logger.info("Original message")

        log_output = captured.getvalue().strip()
        log_entry = json.loads(log_output)

        assert "extra_field" not in log_entry
        assert log_entry["event"] == "Original message"
    finally:
        sys.stdout = original_stdout
