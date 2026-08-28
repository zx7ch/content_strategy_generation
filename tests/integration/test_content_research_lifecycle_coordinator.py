from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.content_research.analysis_persistence import (
    FrozenEvidenceNoteInput,
    SQLiteMarketingAnalysisRepository,
)
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
    assert completed.allowed_actions == ("confirm_brief", "revise_subject", "cancel")
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
async def test_scope_confirmation_keeps_one_query_identity_in_policy_and_scope(tmp_path) -> None:
    db_path = str(tmp_path / "scope-query-identity.db")
    thread_id = await _create_thread(db_path)
    coordinator = ContentResearchPersistenceCoordinator(db_path)
    run_id = "run-scope-query-identity"
    await coordinator.apply(
        LifecycleCommand(
            command_id="submit-scope-query-identity",
            run_id=run_id,
            expected_state=None,
            expected_revision=0,
            kind="submit_research_subject",
            payload={"thread_id": thread_id, "user_id": "user", "seed_text": "夏季凉感T恤"},
        )
    )
    await coordinator.apply(
        LifecycleCommand(
            command_id="presearch-scope-query-identity",
            run_id=run_id,
            expected_state=ContentResearchState.PRESEARCH_RUNNING,
            expected_revision=1,
            kind="presearch_completed",
            payload={
                "brief_id": "brief-scope-query-identity",
                "schema_version": "content_research_brief_v1",
                "status": "draft",
                "subject": "夏季凉感T恤",
                "directions": ["product_marketing"],
                "attempt_id": "attempt-scope-query-identity",
                "subject_structure_hash": "structure-scope-query-identity",
                "subject_structure": {
                    "core_entities": [{"canonical_name": "T恤"}],
                },
            },
        )
    )
    await coordinator.apply(
        LifecycleCommand(
            command_id="brief-scope-query-identity",
            run_id=run_id,
            expected_state=ContentResearchState.BRIEF_CONFIRMATION_REQUIRED,
            expected_revision=2,
            kind="confirm_brief",
            payload={
                "brief_id": "brief-scope-query-identity",
                "brief_confirmation": {"selected_directions": ["product_marketing"]},
                "plan": {
                    "id": "plan-scope-query-identity",
                    "schema_version": "content_research_plan_v2",
                    "payload": {"direction_ids": ["product_marketing"]},
                },
                "directions": [
                    {
                        "id": "direction-scope-query-identity",
                        "schema_version": "content_research_direction_v2",
                        "payload": {"direction_id": "product_marketing"},
                    }
                ],
                "scope_draft": {
                    "id": "draft-scope-query-identity",
                    "workflow_run_id": run_id,
                    "research_plan_id": "plan-scope-query-identity",
                    "structure_hash": "structure-scope-query-identity",
                    "schema_version": "content_research_scope_contract_v2",
                    "core_object": "T恤",
                    "product_experience_aspect": "凉感",
                    "context_audience_aspect": "夏季",
                    "constraints": [
                        {"id": "core_object", "label": "核心对象", "value": "T恤", "mode": "required"}
                    ],
                    "query_groups": [
                        {
                            "suggested_query": query,
                            "final_query": query,
                            "targeted_required_terms": ["T恤"],
                            "origin": "system_suggested",
                        }
                        for query in ("T恤", "T恤 凉感", "T恤 夏季")
                    ],
                    "audit_event_id": "audit-scope-query-identity",
                },
            },
        )
    )
    await coordinator.apply(
        LifecycleCommand(
            command_id="confirm-scope-query-identity",
            run_id=run_id,
            expected_state=ContentResearchState.SCOPE_CONFIRMATION_REQUIRED,
            expected_revision=3,
            kind="confirm_scope",
            payload={"scope_draft_id": "draft-scope-query-identity"},
        )
    )

    with sqlite3.connect(db_path) as connection:
        scope_groups = json.loads(
            connection.execute(
                "SELECT query_groups_json FROM content_research_scope_contracts "
                "WHERE workflow_run_id=?",
                (run_id,),
            ).fetchone()[0]
        )
        locked_groups = json.loads(
            connection.execute(
                "SELECT effective_policy_json FROM content_research_run_policy_snapshots "
                "WHERE workflow_run_id=?",
                (run_id,),
            ).fetchone()[0]
        )["locked_query_plan"]["directions"]["product_marketing"]["query_groups"]

    assert [group["id"] for group in locked_groups] == [
        group["id"] for group in scope_groups
    ]
    assert [group["normalized_query"] for group in locked_groups] == [
        group["final_query"] for group in scope_groups
    ]


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
async def test_retry_analysis_atomically_advances_run_and_creates_one_successor(
    tmp_path,
) -> None:
    db_path = str(tmp_path / "retry-analysis.db")
    thread_id = await _create_thread(db_path)
    coordinator = ContentResearchPersistenceCoordinator(db_path)
    run_id = "run-retry-analysis"
    await coordinator.apply(
        LifecycleCommand(
            command_id="submit-retry-analysis",
            run_id=run_id,
            expected_state=None,
            expected_revision=0,
            kind="submit_research_subject",
            payload={
                "thread_id": thread_id,
                "user_id": "user",
                "seed_text": "凉感T恤",
            },
        )
    )
    failed_run = await coordinator.apply(
        LifecycleCommand(
            command_id="fail-retry-analysis",
            run_id=run_id,
            expected_state=ContentResearchState.PRESEARCH_RUNNING,
            expected_revision=1,
            kind="fail",
            payload={
                "error": {
                    "code": "MARKETING_ANALYSIS_FAILED",
                    "recovery_action": "retry_analysis",
                }
            },
        )
    )
    repository = SQLiteMarketingAnalysisRepository(db_path)
    snapshot = repository.freeze_evidence_snapshot(
        workflow_run_id=run_id,
        scope_contract_id="scope-retry",
        retrieval_execution_unit_id="retrieval-retry",
        retrieval_attempt_no=1,
        query_groups=({"id": "query", "query": "凉感T恤"},),
        notes=(
            FrozenEvidenceNoteInput(
                note_id="note",
                account_id="account",
                title="凉感",
                body="通勤不闷",
                source_url="https://example.test/note",
                captured_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
                query_provenance=("query",),
            ),
        ),
    )
    unit = repository.get_or_create_analysis_unit(
        evidence_snapshot_id=snapshot.id,
        policy_version="policy",
        prompt_hash="prompt",
        response_schema_hash="schema",
        embedding_fingerprint={"model": "test"},
        algorithm_version="algorithm",
        verifier_version="verifier",
    )
    repository.save_analysis_job_context(
        analysis_unit_id=unit.id,
        workflow_run_id=run_id,
        research_plan_id="plan",
        coverage_snapshot_id="coverage",
        execution_authorization_id=None,
        manifest={
            "workflow_run_id": run_id,
            "scope_contract_id": "scope-retry",
            "execution_unit_id": "retrieval-retry",
            "attempt_no": 1,
            "execution_revision": 1,
            "packet_ids": [],
            "checkpoint_ids": [],
        },
    )
    predecessor = repository.create_analysis_attempt(unit.id)
    predecessor = repository.claim_analysis_attempt(
        predecessor.id,
        lease_owner="worker",
        lease_token="lease",
        lease_expires_at=datetime(2026, 8, 26, 10, tzinfo=timezone.utc),
        now=datetime(2026, 8, 26, 9, tzinfo=timezone.utc),
    )
    repository.fail_analysis_attempt(
        predecessor.id,
        lease_token="lease",
        now=datetime(2026, 8, 26, 9, 1, tzinfo=timezone.utc),
    )
    command = LifecycleCommand(
        command_id="retry-analysis-command",
        run_id=run_id,
        expected_state=ContentResearchState.RECOVERY_REQUIRED,
        expected_revision=failed_run.state_revision,
        kind="retry_analysis",
        payload={"predecessor_attempt_id": predecessor.id},
    )

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """CREATE TRIGGER fail_analysis_successor_insert
               BEFORE INSERT ON content_research_analysis_attempts
               WHEN NEW.successor_of_attempt_id IS NOT NULL
               BEGIN SELECT RAISE(ABORT, 'fault injected successor write'); END"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="fault injected successor write"):
        await coordinator.retry_analysis(
            command,
            expected_attempt_id=predecessor.id,
            expected_contract_fingerprint=unit.contract_fingerprint,
        )
    rolled_back = await coordinator.load(run_id)
    assert rolled_back.state is ContentResearchState.RECOVERY_REQUIRED
    assert rolled_back.state_revision == failed_run.state_revision
    latest_after_rollback = repository.get_latest_attempt_for_unit(unit.id)
    assert latest_after_rollback is not None
    assert latest_after_rollback.id == predecessor.id
    assert latest_after_rollback.state == "failed"
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM content_research_lifecycle_commands WHERE command_id=?",
            (command.command_id,),
        ).fetchone() == (0,)
        connection.execute("DROP TRIGGER fail_analysis_successor_insert")

    projection, successor_id = await coordinator.retry_analysis(
        command,
        expected_attempt_id=predecessor.id,
        expected_contract_fingerprint=unit.contract_fingerprint,
    )
    replayed, replayed_successor_id = await coordinator.retry_analysis(
        command,
        expected_attempt_id=predecessor.id,
        expected_contract_fingerprint=unit.contract_fingerprint,
    )

    assert projection.state is ContentResearchState.REPORT_COMPOSING
    assert replayed == projection
    assert replayed_successor_id == successor_id
    successor = repository.get_analysis_attempt(successor_id)
    assert successor is not None
    assert successor.state == "queued"
    assert successor.successor_of_attempt_id == predecessor.id
    cancelled = await coordinator.apply(
        LifecycleCommand(
            command_id="cancel-queued-analysis",
            run_id=run_id,
            expected_state=ContentResearchState.REPORT_COMPOSING,
            expected_revision=projection.state_revision,
            kind="cancel",
            payload={},
        )
    )
    assert cancelled.state is ContentResearchState.CANCELLED_OR_FAILED
    cancelled_successor = repository.get_analysis_attempt(successor_id)
    assert cancelled_successor is not None
    assert cancelled_successor.state == "cancelled"
    assert repository.claim_next_analysis_job(
        lease_owner="late-worker",
        lease_token="late-token",
        lease_expires_at=datetime(2026, 8, 26, 11, tzinfo=timezone.utc),
        now=datetime(2026, 8, 26, 10, tzinfo=timezone.utc),
    ) is None


@pytest.mark.asyncio
async def test_analysis_failure_atomically_closes_attempt_and_moves_run_to_recovery(
    tmp_path,
) -> None:
    db_path = str(tmp_path / "atomic-analysis-failure.db")
    thread_id = await _create_thread(db_path)
    coordinator = ContentResearchPersistenceCoordinator(db_path)
    run_id = "run-atomic-analysis-failure"
    await coordinator.apply(
        LifecycleCommand(
            command_id="submit-atomic-analysis-failure",
            run_id=run_id,
            expected_state=None,
            expected_revision=0,
            kind="submit_research_subject",
            payload={"thread_id": thread_id, "user_id": "user", "seed_text": "凉感T恤"},
        )
    )
    repository = SQLiteMarketingAnalysisRepository(db_path)
    snapshot = repository.freeze_evidence_snapshot(
        workflow_run_id=run_id,
        scope_contract_id="scope-atomic-failure",
        retrieval_execution_unit_id="retrieval-atomic-failure",
        retrieval_attempt_no=1,
        query_groups=({"id": "query", "query": "凉感T恤"},),
        notes=(),
    )
    unit = repository.get_or_create_analysis_unit(
        evidence_snapshot_id=snapshot.id,
        policy_version="policy",
        prompt_hash="prompt",
        response_schema_hash="schema",
        embedding_fingerprint={"model": "test"},
        algorithm_version="algorithm",
        verifier_version="verifier",
    )
    attempt = repository.create_analysis_attempt(unit.id)
    attempt = repository.claim_analysis_attempt(
        attempt.id,
        lease_owner="worker",
        lease_token="lease",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE workflow_runs SET content_research_state='report_composing', "
            "state_revision=2 WHERE run_id=?",
            (run_id,),
        )
        connection.execute(
            """CREATE TRIGGER fail_atomic_attempt_close
               BEFORE UPDATE OF state ON content_research_analysis_attempts
               WHEN NEW.state='failed'
               BEGIN SELECT RAISE(ABORT, 'fault injected attempt close'); END"""
        )
    command = LifecycleCommand(
        command_id="atomic-analysis-failure-command",
        run_id=run_id,
        expected_state=ContentResearchState.REPORT_COMPOSING,
        expected_revision=2,
        kind="fail",
        payload={
            "attempt_id": attempt.id,
            "error": {
                "code": "MARKETING_ANALYSIS_FAILED",
                "message": "模型返回无法解析",
                "retryable": True,
                "recovery_action": "retry_analysis",
            },
        },
    )

    with pytest.raises(sqlite3.IntegrityError, match="fault injected attempt close"):
        await coordinator.fail_analysis_attempt(
            command,
            attempt_id=attempt.id,
            lease_token="lease",
        )
    assert (await coordinator.load(run_id)).state is ContentResearchState.REPORT_COMPOSING
    assert repository.get_analysis_attempt(attempt.id).state == "running"

    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP TRIGGER fail_atomic_attempt_close")
    recovered = await coordinator.fail_analysis_attempt(
        command,
        attempt_id=attempt.id,
        lease_token="lease",
    )

    assert recovered.state is ContentResearchState.RECOVERY_REQUIRED
    assert repository.get_analysis_attempt(attempt.id).state == "failed"
    assert repository.get_effective_attempt_for_run(run_id).id == attempt.id


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
