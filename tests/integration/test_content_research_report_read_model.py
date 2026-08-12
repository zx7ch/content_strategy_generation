from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.reporting.composer import ResearchReportComposer
from app.content_research.reporting.publication_materializer import ReportPublicationMaterializer
from app.content_research.reporting.read_model import (
    PublishedReportNotFoundError,
    PublishedReportReader,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.thread_store import ThreadStore
from app.services.workflow_run_manager import WorkflowRunManager
from tests.integration.test_content_research_report_store import _decision, _publication
from tests.unit.test_content_research_report_composer import _snapshot as _governed_snapshot


@pytest.mark.asyncio
async def test_reader_returns_only_materialized_publication_with_stable_paginated_citations(
    tmp_path,
):
    db_path = str(tmp_path / "report-reader.db")
    store = SQLiteContentResearchStore(db_path)
    threads = ThreadStore(db_path)
    await threads.connect()
    try:
        thread = await threads.create_thread(title="report")
        async with WorkflowRunManager(db_path) as manager:
            run = await manager.start_run(thread_id=thread["id"], user_id="user")
        snapshot = replace(_governed_snapshot(), workflow_run_id=run.run_id)
        draft = ResearchReportComposer().compose(snapshot)
        decision = replace(_decision(draft), workflow_run_id=run.run_id)
        publication = replace(_publication(draft, decision), workflow_run_id=run.run_id)
        store.save_result_snapshot(snapshot)
        store.save_report_draft(draft.to_record())
        store.save_report_faithfulness_decision(decision.to_record())
        store.save_report_publication(publication.to_record())
        started_at = datetime(2026, 7, 20, tzinfo=timezone.utc)
        store.save_stage_checkpoint(
            StageCheckpointRecord(
                "scp-reader-duration", "content_research_stage_checkpoint_v1", {"output_refs": [draft.id]},
                workflow_run_id=run.run_id, subagent_task_id="report:rp_1", stage_name="compose",
                input_fingerprint="reader-duration", status="completed", started_at=started_at,
                finished_at=started_at + timedelta(milliseconds=125),
            )
        )
        async with WorkflowRunManager(db_path) as manager:
            await manager.begin_report_finalization(run.run_id)
        await ReportPublicationMaterializer(store, db_path).materialize(publication.id)
        async with WorkflowRunManager(db_path) as manager:
            await manager.complete_report_finalization(run.run_id)

        payload = await PublishedReportReader(store, db_path).read(
            workflow_run_id=run.run_id,
            research_plan_id=publication.research_plan_id,
            citation_limit=1,
        )

        assert payload["publication_state"] == publication.publication_state
        assert payload["workflow_terminal_state"] == "succeeded"
        assert payload["artifact"]["payload_mode"] == "snapshot"
        assert payload["publication"]["report_publication_id"] == publication.id
        assert payload["citation_total"] == 1
        assert payload["citation_groups"][0]["display_index"] == 7
        assert payload["citation_groups"][0]["evidence_refs"][0]["jump_state"] == "available"
        assert payload["trace"]["checkpoint_summary"]["stages"][0]["duration_ms"] == 125
        assert payload["trace"]["faithfulness"]["usage"]["cost_unknown"] is True
        assert "report_draft_id" not in payload
        assert "prompt_version" not in str(payload)
        with pytest.raises(PublishedReportNotFoundError):
            await PublishedReportReader(store, db_path).read(
                workflow_run_id=run.run_id,
                research_plan_id="plan-other",
            )
        with pytest.raises(PublishedReportNotFoundError):
            await PublishedReportReader(store, db_path).read(
                workflow_run_id=run.run_id,
                publication_id="publication-other",
            )
    finally:
        await threads.close()


@pytest.mark.asyncio
async def test_reader_rejects_cross_run_identity_and_marks_missing_source_url_unavailable(tmp_path):
    db_path = str(tmp_path / "report-reader-isolation.db")
    store = SQLiteContentResearchStore(db_path)
    threads = ThreadStore(db_path)
    await threads.connect()
    try:
        thread = await threads.create_thread(title="report")
        async with WorkflowRunManager(db_path) as manager:
            run = await manager.start_run(thread_id=thread["id"], user_id="user")
        snapshot = _governed_snapshot()
        governed = snapshot.metadata["governed_snapshot"]
        snapshot = replace(
            snapshot,
            workflow_run_id=run.run_id,
            metadata={
                **snapshot.metadata,
                "governed_snapshot": {
                    **governed,
                    "citation_groups": [
                        {
                            **governed["citation_groups"][0],
                            "evidence_refs": [
                                {
                                    **governed["citation_groups"][0]["evidence_refs"][0],
                                    "source_url": None,
                                }
                            ],
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
        # Older safe artifacts can retain a citation with no permalink. The
        # reader must expose it without fabricating a jump target.
        async with WorkflowRunManager(db_path) as manager:
            artifact = await manager.attach_artifact(
                run_id=run.run_id,
                artifact_type="final_result",
                payload={
                    "report_publication_id": publication.id,
                    "sections": [],
                    "citation_groups": [
                        {
                            "citation_group_id": "cg",
                            "display_index": 2,
                            "evidence_refs": [{"quote": "q", "source_url": None}],
                        }
                    ],
                },
                summary_text="safe fixture",
            )
            await manager.complete_run(run.run_id)
        assert artifact.artifact_id
        payload = await PublishedReportReader(store, db_path).read(workflow_run_id=run.run_id)
        assert payload["citation_groups"][0]["evidence_refs"][0]["jump_state"] == "unavailable"
        with pytest.raises(PublishedReportNotFoundError):
            await PublishedReportReader(store, db_path).read(workflow_run_id="another-run")
    finally:
        await threads.close()


@pytest.mark.asyncio
async def test_reader_strictly_isolates_plans_and_keeps_old_publications_after_snapshot_update(tmp_path):
    db_path = str(tmp_path / "report-reader-plan-version-isolation.db")
    store = SQLiteContentResearchStore(db_path)
    threads = ThreadStore(db_path)
    await threads.connect()
    try:
        thread = await threads.create_thread(title="report")
        async with WorkflowRunManager(db_path) as manager:
            run = await manager.start_run(thread_id=thread["id"], user_id="user")

        async def persist(snapshot):
            draft = ResearchReportComposer().compose(snapshot)
            decision = _decision(draft)
            publication = _publication(draft, decision)
            store.save_result_snapshot(snapshot)
            store.save_report_draft(draft.to_record())
            store.save_report_faithfulness_decision(decision.to_record())
            store.save_report_publication(publication.to_record())
            return draft, publication

        plan_a_v1 = replace(
            _governed_snapshot(), id="rrs-plan-a-v1", workflow_run_id=run.run_id,
            research_plan_id="plan-a", snapshot_version="1",
        )
        plan_b = replace(
            _governed_snapshot(), id="rrs-plan-b-v1", workflow_run_id=run.run_id,
            research_plan_id="plan-b", snapshot_version="1",
        )
        old_draft, old_publication = await persist(plan_a_v1)
        _, plan_b_publication = await persist(plan_b)
        plan_a_v2 = replace(plan_a_v1, id="rrs-plan-a-v2", snapshot_version="2")
        new_draft, new_publication = await persist(plan_a_v2)

        async with WorkflowRunManager(db_path) as manager:
            await manager.begin_report_finalization(run.run_id)
        materializer = ReportPublicationMaterializer(store, db_path)
        for publication in (old_publication, plan_b_publication, new_publication):
            await materializer.materialize(publication.id)
        async with WorkflowRunManager(db_path) as manager:
            await manager.complete_report_finalization(run.run_id)

        reader = PublishedReportReader(store, db_path)
        plan_a_old = await reader.read(workflow_run_id=run.run_id, publication_id=old_publication.id)
        plan_a_new = await reader.read(workflow_run_id=run.run_id, publication_id=new_publication.id)
        plan_b_payload = await reader.read(
            workflow_run_id=run.run_id,
            research_plan_id="plan-b",
            publication_id=plan_b_publication.id,
        )
        assert plan_a_old["publication"]["research_plan_id"] == "plan-a"
        assert plan_a_new["publication"]["governed_snapshot_version"] == "2"
        assert plan_b_payload["publication"]["research_plan_id"] == "plan-b"
        assert old_draft.id != new_draft.id
        with pytest.raises(PublishedReportNotFoundError):
            await reader.read(
                workflow_run_id=run.run_id,
                research_plan_id="plan-a",
                publication_id=plan_b_publication.id,
            )
    finally:
        await threads.close()
