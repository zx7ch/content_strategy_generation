from dataclasses import replace

import pytest

from app.content_research.reporting.composer import ResearchReportComposer
from app.content_research.reporting.publication_materializer import ReportPublicationMaterializer
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowArtifactPayloadMode, WorkflowArtifactType
from app.services.workflow_run_manager import WorkflowRunManager
from tests.integration.test_content_research_report_store import (
    _decision,
    _draft,
    _publication,
    _snapshot,
)
from tests.unit.test_content_research_report_composer import _snapshot as _composer_snapshot


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
        assert "evidence_bundle_ids" not in artifact.payload_json

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
