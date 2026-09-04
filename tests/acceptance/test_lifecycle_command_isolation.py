from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.content_research.analysis_persistence import (
    FrozenEvidenceNoteInput,
    SQLiteMarketingAnalysisRepository,
)
from app.content_research.lifecycle.coordinator import (
    ContentResearchPersistenceCoordinator,
    LifecycleCommandConflict,
)
from app.content_research.lifecycle.models import ContentResearchState, LifecycleCommand
from app.core.runtime_schema_bootstrap import bootstrap_canonical_runtime_schema
from app.core.runtime_write_coordinator import RuntimeWriteCoordinator
from app.core.sqlite_connection_roles import open_readonly_database
from app.memory.thread_store import ThreadStore
from app.runtime_write_handlers import production_runtime_write_handlers


def _submit_command(*, command_id: str, run_id: str, thread_id: str) -> LifecycleCommand:
    return LifecycleCommand(
        command_id=command_id,
        run_id=run_id,
        expected_state=None,
        expected_revision=0,
        kind="submit_research_subject",
        payload={
            "thread_id": thread_id,
            "user_id": "acceptance-user",
            "seed_text": f"subject for {run_id}",
        },
    )


def _prepare_running_analysis_attempt(
    repository: SQLiteMarketingAnalysisRepository,
    *,
    run_id: str,
):
    now = datetime.now(timezone.utc)
    snapshot = repository.freeze_evidence_snapshot(
        workflow_run_id=run_id,
        scope_contract_id=f"scope-{run_id}",
        retrieval_execution_unit_id=f"retrieval-{run_id}",
        retrieval_attempt_no=1,
        query_groups=({"id": "query", "query": "isolated lifecycle"},),
        notes=(
            FrozenEvidenceNoteInput(
                note_id=f"note-{run_id}",
                account_id=f"account-{run_id}",
                title="isolated analysis",
                body="evidence",
                source_url=f"https://example.test/{run_id}",
                captured_at=now,
                query_provenance=("query",),
            ),
        ),
    )
    unit = repository.get_or_create_analysis_unit(
        evidence_snapshot_id=snapshot.id,
        policy_version="policy-isolation",
        prompt_hash="prompt-isolation",
        response_schema_hash="schema-isolation",
        embedding_fingerprint={"model": "acceptance"},
        algorithm_version="algorithm-isolation",
        verifier_version="verifier-isolation",
    )
    repository.save_analysis_job_context(
        analysis_unit_id=unit.id,
        workflow_run_id=run_id,
        research_plan_id=f"plan-{run_id}",
        coverage_snapshot_id=f"coverage-{run_id}",
        execution_authorization_id=None,
        manifest={
            "workflow_run_id": run_id,
            "scope_contract_id": f"scope-{run_id}",
            "execution_unit_id": f"retrieval-{run_id}",
            "attempt_no": 1,
            "execution_revision": 1,
            "packet_ids": [],
            "checkpoint_ids": [],
        },
    )
    attempt = repository.create_analysis_attempt(unit.id)
    claimed = repository.claim_analysis_attempt(
        attempt.id,
        lease_owner="acceptance-worker",
        lease_token="acceptance-lease",
        lease_expires_at=now + timedelta(minutes=5),
        now=now,
    )
    return unit, claimed


@pytest.mark.acceptance
def test_two_runs_commands_attempts_and_cancel_are_isolated(tmp_path: Path) -> None:
    async def exercise() -> None:
        database = tmp_path / "two-run-lifecycle.sqlite"
        await bootstrap_canonical_runtime_schema(database, discovery_secret="acceptance-secret")
        writer = RuntimeWriteCoordinator(
            database,
            handlers=production_runtime_write_handlers(),
        )
        await writer.start()
        try:
            async with ThreadStore(str(database)) as threads:
                thread_a, thread_b = await asyncio.gather(
                    threads.create_thread(title="Run A"),
                    threads.create_thread(title="Run B"),
                )

            coordinator = ContentResearchPersistenceCoordinator(str(database))
            submit_a = _submit_command(
                command_id="submit-run-a",
                run_id="run-a",
                thread_id=str(thread_a["id"]),
            )
            submit_b = _submit_command(
                command_id="submit-run-b",
                run_id="run-b",
                thread_id=str(thread_b["id"]),
            )
            created_a, created_b = await asyncio.gather(
                coordinator.apply(submit_a),
                coordinator.apply(submit_b),
            )
            assert (created_a.run_id, created_a.state_revision) == ("run-a", 1)
            assert (created_b.run_id, created_b.state_revision) == ("run-b", 1)

            analysis = SQLiteMarketingAnalysisRepository(str(database))
            unit_a, attempt_a = _prepare_running_analysis_attempt(
                analysis,
                run_id="run-a",
            )
            fail_a_command = LifecycleCommand(
                command_id="fail-analysis-run-a",
                run_id="run-a",
                expected_state=ContentResearchState.PRESEARCH_RUNNING,
                expected_revision=1,
                kind="fail",
                payload={
                    "attempt_id": attempt_a.id,
                    "error": {
                        "code": "MARKETING_ANALYSIS_FAILED",
                        "stage": "marketing_analysis",
                        "retryable": True,
                        "recovery_action": "retry_analysis",
                        "attempt_id": attempt_a.id,
                        "attempt_no": attempt_a.attempt_no,
                    },
                },
            )
            complete_b_command = LifecycleCommand(
                command_id="complete-presearch-run-b",
                run_id="run-b",
                expected_state=ContentResearchState.PRESEARCH_RUNNING,
                expected_revision=1,
                kind="presearch_completed",
                payload={
                    "brief_id": "brief-run-b",
                    "schema_version": "content_research_brief_v1",
                    "status": "draft",
                    "subject": "Run B subject",
                    "competitors": [],
                    "directions": ["product_marketing"],
                    "attempt_id": "presearch-attempt-run-b",
                },
            )
            failed_a, completed_b = await asyncio.gather(
                coordinator.fail_analysis_attempt(
                    fail_a_command,
                    attempt_id=attempt_a.id,
                    lease_token="acceptance-lease",
                ),
                coordinator.apply(complete_b_command),
            )
            assert failed_a.state is ContentResearchState.RECOVERY_REQUIRED
            assert completed_b.state is ContentResearchState.BRIEF_CONFIRMATION_REQUIRED
            transitions_b_before = await coordinator.list_transitions("run-b")

            plan = failed_a.recovery_plan
            assert plan is not None
            retry_a = LifecycleCommand(
                command_id="retry-analysis-run-a",
                run_id="run-a",
                expected_state=ContentResearchState.RECOVERY_REQUIRED,
                expected_revision=failed_a.state_revision,
                kind="retry_analysis",
                payload={
                    "recovery_plan_id": plan.recovery_plan_id,
                    "plan_fingerprint": plan.plan_fingerprint,
                    "predecessor_attempt_id": attempt_a.id,
                },
            )
            retried_a, successor_id = await coordinator.retry_analysis(
                retry_a,
                expected_attempt_id=attempt_a.id,
                expected_contract_fingerprint=unit_a.contract_fingerprint,
            )
            replayed_a, replayed_successor_id = await coordinator.retry_analysis(
                retry_a,
                expected_attempt_id=attempt_a.id,
                expected_contract_fingerprint=unit_a.contract_fingerprint,
            )
            assert replayed_a == retried_a
            assert replayed_successor_id == successor_id
            with open_readonly_database(database) as connection:
                assert connection.execute(
                    "SELECT COUNT(*) FROM content_research_analysis_attempts "
                    "WHERE successor_of_attempt_id=?",
                    (attempt_a.id,),
                ).fetchone() == (1,)

            cancelled_a = await coordinator.apply(
                LifecycleCommand(
                    command_id="cancel-run-a",
                    run_id="run-a",
                    expected_state=ContentResearchState.REPORT_COMPOSING,
                    expected_revision=retried_a.state_revision,
                    kind="cancel",
                    payload={},
                )
            )
            assert cancelled_a.state is ContentResearchState.CANCELLED_OR_FAILED
            successor = analysis.get_analysis_attempt(successor_id)
            assert successor is not None and successor.state == "cancelled"

            transitions_a_before_stale = await coordinator.list_transitions("run-a")
            with pytest.raises(LifecycleCommandConflict, match="revision"):
                await coordinator.apply(
                    LifecycleCommand(
                        command_id="late-run-a-result",
                        run_id="run-a",
                        expected_state=ContentResearchState.CANCELLED_OR_FAILED,
                        expected_revision=retried_a.state_revision,
                        kind="report_published",
                        payload={"publication_id": "late-publication-run-a"},
                    )
                )

            assert await coordinator.load("run-a") == cancelled_a
            assert await coordinator.load("run-b") == completed_b
            assert await coordinator.list_transitions("run-a") == transitions_a_before_stale
            assert await coordinator.list_transitions("run-b") == transitions_b_before
            with open_readonly_database(database) as connection:
                assert connection.execute(
                    "SELECT COUNT(*) FROM content_research_report_publications "
                    "WHERE workflow_run_id IN ('run-a', 'run-b')"
                ).fetchone() == (0,)
        finally:
            await writer.close()

    asyncio.run(exercise())
