"""Unit tests for T2 WorkflowRunManager run-level transitions."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from app.content_research.observation.trace_service import _project_timing
from app.content_research.service import WorkflowRunManagerRuntime
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


async def _seed_nonterminal_descendant_matrix(
    manager: WorkflowRunManager, run_id: str
) -> None:
    steps = await manager.initialize_steps(
        run_id,
        [
            {"step_name": f"matrix.{status}", "phase": "retrieval"}
            for status in ("pending", "running", "retrying", "paused")
        ],
    )
    children = await manager.create_child_tasks(
        run_id=run_id,
        step_id=steps[0].step_id,
        tasks=[
            {"task_type": f"matrix.{status}", "slot_index": index}
            for index, status in enumerate(("pending", "running", "retrying", "paused"))
        ],
    )
    interval_keys = {
        "pending": ("queue_spans",),
        "running": ("execution_spans",),
        "retrying": ("retry_backoff_spans", "waiting_spans"),
        "paused": ("pause_spans",),
    }
    assert manager._conn is not None
    for table, id_column, records in (
        ("workflow_steps", "step_id", steps),
        ("workflow_child_tasks", "child_task_id", children),
    ):
        for status, record in zip(interval_keys, records, strict=True):
            timing = {
                key: [
                    {
                        "started_at": "2026-08-03T01:00:00.000001+00:00",
                        "finished_at": None,
                    },
                    {
                        "started_at": "2026-08-03T01:00:01.000001+00:00",
                        "finished_at": None,
                    },
                ]
                for key in interval_keys[status]
            }
            record_id = getattr(record, id_column)
            await manager._conn.execute(
                f"UPDATE {table} SET status=?, timing_json=? WHERE {id_column}=?",
                (status, json.dumps(timing), record_id),
            )
    await manager._conn.commit()


def _assert_terminal_timing_is_frozen_at(
    records, *, expected_statuses: list[str], boundary
) -> None:
    assert [record.status.value for record in records] == expected_statuses
    for record in records:
        assert record.completed_at == boundary
        assert record.timing_json is not None
        for intervals in record.timing_json.values():
            if isinstance(intervals, list):
                assert {span["finished_at"] for span in intervals} == {
                    boundary.isoformat()
                }
        first = _project_timing(
            record.model_dump(mode="json"), as_of=boundary + timedelta(seconds=1)
        )
        second = _project_timing(
            record.model_dump(mode="json"), as_of=boundary + timedelta(minutes=5)
        )
        assert second == first


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
async def test_step_timing_records_queue_and_closes_active_span(manager):
    run = await manager.start_run(thread_id="thread-timing", user_id="user-timing")
    step = (
        await manager.initialize_steps(
            run.run_id,
            [{"step_name": "formal_research", "phase": "retrieval", "max_attempts": 3}],
        )
    )[0]

    started = await manager.start_step(run.run_id, step.step_name)
    completed = await manager.complete_step(run.run_id, step.step_name)

    assert started.timing_json is not None
    assert started.timing_json["queued_at"].endswith("+00:00")
    assert started.timing_json["execution_spans"][-1]["finished_at"] is None
    assert completed.timing_json is not None
    assert completed.timing_json["execution_spans"][-1]["finished_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_waiting_closes_active_span_and_resumed_step_adds_a_new_span(manager):
    run = await manager.start_run(thread_id="thread-waiting", user_id="user-waiting")
    step = (
        await manager.initialize_steps(
            run.run_id,
            [{"step_name": "formal_research", "phase": "retrieval", "max_attempts": 3}],
        )
    )[0]
    await manager.start_step(run.run_id, step.step_name)

    await manager.wait_for_user_recovery(
        run.run_id,
        step_name=step.step_name,
        reason={"code": "transient_error", "message": "retry later"},
    )
    async with WorkflowStore(manager.db_path) as store:
        waiting_step = (await store.list_steps(run.run_id))[0]
    assert waiting_step.timing_json is not None
    assert waiting_step.timing_json["execution_spans"][-1]["finished_at"] is not None
    assert waiting_step.timing_json["waiting_started_at"].endswith("+00:00")

    await manager.resume_run(run.run_id)
    resumed = await manager.start_step(run.run_id, step.step_name)
    assert resumed.timing_json is not None
    assert len(resumed.timing_json["execution_spans"]) == 2
    assert resumed.timing_json["execution_spans"][-1]["finished_at"] is None


@pytest.mark.asyncio
async def test_recorded_step_boundary_brackets_work_before_state_transition(manager):
    run = await manager.start_run(thread_id="thread-boundary", user_id="user-boundary")
    step = (
        await manager.initialize_steps(
            run.run_id,
            [{"step_name": "plan_build", "phase": "intake", "max_attempts": 1}],
        )
    )[0]

    recorded = await manager.record_step_execution_started(run.run_id, step.step_name)
    started = await manager.start_step(run.run_id, step.step_name)
    completed = await manager.complete_step(run.run_id, step.step_name)

    assert recorded.status.value == "pending"
    assert started.timing_json == recorded.timing_json
    assert completed.timing_json is not None
    assert len(completed.timing_json["execution_spans"]) == 1
    assert completed.timing_json["execution_spans"][0]["finished_at"] is not None


@pytest.mark.asyncio
async def test_brief_and_plan_recorded_execution_spans_do_not_overlap(manager):
    run = await manager.start_run(thread_id="thread-phases", user_id="user-phases")
    await manager.initialize_steps(
        run.run_id,
        [
            {"step_name": "brief_confirm", "phase": "intake", "max_attempts": 1},
            {"step_name": "plan_build", "phase": "intake", "max_attempts": 1},
            {"step_name": "formal_research", "phase": "retrieval", "max_attempts": 1},
        ],
    )
    await manager.record_step_execution_started(run.run_id, "brief_confirm")
    await manager.record_step_execution_finished(run.run_id, "brief_confirm")
    await manager.record_step_execution_started(run.run_id, "plan_build")

    async def no_op_writer(_conn, _child_ids):
        return None

    await manager.complete_brief_and_plan_atomically(
        workflow_run_id=run.run_id,
        task_specs=[{"task_type": "source_collect", "sequence_no": 1}],
        confirmation_writer=no_op_writer,
    )

    async with WorkflowStore(manager.db_path) as store:
        steps = {step.step_name: step for step in await store.list_steps(run.run_id)}
    brief_span = steps["brief_confirm"].timing_json["execution_spans"][0]
    plan_span = steps["plan_build"].timing_json["execution_spans"][0]
    assert len(steps["brief_confirm"].timing_json["execution_spans"]) == 1
    assert len(steps["plan_build"].timing_json["execution_spans"]) == 1
    assert brief_span["finished_at"] <= plan_span["started_at"]
    assert plan_span["finished_at"] is not None


@pytest.mark.asyncio
async def test_pause_boundary_closes_active_span(manager):
    run = await manager.start_run(thread_id="thread-paused", user_id="user-paused")
    step = (
        await manager.initialize_steps(
            run.run_id,
            [{"step_name": "formal_research", "phase": "retrieval", "max_attempts": 3}],
        )
    )[0]
    await manager.start_step(run.run_id, step.step_name)
    await manager.pause_run(run.run_id)
    await manager.ack_pause_at_boundary(run.run_id, step.step_name)

    async with WorkflowStore(manager.db_path) as store:
        paused_step = (await store.list_steps(run.run_id))[0]
    assert paused_step.timing_json is not None
    assert paused_step.timing_json["execution_spans"][-1]["finished_at"] is not None


@pytest.mark.asyncio
async def test_pause_boundary_atomically_freezes_all_running_child_spans(manager):
    run = await manager.start_run(thread_id="thread-paused-children", user_id="user-paused")
    step = (
        await manager.initialize_steps(
            run.run_id,
            [{"step_name": "formal_research", "phase": "retrieval", "max_attempts": 3}],
        )
    )[0]
    await manager.start_step(run.run_id, step.step_name)
    children = await manager.create_child_tasks(
        run_id=run.run_id,
        step_id=step.step_id,
        tasks=[{"task_type": "source_collect", "slot_index": index} for index in range(2)],
    )
    for child in children:
        await manager.start_child_task(child.child_task_id)

    await manager.pause_run(run.run_id)
    await manager.ack_pause_at_boundary(run.run_id, step.step_name)

    async with WorkflowStore(manager.db_path) as store:
        paused_children = await store.list_child_tasks(run.run_id)
    assert [child.status.value for child in paused_children] == ["retrying", "retrying"]
    for child in paused_children:
        assert child.timing_json is not None
        assert child.timing_json["execution_spans"][-1]["finished_at"] is not None
        assert child.timing_json["pause_spans"][-1]["finished_at"] is None


@pytest.mark.asyncio
async def test_cancel_run_closes_child_active_backoff_and_queue_spans(manager):
    run = await manager.start_run(thread_id="thread-cancel-timing", user_id="user-cancel")
    step = (
        await manager.initialize_steps(
            run.run_id,
            [{"step_name": "formal_research", "phase": "retrieval", "max_attempts": 3}],
        )
    )[0]
    await manager.start_step(run.run_id, step.step_name)
    pending_child, running_child, retrying_child = await manager.create_child_tasks(
        run_id=run.run_id,
        step_id=step.step_id,
        tasks=[{"task_type": "source_collect", "slot_index": index} for index in range(3)],
    )
    await manager.start_child_task(running_child.child_task_id)
    await manager.start_child_task(retrying_child.child_task_id)
    await manager.retry_child_task(retrying_child.child_task_id, "temporary")

    await manager.cancel_run(run.run_id)

    async with WorkflowStore(manager.db_path) as store:
        cancelled_children = await store.list_child_tasks(run.run_id)
    assert [child.status.value for child in cancelled_children] == [
        "cancelled",
        "cancelled",
        "cancelled",
    ]
    for child in cancelled_children:
        assert child.timing_json is not None
        for intervals in child.timing_json.values():
            if isinstance(intervals, list):
                assert all(span.get("finished_at") for span in intervals)


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
@pytest.mark.parametrize(
    ("terminal_action", "run_status", "descendant_statuses"),
    [
        (
            "complete",
            WorkflowRunStatus.SUCCEEDED,
            ["cancelled", "cancelled", "cancelled", "cancelled"],
        ),
        (
            "fail",
            WorkflowRunStatus.FAILED,
            ["cancelled", "failed", "failed", "cancelled"],
        ),
    ],
)
async def test_terminal_run_atomically_converges_nonterminal_descendant_matrix(
    manager, terminal_action, run_status, descendant_statuses
):
    run = await manager.start_run(
        thread_id=f"thread-terminal-{terminal_action}", user_id="user-terminal"
    )
    await _seed_nonterminal_descendant_matrix(manager, run.run_id)

    if terminal_action == "complete":
        terminal_run = await manager.complete_run(run.run_id)
        boundary = terminal_run.completed_at
    else:
        terminal_run = await manager.fail_run(run.run_id, "terminal failure")
        boundary = terminal_run.failed_at

    assert terminal_run.status == run_status
    assert boundary is not None
    assert boundary.isoformat().endswith("+00:00")
    async with WorkflowStore(manager.db_path) as store:
        steps = await store.list_steps(run.run_id)
        children = await store.list_child_tasks(run.run_id)
    _assert_terminal_timing_is_frozen_at(
        steps, expected_statuses=descendant_statuses, boundary=boundary
    )
    _assert_terminal_timing_is_frozen_at(
        children, expected_statuses=descendant_statuses, boundary=boundary
    )


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
async def test_recoverable_specialist_failure_waits_for_user_and_resumes(manager):
    run = await manager.start_run(thread_id="thread-recovery", user_id="user-1")
    step = (await manager.initialize_steps(
        run.run_id,
        [{"step_name": "formal_research", "phase": "retrieval", "max_attempts": 1}],
    ))[0]
    await manager.start_step(run.run_id, step.step_name)

    waiting = await manager.wait_for_user_recovery(
        run.run_id,
        step_name="formal_research",
        reason={"code": "transient_error", "message": "provider detail call failed"},
    )

    assert waiting.status == WorkflowRunStatus.WAITING_USER
    resumed = await manager.resume_run(run.run_id)
    assert resumed.status == WorkflowRunStatus.RUNNING
    assert "run_waiting_user" in await _event_types(manager.db_path, run.run_id)


@pytest.mark.asyncio
async def test_recovery_state_writer_and_workflow_transition_roll_back_together(manager):
    run = await manager.start_run(thread_id="thread-atomic-recovery", user_id="user-1")
    await manager.initialize_steps(
        run.run_id,
        [{"step_name": "presearch", "phase": "intake", "max_attempts": 3}],
    )
    await manager.start_step(run.run_id, "presearch")
    assert manager._conn is not None
    await manager._conn.execute("CREATE TABLE recovery_marker (value TEXT NOT NULL)")
    await manager._conn.commit()

    async def failing_writer(conn):
        await conn.execute("INSERT INTO recovery_marker (value) VALUES ('brief-updated')")
        raise RuntimeError("abort shared recovery transaction")

    with pytest.raises(RuntimeError, match="abort shared recovery transaction"):
        await manager.wait_for_user_recovery(
            run.run_id,
            step_name="presearch",
            reason={"code": "llm_auth_invalid", "message": "API Key 无效"},
            state_writer=failing_writer,
        )

    async with manager._conn.execute("SELECT COUNT(*) FROM recovery_marker") as cursor:
        assert (await cursor.fetchone())[0] == 0
    assert (await manager._fetch_run_row(run.run_id))["status"] == "running"
    assert (await manager._fetch_step_row(run.run_id, "presearch"))["status"] == "running"


@pytest.mark.asyncio
async def test_formal_recovery_action_consumes_one_child_recovery_attempt(tmp_path):
    db_path = str(tmp_path / "formal_recovery_attempt.db")
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id="thread-recovery", user_id="user-1")
        step = (
            await manager.initialize_steps(
                run.run_id,
                [
                    {
                        "step_name": "formal_research",
                        "phase": "retrieval",
                        "max_attempts": 3,
                    }
                ],
            )
        )[0]
        await manager.start_step(run.run_id, step.step_name)
        child = (
            await manager.create_child_tasks(
                run_id=run.run_id,
                step_id=step.step_id,
                tasks=[
                    {
                        "task_type": "content_research.product_marketing",
                        "max_attempts": 3,
                    }
                ],
            )
        )[0]
        await manager.start_child_task(child.child_task_id)
        await manager.fail_child_task(child.child_task_id, "provider failed")
        await manager.wait_for_user_recovery(
            run.run_id,
            step_name="formal_research",
            reason="provider recovery required",
        )

    await WorkflowRunManagerRuntime(db_path).restart_formal_research_step(
        workflow_run_id=run.run_id,
        child_task_ids=[child.child_task_id],
    )

    async with WorkflowStore(db_path) as store:
        recovered_run = await store.get_run(run.run_id)
        recovered_steps = await store.list_steps(run.run_id)
        recovered_children = await store.list_child_tasks(run.run_id)

    assert recovered_run is not None
    assert recovered_run.status == WorkflowRunStatus.RUNNING
    assert recovered_steps[0].status.value == "running"
    assert recovered_children[0].status.value == "retrying"
    assert recovered_children[0].attempt_count == 1


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
    assert events[-1].payload_json == {"reason": "user_pause", "paused_job_count": 0}


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
