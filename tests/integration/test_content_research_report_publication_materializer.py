import asyncio
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from app.content_research.admission.candidates import source_text_hash
from app.content_research.api_schemas import ContentResearchWorkflowActionRequest
from app.content_research.bootstrap import _bootstrap_legacy_content_research_schema
from app.content_research.contracts import build_default_snapshot
from app.content_research.evidence.models import EvidenceRecord
from app.content_research.lifecycle.coordinator import ContentResearchPersistenceCoordinator
from app.content_research.lifecycle.models import ContentResearchState, LifecycleCommand
from app.content_research.migrations import apply_content_research_migrations
from app.content_research.models import TraceRecord, utcnow
from app.content_research.persistence_models import (
    CanonicalSourceRecord,
    ClaimAdmissionDecisionRecord,
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
    ReportDraftRecord,
    ReportFaithfulnessDecisionRecord,
    ReportIntegrityEventRecord,
    ReportPublicationRecord,
    StageCheckpointRecord,
)
from app.content_research.reporting.composer import ResearchReportComposer
from app.content_research.reporting.publication_materializer import ReportPublicationMaterializer
from app.content_research.reporting.read_model import PublishedReportReader
from app.content_research.scope_contract import (
    DispatchLeaseContext,
    ExecutionLeaseFencedError,
)
from app.content_research.service import ContentResearchService, WorkflowRunManagerRuntime
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.core.runtime_write_coordinator import (
    PersistenceUnavailableError,
    RuntimeWriteCoordinator,
)
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import (
    WorkflowArtifact,
    WorkflowArtifactPayloadMode,
    WorkflowArtifactType,
)
from app.runtime_write_handlers import production_runtime_write_handlers
from app.services.workflow_run_manager import WorkflowRunManager
from tests.content_research_test_constants import LEGACY_EVIDENCE_BUNDLE_FRAGMENT
from tests.integration.test_content_research_report_store import (
    _decision,
    _draft,
    _publication,
    _snapshot,
)
from tests.unit.test_content_research_report_composer import _snapshot as _composer_snapshot


def _legacy_bundle_tables(db_path: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'content_research_%'"
            )
            if LEGACY_EVIDENCE_BUNDLE_FRAGMENT in row[0]
        }


def _save_live_dispatch_claim(db_path: str, workflow_run_id: str) -> DispatchLeaseContext:
    context = DispatchLeaseContext(workflow_run_id, "worker-a", "token-a")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO content_research_dispatch_jobs
               (workflow_run_id, provider, source_kind, limit_per_specialist, status,
                attempt_count, lease_expires_at, lease_owner, lease_token, created_at, updated_at)
               VALUES (?, 'xiaohongshu', 'search_result', 20, 'running', 1,
                       '2099-01-01T00:00:00+00:00', 'worker-a', 'token-a',
                       '2040-01-01T00:00:00+00:00', '2040-01-01T00:00:00+00:00')""",
            (workflow_run_id,),
        )
    return context


def _take_over_dispatch(db_path: str, workflow_run_id: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """UPDATE content_research_dispatch_jobs
               SET lease_owner='worker-b', lease_token='token-b'
               WHERE workflow_run_id=?""",
            (workflow_run_id,),
        )


@pytest.mark.asyncio
async def test_integrity_repair_creates_one_successor_from_still_valid_outputs(
    tmp_path: Path,
) -> None:
    db_path = str(tmp_path / "integrity-repair.db")
    async with ThreadStore(db_path) as threads:
        thread = await threads.create_thread(title="integrity repair")
    coordinator = ContentResearchPersistenceCoordinator(db_path)
    run = await coordinator.apply(
        LifecycleCommand(
            command_id="submit-integrity-repair",
            run_id="run-integrity-repair",
            expected_state=None,
            expected_revision=0,
            kind="submit_research_subject",
            payload={
                "thread_id": thread["id"],
                "user_id": "user-integrity",
                "seed_text": "凉感衬衫",
            },
        )
    )
    store = SQLiteContentResearchStore(db_path)
    snapshot = replace(_snapshot(), workflow_run_id=run.run_id)
    draft = replace(_draft(), workflow_run_id=run.run_id)
    decision = replace(_decision(draft), workflow_run_id=run.run_id)
    publication = replace(_publication(draft, decision), workflow_run_id=run.run_id)
    store.save_result_snapshot(snapshot)
    store.save_report_draft(draft.to_record())
    store.save_report_faithfulness_decision(decision.to_record())
    store.save_report_publication(publication.to_record())
    async with WorkflowRunManager(db_path) as manager:
        await manager.begin_report_finalization(run.run_id)
    await ReportPublicationMaterializer(store, db_path).materialize(publication.id)
    async with WorkflowRunManager(db_path) as manager:
        await manager.complete_report_finalization(run.run_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE workflow_runs SET content_research_state='report_ready', "
            "state_revision=2, state_entered_at=CURRENT_TIMESTAMP WHERE run_id=?",
            (run.run_id,),
        )
    store.append_report_integrity_event(
        ReportIntegrityEventRecord(
            id="rie-artifact-invalid",
            publication_id=publication.id,
            workflow_run_id=run.run_id,
            event_type="integrity_flagged",
            reason_code="materialized_artifact_invalid",
            recovery_guidance="publish_successor_report",
        )
    )
    service = ContentResearchService(
        store=store,
        presearch=None,
        workflow_runtime=WorkflowRunManagerRuntime(db_path),
    )
    request = ContentResearchWorkflowActionRequest(
        command_id="repair-publication-command",
        expected_state="report_ready",
        expected_revision=2,
        action="repair_publication",
        payload={"publication_id": publication.id},
    )

    repaired = await service.run_workflow_action(
        workflow_run_id=run.run_id, request=request
    )
    replayed = await service.run_workflow_action(
        workflow_run_id=run.run_id, request=request
    )

    assert replayed.result == repaired.result
    successor_id = repaired.result["publication_id"]
    successor = store.get_typed_record(ReportPublicationRecord, successor_id)
    assert successor is not None and successor.previous_version_id == publication.id
    assert len(
        [
            item
            for item in store.list_typed_records(ReportPublicationRecord)
            if item.previous_version_id == publication.id
        ]
    ) == 1
    current = await PublishedReportReader(store, db_path).read(
        workflow_run_id=run.run_id
    )
    assert current["publication"]["report_publication_id"] == successor_id
    store.append_report_integrity_event(
        ReportIntegrityEventRecord(
            id="rie-successor-output-invalid",
            publication_id=successor_id,
            workflow_run_id=run.run_id,
            event_type="integrity_flagged",
            reason_code="frozen_execution_attempt_failed",
            recovery_guidance="publish_successor_report",
        )
    )
    with pytest.raises(Exception, match="verified outputs are no longer valid"):
        await service.run_workflow_action(
            workflow_run_id=run.run_id,
            request=ContentResearchWorkflowActionRequest(
                command_id="repair-invalid-outputs",
                expected_state="report_ready",
                expected_revision=2,
                action="repair_publication",
                payload={"publication_id": successor_id},
            ),
        )


@pytest.mark.asyncio
async def test_integrity_flag_committed_during_materialization_prevents_artifact_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = str(tmp_path / "integrity-flag-materialization-race.db")
    store = SQLiteContentResearchStore(db_path)
    async with ThreadStore(db_path) as threads:
        thread = await threads.create_thread(title="integrity race")
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id=thread["id"], user_id="user-1")
    snapshot = replace(_snapshot(), workflow_run_id=run.run_id)
    draft = replace(_draft(), workflow_run_id=run.run_id)
    decision = replace(_decision(draft), workflow_run_id=run.run_id)
    publication = replace(_publication(draft, decision), workflow_run_id=run.run_id)
    store.save_result_snapshot(snapshot)
    store.save_report_draft(draft.to_record())
    store.save_report_faithfulness_decision(decision.to_record())
    store.save_report_publication(publication.to_record())
    async with WorkflowRunManager(db_path) as manager:
        await manager.begin_report_finalization(run.run_id)

    read_completed = asyncio.Event()
    release_read = asyncio.Event()
    original_list_artifacts = WorkflowStore.list_artifacts

    async def pause_after_artifact_read(
        workflow_store: WorkflowStore, workflow_run_id: str
    ) -> list[WorkflowArtifact]:
        artifacts = await original_list_artifacts(workflow_store, workflow_run_id)
        read_completed.set()
        await release_read.wait()
        return artifacts

    monkeypatch.setattr(WorkflowStore, "list_artifacts", pause_after_artifact_read)
    materialization = asyncio.create_task(
        ReportPublicationMaterializer(store, db_path).materialize(publication.id)
    )
    await asyncio.wait_for(read_completed.wait(), timeout=2)
    store.append_report_integrity_event(
        ReportIntegrityEventRecord(
            id="rie_materialization_race",
            publication_id=publication.id,
            workflow_run_id=run.run_id,
            event_type="integrity_flagged",
            reason_code="frozen_execution_attempt_failed",
            recovery_guidance="publish_successor_report",
        )
    )
    release_read.set()

    with pytest.raises(ValueError, match="integrity-flagged"):
        await materialization
    async with WorkflowStore(db_path) as workflow_store:
        assert await workflow_store.list_artifacts(run.run_id) == []


@pytest.mark.asyncio
async def test_stale_normal_dispatch_cannot_materialize_report_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = str(tmp_path / "stale-dispatch-artifact.db")
    store = SQLiteContentResearchStore(db_path)
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    thread = await thread_store.create_thread(title="stale report artifact")
    try:
        async with WorkflowRunManager(db_path) as manager:
            run = await manager.start_run(thread_id=thread["id"], user_id="user-1")
        snapshot = replace(_snapshot(), workflow_run_id=run.run_id)
        draft = replace(_draft(), workflow_run_id=run.run_id)
        decision = replace(_decision(draft), workflow_run_id=run.run_id)
        publication = replace(_publication(draft, decision), workflow_run_id=run.run_id)
        store.save_result_snapshot(snapshot)
        store.save_report_draft(draft.to_record())
        store.save_report_faithfulness_decision(decision.to_record())
        store.save_report_publication(publication.to_record())
        async with WorkflowRunManager(db_path) as manager:
            await manager.begin_report_finalization(run.run_id)
        context = _save_live_dispatch_claim(db_path, run.run_id)
        materializer = ReportPublicationMaterializer(
            store, db_path, dispatch_context=context
        )
        read_completed = asyncio.Event()
        release_read = asyncio.Event()
        original_list_artifacts = WorkflowStore.list_artifacts

        async def pause_after_artifact_read(
            workflow_store: WorkflowStore, workflow_run_id: str
        ) -> list[WorkflowArtifact]:
            artifacts = await original_list_artifacts(workflow_store, workflow_run_id)
            read_completed.set()
            await release_read.wait()
            return artifacts

        monkeypatch.setattr(WorkflowStore, "list_artifacts", pause_after_artifact_read)
        stale_materialization = asyncio.create_task(materializer.materialize(publication.id))
        await asyncio.wait_for(read_completed.wait(), timeout=2)
        _take_over_dispatch(db_path, run.run_id)
        release_read.set()

        with pytest.raises(ExecutionLeaseFencedError, match="dispatch lease"):
            await stale_materialization

        async with WorkflowStore(db_path) as workflow_store:
            assert await workflow_store.list_artifacts(run.run_id) == []
        assert await thread_store.get_thread_messages(thread["id"]) == []
    finally:
        await thread_store.close()


@pytest.mark.asyncio
async def test_stale_normal_dispatch_cannot_publish_report_timeline_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = str(tmp_path / "stale-dispatch-timeline.db")
    store = SQLiteContentResearchStore(db_path)
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    thread = await thread_store.create_thread(title="stale report timeline")
    try:
        async with WorkflowRunManager(db_path) as manager:
            run = await manager.start_run(thread_id=thread["id"], user_id="user-1")
        snapshot = replace(_snapshot(), workflow_run_id=run.run_id)
        draft = replace(_draft(), workflow_run_id=run.run_id)
        decision = replace(_decision(draft), workflow_run_id=run.run_id)
        publication = replace(_publication(draft, decision), workflow_run_id=run.run_id)
        store.save_result_snapshot(snapshot)
        store.save_report_draft(draft.to_record())
        store.save_report_faithfulness_decision(decision.to_record())
        store.save_report_publication(publication.to_record())
        async with WorkflowRunManager(db_path) as manager:
            await manager.begin_report_finalization(run.run_id)
        context = _save_live_dispatch_claim(db_path, run.run_id)
        materializer = ReportPublicationMaterializer(
            store, db_path, dispatch_context=context
        )
        await materializer.materialize(publication.id)
        async with WorkflowRunManager(db_path) as manager:
            await manager.complete_report_finalization(run.run_id)
        read_completed = asyncio.Event()
        release_read = asyncio.Event()
        original_list_artifacts = WorkflowStore.list_artifacts

        async def pause_after_artifact_read(
            workflow_store: WorkflowStore, workflow_run_id: str
        ) -> list[WorkflowArtifact]:
            artifacts = await original_list_artifacts(workflow_store, workflow_run_id)
            read_completed.set()
            await release_read.wait()
            return artifacts

        monkeypatch.setattr(WorkflowStore, "list_artifacts", pause_after_artifact_read)
        stale_publication = asyncio.create_task(
            materializer.publish_timeline_message(publication.id)
        )
        await asyncio.wait_for(read_completed.wait(), timeout=2)
        _take_over_dispatch(db_path, run.run_id)
        release_read.set()

        with pytest.raises(ExecutionLeaseFencedError, match="dispatch lease"):
            await stale_publication

        assert await thread_store.get_thread_messages(thread["id"]) == []
    finally:
        await thread_store.close()


@pytest.mark.asyncio
async def test_materializes_published_report_as_one_creator_snapshot_and_timeline_result(tmp_path):
    db_path = str(tmp_path / "published-report.db")
    store = SQLiteContentResearchStore(db_path)
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    thread = await thread_store.create_thread(title="调研报告")
    try:
        async with WorkflowRunManager(db_path) as manager:
            run = await manager.start_run(thread_id=thread["id"], user_id="user-1")
        snapshot = replace(_snapshot(), workflow_run_id=run.run_id)
        draft = replace(_draft(), workflow_run_id=run.run_id)
        decision = replace(_decision(draft), workflow_run_id=run.run_id)
        publication = replace(_publication(draft, decision), workflow_run_id=run.run_id)
        store.save_result_snapshot(snapshot)
        store.save_report_draft(draft.to_record())
        store.save_report_faithfulness_decision(decision.to_record())
        store.save_report_publication(publication.to_record())
        async with WorkflowRunManager(db_path) as manager:
            await manager.begin_report_finalization(run.run_id)

        materializer = ReportPublicationMaterializer(store, db_path)
        artifact = await materializer.materialize(publication.id)
        replay = await materializer.materialize(publication.id)

        assert replay.artifact_id == artifact.artifact_id
        assert artifact.artifact_type == WorkflowArtifactType.FINAL_RESULT
        assert artifact.payload_mode == WorkflowArtifactPayloadMode.SNAPSHOT
        assert artifact.parent_artifact_id is None
        assert artifact.payload_json["report_publication_id"] == publication.id
        assert artifact.payload_json["report_draft_id"] == draft.id
        assert artifact.payload_json["faithfulness_decision_id"] == decision.id
        assert artifact.payload_json["governed_snapshot_id"] == snapshot.id
        assert "items" not in artifact.payload_json

        assert await thread_store.get_thread_messages(thread["id"]) == []
        async with WorkflowRunManager(db_path) as manager:
            await manager.complete_report_finalization(run.run_id)
        await materializer.publish_timeline_message(publication.id)
        await materializer.publish_timeline_message(publication.id)

        messages = await thread_store.get_thread_messages(thread["id"])
        result_messages = [item for item in messages if item["message_type"] == "artifact_result"]
        assert len(result_messages) == 1
        assert result_messages[0]["run_id"] == run.run_id

        async with WorkflowStore(db_path) as workflow_store:
            artifacts = await workflow_store.list_artifacts(run.run_id)
        assert [item.artifact_id for item in artifacts].count(artifact.artifact_id) == 1
    finally:
        await thread_store.close()


@pytest.mark.asyncio
async def test_materializes_report_artifact_through_single_writer_typed_mutation(tmp_path):
    database = tmp_path / "published-report-single-writer.db"
    db_path = str(database)
    bootstrap_store = SQLiteContentResearchStore(db_path)
    async with ThreadStore(db_path) as threads:
        thread = await threads.create_thread(title="single writer report")
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id=thread["id"], user_id="user-1")
    snapshot = replace(_snapshot(), workflow_run_id=run.run_id)
    draft = replace(_draft(), workflow_run_id=run.run_id)
    decision = replace(_decision(draft), workflow_run_id=run.run_id)
    publication = replace(_publication(draft, decision), workflow_run_id=run.run_id)
    bootstrap_store.save_result_snapshot(snapshot)
    bootstrap_store.save_report_draft(draft.to_record())
    bootstrap_store.save_report_faithfulness_decision(decision.to_record())
    bootstrap_store.save_report_publication(publication.to_record())
    async with WorkflowRunManager(db_path) as manager:
        await manager.begin_report_finalization(run.run_id)

    writer = RuntimeWriteCoordinator(
        database,
        handlers=production_runtime_write_handlers(),
    )
    await writer.start()
    try:
        store = SQLiteContentResearchStore(db_path, writer=writer)
        materializer = ReportPublicationMaterializer(store, db_path)

        artifact = await materializer.materialize(publication.id)
        replay = await materializer.materialize(publication.id)

        assert replay.artifact_id == artifact.artifact_id
        assert artifact.payload_json["report_publication_id"] == publication.id
        async with WorkflowStore(db_path) as workflow_store:
            artifacts = await workflow_store.list_artifacts(run.run_id)
        assert [item.artifact_id for item in artifacts] == [artifact.artifact_id]
    finally:
        await writer.close()


async def _seed_atomic_publication_commit(db_path: str):
    store = SQLiteContentResearchStore(db_path)
    async with ThreadStore(db_path) as threads:
        thread = await threads.create_thread(title="atomic report publication")
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id=thread["id"], user_id="user-1")
    snapshot = replace(_snapshot(), workflow_run_id=run.run_id)
    draft = replace(_draft(), workflow_run_id=run.run_id)
    decision = replace(_decision(draft), workflow_run_id=run.run_id)
    publication = replace(_publication(draft, decision), workflow_run_id=run.run_id)
    store.save_result_snapshot(snapshot)
    store.save_report_draft(draft.to_record())
    store.save_report_faithfulness_decision(decision.to_record())
    store.save_report_publication(publication.to_record())
    async with WorkflowRunManager(db_path) as manager:
        await manager.begin_report_finalization(run.run_id)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """INSERT INTO content_research_evidence_snapshots
               (id, schema_version, workflow_run_id, scope_contract_id,
                retrieval_execution_unit_id, retrieval_attempt_no,
                snapshot_fingerprint, query_groups_json, created_at)
               VALUES ('snapshot-atomic', 'evidence-snapshot', ?, 'scope-atomic',
                       'retrieval-atomic', 1, 'snapshot-fingerprint', '[]',
                       '2040-01-01T00:00:00+00:00')""",
            (run.run_id,),
        )
        connection.execute(
            """INSERT INTO content_research_analysis_units
               (id, schema_version, workflow_run_id, evidence_snapshot_id,
                contract_fingerprint, policy_version, prompt_hash,
                response_schema_hash, embedding_fingerprint_json,
                algorithm_version, verifier_version, created_at)
               VALUES ('analysis-unit-atomic', 'analysis-unit', ?,
                       'snapshot-atomic', 'contract-fingerprint', 'policy',
                       'prompt', 'response', '{}', 'algorithm', 'verifier',
                       '2040-01-01T00:00:00+00:00')""",
            (run.run_id,),
        )
        connection.execute(
            """INSERT INTO content_research_analysis_attempts
               (id, analysis_unit_id, attempt_no, state, created_at, terminal_at)
               VALUES ('analysis-attempt-atomic', 'analysis-unit-atomic', 1,
                       'succeeded', '2040-01-01T00:00:00+00:00',
                       '2040-01-01T00:00:01+00:00')"""
        )
        connection.execute(
            """UPDATE workflow_runs
               SET effective_analysis_attempt_id='analysis-attempt-atomic',
                   content_research_state='report_composing', state_revision=7,
                   state_entered_at='2040-01-01T00:00:00+00:00'
               WHERE run_id=?""",
            (run.run_id,),
        )
    return store, thread, run, publication


@pytest.mark.asyncio
async def test_commits_effective_publication_artifact_state_and_timeline_atomically(
    tmp_path: Path,
) -> None:
    database = tmp_path / "atomic-report-publication.db"
    db_path = str(database)
    _store, thread, run, publication = await _seed_atomic_publication_commit(db_path)
    writer = RuntimeWriteCoordinator(database, handlers=production_runtime_write_handlers())
    await writer.start()
    try:
        store = SQLiteContentResearchStore(db_path, writer=writer)
        artifact = await ReportPublicationMaterializer(
            store, db_path
        ).commit_publication(publication.id)
    finally:
        await writer.close()

    assert artifact.payload_json["report_publication_id"] == publication.id
    with sqlite3.connect(db_path) as connection:
        status, state, revision, effective_attempt = connection.execute(
            """SELECT status, content_research_state, state_revision,
                      effective_analysis_attempt_id
               FROM workflow_runs WHERE run_id=?""",
            (run.run_id,),
        ).fetchone()
        messages = connection.execute(
            """SELECT run_id, artifact_refs_json FROM creator_messages
               WHERE thread_id=? AND message_type='artifact_result'""",
            (thread["id"],),
        ).fetchall()
    assert (status, state, revision, effective_attempt) == (
        "succeeded",
        ContentResearchState.REPORT_READY.value,
        8,
        "analysis-attempt-atomic",
    )
    assert len(messages) == 1
    assert messages[0][0] == run.run_id
    assert json.loads(messages[0][1])[0]["artifact_id"] == artifact.artifact_id


@pytest.mark.asyncio
async def test_publication_commit_rolls_back_every_projection_when_timeline_write_fails(
    tmp_path: Path,
) -> None:
    database = tmp_path / "atomic-report-publication-rollback.db"
    db_path = str(database)
    _store, thread, run, publication = await _seed_atomic_publication_commit(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """CREATE TRIGGER reject_atomic_publication_timeline
               BEFORE INSERT ON creator_messages
               WHEN NEW.run_id IS NOT NULL
               BEGIN
                   SELECT RAISE(ABORT, 'timeline write rejected');
               END"""
        )
    writer = RuntimeWriteCoordinator(database, handlers=production_runtime_write_handlers())
    await writer.start()
    try:
        store = SQLiteContentResearchStore(db_path, writer=writer)
        with pytest.raises(PersistenceUnavailableError, match="PERSISTENCE_UNAVAILABLE"):
            await ReportPublicationMaterializer(
                store, db_path
            ).commit_publication(publication.id)
    finally:
        await writer.close()

    with sqlite3.connect(db_path) as connection:
        status, state, revision = connection.execute(
            "SELECT status, content_research_state, state_revision "
            "FROM workflow_runs WHERE run_id=?",
            (run.run_id,),
        ).fetchone()
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_artifacts WHERE run_id=?",
            (run.run_id,),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM creator_messages WHERE thread_id=? AND run_id=?",
            (thread["id"], run.run_id),
        ).fetchone() == (0,)
    assert (status, state, revision) == ("finalizing_report", "report_composing", 7)


@pytest.mark.asyncio
async def test_pre_0013_gate2_evidence_survives_bundle_and_report_artifact_purges(tmp_path):
    db_path = str(tmp_path / "pre-0013-artifact.db")
    store = SQLiteContentResearchStore(db_path)
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    thread = await thread_store.create_thread(title="Gate 2 artifact")
    try:
        # Recreate the schema delta owned by 0013 and leave both later
        # migrations pending before persisting the legacy report artifact.
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "DELETE FROM content_research_schema_migrations "
                "WHERE version IN ('0013', '0014')"
            )
            conn.execute(
                "CREATE TABLE content_research_evidence_bundles (id TEXT PRIMARY KEY)"
            )
            conn.execute(
                "CREATE TABLE content_research_evidence_bundle_items (id TEXT PRIMARY KEY)"
            )
            conn.execute(
                "ALTER TABLE content_research_result_snapshots "
                "ADD COLUMN evidence_bundle_ids_json TEXT"
            )
            conn.execute(
                "INSERT INTO content_research_evidence_bundles VALUES ('bundle_legacy')"
            )
        with sqlite3.connect(db_path) as conn:
            assert conn.execute(
                "SELECT 1 FROM content_research_schema_migrations WHERE version = '0013'"
            ).fetchone() is None
        assert _legacy_bundle_tables(db_path) == {
            "content_research_evidence_bundles",
            "content_research_evidence_bundle_items",
        }

        async with WorkflowRunManager(db_path) as manager:
            run = await manager.start_run(thread_id=thread["id"], user_id="user-1")
        now = utcnow()
        store.save_trace(
            TraceRecord(
                id="trace_legacy",
                workflow_run_id=run.run_id,
                thread_id=thread["id"],
                schema_version="content_research_trace_v1",
                status="completed",
                started_at=now,
                payload={"schema_version": "content_research_trace_payload_v1"},
            )
        )
        store.save_evidence_record(
            EvidenceRecord(
                id="evidence_legacy",
                workflow_run_id=run.run_id,
                research_plan_id="plan_legacy",
                research_direction_id="product_marketing",
                trace_id="trace_legacy",
                schema_version="content_research_evidence_record_v1",
                status="accepted",
                source_type="note",
                source_platform="xhs",
                source_url="https://example.test/legacy-note",
                source_id="legacy-note",
                evidence_type="note",
                normalized_payload={
                    "schema_version": "content_research_source_payload_v1",
                    "source_id": "legacy-note",
                },
                title="Legacy source",
                text_excerpt="legacy evidence",
                raw_content_ref="legacy-hash",
                content_hash="legacy-hash",
                dedupe_key="legacy-note",
                retrieval_query="legacy",
            )
        )
        store.save_canonical_source(
            CanonicalSourceRecord(
                id="source_legacy",
                schema_version="content_research_canonical_source_v1",
                payload={"source_id": "legacy-note"},
                platform="xhs",
                platform_source_kind="note",
                platform_source_id="legacy-note",
                canonical_url="https://example.test/legacy-note",
            )
        )
        store.save_stage_checkpoint(
            StageCheckpointRecord(
                id="checkpoint_legacy",
                schema_version="content_research_stage_checkpoint_v1",
                payload={"schema_version": "content_research_checkpoint_payload_v1"},
                workflow_run_id=run.run_id,
                subagent_task_id="task_legacy",
                stage_name="collect",
                input_fingerprint="legacy-fingerprint",
                status="completed",
            )
        )
        source_snapshot = _composer_snapshot()
        governed = source_snapshot.metadata["governed_snapshot"]
        snapshot = replace(
            source_snapshot,
            workflow_run_id=run.run_id,
            metadata={
                **source_snapshot.metadata,
                "governed_snapshot": {
                    **governed,
                    "policy_scope": {
                        **governed["policy_scope"],
                        "direction_set_version": "direction_catalog_v1",
                        "direction_ids": ["product_marketing"],
                    },
                    "direction_results": [
                        {
                            "direction_id": "product_marketing",
                            "state": "completed",
                            "limitations": [],
                            "recovery_actions": [],
                        }
                    ],
                },
            },
        )
        draft = ResearchReportComposer().compose(snapshot)
        decision = replace(_decision(draft), workflow_run_id=run.run_id)
        publication = replace(_publication(draft, decision), workflow_run_id=run.run_id)
        store.save_result_snapshot(snapshot)
        store.save_report_draft(draft.to_record())
        store.save_report_faithfulness_decision(decision.to_record())
        store.save_report_publication(publication.to_record())
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE content_research_result_snapshots "
                "SET evidence_bundle_ids_json = ? WHERE id = ?",
                ('["bundle_legacy"]', snapshot.id),
            )
        async with WorkflowRunManager(db_path) as manager:
            await manager.begin_report_finalization(run.run_id)

        await ReportPublicationMaterializer(store, db_path).materialize(publication.id)
        apply_content_research_migrations(
            db_path, _bootstrap_legacy_content_research_schema
        )
        migrated_store = SQLiteContentResearchStore(db_path)

        assert migrated_store.list_typed_records(ReportPublicationRecord) == []
        async with WorkflowStore(db_path) as workflow_store:
            assert await workflow_store.list_artifacts(run.run_id) == []
        assert await thread_store.get_thread_messages(thread["id"]) == []
        assert (
            migrated_store.get_canonical_source("source_legacy").canonical_url
            == "https://example.test/legacy-note"
        )
        assert migrated_store.get_evidence_record("evidence_legacy").trace_id == "trace_legacy"
        assert migrated_store.get_trace("trace_legacy").workflow_run_id == run.run_id
        assert [
            item.id for item in migrated_store.list_typed_records(StageCheckpointRecord)
        ] == ["checkpoint_legacy"]
        assert _legacy_bundle_tables(db_path) == set()
        with sqlite3.connect(db_path) as conn:
            columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(content_research_result_snapshots)"
                )
            }
        assert "evidence_bundle_ids_json" not in columns
    finally:
        await thread_store.close()


@pytest.mark.asyncio
async def test_materialization_rejects_publication_with_mismatched_lineage_before_artifact_or_message(tmp_path):
    db_path = str(tmp_path / "mismatched-report.db")
    store = SQLiteContentResearchStore(db_path)
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    thread = await thread_store.create_thread(title="调研报告")
    try:
        async with WorkflowRunManager(db_path) as manager:
            run = await manager.start_run(thread_id=thread["id"], user_id="user-1")
        snapshot = replace(_snapshot(), workflow_run_id=run.run_id)
        draft = replace(_draft(), workflow_run_id=run.run_id)
        decision = replace(_decision(draft), workflow_run_id=run.run_id)
        publication = replace(_publication(draft, decision), workflow_run_id=run.run_id, policy_version="wrong_policy")
        store.save_result_snapshot(snapshot)
        store.save_report_draft(draft.to_record())
        store.save_report_faithfulness_decision(decision.to_record())
        store.save_report_publication(publication.to_record())
        async with WorkflowRunManager(db_path) as manager:
            await manager.begin_report_finalization(run.run_id)

        with pytest.raises(ValueError, match="lineage mismatch"):
            await ReportPublicationMaterializer(store, db_path).materialize(publication.id)

        assert await thread_store.get_thread_messages(thread["id"]) == []
        async with WorkflowStore(db_path) as workflow_store:
            assert await workflow_store.list_artifacts(run.run_id) == []
    finally:
        await thread_store.close()


@pytest.mark.asyncio
async def test_materialization_requires_report_finalization_before_artifact_or_message(tmp_path):
    db_path = str(tmp_path / "report-before-finalization.db")
    store = SQLiteContentResearchStore(db_path)
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    thread = await thread_store.create_thread(title="调研报告")
    try:
        async with WorkflowRunManager(db_path) as manager:
            run = await manager.start_run(thread_id=thread["id"], user_id="user-1")
        snapshot = replace(_snapshot(), workflow_run_id=run.run_id)
        draft = replace(_draft(), workflow_run_id=run.run_id)
        decision = replace(_decision(draft), workflow_run_id=run.run_id)
        publication = replace(_publication(draft, decision), workflow_run_id=run.run_id)
        store.save_result_snapshot(snapshot)
        store.save_report_draft(draft.to_record())
        store.save_report_faithfulness_decision(decision.to_record())
        store.save_report_publication(publication.to_record())

        with pytest.raises(ValueError, match="finalizing_report"):
            await ReportPublicationMaterializer(store, db_path).materialize(publication.id)
        assert await thread_store.get_thread_messages(thread["id"]) == []
    finally:
        await thread_store.close()


@pytest.mark.asyncio
async def test_materialized_report_preserves_exact_governed_citation_groups_and_anchors(tmp_path):
    db_path = str(tmp_path / "governed-citations.db")
    store = SQLiteContentResearchStore(db_path)
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    thread = await thread_store.create_thread(title="调研报告")
    try:
        async with WorkflowRunManager(db_path) as manager:
            run = await manager.start_run(thread_id=thread["id"], user_id="user-1")
        snapshot = replace(_composer_snapshot(), workflow_run_id=run.run_id)
        draft = ResearchReportComposer().compose(snapshot)
        decision = _decision(draft)
        publication = _publication(draft, decision)
        store.save_result_snapshot(snapshot)
        store.save_report_draft(draft.to_record())
        store.save_report_faithfulness_decision(decision.to_record())
        store.save_report_publication(publication.to_record())
        async with WorkflowRunManager(db_path) as manager:
            await manager.begin_report_finalization(run.run_id)

        artifact = await ReportPublicationMaterializer(store, db_path).materialize(publication.id)

        groups = artifact.payload_json["citation_groups"]
        assert [group["citation_group_id"] for group in groups] == ["citation_7"]
        assert groups[0]["display_index"] == 7
        assert groups[0]["evidence_refs"] == snapshot.metadata["governed_snapshot"]["citation_groups"][0]["evidence_refs"]
        anchors = artifact.payload_json["sections"][0]["citation_anchors"]
        assert anchors[0]["citation_group_id"] == groups[0]["citation_group_id"]
    finally:
        await thread_store.close()


@pytest.mark.asyncio
async def test_materialization_rejects_incomplete_frozen_citation_before_artifact_or_message(tmp_path):
    db_path = str(tmp_path / "invalid-governed-citation.db")
    store = SQLiteContentResearchStore(db_path)
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    thread = await thread_store.create_thread(title="调研报告")
    try:
        async with WorkflowRunManager(db_path) as manager:
            run = await manager.start_run(thread_id=thread["id"], user_id="user-1")
        valid = replace(_composer_snapshot(), workflow_run_id=run.run_id)
        draft = ResearchReportComposer().compose(valid)
        broken_governed = dict(valid.metadata["governed_snapshot"])
        broken_group = dict(broken_governed["citation_groups"][0])
        broken_group["evidence_refs"] = [{"quote": "通勤"}]
        broken_governed["citation_groups"] = [broken_group]
        snapshot = replace(valid, metadata={**valid.metadata, "governed_snapshot": broken_governed})
        decision = _decision(draft)
        publication = _publication(draft, decision)
        store.save_result_snapshot(snapshot)
        store.save_report_draft(draft.to_record())
        store.save_report_faithfulness_decision(decision.to_record())
        store.save_report_publication(publication.to_record())
        async with WorkflowRunManager(db_path) as manager:
            await manager.begin_report_finalization(run.run_id)

        with pytest.raises(ValueError, match="evidence ref is incomplete"):
            await ReportPublicationMaterializer(store, db_path).materialize(publication.id)

        assert await thread_store.get_thread_messages(thread["id"]) == []
        async with WorkflowStore(db_path) as workflow_store:
            assert await workflow_store.list_artifacts(run.run_id) == []
    finally:
        await thread_store.close()


@pytest.mark.asyncio
async def test_migration_purges_legacy_report_lineage_but_preserves_same_run_non_report_results(
    tmp_path,
):
    db_path = str(tmp_path / "legacy-report-purge.db")
    store = SQLiteContentResearchStore(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "DELETE FROM content_research_schema_migrations WHERE version = '0014'"
        )
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    thread = await thread_store.create_thread(title="legacy report purge")
    try:
        async with WorkflowRunManager(db_path) as manager:
            run = await manager.start_run(thread_id=thread["id"], user_id="user-1")
        policy, _, _ = build_default_snapshot(
            snapshot_id="policy_gate2",
            workflow_run_id=run.run_id,
            brief_id="brief_gate2",
            plan_id="plan_gate2",
            direction_set_version="direction_set_v1",
            direction_ids=("product_marketing",),
            report_compose_mode="template_only",
        )
        store.save_run_policy_snapshot(policy)
        now = utcnow()
        store.save_trace(
            TraceRecord(
                id="trace_gate2",
                workflow_run_id=run.run_id,
                thread_id=thread["id"],
                schema_version="content_research_trace_v1",
                status="completed",
                started_at=now,
                payload={"schema_version": "content_research_trace_payload_v1"},
            )
        )
        store.save_evidence_record(
            EvidenceRecord(
                id="evidence_gate2",
                workflow_run_id=run.run_id,
                research_plan_id="plan_gate2",
                research_direction_id="product_marketing",
                trace_id="trace_gate2",
                schema_version="content_research_evidence_record_v1",
                status="accepted",
                source_type="note",
                source_platform="xhs",
                source_url="https://example.test/gate2-note",
                source_id="gate2-note",
                evidence_type="note",
                normalized_payload={
                    "schema_version": "content_research_source_payload_v1",
                    "source_id": "gate2-note",
                },
                title="Gate 2 source",
                text_excerpt="retained evidence",
                raw_content_ref="gate2-hash",
                content_hash="gate2-hash",
                dedupe_key="gate2-note",
                retrieval_query="gate2",
            )
        )
        store.save_canonical_source(
            CanonicalSourceRecord(
                id="source_gate2",
                schema_version="content_research_canonical_source_v1",
                payload={"source_id": "gate2-note"},
                platform="xhs",
                platform_source_kind="note",
                platform_source_id="gate2-note",
                canonical_url="https://example.test/gate2-note",
            )
        )
        store.save_stage_checkpoint(
            StageCheckpointRecord(
                id="checkpoint_gate2",
                schema_version="content_research_stage_checkpoint_v1",
                payload={"schema_version": "content_research_checkpoint_payload_v1"},
                workflow_run_id=run.run_id,
                subagent_task_id="task_gate2",
                stage_name="collect",
                input_fingerprint="gate2-fingerprint",
                status="completed",
            )
        )
        store.save_directional_evidence_packet(
            DirectionalEvidencePacketRecord(
                "packet_gate2",
                "content_research_directional_packet_v1",
                {
                    "field_projection": {
                        "content_text": "claim",
                        "source_url": "https://example.test/gate2-note",
                    }
                },
                workflow_run_id=run.run_id,
                research_direction_id="product_marketing",
                canonical_source_id="source_gate2",
                field_projection_hash="gate2-packet-hash",
            )
        )
        store.save_claim_candidate(
            ClaimCandidateRecord(
                "claim_gate2",
                "content_research_claim_candidate_v1",
                {
                    "quote_refs": [
                        {
                            "field_path": "content_text",
                            "quote": "claim",
                            "text_start": 0,
                            "text_end": 5,
                            "source_text_hash": source_text_hash("claim"),
                            "source_url": "https://example.test/gate2-note",
                        }
                    ]
                },
                workflow_run_id=run.run_id,
                research_direction_id="product_marketing",
                evidence_packet_id="packet_gate2",
                statement="claim",
                intent_id="intent_gate2",
                claim_type="observation",
            )
        )
        store.save_claim_admission_decision(
            ClaimAdmissionDecisionRecord(
                "admission_gate2",
                "content_research_claim_admission_v1",
                {"reason_codes": []},
                research_direction_id="product_marketing",
                claim_candidate_id="claim_gate2",
                decision="admitted",
                policy_snapshot_id="policy_gate2",
            )
        )
        snapshot = replace(
            _snapshot(),
            workflow_run_id=run.run_id,
            metadata={
                "governed_snapshot": {
                    "schema_version": "governed_v1",
                    "citation_groups": [
                        {
                            "citation_group_id": "citation_gate2",
                            "display_index": 1,
                            "evidence_refs": [
                                {
                                    "field_path": "content_text",
                                    "quote": "claim",
                                    "text_start": 0,
                                    "text_end": 5,
                                    "source_text_hash": source_text_hash("claim"),
                                    "source_url": "https://example.test/gate2-note",
                                }
                            ],
                        }
                    ],
                }
            },
        )
        draft = replace(_draft(), workflow_run_id=run.run_id)
        decision = replace(_decision(draft), workflow_run_id=run.run_id)
        publication = replace(_publication(draft, decision), workflow_run_id=run.run_id)
        store.save_result_snapshot(snapshot)
        store.save_report_draft(draft.to_record())
        store.save_report_faithfulness_decision(decision.to_record())
        store.save_report_publication(publication.to_record())
        async with WorkflowRunManager(db_path) as manager:
            await manager.begin_report_finalization(run.run_id)
        report_artifact = await ReportPublicationMaterializer(store, db_path).materialize(
            publication.id
        )
        async with WorkflowRunManager(db_path) as manager:
            await manager.complete_report_finalization(run.run_id)
        await ReportPublicationMaterializer(store, db_path).publish_timeline_message(
            publication.id
        )
        report_message = (await thread_store.get_thread_messages(thread["id"]))[0]
        async with WorkflowRunManager(db_path) as manager:
            additional_report_artifact = await manager.attach_artifact(
                run_id=run.run_id,
                artifact_type=WorkflowArtifactType.FINAL_RESULT,
                payload={"kind": "additional_legacy_report_result"},
                payload_mode=WorkflowArtifactPayloadMode.SNAPSHOT,
                summary_text="内容调研报告已发布",
            )
        await thread_store.append_message(
            thread_id=thread["id"],
            role="assistant",
            text="内容调研报告已生成。",
            message_type="artifact_result",
            run_id=run.run_id,
            artifact_refs=[
                {
                    "artifact_id": additional_report_artifact.artifact_id,
                    "artifact_type": additional_report_artifact.artifact_type.value,
                    "artifact_version": additional_report_artifact.artifact_version,
                    "parent_artifact_id": additional_report_artifact.parent_artifact_id,
                }
            ],
        )
        async with WorkflowRunManager(db_path) as manager:
            unrelated_artifact = await manager.attach_artifact(
                run_id=run.run_id,
                artifact_type=WorkflowArtifactType.FINAL_RESULT,
                payload={"kind": "same_run_non_report_result"},
                payload_mode=WorkflowArtifactPayloadMode.SNAPSHOT,
                summary_text="其他工作流结果",
            )
        unrelated_message = await thread_store.append_message(
            thread_id=thread["id"],
            role="assistant",
            text="其他工作流结果已生成。",
            message_type="artifact_result",
            run_id=run.run_id,
            artifact_refs=[
                {
                    "artifact_id": unrelated_artifact.artifact_id,
                    "artifact_type": unrelated_artifact.artifact_type.value,
                    "artifact_version": unrelated_artifact.artifact_version,
                    "parent_artifact_id": unrelated_artifact.parent_artifact_id,
                }
            ],
        )
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE workflow_artifacts SET payload_json = ? WHERE artifact_id = ?",
                ("{malformed", report_artifact.artifact_id),
            )
            conn.execute(
                "UPDATE creator_messages SET artifact_refs_json = ? WHERE id = ?",
                ("{malformed", report_message["id"]),
            )

        apply_content_research_migrations(db_path, _bootstrap_legacy_content_research_schema)
        migrated_store = SQLiteContentResearchStore(db_path)
        async with WorkflowStore(db_path) as workflow_store:
            first_artifacts = await workflow_store.list_artifacts(run.run_id)
        first_messages = await thread_store.get_thread_messages(thread["id"])
        first_state = {
            "artifacts": [item.artifact_id for item in first_artifacts],
            "messages": [item["id"] for item in first_messages],
            "publications": [
                item.id
                for item in migrated_store.list_typed_records(ReportPublicationRecord)
            ],
            "drafts": [item.id for item in migrated_store.list_typed_records(ReportDraftRecord)],
            "decisions": [
                item.id
                for item in migrated_store.list_typed_records(ReportFaithfulnessDecisionRecord)
            ],
        }
        apply_content_research_migrations(db_path, _bootstrap_legacy_content_research_schema)
        async with WorkflowStore(db_path) as workflow_store:
            second_artifacts = await workflow_store.list_artifacts(run.run_id)
        second_messages = await thread_store.get_thread_messages(thread["id"])
        second_state = {
            "artifacts": [item.artifact_id for item in second_artifacts],
            "messages": [item["id"] for item in second_messages],
            "publications": [
                item.id
                for item in migrated_store.list_typed_records(ReportPublicationRecord)
            ],
            "drafts": [item.id for item in migrated_store.list_typed_records(ReportDraftRecord)],
            "decisions": [
                item.id
                for item in migrated_store.list_typed_records(ReportFaithfulnessDecisionRecord)
            ],
        }

        assert first_state == second_state == {
            "artifacts": [unrelated_artifact.artifact_id],
            "messages": [unrelated_message["id"]],
            "publications": [],
            "drafts": [],
            "decisions": [],
        }
        assert migrated_store.get_trace("trace_gate2").workflow_run_id == run.run_id
        async with WorkflowStore(db_path) as workflow_store:
            assert (await workflow_store.get_run(run.run_id)).run_id == run.run_id
        assert migrated_store.get_evidence_record("evidence_gate2").trace_id == "trace_gate2"
        assert migrated_store.get_canonical_source("source_gate2").canonical_url == (
            "https://example.test/gate2-note"
        )
        assert [
            item.id for item in migrated_store.list_typed_records(StageCheckpointRecord)
        ] == ["checkpoint_gate2"]
        assert [
            item.id
            for item in migrated_store.list_typed_records(DirectionalEvidencePacketRecord)
        ] == ["packet_gate2"]
        assert [
            item.id
            for item in migrated_store.list_typed_records(ClaimAdmissionDecisionRecord)
        ] == ["admission_gate2"]
        assert migrated_store.list_result_snapshots_for_workflow(run.run_id)[0].metadata[
            "governed_snapshot"
        ]["citation_groups"]

        with pytest.raises(ValueError, match="missing report publication"):
            await ReportPublicationMaterializer(migrated_store, db_path).materialize(publication.id)
        assert [
            item["id"] for item in await thread_store.get_thread_messages(thread["id"])
        ] == [unrelated_message["id"]]
    finally:
        await thread_store.close()
