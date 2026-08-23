from __future__ import annotations

import sqlite3
import asyncio

import pytest

from app.content_research.lifecycle.coordinator import (
    ContentResearchPersistenceCoordinator,
    LifecycleCommandConflict,
    LifecyclePersistenceBusy,
)
from app.content_research.lifecycle.models import (
    ContentResearchState,
    ExecutionEvent,
    LifecycleCommand,
)
from app.memory.thread_store import ThreadStore


async def _create_thread(db_path: str) -> str:
    async with ThreadStore(db_path) as store:
        thread = await store.create_thread(
            title="生命周期测试",
            workspace_id="ws-lifecycle",
            brand_id="brand-lifecycle",
        )
    return str(thread["id"])


def _table_count(db_path: str, table: str) -> int:
    with sqlite3.connect(db_path) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


@pytest.mark.asyncio
async def test_submit_subject_atomically_creates_and_activates_the_presearch_run(
    tmp_path,
) -> None:
    db_path = str(tmp_path / "lifecycle.db")
    thread_id = await _create_thread(db_path)
    coordinator = ContentResearchPersistenceCoordinator(db_path)
    command = LifecycleCommand(
        command_id="cmd-submit-1",
        run_id="run-lifecycle-1",
        expected_state=None,
        expected_revision=0,
        kind="submit_research_subject",
        payload={
            "thread_id": thread_id,
            "user_id": "user-lifecycle",
            "seed_text": "夏季凉感T恤",
        },
    )

    created = await coordinator.apply(command)
    duplicate = await coordinator.apply(command)

    assert created == duplicate
    assert created.run_id == "run-lifecycle-1"
    assert created.thread_id == thread_id
    assert created.state is ContentResearchState.PRESEARCH_RUNNING
    assert created.state_revision == 1
    assert created.allowed_actions == ("cancel",)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        thread = conn.execute(
            "SELECT active_run_id FROM creator_threads WHERE id=?", (thread_id,)
        ).fetchone()
        run = conn.execute(
            """SELECT content_research_state, state_revision
               FROM workflow_runs WHERE run_id=?""",
            (created.run_id,),
        ).fetchone()
        transitions = conn.execute(
            """SELECT from_state, to_state, event, state_revision
               FROM content_research_state_transitions WHERE run_id=?""",
            (created.run_id,),
        ).fetchall()
    assert thread is not None and thread["active_run_id"] == created.run_id
    assert run is not None and dict(run) == {
        "content_research_state": "presearch_running",
        "state_revision": 1,
    }
    assert [dict(row) for row in transitions] == [
        {
            "from_state": None,
            "to_state": "presearch_running",
            "event": "submit_research_subject",
            "state_revision": 1,
        }
    ]
    assert _table_count(db_path, "workflow_runs") == 1
    assert _table_count(db_path, "content_research_lifecycle_commands") == 1


@pytest.mark.asyncio
async def test_presearch_completion_atomically_persists_brief_and_state(tmp_path) -> None:
    db_path = str(tmp_path / "presearch-complete.db")
    thread_id = await _create_thread(db_path)
    coordinator = ContentResearchPersistenceCoordinator(db_path)
    await coordinator.apply(
        LifecycleCommand(
            command_id="cmd-submit-2",
            run_id="run-lifecycle-2",
            expected_state=None,
            expected_revision=0,
            kind="submit_research_subject",
            payload={
                "thread_id": thread_id,
                "user_id": "user-lifecycle",
                "seed_text": "夏季凉感T恤",
            },
        )
    )
    completed = await coordinator.apply(
        LifecycleCommand(
            command_id="cmd-presearch-complete",
            run_id="run-lifecycle-2",
            expected_state=ContentResearchState.PRESEARCH_RUNNING,
            expected_revision=1,
            kind="presearch_completed",
            payload={
                "brief_id": "brief-lifecycle-2",
                "schema_version": "content_research_brief_v1",
                "status": "draft",
                "subject": "夏季凉感T恤",
                "competitors": ["蕉内"],
                "directions": ["product_marketing"],
                "attempt_id": "attempt-lifecycle-2",
            },
        )
    )

    assert completed.state is ContentResearchState.BRIEF_CONFIRMATION_REQUIRED
    assert completed.state_revision == 2
    assert completed.brief_id == "brief-lifecycle-2"
    assert completed.allowed_actions == ("revise_subject", "cancel")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        brief = conn.execute(
            """SELECT id, workflow_run_id, thread_id, status
               FROM content_research_briefs WHERE id='brief-lifecycle-2'"""
        ).fetchone()
        transition_row = conn.execute(
            """SELECT from_state, to_state, event, state_revision
               FROM content_research_state_transitions
               WHERE run_id='run-lifecycle-2' ORDER BY state_revision DESC LIMIT 1"""
        ).fetchone()
    assert brief is not None and dict(brief) == {
        "id": "brief-lifecycle-2",
        "workflow_run_id": "run-lifecycle-2",
        "thread_id": thread_id,
        "status": "draft",
    }
    assert transition_row is not None and dict(transition_row) == {
        "from_state": "presearch_running",
        "to_state": "brief_confirmation_required",
        "event": "presearch_completed",
        "state_revision": 2,
    }
    assert _table_count(db_path, "content_research_scope_contracts") == 0
    assert _table_count(db_path, "content_research_dispatch_jobs") == 0


@pytest.mark.asyncio
async def test_stale_command_has_zero_business_write_delta(tmp_path) -> None:
    db_path = str(tmp_path / "stale.db")
    thread_id = await _create_thread(db_path)
    coordinator = ContentResearchPersistenceCoordinator(db_path)
    await coordinator.apply(
        LifecycleCommand(
            command_id="cmd-submit-3",
            run_id="run-lifecycle-3",
            expected_state=None,
            expected_revision=0,
            kind="submit_research_subject",
            payload={
                "thread_id": thread_id,
                "user_id": "user-lifecycle",
                "seed_text": "夏季凉感T恤",
            },
        )
    )
    before = {
        "commands": _table_count(db_path, "content_research_lifecycle_commands"),
        "transitions": _table_count(db_path, "content_research_state_transitions"),
        "briefs": _table_count(db_path, "content_research_briefs"),
    }

    with pytest.raises(LifecycleCommandConflict, match="revision"):
        await coordinator.apply(
            LifecycleCommand(
                command_id="cmd-stale",
                run_id="run-lifecycle-3",
                expected_state=ContentResearchState.PRESEARCH_RUNNING,
                expected_revision=9,
                kind="presearch_completed",
                payload={
                    "brief_id": "brief-stale",
                    "schema_version": "content_research_brief_v1",
                    "status": "draft",
                    "subject": "夏季凉感T恤",
                    "competitors": [],
                    "directions": ["product_marketing"],
                    "attempt_id": "attempt-stale",
                },
            )
        )

    after = {
        "commands": _table_count(db_path, "content_research_lifecycle_commands"),
        "transitions": _table_count(db_path, "content_research_state_transitions"),
        "briefs": _table_count(db_path, "content_research_briefs"),
    }
    assert after == before


@pytest.mark.asyncio
async def test_startup_reconciliation_converges_interrupted_presearch_without_replaying_llm(
    tmp_path,
) -> None:
    db_path = str(tmp_path / "restart-reconcile.db")
    thread_id = await _create_thread(db_path)
    coordinator = ContentResearchPersistenceCoordinator(db_path)
    await coordinator.apply(
        LifecycleCommand(
            command_id="cmd-submit-before-crash",
            run_id="run-before-crash",
            expected_state=None,
            expected_revision=0,
            kind="submit_research_subject",
            payload={
                "thread_id": thread_id,
                "user_id": "user-lifecycle",
                "workspace_id": "ws-lifecycle",
                "seed_text": "夏季凉感T恤",
                "user_note": "关注通勤",
            },
        )
    )

    restarted = ContentResearchPersistenceCoordinator(db_path)
    reconciled = await restarted.reconcile_interrupted_presearch()
    duplicate_scan = await restarted.reconcile_interrupted_presearch()

    assert [item.run_id for item in reconciled] == ["run-before-crash"]
    assert duplicate_scan == []
    projection = await restarted.load("run-before-crash")
    assert projection.state is ContentResearchState.RECOVERY_REQUIRED
    assert projection.state_revision == 2
    assert projection.reason_code == "PRESEARCH_PROCESS_INTERRUPTED"
    assert projection.brief_id is not None
    with sqlite3.connect(db_path) as connection:
        brief_payload = connection.execute(
            "SELECT payload_json FROM content_research_briefs WHERE id=?",
            (projection.brief_id,),
        ).fetchone()[0]
    assert "夏季凉感T恤" in brief_payload
    assert "关注通勤" in brief_payload


@pytest.mark.asyncio
async def test_projection_hides_stale_brief_while_subject_revision_is_running(tmp_path) -> None:
    db_path = str(tmp_path / "stale-brief-projection.db")
    thread_id = await _create_thread(db_path)
    coordinator = ContentResearchPersistenceCoordinator(db_path)
    await coordinator.apply(LifecycleCommand(
        command_id="submit-for-revision",
        run_id="run-for-revision",
        expected_state=None,
        expected_revision=0,
        kind="submit_research_subject",
        payload={"thread_id": thread_id, "user_id": "user", "seed_text": "凉感T恤"},
    ))
    await coordinator.apply(LifecycleCommand(
        command_id="complete-before-revision",
        run_id="run-for-revision",
        expected_state=ContentResearchState.PRESEARCH_RUNNING,
        expected_revision=1,
        kind="presearch_completed",
        payload={
            "brief_id": "brief-before-revision",
            "schema_version": "content_research_brief_v1",
            "brief_status": "draft",
            "subject": "凉感T恤",
            "directions": ["product_marketing"],
            "attempt_id": "attempt-before-revision",
        },
    ))

    revising = await coordinator.apply(LifecycleCommand(
        command_id="revise-now",
        run_id="run-for-revision",
        expected_state=ContentResearchState.BRIEF_CONFIRMATION_REQUIRED,
        expected_revision=2,
        kind="revise_subject",
        payload={},
    ))

    assert revising.state is ContentResearchState.PRESEARCH_RUNNING
    assert revising.brief_id is None


@pytest.mark.asyncio
async def test_brief_state_rejects_premature_frozen_scope_artifact(tmp_path) -> None:
    db_path = str(tmp_path / "illegal-scope-artifact.db")
    thread_id = await _create_thread(db_path)
    coordinator = ContentResearchPersistenceCoordinator(db_path)
    await coordinator.apply(LifecycleCommand(
        command_id="submit-before-illegal-scope",
        run_id="run-before-illegal-scope",
        expected_state=None,
        expected_revision=0,
        kind="submit_research_subject",
        payload={"thread_id": thread_id, "user_id": "user", "seed_text": "凉感T恤"},
    ))
    await coordinator.apply(LifecycleCommand(
        command_id="complete-before-illegal-scope",
        run_id="run-before-illegal-scope",
        expected_state=ContentResearchState.PRESEARCH_RUNNING,
        expected_revision=1,
        kind="presearch_completed",
        payload={
            "brief_id": "brief-before-illegal-scope",
            "schema_version": "content_research_brief_v1",
            "brief_status": "draft",
            "subject": "凉感T恤",
            "directions": ["product_marketing"],
            "attempt_id": "attempt-before-illegal-scope",
        },
    ))
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """INSERT INTO content_research_scope_contracts
               (id, workflow_run_id, research_plan_id, version, schema_version,
                constraints_json, query_groups_json, created_at)
               VALUES ('scope-illegal', 'run-before-illegal-scope', 'plan-illegal',
                       1, 'scope_v2', '[]', '[]', '2026-08-23T00:00:00+00:00')"""
        )
        connection.commit()

    with pytest.raises(ValueError, match="cannot own frozen Scope"):
        await coordinator.load("run-before-illegal-scope")


@pytest.mark.asyncio
async def test_execution_event_rejects_non_applicable_attempt_and_lease_identity(tmp_path) -> None:
    db_path = str(tmp_path / "execution-event.db")
    thread_id = await _create_thread(db_path)
    coordinator = ContentResearchPersistenceCoordinator(db_path)
    await coordinator.apply(LifecycleCommand(
        command_id="submit-before-event",
        run_id="run-before-event",
        expected_state=None,
        expected_revision=0,
        kind="submit_research_subject",
        payload={"thread_id": thread_id, "user_id": "user", "seed_text": "凉感T恤"},
    ))

    with pytest.raises(LifecycleCommandConflict, match="not applicable"):
        await coordinator.record(ExecutionEvent(
            run_id="run-before-event",
            expected_revision=1,
            attempt_id="attempt-event",
            lease_token="lease-event",
            kind="fail",
            payload={"error": {"code": "TEST_FAILURE", "message": "safe", "retryable": True}},
        ))

    transitions = await coordinator.list_transitions("run-before-event")
    assert [item["event"] for item in transitions] == ["submit_research_subject"]


@pytest.mark.asyncio
async def test_historical_v1_run_has_explicit_read_only_decoder_and_no_authority(tmp_path) -> None:
    db_path = str(tmp_path / "historical-v1.db")
    thread_id = await _create_thread(db_path)
    coordinator = ContentResearchPersistenceCoordinator(db_path)
    await coordinator.apply(LifecycleCommand(
        command_id="submit-before-marking-historical",
        run_id="run-historical-v1",
        expected_state=None,
        expected_revision=0,
        kind="submit_research_subject",
        payload={"thread_id": thread_id, "user_id": "user", "seed_text": "历史调研"},
    ))
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """UPDATE workflow_runs
               SET content_research_state=NULL, state_revision=NULL,
                   state_entered_at=NULL, lifecycle_schema_version=NULL
               WHERE run_id='run-historical-v1'"""
        )
        connection.commit()

    with pytest.raises(ValueError, match="historical workflow run"):
        await coordinator.load("run-historical-v1")
    historical = await coordinator.load_historical_read_only("run-historical-v1")

    assert historical["run_id"] == "run-historical-v1"
    assert historical["read_only"] is True
    assert historical["mutation_authority"] is None


@pytest.mark.asyncio
async def test_transient_sqlite_writer_contention_is_retried_inside_coordinator(tmp_path) -> None:
    db_path = str(tmp_path / "transient-contention.db")
    thread_id = await _create_thread(db_path)
    coordinator = ContentResearchPersistenceCoordinator(db_path)
    await coordinator._ensure_schema()
    blocker = sqlite3.connect(db_path, timeout=0)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        pending = asyncio.create_task(coordinator.apply(LifecycleCommand(
            command_id="submit-after-contention",
            run_id="run-after-contention",
            expected_state=None,
            expected_revision=0,
            kind="submit_research_subject",
            payload={"thread_id": thread_id, "user_id": "user", "seed_text": "凉感T恤"},
        )))
        await asyncio.sleep(0.1)
        blocker.commit()
        created = await pending
    finally:
        blocker.close()

    assert created.state is ContentResearchState.PRESEARCH_RUNNING
    assert created.state_revision == 1


@pytest.mark.asyncio
async def test_lifecycle_reads_retry_and_classify_sqlite_busy(tmp_path, monkeypatch) -> None:
    db_path = str(tmp_path / "read-contention.db")
    thread_id = await _create_thread(db_path)
    coordinator = ContentResearchPersistenceCoordinator(db_path)
    created = await coordinator.apply(LifecycleCommand(
        command_id="submit-before-read-contention",
        run_id="run-before-read-contention",
        expected_state=None,
        expected_revision=0,
        kind="submit_research_subject",
        payload={"thread_id": thread_id, "user_id": "user", "seed_text": "凉感T恤"},
    ))
    original_load_once = coordinator._load_once
    calls = 0

    async def transiently_locked(run_id: str):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise sqlite3.OperationalError("database is locked")
        return await original_load_once(run_id)

    monkeypatch.setattr(coordinator, "_load_once", transiently_locked)
    assert await coordinator.load(created.run_id) == created
    assert calls == 3

    async def permanently_locked(_run_id: str):
        raise sqlite3.OperationalError("database is busy")

    monkeypatch.setattr(coordinator, "_load_once", permanently_locked)
    with pytest.raises(LifecyclePersistenceBusy, match="after 3 attempts"):
        await coordinator.load(created.run_id)
