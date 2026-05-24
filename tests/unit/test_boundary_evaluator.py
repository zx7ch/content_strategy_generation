"""Unit tests for T5 BoundaryEvaluator."""

from __future__ import annotations

import pytest

from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowConstraintType, WorkflowPhase
from app.services.boundary_evaluator import BoundaryEvaluator
from app.services.context_builder import ContextBuilder
from app.services.workflow_run_manager import WorkflowRunManager


async def _context_for(
    tmp_path,
    *,
    run_status: str = "running",
    run_phase: str = "generation",
    constraint_type: WorkflowConstraintType = WorkflowConstraintType.STYLE,
    step_name: str = "generation.generate_notes_parallel",
):
    db_path = str(tmp_path / f"boundary_{run_status}_{constraint_type.value}.db")
    async with ThreadStore(db_path) as thread_store:
        thread = await thread_store.create_thread(title="Boundary")
        message = await thread_store.append_message(
            thread_id=thread["id"],
            role="user",
            text="帮我生成内容",
            intent="start_workflow",
        )
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(
            thread_id=thread["id"],
            user_id="user-1",
            user_message_id=message["id"],
        )
        await manager.initialize_steps(
            run.run_id,
            [{"step_name": step_name, "phase": WorkflowPhase(run_phase)}],
        )
    async with WorkflowStore(db_path) as store:
        assert store._conn is not None
        await store._conn.execute(
            "UPDATE workflow_runs SET status=?, phase=? WHERE run_id=?",
            (run_status, run_phase, run.run_id),
        )
        await store._conn.commit()
        await store.create_constraint(
            run_id=run.run_id,
            thread_id=run.thread_id,
            message_id="constraint-msg",
            raw_text="constraint",
            constraint_type=constraint_type,
            scope="run",
            normalized={"value": "x"},
        )
    return await ContextBuilder(db_path).build_context(run.run_id, step_name)


@pytest.mark.asyncio
async def test_cancel_before_commit_is_blocked_by_commit_guard(tmp_path):
    context = await _context_for(tmp_path, run_status="cancelling")

    decision = BoundaryEvaluator().evaluate(context, boundary="before_artifact_commit")

    assert decision.action == "cancel"
    assert decision.reason == "commit_guard_cancel"


@pytest.mark.asyncio
async def test_pausing_at_safe_boundary_returns_pause(tmp_path):
    context = await _context_for(tmp_path, run_status="pausing")

    decision = BoundaryEvaluator().evaluate(context, boundary="after_step_complete")

    assert decision.action == "pause"
    assert decision.reason == "safe_boundary_reached"


@pytest.mark.asyncio
async def test_topic_change_in_generation_recommends_rerun(tmp_path):
    context = await _context_for(
        tmp_path,
        run_status="running",
        run_phase="generation",
        constraint_type=WorkflowConstraintType.TOPIC_CHANGE,
    )

    assert context.constraints == []
    assert [constraint["constraint_type"] for constraint in context.pending_constraints] == ["topic_change"]

    decision = BoundaryEvaluator().evaluate(context, boundary="before_next_step")

    assert decision.action == "rerun_step"
    assert decision.reason == "topic_change_requires_replan"


@pytest.mark.asyncio
async def test_style_constraint_in_generation_applies_downstream(tmp_path):
    context = await _context_for(
        tmp_path,
        run_status="running",
        run_phase="generation",
        constraint_type=WorkflowConstraintType.STYLE,
    )

    decision = BoundaryEvaluator().evaluate(context, boundary="before_next_step")

    assert decision.action == "apply_downstream"
    assert decision.reason == "style_constraint_applies_to_generation"


@pytest.mark.asyncio
async def test_no_intervention_returns_commit(tmp_path):
    context = await _context_for(
        tmp_path,
        run_status="running",
        run_phase="discovery",
        constraint_type=WorkflowConstraintType.STYLE,
        step_name="discovery.spider_search",
    )

    decision = BoundaryEvaluator().evaluate(context, boundary="before_next_step")

    assert decision.action == "commit"
    assert decision.reason == "no_boundary_intervention"
