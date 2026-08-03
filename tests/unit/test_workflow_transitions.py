"""Unit tests for T2 WorkflowRunManager step transitions."""

from __future__ import annotations

import json

import pytest

from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowPhase, WorkflowRunStatus, WorkflowStepStatus
from app.services.workflow_run_manager import WorkflowRunManager, WorkflowTransitionError


@pytest.fixture
async def manager(tmp_path):
    db_path = str(tmp_path / "workflow_transitions.db")
    async with WorkflowRunManager(db_path) as m:
        yield m


async def _create_run_with_steps(manager: WorkflowRunManager):
    run = await manager.start_run(thread_id="thread-1", user_id="user-1")
    steps = await manager.initialize_steps(
        run.run_id,
        [
            {"step_name": "intake.capture_request", "phase": WorkflowPhase.INTAKE},
            {"step_name": "strategy.llm_synthesize", "phase": WorkflowPhase.STRATEGY},
            {"step_name": "finalization.persist_artifacts", "phase": WorkflowPhase.FINALIZATION},
        ],
    )
    return run, steps


@pytest.mark.asyncio
async def test_initialize_and_start_step_updates_run_and_step(manager):
    run, steps = await _create_run_with_steps(manager)

    started = await manager.start_step(run.run_id, "intake.capture_request", job_id="job-1")

    assert len(steps) == 3
    assert started.status == WorkflowStepStatus.RUNNING
    assert started.active_job_id == "job-1"

    async with WorkflowStore(manager.db_path) as store:
        refreshed = await store.get_run(run.run_id)
        events = await store.list_events(run.run_id)

    assert refreshed is not None
    assert refreshed.current_step == "intake.capture_request"
    assert refreshed.active_job_id == "job-1"
    assert "steps_initialized" in [event.event_type for event in events]
    assert "step_started" in [event.event_type for event in events]


@pytest.mark.asyncio
async def test_complete_step_writes_artifact_refs_and_advance_to_next_step(manager):
    run, _ = await _create_run_with_steps(manager)
    await manager.start_step(run.run_id, "intake.capture_request")

    completed = await manager.complete_step(
        run.run_id,
        "intake.capture_request",
        artifact_refs=[{"artifact_id": "artifact-1", "type": "source_snapshot"}],
    )
    advanced = await manager.advance_to_next_step(run.run_id)

    assert completed.status == WorkflowStepStatus.SUCCEEDED
    assert completed.output_artifact_refs_json == [
        {"artifact_id": "artifact-1", "type": "source_snapshot"}
    ]
    assert advanced.current_step == "strategy.llm_synthesize"
    assert advanced.phase == WorkflowPhase.STRATEGY


@pytest.mark.asyncio
async def test_retry_fail_cancel_and_skip_step_transitions(manager):
    run, _ = await _create_run_with_steps(manager)

    await manager.start_step(run.run_id, "intake.capture_request")
    retrying = await manager.retry_step(
        run.run_id,
        "intake.capture_request",
        {"code": "TEMP", "message": "temporary"},
    )
    assert retrying.status == WorkflowStepStatus.RETRYING
    assert retrying.attempt_count == 1
    assert retrying.error_code == "TEMP"

    await manager.start_step(run.run_id, "intake.capture_request")
    failed = await manager.fail_step(run.run_id, "intake.capture_request", "fatal")
    assert failed.status == WorkflowStepStatus.FAILED

    skipped = await manager.skip_step(run.run_id, "strategy.llm_synthesize", reason="not needed")
    assert skipped.status == WorkflowStepStatus.SKIPPED

    cancelled = await manager.cancel_step(run.run_id, "finalization.persist_artifacts")
    assert cancelled.status == WorkflowStepStatus.CANCELLED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initial_status", "open_interval_key"),
    [
        ("pending", "queue_spans"),
        ("running", "execution_spans"),
        ("retrying", "retry_backoff_spans"),
        ("paused", "pause_spans"),
        ("retrying", "waiting_spans"),
    ],
)
async def test_cancel_step_closes_every_open_interval_for_each_cancellable_state(
    manager, initial_status, open_interval_key
):
    run = await manager.start_run(
        thread_id=f"thread-cancel-{initial_status}-{open_interval_key}", user_id="user-1"
    )
    step = (
        await manager.initialize_steps(
            run.run_id,
            [{"step_name": "formal_research", "phase": WorkflowPhase.RETRIEVAL}],
        )
    )[0]
    open_at = "2026-08-03T01:00:00.000001+00:00"
    assert manager._conn is not None
    await manager._conn.execute(
        "UPDATE workflow_steps SET status=?, timing_json=? WHERE step_id=?",
        (
            initial_status,
            json.dumps(
                {
                    open_interval_key: [
                        {"started_at": open_at, "finished_at": None}
                    ]
                }
            ),
            step.step_id,
        ),
    )
    await manager._conn.commit()

    cancelled = await manager.cancel_step(run.run_id, step.step_name)

    assert cancelled.status == WorkflowStepStatus.CANCELLED
    assert cancelled.completed_at is not None
    assert cancelled.timing_json is not None
    assert cancelled.timing_json[open_interval_key][0]["finished_at"] is not None
    assert cancelled.timing_json[open_interval_key][0]["finished_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_complete_step_commit_guard_makes_cancel_win(manager):
    run, _ = await _create_run_with_steps(manager)
    await manager.start_step(run.run_id, "intake.capture_request")
    cancelling = await manager.cancel_run(run.run_id, reason="user_cancelled")
    assert cancelling.status == WorkflowRunStatus.CANCELLING

    guarded = await manager.complete_step(run.run_id, "intake.capture_request")

    assert guarded.status == WorkflowStepStatus.CANCELLED
    async with WorkflowStore(manager.db_path) as store:
        refreshed = await store.get_run(run.run_id)
        events = await store.list_events(run.run_id)

    assert refreshed is not None
    assert refreshed.status == WorkflowRunStatus.CANCELLED
    event_types = [event.event_type for event in events]
    assert "step_completed" not in event_types
    assert "step_cancelled" in event_types
    assert guarded.timing_json is not None
    assert guarded.timing_json["execution_spans"][-1]["finished_at"] is not None


@pytest.mark.asyncio
async def test_step_transition_rejects_illegal_state(manager):
    run, _ = await _create_run_with_steps(manager)

    with pytest.raises(WorkflowTransitionError):
        await manager.complete_step(run.run_id, "intake.capture_request")
