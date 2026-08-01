import json
import sqlite3
from dataclasses import replace

import pytest

from app.content_research.contracts import build_default_snapshot
from app.content_research.models import ResearchBriefRecord
from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.reporting.composer import ResearchReportComposer
from app.content_research.reporting.lite_read_model import (
    LiteReportReader,
    PublishedReportNotFoundError,
    _direction_states,
)
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
                    "claim_cards": [
                        {
                            **governed["claim_cards"][0],
                            "claim_type": "product_value_expression",
                            "scope": {"sample": "selected_packets"},
                        }
                    ],
                    "citation_groups": [
                        {
                            **citation_group,
                            "admission_decision_id": governed["claim_cards"][0][
                                "admission_decision_id"
                            ],
                            "evidence_refs": [
                                {
                                    **citation_group["evidence_refs"][0],
                                    "canonical_note_id": "note-one",
                                    "source_url": "https://www.xiaohongshu.com/explore/note-one",
                                    "source_collected_at": "2026-07-21T00:00:00Z",
                                }
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
            "compose_mode": "template_only",
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
        ] == ["available"]
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


async def _project_formal_cards(
    tmp_path,
    *,
    claim_cards,
    citation_groups,
    weak_signals=None,
    compose_mode="template_only",
):
    db_path = str(tmp_path / "lite-projection-guard.db")
    store = SQLiteContentResearchStore(db_path)
    async with ThreadStore(db_path) as threads:
        thread = await threads.create_thread(title="projection guard")
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id=thread["id"], user_id="user")
    base_snapshot = _snapshot()
    governed = base_snapshot.metadata["governed_snapshot"]
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
                    "direction_ids": ["product_marketing", "content_performance"],
                },
                "direction_results": [
                    {
                        "direction_id": direction_id,
                        "state": "formal_directional_result",
                        "limitations": [],
                        "recovery_actions": [],
                    }
                    for direction_id in ("product_marketing", "content_performance")
                ],
                "weak_signals": weak_signals or [],
                "cross_direction_records": [],
                "aggregate_claims": [],
                "claim_cards": claim_cards,
                "citation_groups": citation_groups,
            },
        },
    )
    draft = ResearchReportComposer().compose(snapshot)
    decision = replace(_decision(draft), workflow_run_id=run.run_id)
    publication = replace(
        _publication(draft, decision),
        workflow_run_id=run.run_id,
        compose_mode=compose_mode,
    )
    store.save_result_snapshot(snapshot)
    store.save_report_draft(draft.to_record())
    store.save_report_faithfulness_decision(decision.to_record())
    store.save_report_publication(publication.to_record())
    async with WorkflowRunManager(db_path) as manager:
        await manager.complete_run(run.run_id)
    await ReportPublicationMaterializer(store, db_path).materialize(publication.id)

    return await LiteReportReader(store, db_path).read(workflow_run_id=run.run_id)


def _card(
    claim_id,
    admission_id,
    *,
    direction="product_marketing",
    claim_type="product_value_expression",
    admission_state="admitted",
):
    return {
        "claim_candidate_id": claim_id,
        "admission_decision_id": admission_id,
        "admission_state": admission_state,
        "direction_id": direction,
        "claim_type": claim_type,
        "statement": f"statement for {claim_id}",
        "scope": {"sample": "selected_packets"},
    }


def _citation(
    group_id,
    claim_id,
    admission_id,
    *,
    field_path="content_text",
    display_index=1,
):
    quote = f"quote for {group_id}"
    return {
        "citation_group_id": group_id,
        "display_index": display_index,
        "claim_candidate_id": claim_id,
        "admission_decision_id": admission_id,
        "evidence_refs": [
            {
                "field_path": field_path,
                "quote": quote,
                "text_start": 0,
                "text_end": len(quote),
                "source_text_hash": "a" * 64,
                "canonical_note_id": f"note-{group_id}",
                "source_url": f"https://www.xiaohongshu.com/explore/{group_id}",
            }
        ],
    }


@pytest.mark.asyncio
async def test_lite_reader_rejects_cards_without_matching_admitted_citation_identity(tmp_path):
    report = await _project_formal_cards(
        tmp_path,
        claim_cards=[
            _card("cc_accepted", "cad_accepted", admission_state="accepted"),
            _card("cc_identity_mismatch", "cad_expected"),
        ],
        citation_groups=[
            _citation("citation_accepted", "cc_accepted", "cad_accepted"),
            _citation("citation_mismatch", "cc_identity_mismatch", "cad_other"),
        ],
    )

    assert report["sections"]["main_findings"] == []
    assert report["status_strip"]["admitted_finding_count"] == 0


@pytest.mark.asyncio
async def test_lite_reader_retains_title_backed_message_angle_but_not_raw_product_title(tmp_path):
    report = await _project_formal_cards(
        tmp_path,
        claim_cards=[
            _card("cc_message_angle", "cad_message", claim_type="message_angle"),
            _card("cc_product_value", "cad_value"),
        ],
        citation_groups=[
            _citation("citation_message", "cc_message_angle", "cad_message", field_path="title"),
            _citation("citation_value", "cc_product_value", "cad_value", field_path="title"),
        ],
    )

    assert report["sections"]["main_findings"] == [
        {
            "statement": "statement for cc_message_angle",
            "claim_type": "message_angle",
            "card_kind": "finding",
            "direction": "product_marketing",
            "sample_summary": {"sample": "selected_packets"},
            "scope": {"sample": "selected_packets"},
            "citation_group_ids": ["citation_message"],
        }
    ]
    assert report["status_strip"]["admitted_finding_count"] == 1
    assert report["status_strip"]["observation_count"] == 0


@pytest.mark.asyncio
async def test_lite_reader_retains_formal_content_performance_observation_card(tmp_path):
    report = await _project_formal_cards(
        tmp_path,
        claim_cards=[
            _card(
                "cc_observation",
                "cad_observation",
                direction="content_performance",
                claim_type="observed_high_engagement_sample",
            )
        ],
        citation_groups=[
            _citation("citation_observation", "cc_observation", "cad_observation", field_path="title")
        ],
    )

    assert report["sections"]["main_findings"][0]["card_kind"] == "observation"
    assert report["sections"]["main_findings"][0]["claim_type"] == (
        "observed_high_engagement_sample"
    )
    assert report["status_strip"]["admitted_finding_count"] == 0
    assert report["status_strip"]["observation_count"] == 1


@pytest.mark.asyncio
async def test_lite_reader_does_not_leak_colliding_citation_identity_into_card(tmp_path):
    report = await _project_formal_cards(
        tmp_path,
        claim_cards=[_card("cc_collision", "cad_expected")],
        citation_groups=[
            _citation("citation_expected", "cc_collision", "cad_expected"),
            _citation("citation_other", "cc_collision", "cad_other"),
        ],
    )

    assert report["sections"]["main_findings"][0]["citation_group_ids"] == [
        "citation_expected"
    ]


@pytest.mark.asyncio
async def test_lite_reader_excludes_a_group_with_mixed_note_identity_or_source_url(tmp_path):
    citation = _citation("citation_mixed", "cc_mixed", "cad_mixed")
    citation["evidence_refs"] = [
        {
            **citation["evidence_refs"][0],
            "canonical_note_id": "note-one",
            "source_url": "https://www.xiaohongshu.com/explore/note-one",
        },
        {
            **citation["evidence_refs"][0],
            "canonical_note_id": "note-two",
            "source_url": "https://www.xiaohongshu.com/explore/note-two",
        },
    ]

    report = await _project_formal_cards(
        tmp_path,
        claim_cards=[_card("cc_mixed", "cad_mixed")],
        citation_groups=[citation],
    )

    assert report["citations"] == []
    assert report["sections"]["main_findings"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_url", "navigation_state", "expected_state"),
    [
        (None, None, "missing_source_url"),
        (
            "https://www.xiaohongshu.com/explore/note-one",
            "navigation_unavailable",
            "navigation_unavailable",
        ),
    ],
)
async def test_lite_reader_retains_single_note_groups_without_direct_navigation(
    tmp_path, source_url, navigation_state, expected_state
):
    citation = _citation("citation_one", "cc_one", "cad_one")
    citation["evidence_refs"] = [
        {
            **citation["evidence_refs"][0],
            "canonical_note_id": "note-one",
            "source_url": source_url,
            "navigation_state": navigation_state,
        }
    ]

    report = await _project_formal_cards(
        tmp_path,
        claim_cards=[_card("cc_one", "cad_one")],
        citation_groups=[citation],
    )

    assert report["sections"]["main_findings"][0]["citation_group_ids"] == [
        "citation_one"
    ]
    assert report["citations"][0]["navigation_state"] == expected_state
    assert report["citations"][0]["source_url"] is None


@pytest.mark.asyncio
async def test_lite_reader_rejects_non_template_only_publication(tmp_path):
    db_path = str(tmp_path / "lite-prose-publication.db")
    store = SQLiteContentResearchStore(db_path)
    async with ThreadStore(db_path) as threads:
        thread = await threads.create_thread(title="prose publication")
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id=thread["id"], user_id="user")
    base_snapshot = _snapshot()
    snapshot = replace(base_snapshot, workflow_run_id=run.run_id)
    draft = ResearchReportComposer().compose(snapshot)
    decision = replace(_decision(draft), workflow_run_id=run.run_id)
    publication = replace(_publication(draft, decision), workflow_run_id=run.run_id)
    store.save_result_snapshot(snapshot)
    store.save_report_draft(draft.to_record())
    store.save_report_faithfulness_decision(decision.to_record())
    store.save_report_publication(publication.to_record())
    async with WorkflowRunManager(db_path) as manager:
        await manager.complete_run(run.run_id)
    await ReportPublicationMaterializer(store, db_path).materialize(publication.id)

    with pytest.raises(PublishedReportNotFoundError, match="unsupported compose mode"):
        await LiteReportReader(store, db_path).read(workflow_run_id=run.run_id)


@pytest.mark.asyncio
async def test_lite_reader_reads_every_frozen_citation_group_in_publication_order(tmp_path):
    report = await _project_formal_cards(
        tmp_path,
        claim_cards=[_card(f"cc_{index:03d}", f"cad_{index:03d}") for index in range(1, 52)],
        citation_groups=[
            _citation(
                f"citation_{index:03d}",
                f"cc_{index:03d}",
                f"cad_{index:03d}",
                display_index=index,
            )
            for index in range(1, 52)
        ],
    )

    expected_group_ids = [f"citation_{index:03d}" for index in range(1, 52)]
    assert [item["citation_group_id"] for item in report["citations"]] == expected_group_ids
    assert [
        item["citation_group_ids"][0] for item in report["sections"]["main_findings"]
    ] == expected_group_ids
    assert report["status_strip"]["admitted_finding_count"] == 51


@pytest.mark.asyncio
async def test_lite_reader_retains_only_weak_signals_with_matching_frozen_identity(tmp_path):
    report = await _project_formal_cards(
        tmp_path,
        claim_cards=[
            _card("cc_valid", "cad_valid", admission_state="accepted"),
            _card("cc_bad_admission", "cad_expected", admission_state="accepted"),
            _card("cc_other", "cad_bad_claim", admission_state="accepted"),
        ],
        citation_groups=[
            _citation("citation_valid", "cc_valid", "cad_valid"),
            _citation("citation_wrong_admission", "cc_bad_admission", "cad_other"),
            _citation("citation_wrong_claim", "cc_other", "cad_bad_claim"),
        ],
        weak_signals=[
            {
                "weak_signal_id": "ws_valid",
                "claim_candidate_id": "cc_valid",
                "admission_decision_id": "cad_valid",
                "direction_id": "product_marketing",
                "reason": "valid weak signal",
            },
            {
                "weak_signal_id": "ws_bad_admission",
                "claim_candidate_id": "cc_bad_admission",
                "admission_decision_id": "cad_expected",
                "direction_id": "product_marketing",
                "reason": "wrong admission",
            },
            {
                "weak_signal_id": "ws_bad_claim",
                "claim_candidate_id": "cc_bad_claim",
                "admission_decision_id": "cad_bad_claim",
                "direction_id": "product_marketing",
                "reason": "wrong claim",
            },
        ],
    )

    assert report["sections"]["main_findings"] == []
    assert report["sections"]["weak_signals"] == [
        {
            "statement": "valid weak signal",
            "direction": "product_marketing",
            "sample_summary": None,
            "qualification_reason": "valid weak signal",
            "citation_group_ids": ["citation_valid"],
        }
    ]
    assert report["status_strip"] == {
        "completed_direction_count": 0,
        "admitted_finding_count": 0,
        "observation_count": 0,
        "lead_count": 1,
    }


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
