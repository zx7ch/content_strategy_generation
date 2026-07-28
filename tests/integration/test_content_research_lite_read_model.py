from dataclasses import replace
import json
import sqlite3

import pytest

from app.content_research.contracts import build_default_snapshot
from app.content_research.models import ResearchBriefRecord
from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.reporting.composer import ResearchReportComposer
from app.content_research.reporting.lite_read_model import LiteReportReader, _direction_states
from app.content_research.reporting.publication_materializer import ReportPublicationMaterializer
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.services.workflow_run_manager import WorkflowRunManager
from tests.integration.test_content_research_report_store import _decision, _publication
from tests.unit.test_content_research_report_composer import _snapshot


def test_lite_direction_states_normalize_requested_not_started_to_unavailable():
    states = _direction_states(
        {
            "release": {"direction_ids": ["product_marketing"]},
            "run_direction_states": [
                {
                    "direction": "product_marketing",
                    "state": "not_started",
                    "reason_codes": [],
                    "recovery_actions": [],
                }
            ],
        }
    )

    assert states[0] == {
        "direction": "product_marketing",
        "state": "unavailable",
        "reason_code": "collection_result_unavailable",
        "recovery_action": None,
    }


def test_lite_projection_with_empty_requested_scope_exposes_no_directional_data(tmp_path):
    db_path = str(tmp_path / "empty-scope.db")
    reader = LiteReportReader(SQLiteContentResearchStore(db_path), db_path)
    report = {
        "workflow_run_id": "run_empty_scope",
        "workflow_terminal_state": "succeeded",
        "publication_state": "complete_verified_report",
        "publication": {"compose_mode": "template_only"},
        "release": {
            "direction_set_version": "direction_set_v1",
            "direction_ids": [],
        },
        "claim_cards": [
            {
                "claim_candidate_id": "claim_product",
                "direction_id": "product_marketing",
                "admission_state": "admitted",
                "statement": "must remain hidden",
                "claim_type": "finding",
                "scope": "one sample",
            }
        ],
        "weak_signals": [
            {
                "claim_candidate_id": "weak_product",
                "direction_id": "product_marketing",
                "statement": "must also remain hidden",
            }
        ],
        "citation_groups": [
            {
                "citation_group_id": "cg_product",
                "display_index": 1,
                "claim_candidate_id": "claim_product",
                "evidence_refs": [
                    {
                        "field_path": "content_text",
                        "quote": "private directional evidence",
                        "source_url": "https://example.test/private",
                    }
                ],
            }
        ],
        "run_direction_states": [
            {"direction": "product_marketing", "state": "completed"}
        ],
        "limitations_recovery": [],
    }

    payload = reader._published_projection(report, citation_group_ids=None)

    assert payload["sections"]["main_findings"] == []
    assert payload["sections"]["weak_signals"] == []
    assert payload["citations"] == []
    assert payload["status_strip"]["admitted_finding_count"] == 0


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
        base_snapshot = _snapshot()
        governed = base_snapshot.metadata["governed_snapshot"]
        citation_group = governed["citation_groups"][0]
        snapshot = replace(
            base_snapshot,
            workflow_run_id=run.run_id,
            metadata={
                **base_snapshot.metadata,
                "governed_snapshot": {
                    **governed,
                    "policy_scope": {
                        **governed["policy_scope"],
                        "direction_set_version": "direction_set_v1",
                        "direction_ids": ["product_marketing"],
                    },
                    "direction_results": [
                        {
                            "direction_id": "product_marketing",
                            "state": "formal_directional_result",
                            "limitations": [],
                            "recovery_actions": [],
                        }
                    ],
                    "citation_groups": [
                        {
                            **citation_group,
                            "evidence_refs": [
                                {
                                    **citation_group["evidence_refs"][0],
                                    "source_collected_at": "2026-07-21T00:00:00Z",
                                },
                                {
                                    "field_path": "title",
                                    "quote": "missing",
                                    "text_start": 0,
                                    "text_end": 7,
                                    "source_text_hash": "b" * 64,
                                    "source_url": None,
                                    "source_collected_at": "2026-07-21T00:01:00Z",
                                },
                                {
                                    "field_path": "title",
                                    "quote": "locked",
                                    "text_start": 0,
                                    "text_end": 6,
                                    "source_text_hash": "c" * 64,
                                    "source_url": "https://example.test/locked",
                                    "source_collected_at": "2026-07-21T00:02:00Z",
                                    "navigation_state": "navigation_unavailable",
                                    "navigation_reason": "provider_auth_required",
                                },
                            ],
                        }
                    ],
                },
            },
        )
        draft = ResearchReportComposer().compose(snapshot)
        decision = replace(_decision(draft), workflow_run_id=run.run_id)
        if publication_state == "evidence_only_report":
            decision = replace(
                decision,
                audit_state="failed",
                reason_codes=("insufficient_admitted_evidence",),
            )
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
        assert [
            item["navigation_state"]
            for item in first["citations"][0]["evidence_refs"]
        ] == ["available", "missing_source_url", "navigation_unavailable"]
        if publication_state == "evidence_only_report":
            assert first["publication"]["publication_reason"] == (
                "insufficient_admitted_evidence"
            )
            assert first["sections"]["main_findings"] == []
            assert first["sections"]["weak_signals"] == []
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


@pytest.mark.asyncio
async def test_lite_reader_never_turns_an_existing_corrupt_publication_into_recovery(
    tmp_path,
):
    db_path = str(tmp_path / "corrupt-publication.db")
    store = SQLiteContentResearchStore(db_path)
    async with ThreadStore(db_path) as threads:
        thread = await threads.create_thread(title="corrupt publication")
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id=thread["id"], user_id="user")
    store.save_brief(
        ResearchBriefRecord(
            id="brief_corrupt",
            workflow_run_id=run.run_id,
            thread_id=thread["id"],
            schema_version="content_research_brief_v1",
            status="ready",
            payload={
                "schema_version": "content_research_brief_payload_v1",
                "confirmed_subject": "损坏报告",
            },
        )
    )
    base_snapshot = _snapshot()
    snapshot = replace(base_snapshot, workflow_run_id=run.run_id)
    draft = ResearchReportComposer().compose(snapshot)
    decision = replace(_decision(draft), workflow_run_id=run.run_id)
    publication = replace(
        _publication(draft, decision),
        workflow_run_id=run.run_id,
    )
    store.save_result_snapshot(snapshot)
    store.save_report_draft(draft.to_record())
    store.save_report_faithfulness_decision(decision.to_record())
    store.save_report_publication(publication.to_record())
    async with WorkflowRunManager(db_path) as manager:
        await manager.complete_run(run.run_id)
    await ReportPublicationMaterializer(store, db_path).materialize(publication.id)
    store.save_stage_checkpoint(
        StageCheckpointRecord(
            "checkpoint_corrupt",
            "content_research_stage_checkpoint_v1",
            {"reason_code": "auth_expired"},
            workflow_run_id=run.run_id,
            subagent_task_id="task_corrupt",
            stage_name="collect",
            input_fingerprint="corrupt-operation",
            status="failed",
        )
    )
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT artifact_id, payload_json FROM workflow_artifacts WHERE run_id = ?",
            (run.run_id,),
        ).fetchone()
        assert row is not None
        payload = json.loads(row[1])
        payload.pop("sections")
        connection.execute(
            "UPDATE workflow_artifacts SET payload_json = ? WHERE artifact_id = ?",
            (json.dumps(payload), row[0]),
        )
        connection.execute(
            "UPDATE workflow_runs SET status = 'paused' WHERE run_id = ?",
            (run.run_id,),
        )

    with pytest.raises(RuntimeError, match="existing publication is unreadable"):
        await LiteReportReader(store, db_path).read(workflow_run_id=run.run_id)
