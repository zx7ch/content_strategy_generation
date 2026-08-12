from dataclasses import replace

from app.content_research.admission.candidates import (
    build_claim_candidate,
    extract_facts,
)
from app.content_research.admission.cross_direction import (
    ActionHypothesisRequest,
    CrossDirectionGovernanceService,
)
from app.content_research.admission.governance_keys import derive_governance_key
from app.content_research.contracts import build_default_snapshot
from app.content_research.persistence_models import (
    CanonicalSourceRecord,
    ClaimAdmissionDecisionRecord,
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
    StageCheckpointRecord,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.content_research.workflow.directional_pipeline import build_packet


def _seed_claim(store, *, run, snapshot_id, direction, source_id, text, decision, scope, claim_type="observation"):
    if store.get_typed_record(CanonicalSourceRecord, source_id) is None:
        store.save_canonical_source(CanonicalSourceRecord(
            source_id, "v1", {"schema_version": "v1"}, platform="xhs",
            platform_source_kind="note", platform_source_id=source_id,
        ))
    is_comment = direction in {"comment_insight", "ugc_community"}
    packet_payload = build_packet(
        direction_id=direction, canonical_source_id=source_id,
        fields={("comment_text" if is_comment else "content_text"): text, "source_url": f"https://example/{source_id}"},
        availability={("comment_text" if is_comment else "content_text"): "present"},
        retrieval_context={"source_kind": "comment" if is_comment else "note_detail", **({"parent_note_canonical_source_id": source_id} if is_comment else {})},
    )
    packet_id = f"dep_{direction}_{source_id}"
    packet = DirectionalEvidencePacketRecord(
        packet_id, "v1", packet_payload, workflow_run_id=run,
        research_direction_id=direction, canonical_source_id=source_id,
        field_projection_hash=packet_payload["field_projection_hash"],
    )
    store.save_directional_evidence_packet(packet)
    fact = extract_facts(packet)[0]
    candidate = build_claim_candidate(
        workflow_run_id=run, direction_id=direction, intent_id="observation",
        claim_type=claim_type, statement=text,
        scope={**({"parent_note_canonical_source_id": source_id} if is_comment else {}), **scope}, fact=fact,
        quote=text, text_start=0, text_end=len(text),
    )
    store.save_claim_candidate(candidate)
    admission = ClaimAdmissionDecisionRecord(
        f"cad_{candidate.id}", "v1", {"schema_version": "v1", "reason_codes": []},
        research_direction_id=direction, claim_candidate_id=candidate.id,
        decision=decision, policy_snapshot_id=snapshot_id,
    )
    store.save_claim_admission_decision(admission)
    return candidate


def test_cross_direction_governance_is_run_scoped_append_only_and_replayable(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "governance.db"))
    snapshot, _, _ = build_default_snapshot(
        snapshot_id="rps", workflow_run_id="run", brief_id="brief", plan_id="plan",
    )
    store.save_run_policy_snapshot(snapshot)
    first = _seed_claim(
        store, run="run", snapshot_id=snapshot.id, direction="product_marketing",
        source_id="cs_shared", text="适合通勤", decision="admitted",
        scope={"aggregate_key": "commute", "reconciliation_key": "fit", "reconciliation_polarity": "positive"},
    )
    _seed_claim(
        store, run="run", snapshot_id=snapshot.id, direction="content_performance",
        source_id="cs_shared", text="通勤格式", decision="admitted",
        scope={"aggregate_key": "commute"},
    )
    third = _seed_claim(
        store, run="run", snapshot_id=snapshot.id, direction="comment_insight",
        source_id="cs_other", text="comfort 不好用", decision="admitted", claim_type="objection_or_failure",
        scope={},
    )
    _seed_claim(
        store, run="run", snapshot_id=snapshot.id, direction="ugc_community",
        source_id="cs_positive", text="comfort 很好", decision="admitted", claim_type="sampled_language",
        scope={},
    )
    _seed_claim(
        store, run="run", snapshot_id=snapshot.id, direction="brand_activity",
        source_id="cs_rejected", text="ignored", decision="rejected", scope={"aggregate_key": "commute"},
    )
    other_snapshot, _, _ = build_default_snapshot(
        snapshot_id="rps-other", workflow_run_id="other-run", brief_id="brief-other", plan_id="other-plan",
    )
    store.save_run_policy_snapshot(other_snapshot)
    _seed_claim(
        store, run="other-run", snapshot_id=other_snapshot.id, direction="product_marketing",
        source_id="cs_other_run", text="other", decision="admitted", scope={"aggregate_key": "commute"},
    )

    service = CrossDirectionGovernanceService(store)
    output = service.execute(
        workflow_run_id="run", research_plan_id="plan", subagent_task_id="governance",
        action_hypotheses=[ActionHypothesisRequest("测试通勤呈现方式。", (first.id, third.id))],
    )

    assert not output.replayed
    assert len(output.overlaps) == 1
    assert output.overlaps[0].payload["canonical_source_ids"] == ["cs_shared"]
    assert "governance_keys" not in output.overlaps[0].payload
    assert len(output.contradictions) == 1
    assert output.contradictions[0].payload["governance_keys"][0]["governance_key_version"] == "content_research_governance_keys_v1"
    assert output.contradictions[0].payload["governance_keys"][0]["literal_evidence_ref"]["source_url"].startswith("https://example/")
    assert {item.aggregate_type for item in output.aggregates} == {
        "cross_direction_corroboration", "cross_direction_tension", "action_hypothesis",
    }
    corroboration = next(item for item in output.aggregates if item.aggregate_type == "cross_direction_corroboration")
    assert corroboration.payload["canonical_source_ids"] == ["cs_other", "cs_positive"]
    assert corroboration.payload["governance_keys"][0]["literal_evidence_ref"]["source_text_hash"]
    hypothesis = next(item for item in output.aggregates if item.aggregate_type == "action_hypothesis")
    assert hypothesis.payload["hypothesis_only"] is True

    replay = service.execute(
        workflow_run_id="run", research_plan_id="plan", subagent_task_id="governance",
        action_hypotheses=[ActionHypothesisRequest("测试通勤呈现方式。", (first.id, third.id))],
    )
    assert replay.replayed
    checkpoints = [item.stage_name for item in store.list_typed_records(StageCheckpointRecord)]
    assert checkpoints.count("reconcile") == checkpoints.count("aggregate") == 1
    assert len(store.list_typed_records(ClaimCandidateRecord)) == 6


def test_governance_never_turns_same_source_or_non_polar_claims_into_contradictions(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "governance-negative.db"))
    snapshot, _, _ = build_default_snapshot(snapshot_id="rps_negative", workflow_run_id="run_negative", brief_id="brief", plan_id="plan")
    store.save_run_policy_snapshot(snapshot)
    _seed_claim(store, run="run_negative", snapshot_id=snapshot.id, direction="comment_insight", source_id="cs_same", text="comfort 不好用", decision="admitted", claim_type="objection_or_failure", scope={})
    _seed_claim(store, run="run_negative", snapshot_id=snapshot.id, direction="ugc_community", source_id="cs_same", text="comfort 很好", decision="admitted", claim_type="sampled_language", scope={})
    _seed_claim(store, run="run_negative", snapshot_id=snapshot.id, direction="comment_insight", source_id="cs_other", text="comfort？", decision="admitted", claim_type="explicit_question", scope={})

    output = CrossDirectionGovernanceService(store).execute(workflow_run_id="run_negative", research_plan_id="plan", subagent_task_id="governance")

    assert len(output.overlaps) == 1
    assert output.contradictions == ()


def test_governance_key_rejects_missing_quote_and_legacy_scope_injection(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "key-negative.db"))
    snapshot, _, _ = build_default_snapshot(snapshot_id="rps_key", workflow_run_id="run_key", brief_id="brief", plan_id="plan")
    store.save_run_policy_snapshot(snapshot)
    candidate = _seed_claim(store, run="run_key", snapshot_id=snapshot.id, direction="product_marketing", source_id="cs_key", text="comfort", decision="admitted", scope={"aggregate_key": "forged", "reconciliation_key": "forged", "reconciliation_polarity": "positive"})

    assert derive_governance_key(candidate, snapshot.effective_policy) is None
    assert derive_governance_key(replace(candidate, payload={**candidate.payload, "quote_refs": []}), snapshot.effective_policy) is None
