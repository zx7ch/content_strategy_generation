from __future__ import annotations

from dataclasses import replace

import pytest

from app.content_research.admission.candidates import build_claim_candidate, extract_facts
from app.content_research.contracts import build_default_snapshot
from app.content_research.models import (
    ObservationEventRecord,
    ResearchBriefRecord,
    ResearchDirectionRecord,
    SubagentTaskRecord,
    TraceRecord,
    utcnow,
)
from app.content_research.persistence_models import (
    AggregateClaimRecord,
    CanonicalSourceRecord,
    ClaimAdmissionDecisionRecord,
    CoverageManifest,
    CrossDirectionRecord,
    DirectionalEvidencePacketRecord,
    DirectionResultDecisionRecord,
    MarketingConclusionCandidateRecord,
    MarketingConclusionDecisionRecord,
    StageCheckpointRecord,
    WeakSignalRecord,
)
from app.content_research.scope_contract import (
    ScopeConstraint,
    ScopeQueryGroupInput,
    build_scope_contract,
)
from app.content_research.service import ContentResearchService
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore


class CapturingRuntime:
    def __init__(self) -> None:
        self.completed: list[dict] = []
        self.events: list[dict] = []
        self.failed: list[dict] = []

    async def get_runtime_snapshot(self, _workflow_run_id: str) -> dict:
        return {"run_status": "running"}

    async def complete_formal_research(self, **kwargs) -> bool:
        self.completed.append(kwargs)
        return True

    async def append_event(self, **kwargs) -> None:
        self.events.append(kwargs)

    async def fail_formal_research(self, **kwargs) -> dict:
        self.failed.append(kwargs)
        return {"status": "failed", "recoverable": False}


def _authorize_initial_collection(
    store: SQLiteContentResearchStore,
    *,
    workflow_run_id: str,
    research_plan_id: str,
) -> None:
    store.save_scope_contract(
        build_scope_contract(
            workflow_run_id=workflow_run_id,
            research_plan_id=research_plan_id,
            version=1,
            constraints=(
                ScopeConstraint("core_object", "核心对象", "长袖衬衫", "required"),
            ),
            query_groups=(ScopeQueryGroupInput("长袖衬衫", "长袖衬衫"),),
        )
    )


@pytest.mark.asyncio
async def test_formal_execution_reuses_the_existing_workflow_trace(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "formal-trace-reuse.db"))
    brief = ResearchBriefRecord(
        id="rb_trace", workflow_run_id="run_trace", thread_id="thread_trace",
        schema_version="v1", status="confirmed", payload={"schema_version": "v1"},
    )
    task = SubagentTaskRecord(
        id="sat_trace", workflow_run_id=brief.workflow_run_id, thread_id=brief.thread_id,
        schema_version="v1", status="queued", plan_id="rp_trace", direction_id="product_marketing",
        payload={"schema_version": "v1", "workflow_child_task_id": "child_trace"},
    )
    trace = TraceRecord(
        id="trc_trace", workflow_run_id=brief.workflow_run_id, thread_id=brief.thread_id,
        schema_version="v1", status="running", started_at=utcnow(),
        payload={"schema_version": "v1"},
    )
    store.save_brief(brief)
    store.save_subagent_task(task)
    store.save_trace(trace)
    _authorize_initial_collection(
        store,
        workflow_run_id=brief.workflow_run_id,
        research_plan_id=task.plan_id,
    )

    class CapturingRouter:
        def __init__(self) -> None:
            self.trace_ids: list[str | None] = []

        async def execute_task(self, received_task, **kwargs):
            self.trace_ids.append(kwargs.get("trace_id"))
            return replace(
                received_task,
                status="failed",
                payload={
                    **received_task.payload,
                    "output_payload": {"error_message": "synthetic failure"},
                },
            )

    runtime = CapturingRuntime()
    service = ContentResearchService(store=store, presearch=None, workflow_runtime=runtime)
    router = CapturingRouter()
    service._task_router = router

    await service._execute_formal_research(
        brief=brief, provider="xiaohongshu", source_kind="search", limit=10,
    )

    assert router.trace_ids == ["trc_trace"]


@pytest.mark.asyncio
async def test_failed_direction_does_not_run_cross_direction_governance(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "failed-governance.db"))
    brief = ResearchBriefRecord(
        id="rb_failed", workflow_run_id="run_failed", thread_id="thread_failed",
        schema_version="v1", status="confirmed", payload={"schema_version": "v1"},
    )
    store.save_brief(brief)
    store.save_subagent_task(SubagentTaskRecord(
        id="sat_failed", workflow_run_id=brief.workflow_run_id, thread_id=brief.thread_id,
        schema_version="v1", status="failed", plan_id="rp_missing", direction_id="product_marketing",
        payload={"schema_version": "v1", "workflow_child_task_id": "child_failed", "output_payload": {"error_message": "adapter failed"}},
    ))
    _authorize_initial_collection(
        store,
        workflow_run_id=brief.workflow_run_id,
        research_plan_id="rp_missing",
    )
    runtime = CapturingRuntime()
    service = ContentResearchService(store=store, presearch=None, workflow_runtime=runtime)

    await service._execute_formal_research(brief=brief, provider="xiaohongshu", source_kind="search", limit=10)

    assert not store.list_typed_records(StageCheckpointRecord)
    assert runtime.completed[0]["artifact_refs"] == []
    assert runtime.events[0]["event_type"] == "formal_research_needs_retry"


def test_governed_snapshot_partitions_admitted_claims_and_weak_signals(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "governed-snapshot.db"))
    snapshot, policies, contracts = build_default_snapshot(
        snapshot_id="rps_snapshot", workflow_run_id="run_snapshot", brief_id="rb_snapshot", plan_id="rp_snapshot",
    )
    store.save_run_policy_snapshot(snapshot)
    for policy in policies:
        store.save_sample_policy(policy)
    for contract in contracts:
        store.save_direction_contract(contract)
    store.save_canonical_source(CanonicalSourceRecord(
        "cs_snapshot", "v1", {}, platform="xhs", platform_source_kind="note", platform_source_id="note_snapshot",
    ))
    packet = DirectionalEvidencePacketRecord(
        "dep_snapshot", "v1",
        {"field_projection": {"content_text": "轻量透气", "source_url": "https://example/note"}, "retrieval_context": {}},
        workflow_run_id="run_snapshot", research_direction_id="product_marketing", canonical_source_id="cs_snapshot", field_projection_hash="hash_snapshot",
    )
    store.save_directional_evidence_packet(packet)
    fact = extract_facts(packet)[0]
    admitted_candidate = build_claim_candidate(
        workflow_run_id="run_snapshot", direction_id="product_marketing", intent_id="value", claim_type="observation",
        statement="样本提到轻量", scope={"sample": "selected_notes"}, fact=fact, quote="轻量", text_start=0, text_end=2,
    )
    weak_candidate = build_claim_candidate(
        workflow_run_id="run_snapshot", direction_id="product_marketing", intent_id="value", claim_type="observation",
        statement="样本提到透气", scope={"sample": "selected_notes"}, fact=fact, quote="透气", text_start=2, text_end=4,
    )
    store.save_claim_candidate(admitted_candidate)
    store.save_claim_candidate(weak_candidate)
    admitted = ClaimAdmissionDecisionRecord("cad_snapshot_ok", "v1", {"computed_metrics": {"independent_source_count": 1}}, research_direction_id="product_marketing", claim_candidate_id=admitted_candidate.id, decision="admitted", policy_snapshot_id=snapshot.id)
    downgraded = ClaimAdmissionDecisionRecord("cad_snapshot_low", "v1", {"reason_codes": ["sample_threshold_unmet"], "recovery_action": "collect_more"}, research_direction_id="product_marketing", claim_candidate_id=weak_candidate.id, decision="downgraded", policy_snapshot_id=snapshot.id)
    store.save_claim_admission_decision(admitted)
    store.save_claim_admission_decision(downgraded)
    previous_snapshot, _previous_policies, _previous_contracts = (
        build_default_snapshot(
            snapshot_id="rps_previous_policy",
            workflow_run_id="run_previous_policy",
            brief_id="rb_previous_policy",
            plan_id="rp_previous_policy",
        )
    )
    store.save_run_policy_snapshot(previous_snapshot)
    store.save_claim_admission_decision(
        ClaimAdmissionDecisionRecord(
            "cad_snapshot_stale",
            "v1",
            {"computed_metrics": {"eligible_source_count": 99}},
            research_direction_id="product_marketing",
            claim_candidate_id=weak_candidate.id,
            decision="admitted",
            policy_snapshot_id="rps_previous_policy",
        )
    )
    store.save_weak_signal(WeakSignalRecord(
        "ws_snapshot", "v1",
        {
            "reason_codes": ["sample_threshold_unmet"],
            "recovery_actions": ["collect_more"],
            "raw_payload": {"cookie": "must-not-leak"},
        },
        admission_decision_id=downgraded.id,
    ))
    store.save_direction_result_decision(DirectionResultDecisionRecord("drd_snapshot", "v1", {"state": "formal_directional_result", "admitted_claim_ids": [admitted_candidate.id], "limitations": ["sample_threshold_unmet"], "recovery_actions": ["collect_more"]}, research_direction_id="product_marketing", policy_snapshot_id=snapshot.id))
    store.save_cross_direction_record(CrossDirectionRecord(
        "cdr_snapshot", "v1",
        {"workflow_run_id": "run_snapshot", "claim_ids": [admitted_candidate.id], "prompt": "must-not-leak"},
        research_plan_id="rp_snapshot", record_type="overlap",
    ))
    store.save_aggregate_claim(AggregateClaimRecord("ac_snapshot", "v1", {"workflow_run_id": "run_snapshot", "source_claim_ids": [admitted_candidate.id]}, research_plan_id="rp_snapshot", aggregate_type="cross_direction_corroboration"))
    trace = TraceRecord(
        id="trc_snapshot", workflow_run_id="run_snapshot", thread_id="thread", schema_version="v1",
        status="completed", started_at=utcnow(),
        payload={"schema_version": "v1", "token": "must-not-leak"},
    )
    store.save_trace(trace)
    store.append_observation_event(ObservationEventRecord(
        id="obs_snapshot", trace_id=trace.id, workflow_run_id="run_snapshot", thread_id="thread",
        schema_version="v1", status="recorded", sequence_no=1, event_type="admission",
        event_name="admission_completed", timestamp=utcnow(),
        payload={"schema_version": "v1", "raw_payload": "must-not-leak"},
    ))
    service = ContentResearchService(store=store, presearch=None, workflow_runtime=CapturingRuntime())
    direction = ResearchDirectionRecord("rd_snapshot", "rp_snapshot", "run_snapshot", "thread", "v1", "completed", 1, {"schema_version": "v1", "direction_id": "product_marketing"})

    governed = service._build_governed_snapshot(workflow_run_id="run_snapshot", plan_id="rp_snapshot", direction_records=[direction])

    assert governed["publication_state"] == "partial_verified_report"
    assert governed["policy_scope"]["direction_set_version"] == "formal_v1"
    assert governed["policy_scope"]["direction_ids"] == list(snapshot.effective_policy["direction_ids"])
    assert governed["policy_scope"]["report_compose_mode"] == "prose"
    assert [item["claim_candidate_id"] for item in governed["claim_cards"]] == [admitted_candidate.id]
    assert [item["weak_signal_id"] for item in governed["weak_signals"]] == ["ws_snapshot"]
    assert [item["cross_direction_record_id"] for item in governed["cross_direction_records"]] == ["cdr_snapshot"]
    assert [item["aggregate_claim_id"] for item in governed["aggregate_claims"]] == ["ac_snapshot"]
    assert governed["citation_groups"][0]["citation_group_id"] == "citation_1"
    assert governed["citation_groups"][0]["citation_id"] == "citation_1"
    assert governed["citation_groups"][0]["claim_candidate_id"] == admitted_candidate.id
    assert governed["report_section_refs"] == {
        "formal_observations": [admitted_candidate.id],
        "weak_signals": ["ws_snapshot"],
        "cross_direction": ["cdr_snapshot"],
        "aggregate_observations": ["ac_snapshot"],
    }
    assert governed["weak_signals"][0]["recovery_actions"] == ["collect_more"]
    assert "raw_payload" not in governed["weak_signals"][0]
    assert "prompt" not in governed["cross_direction_records"][0]
    assert governed["checkpoint_summary"]["trace_summary"] == {
        "trace_count": 1,
        "trace_ids": ["trc_snapshot"],
        "trace_statuses": ["completed"],
        "observation_event_count": 1,
        "observation_event_types": {"admission": 1},
    }


def test_governed_snapshot_uses_only_manifest_owned_claims_governance_and_checkpoints(
    tmp_path,
) -> None:
    """Workflow-wide reads must not reintroduce an older execution's records."""
    store = SQLiteContentResearchStore(str(tmp_path / "manifest-governed-snapshot.db"))
    policy, policies, contracts = build_default_snapshot(
        snapshot_id="rps_manifest",
        workflow_run_id="run_manifest",
        brief_id="rb_manifest",
        plan_id="rp_manifest",
    )
    store.save_run_policy_snapshot(policy)
    for sample_policy in policies:
        store.save_sample_policy(sample_policy)
    for direction_contract in contracts:
        store.save_direction_contract(direction_contract)
    ownership = {
        "scope_contract_id": "rsc_manifest",
        "execution_unit_id": "seu_current",
        "attempt_no": 1,
        "execution_revision": 2,
    }
    old_ownership = {**ownership, "execution_unit_id": "seu_old", "attempt_no": 0}
    candidates = []
    for suffix, record_ownership in (("current", ownership), ("old", old_ownership)):
        source = CanonicalSourceRecord(
            f"cs_{suffix}",
            "v1",
            {},
            platform="xhs",
            platform_source_kind="note",
            platform_source_id=f"note_{suffix}",
        )
        store.save_canonical_source(source)
        packet = DirectionalEvidencePacketRecord(
            f"dep_{suffix}",
            "v1",
            {
                "field_projection": {
                    "content_text": f"{suffix} claim",
                    "source_url": f"https://example/{suffix}",
                },
                "field_availability": {},
                "retrieval_context": {},
            },
            workflow_run_id="run_manifest",
            research_direction_id="product_marketing",
            canonical_source_id=source.id,
            field_projection_hash=f"hash_{suffix}",
            **record_ownership,
        )
        store.save_directional_evidence_packet(packet)
        candidate = replace(
            build_claim_candidate(
                workflow_run_id="run_manifest",
                direction_id="product_marketing",
                intent_id="value",
                claim_type="observation",
                statement=f"{suffix} statement",
                scope={"sample": "selected_notes"},
                fact=extract_facts(packet)[0],
                quote=suffix,
                text_start=0,
                text_end=len(suffix),
            ),
            **record_ownership,
        )
        store.save_claim_candidate(candidate)
        store.save_claim_admission_decision(
            ClaimAdmissionDecisionRecord(
                f"cad_{suffix}",
                "v1",
                {},
                research_direction_id="product_marketing",
                claim_candidate_id=candidate.id,
                decision="admitted",
                policy_snapshot_id=policy.id,
            )
        )
        candidates.append(candidate)
        store.save_cross_direction_record(
            CrossDirectionRecord(
                f"cdr_{suffix}",
                "v1",
                {"workflow_run_id": "run_manifest", "claim_ids": [candidate.id]},
                research_plan_id="rp_manifest",
                record_type="overlap",
            )
        )
        store.save_aggregate_claim(
            AggregateClaimRecord(
                f"ac_{suffix}",
                "v1",
                {"workflow_run_id": "run_manifest", "source_claim_ids": [candidate.id]},
                research_plan_id="rp_manifest",
                aggregate_type="cross_direction_corroboration",
            )
        )
        if suffix == "old":
            store.save_stage_checkpoint(
                StageCheckpointRecord(
                    "scp_marketing_old",
                    "v1",
                    {},
                    workflow_run_id="run_manifest",
                    subagent_task_id="marketing:old",
                    stage_name="marketing_conclusion",
                    input_fingerprint="fp_old",
                    status="completed",
                    **record_ownership,
                )
            )
        marketing_candidate = MarketingConclusionCandidateRecord(
            f"mcc_{suffix}",
            "v1",
            {"statement": f"{suffix} conclusion", "supporting_claim_ids": [candidate.id]},
            workflow_run_id="run_manifest",
            research_plan_id="rp_manifest",
            track="value",
        )
        store.save_marketing_conclusion_candidate(marketing_candidate)
        store.save_marketing_conclusion_decision(
            MarketingConclusionDecisionRecord(
                f"mcd_{suffix}",
                "v1",
                {"input_fingerprint": f"fp_{suffix}"},
                workflow_run_id="run_manifest",
                research_plan_id="rp_manifest",
                candidate_id=marketing_candidate.id,
                track="value",
                state="selected",
            )
        )

    store.save_stage_checkpoint(
        StageCheckpointRecord(
            "scp_marketing_unmanifested",
            "v1",
            {},
            workflow_run_id="run_manifest",
            subagent_task_id="marketing:unmanifested",
            stage_name="marketing_conclusion",
            input_fingerprint="fp_unmanifested",
            status="completed",
            **ownership,
        )
    )
    manifest = CoverageManifest(
        workflow_run_id="run_manifest",
        packet_ids=("dep_current",),
        checkpoint_ids=(),
        **ownership,
    )
    service = ContentResearchService(
        store=store, presearch=None, workflow_runtime=CapturingRuntime()
    )
    generated_checkpoint = StageCheckpointRecord(
        "scp_marketing_current",
        "v1",
        {},
        workflow_run_id="run_manifest",
        subagent_task_id="marketing:current",
        stage_name="marketing_conclusion",
        input_fingerprint="fp_current",
        status="completed",
        **ownership,
    )
    store.save_stage_checkpoint(generated_checkpoint)
    publication_manifest = service._extend_manifest_with_generated_checkpoints(
        manifest, (generated_checkpoint,)
    )
    direction = ResearchDirectionRecord(
        "rd_manifest",
        "rp_manifest",
        "run_manifest",
        "thread",
        "v1",
        "completed",
        1,
        {"schema_version": "v1", "direction_id": "product_marketing"},
    )

    governed = service._build_governed_snapshot(
        workflow_run_id="run_manifest",
        plan_id="rp_manifest",
        direction_records=[direction],
        manifest=publication_manifest,
    )

    assert [item["claim_candidate_id"] for item in governed["claim_cards"]] == [
        candidates[0].id
    ]
    assert [item["cross_direction_record_id"] for item in governed["cross_direction_records"]] == [
        "cdr_current"
    ]
    assert [item["aggregate_claim_id"] for item in governed["aggregate_claims"]] == [
        "ac_current"
    ]
    assert [item["statement"] for item in governed["marketing_conclusions"]] == [
        "current conclusion"
    ]
    assert [item["checkpoint_id"] for item in governed["checkpoint_summary"]["stages"]] == [
        "scp_marketing_current"
    ]


def test_marketing_checkpoint_extends_manifest_with_snapshot_derived_packets(
    tmp_path,
) -> None:
    store = SQLiteContentResearchStore(str(tmp_path / "analysis-manifest.db"))
    store.save_canonical_source(
        CanonicalSourceRecord(
            "source_1",
            "v1",
            {},
            platform="xhs",
            platform_source_kind="note",
            platform_source_id="note_1",
        )
    )
    ownership = {
        "workflow_run_id": "run_1",
        "scope_contract_id": "scope_1",
        "execution_unit_id": "execution_1",
        "attempt_no": 1,
        "execution_revision": 1,
    }
    packet = DirectionalEvidencePacketRecord(
        "sep_1",
        "content_research_directional_evidence_packet_v2",
        {
            "field_projection": {
                "content_text": "上身凉爽",
                "source_url": "https://example.test/note_1",
            },
            "evidence_snapshot_id": "snapshot_1",
        },
        research_direction_id="product_marketing",
        canonical_source_id="source_1",
        field_projection_hash="hash_1",
        **ownership,
    )
    store.save_directional_evidence_packet(packet)
    checkpoint = StageCheckpointRecord(
        "scp_1",
        "content_research_stage_checkpoint_v1",
        {
            "evidence_snapshot_id": "snapshot_1",
            "projected_packet_ids": [packet.id],
        },
        subagent_task_id="marketing-conclusion:plan_1",
        stage_name="marketing_conclusion",
        input_fingerprint="fingerprint_1",
        status="completed",
        **ownership,
    )
    store.save_stage_checkpoint(checkpoint)
    manifest = CoverageManifest(packet_ids=(), checkpoint_ids=(), **ownership)
    service = ContentResearchService(
        store=store, presearch=None, workflow_runtime=CapturingRuntime()
    )

    extended = service._extend_manifest_with_generated_checkpoints(
        manifest, (checkpoint,)
    )

    assert extended.packet_ids == (packet.id,)
    assert extended.checkpoint_ids == (checkpoint.id,)
