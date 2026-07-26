from dataclasses import replace

import pytest

from app.content_research.contracts import build_default_snapshot
from app.content_research.models import ResearchBriefRecord
from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.reporting.composer import ResearchReportComposer
from app.content_research.reporting.lite_read_model import LiteReportReader
from app.content_research.reporting.publication_materializer import ReportPublicationMaterializer
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.services.workflow_run_manager import WorkflowRunManager
from tests.integration.test_content_research_report_store import _decision, _publication
from tests.unit.test_content_research_report_composer import _snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("publication_state", "expected_status_strip"),
    [
        ("complete_verified_report", "admitted_finding_count"),
        ("partial_verified_report", "admitted_finding_count"),
        ("evidence_only_report", "saved_evidence_count"),
    ],
)
async def test_lite_reader_projects_each_formal_publication_without_writing(
    tmp_path, publication_state, expected_status_strip
):
    db_path = str(tmp_path / "lite-report.db")
    store = SQLiteContentResearchStore(db_path)
    threads = ThreadStore(db_path)
    await threads.connect()
    try:
        thread = await threads.create_thread(title="lite")
        async with WorkflowRunManager(db_path) as manager:
            run = await manager.start_run(thread_id=thread["id"], user_id="user")
        snapshot = replace(_snapshot(), workflow_run_id=run.run_id)
        draft = ResearchReportComposer().compose(snapshot)
        decision = replace(_decision(draft), workflow_run_id=run.run_id)
        publication_changes = {
            "workflow_run_id": run.run_id,
            "publication_state": publication_state,
        }
        if publication_state == "partial_verified_report":
            publication_changes["omitted_section_ids"] = ("sec_core",)
        if publication_state == "evidence_only_report":
            publication_changes["has_free_prose"] = False
            publication_changes["verified_section_ids"] = ()
            publication_changes["verified_section_kinds"] = ()
            publication_changes["structured_card_section_ids"] = ()
        publication = replace(_publication(draft, decision), **publication_changes)
        store.save_result_snapshot(snapshot)
        store.save_report_draft(draft.to_record())
        store.save_report_faithfulness_decision(decision.to_record())
        store.save_report_publication(publication.to_record())
        async with WorkflowRunManager(db_path) as manager:
            await manager.complete_run(run.run_id)
        await ReportPublicationMaterializer(store, db_path).materialize(publication.id)

        reader = LiteReportReader(store, db_path)
        first = await reader.read(workflow_run_id=run.run_id)
        second = await reader.read(workflow_run_id=run.run_id)

        assert first == second
        assert first["publication"]["state"] == publication_state
        assert expected_status_strip in first["status_strip"]
        if publication_state != "evidence_only_report":
            assert first["citations"][0]["evidence_refs"][0]["navigation_state"] == "available"
        assert first["recovery_projection"] is None
        async with WorkflowStore(db_path) as workflow_store:
            artifacts = await workflow_store.list_artifacts(run.run_id)
        assert len(artifacts) == 1
    finally:
        await threads.close()


@pytest.mark.asyncio
async def test_lite_reader_returns_non_report_recovery_projection_without_artifact(tmp_path):
    db_path = str(tmp_path / "lite-recovery.db")
    store = SQLiteContentResearchStore(db_path)
    threads = ThreadStore(db_path)
    await threads.connect()
    try:
        thread = await threads.create_thread(title="lite recovery")
        async with WorkflowRunManager(db_path) as manager:
            run = await manager.start_run(thread_id=thread["id"], user_id="user")
            await manager.fail_run(
                run.run_id, {"code": "auth_expired", "message": "Cookie expired"}
            )
        store.save_brief(
            ResearchBriefRecord(
                id="brief_recovery",
                workflow_run_id=run.run_id,
                thread_id=thread["id"],
                schema_version="content_research_brief_v1",
                status="ready",
                payload={
                    "schema_version": "content_research_brief_payload_v1",
                    "confirmed_subject": "夏季通勤短裤",
                },
            )
        )
        policy, _, _ = build_default_snapshot(
            snapshot_id="policy_recovery",
            workflow_run_id=run.run_id,
            brief_id="brief_recovery",
            plan_id="plan_recovery",
            direction_set_version="direction_set_v1",
            direction_ids=("product_marketing", "competitor_discovery", "content_performance"),
            report_compose_mode="template_only",
        )
        store.save_run_policy_snapshot(policy)
        store.save_stage_checkpoint(
            StageCheckpointRecord(
                "checkpoint_recovery",
                "content_research_stage_checkpoint_v1",
                {"reason_code": "auth_expired"},
                workflow_run_id=run.run_id,
                subagent_task_id="task_recovery",
                stage_name="collect",
                input_fingerprint="frozen-operation",
                status="failed",
            )
        )

        payload = await LiteReportReader(store, db_path).read(workflow_run_id=run.run_id)

        assert payload["publication"] == {"state": None}
        assert payload["recovery_projection"] == {
            "reason_code": "auth_expired",
            "completed_stages": [],
            "next_action": "resume_run",
            "actionability": "available",
        }
        assert payload["frozen_scope"] == {
            "direction_set_version": "direction_set_v1",
            "direction_ids": ["product_marketing", "competitor_discovery", "content_performance"],
            "report_compose_mode": "template_only",
        }
        assert [item["direction"] for item in payload["run_direction_states"]] == payload[
            "frozen_scope"
        ]["direction_ids"]
        async with WorkflowStore(db_path) as workflow_store:
            assert await workflow_store.list_artifacts(run.run_id) == []
    finally:
        await threads.close()
