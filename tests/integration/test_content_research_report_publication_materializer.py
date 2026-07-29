import sqlite3
from dataclasses import replace

import pytest

from app.content_research.bootstrap import _bootstrap_legacy_content_research_schema
from app.content_research.evidence.models import EvidenceRecord
from app.content_research.migrations import apply_content_research_migrations
from app.content_research.models import TraceRecord, utcnow
from app.content_research.persistence_models import CanonicalSourceRecord, StageCheckpointRecord
from app.content_research.reporting.composer import ResearchReportComposer
from app.content_research.reporting.lite_read_model import LiteReportReader
from app.content_research.reporting.publication_materializer import ReportPublicationMaterializer
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowArtifactPayloadMode, WorkflowArtifactType
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
            await manager.complete_run(run.run_id)

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
async def test_pre_0013_gate2_artifact_survives_bundle_removal_and_remains_lite_readable(tmp_path):
    db_path = str(tmp_path / "pre-0013-artifact.db")
    store = SQLiteContentResearchStore(db_path)
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    thread = await thread_store.create_thread(title="Gate 2 artifact")
    try:
        # Recreate the exact schema delta owned by 0013, and remove only its
        # ledger entry, before persisting the legacy artifact below.
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "DELETE FROM content_research_schema_migrations WHERE version = '0013'"
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
            await manager.complete_run(run.run_id)

        artifact = await ReportPublicationMaterializer(store, db_path).materialize(publication.id)
        apply_content_research_migrations(
            db_path, _bootstrap_legacy_content_research_schema
        )
        migrated_store = SQLiteContentResearchStore(db_path)
        report = await LiteReportReader(migrated_store, db_path).read(
            workflow_run_id=run.run_id
        )

        assert artifact.payload_json["citation_groups"]
        assert report["publication"]["state"] == "complete_verified_report"
        assert [citation["citation_group_id"] for citation in report["citations"]] == ["citation_7"]
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
            await manager.complete_run(run.run_id)

        with pytest.raises(ValueError, match="lineage mismatch"):
            await ReportPublicationMaterializer(store, db_path).materialize(publication.id)

        assert await thread_store.get_thread_messages(thread["id"]) == []
        async with WorkflowStore(db_path) as workflow_store:
            assert await workflow_store.list_artifacts(run.run_id) == []
    finally:
        await thread_store.close()


@pytest.mark.asyncio
async def test_materialization_requires_terminal_workflow_before_artifact_or_message(tmp_path):
    db_path = str(tmp_path / "report-before-terminal.db")
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

        with pytest.raises(ValueError, match="before workflow completion"):
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
            await manager.complete_run(run.run_id)

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
            await manager.complete_run(run.run_id)

        with pytest.raises(ValueError, match="evidence ref is incomplete"):
            await ReportPublicationMaterializer(store, db_path).materialize(publication.id)

        assert await thread_store.get_thread_messages(thread["id"]) == []
        async with WorkflowStore(db_path) as workflow_store:
            assert await workflow_store.list_artifacts(run.run_id) == []
    finally:
        await thread_store.close()
