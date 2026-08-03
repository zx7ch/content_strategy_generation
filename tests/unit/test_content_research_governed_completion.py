from __future__ import annotations

import pytest

from app.content_research.admission.candidates import build_claim_candidate, extract_facts
from app.content_research.contracts import build_default_snapshot
from app.content_research.models import (
    ObservationEventRecord,
    ResearchBriefRecord,
    ResearchDirectionRecord,
    ResearchPlanRecord,
    SubagentTaskRecord,
    TraceRecord,
    utcnow,
)
from app.content_research.persistence_models import (
    AggregateClaimRecord,
    CanonicalSourceRecord,
    ClaimAdmissionDecisionRecord,
    CrossDirectionRecord,
    DirectionalEvidencePacketRecord,
    DirectionResultDecisionRecord,
    StageCheckpointRecord,
    WeakSignalRecord,
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


@pytest.mark.asyncio
async def test_formal_completion_without_a_live_creator_run_reports_not_published(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "governed-completion.db"))
    snapshot, policies, contracts = build_default_snapshot(
        snapshot_id="rps_governed", workflow_run_id="run_governed", brief_id="rb_governed", plan_id="rp_governed",
    )
    store.save_run_policy_snapshot(snapshot)
    for policy in policies:
        store.save_sample_policy(policy)
    for contract in contracts:
        store.save_direction_contract(contract)
    brief = ResearchBriefRecord(
        id="rb_governed", workflow_run_id="run_governed", thread_id="thread_governed",
        schema_version="v1", status="confirmed", payload={"schema_version": "v1"},
    )
    store.save_brief(brief)
    store.save_plan(ResearchPlanRecord(
        id="rp_governed", brief_id=brief.id, workflow_run_id=brief.workflow_run_id,
        thread_id=brief.thread_id, schema_version="v1", status="confirmed",
        payload={"schema_version": "v1"},
    ))
    task = SubagentTaskRecord(
        id="sat_governed", workflow_run_id="run_governed", thread_id="thread_governed", schema_version="v1",
        status="partial_completed", plan_id="rp_governed", direction_id="product_marketing",
        payload={
            "schema_version": "v1",
            "workflow_child_task_id": "child_governed",
            "output_payload": {"metadata": {"packet_ids": ["dep_note_1"]}},
        },
    )
    store.save_subagent_task(task)
    result = DirectionResultDecisionRecord(
        "drd_governed", "v1", {"state": "insufficient_evidence", "admitted_claim_ids": [], "weak_signal_ids": []},
        research_direction_id="product_marketing", policy_snapshot_id=snapshot.id,
    )
    store.save_direction_result_decision(result)
    runtime = CapturingRuntime()
    service = ContentResearchService(store=store, presearch=None, workflow_runtime=runtime)

    await service._execute_formal_research(brief=brief, provider="xiaohongshu", source_kind="search", limit=10)

    completion = runtime.completed[0]
    child_refs = completion["task_outcomes"][0]["artifact_refs"]
    assert {item["type"] for item in child_refs} == {
        "content_research_directional_packet", "content_research_direction_result",
    }
    assert {item["type"] for item in completion["artifact_refs"]} == {
        "content_research_directional_packet",
        "content_research_direction_result",
    }
    assert completion["task_outcomes"][0]["status"] == "partial_completed"
    event = runtime.events[0]
    assert event["event_type"] == "formal_research_governed_completed"
    assert event["payload"]["workflow_execution_state"] == "completed"
    assert event["payload"]["publication_state"] == "not_published"
    assert event["payload"]["governance_replayed"] is False
    assert {item.stage_name for item in store.list_typed_records(StageCheckpointRecord)} == {"reconcile", "aggregate"}

    await service._execute_formal_research(brief=brief, provider="xiaohongshu", source_kind="search", limit=10)

    assert runtime.events[-1]["payload"]["governance_replayed"] is True
    checkpoints = [item.stage_name for item in store.list_typed_records(StageCheckpointRecord)]
    assert checkpoints.count("reconcile") == checkpoints.count("aggregate") == 1


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
