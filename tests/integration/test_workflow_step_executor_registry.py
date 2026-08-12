"""Integration tests for workflow step executor production registry."""

from __future__ import annotations

import json
import uuid

import pytest

from app.agents.orchestrator import JobOrchestrationError, Orchestrator
from app.memory.job_store import JobRecord
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowPhase
from app.services.step_executors import StepExecutorRegistry, StrategyStepExecutor
from app.services.workflow_run_manager import WorkflowRunManager


async def _seed_strategy_run(db_path: str):
    async with ThreadStore(db_path) as thread_store:
        thread = await thread_store.create_thread(title="Registry Test")
        message = await thread_store.append_message(
            thread_id=thread["id"],
            role="user",
            text="帮我生成防晒衣策略",
            intent="start_workflow",
        )
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(
            thread_id=thread["id"],
            user_id="u1",
            user_message_id=message["id"],
            initial_request=message["text"],
        )
        steps = await manager.initialize_steps(
            run.run_id,
            [{"step_name": "strategy.llm_synthesize", "phase": WorkflowPhase.STRATEGY}],
        )
    return run, steps[0]


def _job(*, run_id: str, step_name: str) -> JobRecord:
    return JobRecord(
        id=f"job_{uuid.uuid4().hex[:8]}",
        session_id=f"session-{uuid.uuid4().hex[:8]}",
        job_type="strategy",
        payload_json=json.dumps({"step_name": step_name}),
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
async def test_registry_routes_workflow_step_to_registered_executor(tmp_path):
    db_path = str(tmp_path / "workflow_step_executor_registry.db")
    run, _step = await _seed_strategy_run(db_path)

    async def fake_strategy(context):
        assert context.step["step_name"] == "strategy.llm_synthesize"
        return {"positioning": "城市轻户外", "source": "registry"}

    registry = StepExecutorRegistry(
        {
            "strategy.llm_synthesize": StrategyStepExecutor(
                db_path=db_path,
                strategy_runner=fake_strategy,
            )
        }
    )

    result = await Orchestrator(
        db_path=db_path,
        step_executor_registry=registry,
    ).run_workflow_step(_job(run_id=run.run_id, step_name="strategy.llm_synthesize"))

    assert result["success"] is True
    assert result["step_name"] == "strategy.llm_synthesize"
    assert result["artifact_refs"][0]["artifact_type"] == "strategy"

    async with WorkflowStore(db_path) as store:
        artifacts = await store.list_artifacts(run.run_id)

    assert len(artifacts) == 1
    assert artifacts[0].payload_json["source"] == "registry"


@pytest.mark.asyncio
async def test_registry_unsupported_step_maps_to_clear_orchestration_error(tmp_path):
    db_path = str(tmp_path / "workflow_step_executor_registry_unsupported.db")
    run, _step = await _seed_strategy_run(db_path)

    orchestrator = Orchestrator(db_path=db_path, step_executor_registry=StepExecutorRegistry())

    with pytest.raises(JobOrchestrationError) as exc_info:
        await orchestrator.run_workflow_step(_job(run_id=run.run_id, step_name="strategy.llm_synthesize"))

    assert exc_info.value.error_code == "WORKFLOW_STEP_UNSUPPORTED"
    assert exc_info.value.retryable is False
