from __future__ import annotations

from app.agents.content_generation_agent import GenerationExecutionResult
from app.agents.orchestrator import JobOrchestrationError, Orchestrator

import pytest


@pytest.mark.asyncio
async def test_run_generation_job_uses_session_backed_execute(monkeypatch):
    captured = {}

    async def fake_execute(self, session_id):
        captured["session_id"] = session_id
        return GenerationExecutionResult(
            success=True,
            status="success",
            notes=[],
            similarity_report={"notes_generated": 0},
            message="ok",
            error_code=None,
        )

    monkeypatch.setattr(
        "app.agents.content_generation_agent.ContentGenerationAgent.execute",
        fake_execute,
    )

    orchestrator = Orchestrator(db_path="test.db")
    result = await orchestrator._run_generation_job("session-1", {"topic": "护肤"})

    assert captured["session_id"] == "session-1"
    assert result["success"] is True
    assert result["status"] == "success"
    assert result["similarity_report"] == {"notes_generated": 0}


@pytest.mark.asyncio
async def test_run_generation_job_raises_orchestration_error_on_failed_execute(monkeypatch):
    async def fake_execute(self, session_id):
        return GenerationExecutionResult(
            success=False,
            status="failed",
            notes=[],
            similarity_report={},
            message=f"generation failed for {session_id}",
            error_code="INVALID_STAGE",
        )

    monkeypatch.setattr(
        "app.agents.content_generation_agent.ContentGenerationAgent.execute",
        fake_execute,
    )

    orchestrator = Orchestrator(db_path="test.db")

    with pytest.raises(JobOrchestrationError) as exc_info:
        await orchestrator._run_generation_job("session-1", {})

    assert exc_info.value.error_code == "INVALID_STAGE"
    assert exc_info.value.retryable is False
