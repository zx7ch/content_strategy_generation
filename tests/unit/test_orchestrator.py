from __future__ import annotations

import json
import uuid

from app.agents.content_generation_agent import GenerationExecutionResult
from app.agents.orchestrator import JobOrchestrationError, Orchestrator
from app.memory.job_store import JobRecord
from app.memory.session_state import SessionManager
from app.services.step_executors import StepExecutionResult, StepExecutorRegistry

import pytest


def _job_record(*, session_id: str = "session-1", run_id: str | None = None, step_name: str | None = None) -> JobRecord:
    return JobRecord(
        id=f"job_{uuid.uuid4().hex[:8]}",
        session_id=session_id,
        job_type="strategy",
        payload_json=json.dumps({"step_name": step_name} if step_name else {}),
        status="running",
        priority=100,
        attempts=1,
        max_attempts=3,
        not_before=None,
        lease_expires_at=None,
        idempotency_key=None,
        last_error_code=None,
        last_error_message=None,
        cancel_reason=None,
        run_id=run_id,
        step_id=None,
        child_task_id=None,
        created_at=None,
        updated_at=None,
    )


@pytest.mark.asyncio
async def test_run_generation_job_uses_session_backed_execute(monkeypatch):
    captured = {}

    async def fake_execute(self, session_id, **kwargs):
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
    async def fake_execute(self, session_id, **kwargs):
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


@pytest.mark.asyncio
async def test_run_job_uses_step_executor_registry_for_workflow_bound_job(tmp_path):
    db_path = str(tmp_path / "orchestrator_workflow_step.db")
    session_id = "session-workflow"
    async with SessionManager(db_path) as manager:
        await manager.create_session(session_id, "u1", "workflow path")

    class FakeExecutor:
        async def execute(self, run_id, step_name):
            return StepExecutionResult(
                step_name=step_name,
                artifact_refs=[{"artifact_id": "artifact-1", "artifact_type": "strategy"}],
            )

    registry = StepExecutorRegistry({"strategy.llm_synthesize": FakeExecutor()})
    orchestrator = Orchestrator(db_path=db_path, step_executor_registry=registry)

    result = await orchestrator.run_job(
        _job_record(
            session_id=session_id,
            run_id="run-1",
            step_name="strategy.llm_synthesize",
        )
    )

    assert result["success"] is True
    assert result["step_name"] == "strategy.llm_synthesize"
    assert result["artifact_refs"] == [{"artifact_id": "artifact-1", "artifact_type": "strategy"}]


@pytest.mark.asyncio
async def test_run_workflow_step_requires_step_name():
    orchestrator = Orchestrator(db_path="test.db", step_executor_registry=StepExecutorRegistry())

    with pytest.raises(JobOrchestrationError) as exc_info:
        await orchestrator.run_workflow_step(_job_record(run_id="run-1"))

    assert exc_info.value.error_code == "WORKFLOW_STEP_NAME_MISSING"
    assert exc_info.value.retryable is False
