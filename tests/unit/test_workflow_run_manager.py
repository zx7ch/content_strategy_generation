"""Unit tests for T2 WorkflowRunManager run-level transitions."""

from __future__ import annotations

import pytest

from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowConstraintType, WorkflowRunStatus
from app.services.workflow_run_manager import WorkflowRunManager, WorkflowTransitionError


@pytest.fixture
async def manager(tmp_path):
    db_path = str(tmp_path / "workflow_manager.db")
    async with WorkflowRunManager(db_path) as m:
        yield m


async def _event_types(db_path: str, run_id: str) -> list[str]:
    async with WorkflowStore(db_path) as store:
        events = await store.list_events(run_id)
    return [event.event_type for event in events]


@pytest.mark.asyncio
async def test_start_run_sets_running_appends_event_and_updates_thread_active_run(tmp_path):
    db_path = str(tmp_path / "thread_active_run.db")
    async with ThreadStore(db_path) as thread_store:
        thread = await thread_store.create_thread(title="Workflow")

    async with WorkflowRunManager(db_path) as m:
        run = await m.start_run(
            thread_id=thread["id"],
            user_id="user-1",
            user_message_id="msg-1",
            initial_request="生成防晒衣笔记",
        )

    async with ThreadStore(db_path) as thread_store:
        updated_thread = await thread_store.get_thread(thread["id"])

    assert run.status == WorkflowRunStatus.RUNNING
    assert run.started_at is not None
    assert updated_thread is not None
    assert updated_thread["active_run_id"] == run.run_id
    assert await _event_types(db_path, run.run_id) == ["run_started"]


@pytest.mark.asyncio
async def test_legal_run_transitions(manager):
    run = await manager.start_run(thread_id="thread-1", user_id="user-1")

    pausing = await manager.pause_run(run.run_id)
    assert pausing.status == WorkflowRunStatus.PAUSING

    cancelling = await manager.cancel_run(run.run_id)
    assert cancelling.status == WorkflowRunStatus.CANCELLING

    second_cancel = await manager.cancel_run(run.run_id)
    assert second_cancel.status == WorkflowRunStatus.CANCELLING


@pytest.mark.asyncio
async def test_resume_complete_and_fail_run_transitions_use_allowed_states(tmp_path):
    db_path = str(tmp_path / "run_states.db")
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id="thread-1", user_id="user-1")
        assert manager._conn is not None
        await manager._conn.execute(
            "UPDATE workflow_runs SET status='paused' WHERE run_id=?", (run.run_id,)
        )
        await manager._conn.commit()

        resumed = await manager.resume_run(run.run_id)
        assert resumed.status == WorkflowRunStatus.RUNNING

        completed = await manager.complete_run(run.run_id)
        assert completed.status == WorkflowRunStatus.SUCCEEDED

        failed_run = await manager.start_run(thread_id="thread-2", user_id="user-1")
        failed = await manager.fail_run(
            failed_run.run_id,
            {"code": "BOOM", "message": "non recoverable"},
        )
        assert failed.status == WorkflowRunStatus.FAILED
        assert failed.error_code == "BOOM"
        assert failed.error_message == "non recoverable"


@pytest.mark.asyncio
async def test_terminal_state_rejects_illegal_transition(manager):
    run = await manager.start_run(thread_id="thread-1", user_id="user-1")
    completed = await manager.complete_run(run.run_id)

    with pytest.raises(WorkflowTransitionError):
        await manager.pause_run(completed.run_id)


@pytest.mark.asyncio
async def test_repeated_pause_does_not_duplicate_event(manager):
    run = await manager.start_run(thread_id="thread-1", user_id="user-1")

    first = await manager.pause_run(run.run_id)
    second = await manager.pause_run(run.run_id)

    assert first.status == WorkflowRunStatus.PAUSING
    assert second.status == WorkflowRunStatus.PAUSING
    event_types = await _event_types(manager.db_path, run.run_id)
    assert event_types.count("run_pause_requested") == 1


@pytest.mark.asyncio
async def test_state_update_and_event_append_succeed_together(manager):
    run = await manager.start_run(thread_id="thread-1", user_id="user-1")

    paused = await manager.pause_run(run.run_id, reason="user_pause")

    async with WorkflowStore(manager.db_path) as store:
        refreshed = await store.get_run(run.run_id)
        events = await store.list_events(run.run_id)

    assert paused.status == WorkflowRunStatus.PAUSING
    assert refreshed is not None
    assert refreshed.status == WorkflowRunStatus.PAUSING
    assert events[-1].event_type == "run_pause_requested"
    assert events[-1].payload_json == {"reason": "user_pause"}


@pytest.mark.asyncio
async def test_event_append_failure_rolls_back_run_state(manager, monkeypatch):
    run = await manager.start_run(thread_id="thread-1", user_id="user-1")

    async def fail_append_event(**kwargs):
        raise RuntimeError("event write failed")

    monkeypatch.setattr(manager, "_append_event", fail_append_event)

    with pytest.raises(RuntimeError, match="event write failed"):
        await manager.pause_run(run.run_id)

    async with WorkflowStore(manager.db_path) as store:
        refreshed = await store.get_run(run.run_id)

    assert refreshed is not None
    assert refreshed.status == WorkflowRunStatus.RUNNING


@pytest.mark.asyncio
async def test_add_constraint_updates_run_version_and_appends_event(manager):
    run = await manager.start_run(thread_id="thread-1", user_id="user-1")

    constraint = await manager.add_constraint(
        run_id=run.run_id,
        message_id="msg-1",
        raw_text="语气更年轻",
        constraint_type=WorkflowConstraintType.STYLE,
        scope="generation",
        normalized_constraint={"tone": "young"},
        confidence=0.9,
    )

    async with WorkflowStore(manager.db_path) as store:
        refreshed = await store.get_run(run.run_id)
        events = await store.list_events(run.run_id)

    assert constraint.constraint_version == 1
    assert refreshed is not None
    assert refreshed.constraint_version == 1
    assert events[-1].event_type == "constraint_added"
