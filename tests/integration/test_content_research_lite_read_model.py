import json
import sqlite3
from dataclasses import replace

import pytest

from app.content_research.bootstrap import _bootstrap_legacy_content_research_schema
from app.content_research.contracts import build_default_snapshot
from app.content_research.migrations import apply_content_research_migrations
from app.content_research.models import ResearchBriefRecord
from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.reporting import read_model as report_read_model
from app.content_research.reporting.composer import ResearchReportComposer
from app.content_research.reporting.lite_read_model import (
    LiteReportReader,
    PublishedReportNotFoundError,
    _direction_states,
)
from app.content_research.reporting.publication_materializer import ReportPublicationMaterializer
from app.content_research.scope_contract import (
    CoverageSnapshot,
    ScopeAuditEvent,
    ScopeConstraint,
    ScopeExecutionAuthorization,
    ScopeExecutionContinuation,
    ScopeQueryGroupInput,
    build_scope_contract,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.services.workflow_run_manager import WorkflowRunManager
from tests.integration.test_content_research_report_store import _decision, _publication
from tests.unit.test_content_research_report_composer import _snapshot


def test_execution_trace_reader_reports_unknown_request_outcome_and_fenced_fact(tmp_path):
    """Trusting mutable unit state must not invent provider success without an outcome fact."""
    db_path = str(tmp_path / "durable-execution-trace.db")
    store = SQLiteContentResearchStore(db_path)
    contract = build_scope_contract(
        workflow_run_id="run_durable_trace",
        research_plan_id="rp_durable_trace",
        version=1,
        constraints=(
            ScopeConstraint("core_object", "核心对象", "长袖衬衫", "required"),
            ScopeConstraint("season", "季节", "夏季", "required"),
        ),
        query_groups=(
            ScopeQueryGroupInput("夏季 长袖衬衫", "夏季 长袖衬衫"),
        ),
    )
    store.save_scope_contract(contract)
    coverage = CoverageSnapshot(
        id="scv_durable_trace",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        state="awaiting_scope_decision",
        constraint_counts={},
        unmet_constraint_ids=("season",),
    )
    store.save_coverage_snapshot(coverage)
    unit, _created = store.resolve_coverage_to_execution_unit_atomically(
        snapshot=coverage,
        decision={"resolution": "generate_limited_report"},
    )
    claim = store.claim_execution_unit(execution_unit_id=unit.id, owner="worker-secret")
    assert claim is not None and claim.lease_token is not None
    assert store.record_provider_request(
        execution_unit_id=unit.id,
        attempt_no=claim.attempt_no,
        lease_token=claim.lease_token,
        payload={
            "provider": "xiaohongshu",
            "provider_operation": "compose_report",
            "query": "secret-query",
            "cookie": "secret-cookie",
        },
    )
    assert not store.record_provider_outcome(
        execution_unit_id=unit.id,
        attempt_no=claim.attempt_no,
        lease_token="stale-token",
        provider_state="succeeded",
        payload={"result_status": "succeeded"},
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE content_research_scope_execution_units SET state='completed' WHERE id=?",
            (unit.id,),
        )
        conn.execute(
            "UPDATE content_research_scope_execution_attempts SET state='completed' "
            "WHERE execution_unit_id=? AND attempt_no=?",
            (unit.id, claim.attempt_no),
        )

    trace = report_read_model.ExecutionTraceReader(store).read(unit.id)

    assert trace["outcome_state"] == "outcome_unknown"
    assert [fact["kind"] for fact in trace["facts"]][-2:] == [
        "provider_request_recorded",
        "lease_fenced",
    ]
    assert [fact["sequence_no"] for fact in trace["facts"]] == list(
        range(1, len(trace["facts"]) + 1)
    )
    assert "secret-query" not in json.dumps(trace)
    assert "secret-cookie" not in json.dumps(trace)


def test_report_composer_carries_frozen_execution_lineage_into_the_draft():
    """Removing any governed lineage field must change the report draft contract."""
    base = _snapshot()
    governed = {
        **base.metadata["governed_snapshot"],
        "execution_lineage": {
            "scope_contract_id": "rsc_frozen",
            "execution_unit_id": "seu_frozen",
            "coverage_snapshot_id": "scv_frozen",
            "successful_attempt_no": 3,
        },
        "scope_contract": {"id": "rsc_frozen", "version": 2},
        "coverage_snapshot": {"id": "scv_frozen", "state": "satisfied"},
        "execution_trace": {
            "execution_unit_id": "seu_frozen",
            "attempt_no": 3,
            "outcome_state": "succeeded",
            "facts": [],
        },
    }
    snapshot = replace(
        base,
        metadata={
            **base.metadata,
            "governed_snapshot": governed,
            "governed_input_fingerprint": "frozen-lineage-fingerprint",
        },
    )

    draft = ResearchReportComposer().compose(snapshot)

    assert (
        draft.scope_contract_id,
        draft.execution_unit_id,
        draft.coverage_snapshot_id,
        draft.attempt_no,
    ) == ("rsc_frozen", "seu_frozen", "scv_frozen", 3)
    assert (
        draft.to_record().scope_contract_id,
        draft.to_record().execution_unit_id,
        draft.to_record().coverage_snapshot_id,
        draft.to_record().attempt_no,
    ) == ("rsc_frozen", "seu_frozen", "scv_frozen", 3)


async def _materialize_completed_publication(store, db_path: str, run_id: str, publication_id: str):
    async with WorkflowRunManager(db_path) as manager:
        await manager.begin_report_finalization(run_id)
    artifact = await ReportPublicationMaterializer(store, db_path).materialize(publication_id)
    async with WorkflowRunManager(db_path) as manager:
        await manager.complete_report_finalization(run_id)
    return artifact


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


def test_lite_direction_states_do_not_present_rejected_candidates_as_direction_failure():
    states = _direction_states(
        {
            "release": {"direction_ids": ["product_marketing"]},
            "run_direction_states": [
                {
                    "direction": "product_marketing",
                    "state": "formal_directional_result",
                    "reason_codes": ["query_subject_not_supported"],
                    "recovery_actions": [],
                }
            ],
        }
    )

    assert states[0] == {
        "direction": "product_marketing",
        "state": "formal_directional_result",
        "reason_code": None,
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
        await _materialize_completed_publication(store, db_path, run.run_id, publication.id)

        reader = LiteReportReader(store, db_path)
        first = await reader.read(workflow_run_id=run.run_id)
        second = await reader.read(workflow_run_id=run.run_id)

        assert first == second
        assert first["publication"]["state"] == publication_state
        assert expected_status_strip in first["status_strip"]
        if publication_state != "evidence_only_report":
            assert first["status_strip"]["completed_direction_count"] == 1
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
    publication_state="complete_verified_report",
    artifact_payload_mutator=None,
    citation_group_ids=None,
    marketing_conclusions=None,
    before_read=None,
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
                    "report_compose_mode": compose_mode,
                    **(
                        {
                            "marketing_conclusion_policy": {
                                "primary_marketing_goal": "content_seeding",
                                "tracks": ["need", "value", "message"],
                                "minimum_notes_per_conclusion": 3,
                                "minimum_independent_authors_per_conclusion": 2,
                                "require_core_and_first_intent_support": True,
                                "maximum_primary_conclusions_per_track": 1,
                            }
                        }
                        if marketing_conclusions is not None
                        else {}
                    ),
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
                **(
                    {"marketing_conclusions": marketing_conclusions}
                    if marketing_conclusions is not None
                    else {}
                ),
                "claim_cards": claim_cards,
                "citation_groups": citation_groups,
            },
        },
    )
    draft = ResearchReportComposer().compose(snapshot)
    decision = replace(_decision(draft), workflow_run_id=run.run_id)
    publication_changes = {
        "workflow_run_id": run.run_id,
        "compose_mode": compose_mode,
        "publication_state": publication_state,
    }
    if publication_state == "partial_verified_report":
        publication_changes["omitted_section_ids"] = ("sec_core",)
    publication = replace(
        _publication(draft, decision, compose_mode=compose_mode),
        **publication_changes,
    )
    store.save_result_snapshot(snapshot)
    store.save_report_draft(draft.to_record())
    store.save_report_faithfulness_decision(decision.to_record())
    store.save_report_publication(publication.to_record())
    artifact = await _materialize_completed_publication(store, db_path, run.run_id, publication.id)
    if artifact_payload_mutator is not None:
        payload = artifact.payload_json
        artifact_payload_mutator(payload)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE workflow_artifacts SET payload_json = ? WHERE artifact_id = ?",
                (json.dumps(payload), artifact.artifact_id),
            )

    if before_read is not None:
        before_read(store, run.run_id)

    return await LiteReportReader(store, db_path).read(
        workflow_run_id=run.run_id,
        citation_group_ids=citation_group_ids,
    )


def _persist_unmet_season_scope(store, workflow_run_id: str, *, authorize_limited: bool) -> None:
    contract = build_scope_contract(
        workflow_run_id=workflow_run_id,
        research_plan_id="rp_limited_scope",
        version=1,
        constraints=(
            ScopeConstraint("core_object", "核心对象", "长袖衬衫", "required"),
            ScopeConstraint("season", "季节", "夏季", "required"),
        ),
        query_groups=(
            ScopeQueryGroupInput("夏季 长袖衬衫", "夏季 长袖衬衫", ("夏季", "长袖衬衫")),
        ),
    )
    store.save_scope_contract(contract)
    snapshot = CoverageSnapshot(
        id="scv_limited_season",
        workflow_run_id=workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=1,
        state="awaiting_scope_decision",
        constraint_counts={
            "core_object": {
                "matched_candidate_count": 2,
                "independent_author_count": 2,
                "required": True,
            },
            "season": {
                "matched_candidate_count": 1,
                "independent_author_count": 1,
                "required": True,
            },
            "_summary": {
                "minimum_samples": 2,
                "minimum_independent_authors": 2,
                "reason_codes": ["required_constraint_coverage_unmet:season"],
            },
        },
        unmet_constraint_ids=("season",),
    )
    store.save_coverage_snapshot(snapshot)
    if authorize_limited:
        authorization = ScopeExecutionAuthorization(
            id="sea_limited_season",
            workflow_run_id=workflow_run_id,
            scope_contract_id=contract.id,
            scope_contract_version=1,
            coverage_snapshot_id=snapshot.id,
            resolution="generate_limited_report",
            execution_revision=2,
            state="authorized_limited_report",
        )
        store.resolve_coverage_and_authorize_execution_atomically(
            snapshot=snapshot,
            authorization=authorization,
            continuation=ScopeExecutionContinuation(
                id="sec_limited_season",
                authorization_id=authorization.id,
                workflow_run_id=workflow_run_id,
                execution_revision=2,
                operation="limited_report",
                supplementary_queries=(),
                state="pending",
            ),
            event=ScopeAuditEvent(
                id="sae_limited_season",
                workflow_run_id=workflow_run_id,
                scope_contract_id=contract.id,
                scope_contract_version=1,
                event_name="coverage_resolved",
                payload={
                    "schema_version": "content_research_scope_audit_event_v1",
                    "coverage_snapshot_id": snapshot.id,
                    "resolution": "generate_limited_report",
                    "source_scope_contract_version": 1,
                    "resulting_scope_contract_version": 1,
                    "report_mode": "limited",
                    "unmet_constraint_ids": ["season"],
                    "constraint_counts": snapshot.constraint_counts,
                },
            ),
        )


@pytest.mark.asyncio
async def test_lite_reader_blocks_published_report_while_scope_resolution_is_pending(tmp_path):
    """Removing the pending-scope authorization guard must make this test fail."""
    with pytest.raises(PublishedReportNotFoundError, match="scope decision is pending"):
        await _project_formal_cards(
            tmp_path,
            claim_cards=[_card("cc_pending", "cad_pending")],
            citation_groups=[_citation("citation_pending", "cc_pending", "cad_pending")],
            before_read=lambda store, run_id: _persist_unmet_season_scope(
                store, run_id, authorize_limited=False
            ),
        )


@pytest.mark.asyncio
async def test_limited_lite_report_projects_exact_season_limitation_and_audit(tmp_path):
    """Dropping the frozen season limitation or its audit must make this test fail."""
    persisted = {}

    def persist_scope(store, run_id):
        _persist_unmet_season_scope(store, run_id, authorize_limited=True)
        persisted.update(store=store, run_id=run_id)

    report = await _project_formal_cards(
        tmp_path,
        claim_cards=[_card("cc_limited", "cad_limited")],
        citation_groups=[_citation("citation_limited", "cc_limited", "cad_limited")],
        before_read=persist_scope,
    )

    assert report["status_strip"]["report_mode"] == "limited"
    assert persisted["store"].list_scope_execution_authorizations(
        persisted["run_id"]
    )[0].state == "authorized_limited_report"
    assert report["frozen_scope"]["scope_contract_version"] == 1
    assert report["frozen_scope"]["unmet_constraint_ids"] == ["season"]
    assert report["frozen_scope"]["constraint_counts"]["season"] == {
        "matched_candidate_count": 1,
        "independent_author_count": 1,
        "required": True,
    }
    limitation = next(
        item
        for item in report["sections"]["limitations_scope"]
        if item.get("constraint_id") == "season"
    )
    assert limitation["constraint_value"] == "夏季"
    assert limitation["matched_candidate_count"] == 1
    assert limitation["minimum_samples"] == 2
    projected_events = [
        event
        for event in persisted["store"].list_scope_audit_events(
            persisted["run_id"], version=1
        )
        if event.event_name == "report_scope_projected"
    ]
    assert len(projected_events) == 1
    assert projected_events[0].payload["report_mode"] == "limited"
    assert projected_events[0].payload["resolution"] == "generate_limited_report"
    assert projected_events[0].payload["unmet_constraint_ids"] == ["season"]
    assert projected_events[0].payload["limitations"] == [limitation]


@pytest.mark.asyncio
async def test_lite_report_read_reconciles_concurrent_projection_event(tmp_path, monkeypatch):
    """Propagating a concurrent deterministic audit duplicate must make this test fail."""
    persisted = {}

    def persist_scope_and_install_race(store, run_id):
        _persist_unmet_season_scope(store, run_id, authorize_limited=True)
        append = store.append_scope_audit_event

        def append_after_competing_reader(event):
            append(event)
            return append(event)

        monkeypatch.setattr(
            store, "append_scope_audit_event", append_after_competing_reader
        )
        persisted.update(store=store, run_id=run_id)

    report = await _project_formal_cards(
        tmp_path,
        claim_cards=[_card("cc_concurrent", "cad_concurrent")],
        citation_groups=[
            _citation("citation_concurrent", "cc_concurrent", "cad_concurrent")
        ],
        before_read=persist_scope_and_install_race,
    )

    assert report["status_strip"]["report_mode"] == "limited"
    assert len(
        [
            event
            for event in persisted["store"].list_scope_audit_events(
                persisted["run_id"], version=1
            )
            if event.event_name == "report_scope_projected"
        ]
    ) == 1


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
async def test_lite_report_projects_only_primary_marketing_conclusion(tmp_path):
    claim_cards = [
        _card(f"cc_need_{index}", f"cad_need_{index}", claim_type="use_context")
        for index in range(1, 4)
    ]
    citation_groups = [
        _citation(
            f"citation_need_{index}",
            f"cc_need_{index}",
            f"cad_need_{index}",
            display_index=index,
        )
        for index in range(1, 4)
    ]
    marketing_conclusions = [
        {
            "track": "need",
            "state": "selected",
            "candidate_id": "mc_need_primary",
            "statement": "高温通勤场景中的凉感需求…",
            "supporting_claim_ids": [f"cc_need_{index}" for index in range(1, 4)],
            "supporting_note_count": 3,
            "independent_author_count": 2,
            "reason_codes": [],
        },
        {
            "track": "need",
            "state": "qualified",
            "candidate_id": "mc_need_secondary",
            "statement": "次要合格结论不应出现在报告中",
            "supporting_claim_ids": [f"cc_need_{index}" for index in range(1, 4)],
            "supporting_note_count": 3,
            "independent_author_count": 2,
            "reason_codes": [],
        },
        {
            "track": "value",
            "state": "directional",
            "candidate_id": "mc_value_directional",
            "statement": "轻薄垂感与凉感材质方向",
            "supporting_claim_ids": ["cc_need_1"],
            "supporting_note_count": 1,
            "independent_author_count": 1,
            "reason_codes": ["conclusion_note_count_unmet", "conclusion_author_count_unmet"],
        },
        {
            "track": "message",
            "state": "no_single_primary_conclusion",
            "candidate_id": None,
            "statement": None,
            "supporting_claim_ids": [],
            "supporting_note_count": 3,
            "independent_author_count": 2,
            "reason_codes": [],
        },
    ]

    report = await _project_formal_cards(
        tmp_path,
        claim_cards=claim_cards,
        citation_groups=citation_groups,
        marketing_conclusions=marketing_conclusions,
    )
    need = report["sections"]["marketing_conclusions"]["need"]

    assert need["state"] == "selected"
    assert need["statement"] == "高温通勤场景中的凉感需求…"
    assert need["supporting_note_count"] == 3
    assert need["independent_author_count"] == 2
    assert need["additional_qualified_count"] == 1
    assert len(need["citation_group_ids"]) == 3
    assert "other_qualified_statements" not in need
    assert set(report["sections"]["marketing_conclusions"]) == {
        "need",
        "value",
        "message",
    }
    value = report["sections"]["marketing_conclusions"]["value"]
    assert value["state"] == "directional"
    assert value["statement"] == "轻薄垂感与凉感材质方向"
    assert value["supporting_note_count"] == 1
    assert value["independent_author_count"] == 1
    assert value["note_gap"] == 2
    assert value["author_gap"] == 1
    assert value["citation_group_ids"] == ["citation_need_1"]
    assert "statement" not in report["sections"]["marketing_conclusions"]["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "publication_state", ["complete_verified_report", "partial_verified_report"]
)
async def test_lite_reader_fails_closed_when_verified_publication_contains_invalid_card_identity(
    tmp_path, publication_state
):
    with pytest.raises(
        PublishedReportNotFoundError, match="governed card identity is invalid"
    ):
        await _project_formal_cards(
            tmp_path,
            publication_state=publication_state,
            claim_cards=[
                _card("cc_accepted", "cad_accepted", admission_state="accepted"),
                _card("cc_identity_mismatch", "cad_expected"),
            ],
            citation_groups=[
                _citation("citation_accepted", "cc_accepted", "cad_accepted"),
                _citation("citation_mismatch", "cc_identity_mismatch", "cad_other"),
            ],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("missing_field", "text_start", "text_end"),
    [
        ("text_start", 0, 10),
        (None, 0, 0),
        (None, 4, 3),
    ],
)
async def test_lite_reader_rejects_missing_or_invalid_frozen_quote_span(
    tmp_path, missing_field, text_start, text_end
):
    citation = _citation("citation_span", "cc_span", "cad_span")

    def corrupt_artifact_span(payload):
        evidence_ref = payload["citation_groups"][0]["evidence_refs"][0]
        evidence_ref["text_start"] = text_start
        evidence_ref["text_end"] = text_end
        if missing_field is not None:
            evidence_ref.pop(missing_field)

    with pytest.raises(
        PublishedReportNotFoundError, match="governed card identity is invalid"
    ):
        await _project_formal_cards(
            tmp_path,
            claim_cards=[_card("cc_span", "cad_span")],
            citation_groups=[citation],
            artifact_payload_mutator=corrupt_artifact_span,
        )


@pytest.mark.asyncio
async def test_lite_reader_retains_title_backed_message_angle_but_not_raw_product_title(tmp_path):
    report = await _project_formal_cards(
        tmp_path,
        claim_cards=[
            _card("cc_message_angle", "cad_message", claim_type="message_angle"),
        ],
        citation_groups=[
            _citation("citation_message", "cc_message_angle", "cad_message", field_path="title"),
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

    with pytest.raises(
        PublishedReportNotFoundError, match="governed card identity is invalid"
    ):
        await _project_formal_cards(
            tmp_path,
            claim_cards=[_card("cc_mixed", "cad_mixed")],
            citation_groups=[citation],
        )


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
    await _materialize_completed_publication(store, db_path, run.run_id, publication.id)

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
async def test_lite_reader_detail_read_validates_all_cards_before_selecting_one_citation_group(
    tmp_path,
):
    report = await _project_formal_cards(
        tmp_path,
        claim_cards=[
            _card("cc_one", "cad_one"),
            _card("cc_two", "cad_two"),
        ],
        citation_groups=[
            _citation("citation_one", "cc_one", "cad_one", display_index=1),
            _citation("citation_two", "cc_two", "cad_two", display_index=2),
        ],
        citation_group_ids=["citation_two"],
    )

    assert [item["citation_group_id"] for item in report["citations"]] == [
        "citation_two"
    ]
    assert [item["statement"] for item in report["sections"]["main_findings"]] == [
        "statement for cc_one",
        "statement for cc_two",
    ]
    assert report["status_strip"]["admitted_finding_count"] == 2


@pytest.mark.asyncio
async def test_lite_reader_retains_only_weak_signals_with_matching_frozen_identity(tmp_path):
    report = await _project_formal_cards(
        tmp_path,
        claim_cards=[
            _card("cc_valid", "cad_valid"),
            _card("cc_bad_admission", "cad_other"),
            _card("cc_other", "cad_bad_claim"),
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

    assert len(report["sections"]["main_findings"]) == 3
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
        "completed_direction_count": 2,
        "admitted_finding_count": 3,
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
    await _materialize_completed_publication(store, db_path, run.run_id, publication.id)
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

    with pytest.raises(PublishedReportNotFoundError, match="artifact is malformed"):
        await LiteReportReader(store, db_path).read(workflow_run_id=run.run_id)


@pytest.mark.asyncio
async def test_lite_reader_rejects_a_malformed_persisted_publication_without_recovery_or_message(
    tmp_path,
):
    db_path = str(tmp_path / "malformed-persisted-publication.db")
    store = SQLiteContentResearchStore(db_path)
    async with ThreadStore(db_path) as threads:
        thread = await threads.create_thread(title="malformed persisted publication")
        async with WorkflowRunManager(db_path) as manager:
            run = await manager.start_run(thread_id=thread["id"], user_id="user")
        snapshot = replace(_snapshot(), workflow_run_id=run.run_id)
        draft = ResearchReportComposer().compose(snapshot)
        decision = replace(_decision(draft), workflow_run_id=run.run_id)
        publication = replace(_publication(draft, decision), workflow_run_id=run.run_id)
        store.save_result_snapshot(snapshot)
        store.save_report_draft(draft.to_record())
        store.save_report_faithfulness_decision(decision.to_record())
        store.save_report_publication(publication.to_record())
        await _materialize_completed_publication(store, db_path, run.run_id, publication.id)
        before_messages = await threads.get_thread_messages(thread["id"])
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "UPDATE content_research_report_publications "
                "SET faithfulness_decision_id = 'missing_decision' WHERE id = ?",
                (publication.id,),
            )
            connection.execute(
                "UPDATE workflow_runs SET status = 'paused' WHERE run_id = ?",
                (run.run_id,),
            )

        with pytest.raises(PublishedReportNotFoundError, match="audit is missing"):
            await LiteReportReader(store, db_path).read(workflow_run_id=run.run_id)
        assert await threads.get_thread_messages(thread["id"]) == before_messages


@pytest.mark.asyncio
async def test_lite_reader_rejects_malformed_or_purged_legacy_publication_without_recovery_or_message(
    tmp_path,
):
    db_path = str(tmp_path / "purged-legacy-publication.db")
    store = SQLiteContentResearchStore(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "DELETE FROM content_research_schema_migrations WHERE version = '0014'"
        )
    async with ThreadStore(db_path) as threads:
        thread = await threads.create_thread(title="purged legacy publication")
        async with WorkflowRunManager(db_path) as manager:
            run = await manager.start_run(thread_id=thread["id"], user_id="user")
        snapshot = replace(_snapshot(), workflow_run_id=run.run_id)
        draft = ResearchReportComposer().compose(snapshot)
        decision = replace(_decision(draft), workflow_run_id=run.run_id)
        publication = replace(_publication(draft, decision), workflow_run_id=run.run_id)
        store.save_result_snapshot(snapshot)
        store.save_report_draft(draft.to_record())
        store.save_report_faithfulness_decision(decision.to_record())
        store.save_report_publication(publication.to_record())
        await _materialize_completed_publication(store, db_path, run.run_id, publication.id)

        apply_content_research_migrations(db_path, _bootstrap_legacy_content_research_schema)

        with pytest.raises(PublishedReportNotFoundError, match="published report not found"):
            await LiteReportReader(SQLiteContentResearchStore(db_path), db_path).read(
                workflow_run_id=run.run_id
            )
        assert await threads.get_thread_messages(thread["id"]) == []
