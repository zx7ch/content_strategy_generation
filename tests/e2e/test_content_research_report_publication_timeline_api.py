from dataclasses import replace

import httpx
import pytest

from app.api.routes.router import app
from app.config import settings
from app.content_research.contracts import build_default_snapshot
from app.content_research.models import ResearchBriefRecord
from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.reporting.composer import ResearchReportComposer
from app.content_research.reporting.lite_read_model import LiteReportReader
from app.content_research.reporting.publication_materializer import ReportPublicationMaterializer
from app.content_research.reporting.read_model import PublishedReportReader
from app.content_research.scope_contract import (
    CoverageSnapshot,
    ScopeConstraint,
    ScopeQueryGroupInput,
    build_scope_contract,
)
from app.content_research.service import _governed_input_fingerprint
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.thread_store import ThreadStore
from app.services.workflow_run_manager import WorkflowRunManager
from tests.integration.test_content_research_report_store import (
    _decision,
    _publication,
)
from tests.unit.test_content_research_report_composer import _snapshot as _governed_snapshot


@pytest.mark.asyncio
async def test_selected_historical_publication_freezes_semantic_scope_coverage_and_trace(
    tmp_path,
):
    """Dropping report lineage from identity/readback must collapse R1 into latest Scope."""
    db_path = str(tmp_path / "historical-publication-lineage.db")
    store = SQLiteContentResearchStore(db_path)
    async with ThreadStore(db_path) as threads:
        thread = await threads.create_thread(title="历史报告")
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id=thread["id"], user_id="user-1")

    scopes = (
        build_scope_contract(
            workflow_run_id=run.run_id,
            research_plan_id="rp_1",
            version=1,
            constraints=(
                ScopeConstraint("core_object", "核心对象", "长袖衬衫", "required"),
                ScopeConstraint("season", "季节", "夏季", "required"),
            ),
            query_groups=(
                ScopeQueryGroupInput("夏季 长袖衬衫", "夏季 长袖衬衫"),
            ),
        ),
        build_scope_contract(
            workflow_run_id=run.run_id,
            research_plan_id="rp_1",
            version=2,
            constraints=(
                ScopeConstraint("core_object", "核心对象", "长袖衬衫", "required"),
                ScopeConstraint("season", "季节", "夏季", "preferred"),
            ),
            query_groups=(
                ScopeQueryGroupInput("通勤 长袖衬衫", "通勤 长袖衬衫"),
            ),
        ),
    )
    base = _governed_snapshot()
    base_governed = base.metadata["governed_snapshot"]
    snapshots = []
    coverages = []
    publications = []
    for index, scope in enumerate(scopes, start=1):
        store.save_scope_contract(scope)
        coverage = CoverageSnapshot(
            id=f"scv_historical_{index}",
            workflow_run_id=run.run_id,
            scope_contract_id=scope.id,
            scope_contract_version=scope.version,
            state="awaiting_scope_decision",
            constraint_counts={
                "_summary": {
                    "minimum_samples": 1,
                    "minimum_independent_authors": 1,
                    "reason_codes": ["required_constraint_coverage_unmet:season"],
                }
            },
            unmet_constraint_ids=("season",),
        )
        store.save_coverage_snapshot(coverage)
        unit, created = store.resolve_coverage_to_execution_unit_atomically(
            snapshot=coverage,
            decision={"resolution": "generate_limited_report"},
        )
        assert created is True
        claim = store.claim_execution_unit(execution_unit_id=unit.id, owner=f"worker-{index}")
        assert claim is not None
        frozen_trace = {
            "execution_unit_id": unit.id,
            "attempt_no": claim.attempt_no,
            "outcome_state": "not_requested",
            "facts": [
                {
                    "attempt_no": fact.attempt_no,
                    "sequence_no": fact.sequence_no,
                    "kind": fact.kind,
                    "payload": {},
                }
                for fact in store.execution_trace(unit.id)
            ],
        }
        scope_payload = {
            "id": scope.id,
            "version": scope.version,
            "constraints": [
                {"id": item.id, "value": item.value, "mode": item.mode}
                for item in scope.constraints
            ],
            "query_groups": [
                {"id": item.id, "final_query": item.final_query}
                for item in scope.query_groups
            ],
        }
        coverage_payload = {
            "id": coverage.id,
            "state": coverage.state,
            "constraint_counts": coverage.constraint_counts,
            "unmet_constraint_ids": list(coverage.unmet_constraint_ids),
        }
        governed = {
            **base_governed,
            "research_plan_id": "rp_1",
            "policy_scope": {
                **base_governed["policy_scope"],
                "direction_set_version": "direction_set_historical",
                "direction_ids": ["product_marketing"],
                "report_compose_mode": "template_only",
            },
            "direction_results": [],
            "claim_cards": [],
            "citation_groups": [],
            "weak_signals": [],
            "cross_direction_records": [],
            "aggregate_claims": [],
            "marketing_conclusions": [],
            "execution_lineage": {
                "scope_contract_id": scope.id,
                "execution_unit_id": unit.id,
                "coverage_snapshot_id": coverage.id,
                "successful_attempt_no": claim.attempt_no,
            },
            "scope_contract": scope_payload,
            "coverage_snapshot": coverage_payload,
            "execution_trace": frozen_trace,
        }
        snapshot = replace(
            base,
            id=f"rrs_historical_{index}",
            workflow_run_id=run.run_id,
            snapshot_version=str(index),
            metadata={
                "governed_snapshot": governed,
                "governed_input_fingerprint": _governed_input_fingerprint(governed),
            },
        )
        draft = ResearchReportComposer().compose(snapshot)
        decision = replace(
            _decision(draft),
            workflow_run_id=run.run_id,
            scope_contract_id=draft.scope_contract_id,
            execution_unit_id=draft.execution_unit_id,
            coverage_snapshot_id=draft.coverage_snapshot_id,
            attempt_no=draft.attempt_no,
        )
        publication = replace(
            _publication(draft, decision, compose_mode="template_only"),
            workflow_run_id=run.run_id,
            scope_contract_id=draft.scope_contract_id,
            execution_unit_id=draft.execution_unit_id,
            coverage_snapshot_id=draft.coverage_snapshot_id,
            attempt_no=draft.attempt_no,
        )
        store.save_result_snapshot(snapshot)
        store.save_report_draft(draft.to_record())
        store.save_report_faithfulness_decision(decision.to_record())
        store.save_report_publication(publication.to_record())
        snapshots.append(snapshot)
        coverages.append(coverage)
        publications.append(publication)

    assert snapshots[0].metadata["governed_input_fingerprint"] != snapshots[1].metadata[
        "governed_input_fingerprint"
    ]
    assert publications[0].id != publications[1].id

    foreign_coverage_publication = replace(
        publications[1].to_record(),
        id="rpp_foreign_coverage",
        coverage_snapshot_id=coverages[0].id,
    )
    with pytest.raises(ValueError, match="report.*lineage mismatch"):
        store.save_report_publication(foreign_coverage_publication)

    async with WorkflowRunManager(db_path) as manager:
        await manager.begin_report_finalization(run.run_id)
    materializer = ReportPublicationMaterializer(store, db_path)
    for publication in publications:
        await materializer.materialize(publication.id)
    async with WorkflowRunManager(db_path) as manager:
        await manager.complete_report_finalization(run.run_id)

    reader = PublishedReportReader(store, db_path)
    first = await reader.read(
        workflow_run_id=run.run_id,
        publication_id=publications[0].id,
    )
    second = await reader.read(
        workflow_run_id=run.run_id,
        publication_id=publications[1].id,
    )

    assert first["scope_contract_id"] == scopes[0].id
    assert second["scope_contract_id"] == scopes[1].id
    assert first["scope_contract"]["constraints"][1]["mode"] == "required"
    assert second["scope_contract"]["constraints"][1]["mode"] == "preferred"
    assert first["coverage_snapshot"]["id"] == "scv_historical_1"
    assert second["coverage_snapshot"]["id"] == "scv_historical_2"
    assert first["trace"]["execution"]["execution_unit_id"] != second["trace"][
        "execution"
    ]["execution_unit_id"]

    lite_first = await LiteReportReader(store, db_path).read(
        workflow_run_id=run.run_id,
        publication_id=publications[0].id,
    )
    lite_second = await LiteReportReader(store, db_path).read(
        workflow_run_id=run.run_id,
        publication_id=publications[1].id,
    )
    assert lite_first["frozen_scope"]["scope_contract_id"] == scopes[0].id
    assert lite_second["frozen_scope"]["scope_contract_id"] == scopes[1].id
    assert lite_first["frozen_scope"]["coverage_snapshot_id"] == coverages[0].id
    assert lite_second["frozen_scope"]["coverage_snapshot_id"] == coverages[1].id
    assert lite_first["publication"]["execution_unit_id"] != lite_second["publication"][
        "execution_unit_id"
    ]
    assert lite_first["publication"]["attempt_no"] == 0


@pytest.mark.asyncio
async def test_creator_timeline_api_exposes_one_materialized_report_result_and_replay_is_idempotent(
    tmp_path, monkeypatch
):
    db_path = str(tmp_path / "published-report-timeline.db")
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", db_path)
    store = SQLiteContentResearchStore(db_path)
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    original_thread_store = getattr(app.state, "thread_store", None)
    app.state.thread_store = thread_store
    try:
        thread = await thread_store.create_thread(title="调研报告")
        async with WorkflowRunManager(db_path) as manager:
            run = await manager.start_run(thread_id=thread["id"], user_id="user-1")
        store.save_brief(
            ResearchBriefRecord(
                id="brief_existing_publication_selector_mismatch",
                workflow_run_id=run.run_id,
                thread_id=thread["id"],
                schema_version="content_research_brief_v1",
                status="ready",
                payload={
                    "schema_version": "content_research_brief_payload_v1",
                    "confirmed_subject": "已有报告不应进入恢复",
                },
            )
        )
        governed = _governed_snapshot().metadata["governed_snapshot"]
        frozen_citations = [
            {
                **governed["citation_groups"][0],
                "citation_group_id": "cg_1",
                "display_index": 1,
                "admission_decision_id": "cad_1",
                "evidence_refs": [
                    {
                        **governed["citation_groups"][0]["evidence_refs"][0],
                        "canonical_note_id": "note_1",
                        "source_url": "https://www.xiaohongshu.com/explore/note_1",
                    }
                ],
            },
            {
                **governed["citation_groups"][0],
                "citation_group_id": "cg_2",
                "display_index": 2,
                "admission_decision_id": "cad_1",
                "evidence_refs": [
                    {
                        **governed["citation_groups"][0]["evidence_refs"][0],
                        "canonical_note_id": "note_1",
                        "source_url": "https://www.xiaohongshu.com/explore/note_1",
                    }
                ],
            },
        ]
        snapshot = replace(
            _governed_snapshot(),
            workflow_run_id=run.run_id,
            metadata={
                "governed_snapshot": {
                    **governed,
                    "claim_cards": [
                        {
                            **governed["claim_cards"][0],
                            "claim_type": "use_context",
                            "scope": {"sample": "selected_packets"},
                        }
                    ],
                    "policy_scope": {
                        **governed["policy_scope"],
                        "direction_set_version": "direction_set_v1",
                        "direction_ids": ["product_marketing"],
                        "report_compose_mode": "template_only",
                    },
                    "citation_groups": frozen_citations,
                },
                "governed_input_fingerprint": "timeline_frozen_citations",
            },
        )
        draft = ResearchReportComposer().compose(snapshot)
        decision = replace(_decision(draft), workflow_run_id=run.run_id)
        publication = replace(
            _publication(draft, decision, compose_mode="template_only"),
            workflow_run_id=run.run_id,
        )
        store.save_result_snapshot(snapshot)
        store.save_report_draft(draft.to_record())
        store.save_report_faithfulness_decision(decision.to_record())
        store.save_report_publication(publication.to_record())
        async with WorkflowRunManager(db_path) as manager:
            await manager.begin_report_finalization(run.run_id)

        materializer = ReportPublicationMaterializer(store, db_path)
        artifact = await materializer.materialize(publication.id)
        await materializer.materialize(publication.id)
        # The artifact is private until publication finalization commits the
        # workflow.  A finalizing run must not look like a completed report.
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            finalizing_response = await client.get(
                f"/content-research/workflows/{run.run_id}/lite-report"
            )
            finalizing_timeline = await client.get(f"/threads/{thread['id']}/timeline")
        assert finalizing_response.status_code == 404, finalizing_response.text
        assert not [
            message
            for message in finalizing_timeline.json()["messages"]
            if message["message_type"] == "artifact_result" and message["run_id"] == run.run_id
        ]
        async with WorkflowRunManager(db_path) as manager:
            await manager.complete_report_finalization(run.run_id)
        await materializer.publish_timeline_message(publication.id)
        await materializer.publish_timeline_message(publication.id)
        # A committed publication must not be replaced by a later failed
        # checkpoint, and a selector that does not resolve to it stays absent.
        store.save_stage_checkpoint(
            StageCheckpointRecord(
                id="checkpoint_existing_publication_selector_mismatch",
                schema_version="content_research_stage_checkpoint_v1",
                payload={"reason_code": "auth_expired"},
                workflow_run_id=run.run_id,
                subagent_task_id="task_existing_publication_selector_mismatch",
                stage_name="collect",
                input_fingerprint="existing-publication-selector-mismatch",
                status="failed",
            )
        )

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(f"/threads/{thread['id']}/timeline")
            lite_response = await client.get(
                f"/content-research/workflows/{run.run_id}/lite-report?citation_group_ids=cg_1"
            )
            unavailable_citation_response = await client.get(
                f"/content-research/workflows/{run.run_id}/lite-report?citation_group_ids=cg_missing"
            )
            selector_mismatch_response = await client.get(
                f"/content-research/workflows/{run.run_id}/lite-report?publication_id=publication_missing"
            )
            report_response = await client.get(f"/content-research/workflows/{run.run_id}/report")
            legacy_response = await client.get(f"/content-research/workflows/{run.run_id}/results")
        assert response.status_code == 200
        results = [
            message
            for message in response.json()["messages"]
            if message["message_type"] == "artifact_result" and message["run_id"] == run.run_id
        ]
        assert len(results) == 1
        reference = results[0]["artifact_refs"][0]
        assert reference["artifact_id"] == artifact.artifact_id
        assert reference["artifact"]["payload_mode"] == "snapshot"
        assert (
            reference["artifact"]["materialized_payload_json"]["report_publication_id"]
            == publication.id
        )
        assert report_response.status_code == 404, report_response.text
        assert lite_response.status_code == 200, lite_response.text
        assert unavailable_citation_response.status_code == 404
        assert selector_mismatch_response.status_code == 404
        assert legacy_response.status_code == 404
        lite = lite_response.json()
        assert lite["publication"]["state"] == "complete_verified_report"
        assert isinstance(lite["status_strip"]["admitted_finding_count"], int)
        assert [item["citation_group_id"] for item in lite["citations"]] == ["cg_1"]
        assert [
            ref["quote"]
            for citation in lite["citations"]
            for ref in citation["evidence_refs"]
        ] == ["通勤"]
    finally:
        app.state.thread_store = original_thread_store
        await thread_store.close()


@pytest.mark.asyncio
async def test_lite_report_api_exposes_recovery_as_non_report_projection(tmp_path, monkeypatch):
    db_path = str(tmp_path / "lite-recovery-api.db")
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", db_path)
    store = SQLiteContentResearchStore(db_path)
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    original_thread_store = getattr(app.state, "thread_store", None)
    app.state.thread_store = thread_store
    try:
        thread = await thread_store.create_thread(title="恢复调研")
        async with WorkflowRunManager(db_path) as manager:
            run = await manager.start_run(thread_id=thread["id"], user_id="user-1")
            await manager.fail_run(
                run.run_id, {"code": "auth_expired", "message": "Cookie expired"}
            )
        store.save_brief(
            ResearchBriefRecord(
                id="brief_recovery_api",
                workflow_run_id=run.run_id,
                thread_id=thread["id"],
                schema_version="content_research_brief_v1",
                status="ready",
                payload={
                    "schema_version": "content_research_brief_payload_v1",
                    "confirmed_subject": "Satisfy Running",
                },
            )
        )
        policy, _, _ = build_default_snapshot(
            snapshot_id="policy_recovery_api",
            workflow_run_id=run.run_id,
            brief_id="brief_recovery_api",
            plan_id="plan_recovery_api",
            direction_set_version="direction_set_v1",
            direction_ids=("product_marketing", "competitor_discovery", "content_performance"),
            report_compose_mode="template_only",
        )
        store.save_run_policy_snapshot(policy)
        store.save_stage_checkpoint(
            StageCheckpointRecord(
                "checkpoint_recovery_api",
                "content_research_stage_checkpoint_v1",
                {"reason_code": "auth_expired"},
                workflow_run_id=run.run_id,
                subagent_task_id="task_recovery_api",
                stage_name="collect",
                input_fingerprint="frozen-operation",
                status="failed",
            )
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(f"/content-research/workflows/{run.run_id}/lite-report")

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["publication"] == {"state": None}
        assert payload["recovery_projection"]["reason_code"] == "auth_expired"
        assert payload["recovery_projection"]["next_action"] == "resume_run"
        assert payload["frozen_scope"]["report_compose_mode"] == "template_only"
        assert len(payload["run_direction_states"]) == 3
    finally:
        app.state.thread_store = original_thread_store
        await thread_store.close()
