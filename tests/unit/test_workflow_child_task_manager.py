"""Unit tests for T2.1 WorkflowRunManager child task transitions."""

from __future__ import annotations

import pytest

from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowPhase, WorkflowStepStatus
from app.services.workflow_run_manager import WorkflowRunManager, WorkflowTransitionError


@pytest.fixture
async def seeded_manager(tmp_path):
    db_path = str(tmp_path / "workflow_child_task_manager.db")
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id="thread-1", user_id="user-1")
        steps = await manager.initialize_steps(
            run.run_id,
            [
                {
                    "step_name": "generation.generate_notes_parallel",
                    "phase": WorkflowPhase.GENERATION,
                }
            ],
        )
        yield manager, run, steps[0]


async def _event_types(db_path: str, run_id: str) -> list[str]:
    async with WorkflowStore(db_path) as store:
        events = await store.list_events(run_id)
    return [event.event_type for event in events]


@pytest.mark.asyncio
async def test_create_start_retry_and_complete_child_task(seeded_manager):
    manager, run, step = seeded_manager

    children = await manager.create_child_tasks(
        run_id=run.run_id,
        step_id=step.step_id,
        tasks=[
            {"task_type": "note_generation", "slot_index": 0, "proposal_id": "proposal-1"}
        ],
    )
    child = children[0]

    started = await manager.start_child_task(child.child_task_id)
    retrying = await manager.retry_child_task(
        child.child_task_id,
        {"code": "TEMP", "message": "temporary"},
    )
    restarted = await manager.start_child_task(child.child_task_id)
    completed = await manager.complete_child_task(
        child.child_task_id,
        artifact_refs=[{"artifact_id": "artifact-1", "artifact_type": "generated_note"}],
        note_id="artifact-1",
    )

    assert started.status == WorkflowStepStatus.RUNNING
    assert retrying.status == WorkflowStepStatus.RETRYING
    assert retrying.attempt_count == 1
    assert restarted.status == WorkflowStepStatus.RUNNING
    assert completed.status == WorkflowStepStatus.SUCCEEDED
    assert completed.note_id == "artifact-1"
    assert completed.output_artifact_refs_json == [
        {"artifact_id": "artifact-1", "artifact_type": "generated_note"}
    ]
    event_types = await _event_types(manager.db_path, run.run_id)
    assert "child_tasks_created" in event_types
    assert "child_task_started" in event_types
    assert "child_task_retry_scheduled" in event_types
    assert "child_task_completed" in event_types


@pytest.mark.asyncio
async def test_fail_and_cancel_child_task(seeded_manager):
    manager, run, step = seeded_manager
    failed_child, cancelled_child = await manager.create_child_tasks(
        run_id=run.run_id,
        step_id=step.step_id,
        tasks=[
            {"task_type": "note_generation", "slot_index": 0},
            {"task_type": "note_generation", "slot_index": 1},
        ],
    )

    await manager.start_child_task(failed_child.child_task_id)
    failed = await manager.fail_child_task(
        failed_child.child_task_id,
        {"code": "FATAL", "message": "bad note"},
    )
    cancelled = await manager.cancel_child_task(cancelled_child.child_task_id, reason="user_cancel")
    second_cancel = await manager.cancel_child_task(cancelled_child.child_task_id)

    assert failed.status == WorkflowStepStatus.FAILED
    assert failed.error_code == "FATAL"
    assert cancelled.status == WorkflowStepStatus.CANCELLED
    assert second_cancel.status == WorkflowStepStatus.CANCELLED
    event_types = await _event_types(manager.db_path, run.run_id)
    assert "child_task_failed" in event_types
    assert event_types.count("child_task_cancelled") == 1


@pytest.mark.asyncio
async def test_child_task_transition_rejects_illegal_state(seeded_manager):
    manager, run, step = seeded_manager
    child = (
        await manager.create_child_tasks(
            run_id=run.run_id,
            step_id=step.step_id,
            tasks=[{"task_type": "note_generation", "slot_index": 0}],
        )
    )[0]

    with pytest.raises(WorkflowTransitionError):
        await manager.complete_child_task(child.child_task_id)


@pytest.mark.asyncio
async def test_child_task_retry_event_failure_rolls_back_state(seeded_manager, monkeypatch):
    manager, run, step = seeded_manager
    child = (
        await manager.create_child_tasks(
            run_id=run.run_id,
            step_id=step.step_id,
            tasks=[{"task_type": "note_generation", "slot_index": 0}],
        )
    )[0]
    await manager.start_child_task(child.child_task_id)

    async def fail_append_event(**kwargs):
        raise RuntimeError("event write failed")

    monkeypatch.setattr(manager, "_append_event", fail_append_event)

    with pytest.raises(RuntimeError, match="event write failed"):
        await manager.retry_child_task(child.child_task_id, "temporary")

    async with WorkflowStore(manager.db_path) as store:
        refreshed = await store.get_child_task(child.child_task_id)

    assert refreshed is not None
    assert refreshed.status == WorkflowStepStatus.RUNNING
