from app.content_research.admission.evaluator import ClaimAdmissionEvaluator
from app.content_research.contracts import build_default_snapshot
from app.content_research.persistence_models import (
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
)


def _inputs(*, claim_type="product_value_expression", available=True):
    snapshot, policies, contracts = build_default_snapshot(snapshot_id="rps_1", workflow_run_id="run_1", brief_id="rb_1", plan_id="rp_1")
    contract = next(item for item in contracts if item.direction_id == "product_marketing")
    availability = {field: "present" for field in contract.required_note_fields} if available else {}
    packet = DirectionalEvidencePacketRecord("dep_1", "v1", {"field_availability": availability}, workflow_run_id="run_1", research_direction_id="product_marketing", canonical_source_id="cs_1", field_projection_hash="packet-hash")
    candidate = ClaimCandidateRecord("cc_1", "v2", {"scope": {"sample": "selected_packets"}, "quote_refs": [{"field_path": "content_text"}]}, workflow_run_id="run_1", research_direction_id="product_marketing", evidence_packet_id="dep_1", statement="样本表达", intent_id="value_proposition", claim_type=claim_type)
    return candidate, packet, contract, next(item for item in policies if item.direction_id == "product_marketing"), snapshot


def test_admission_is_deterministic_and_records_recomputed_metrics():
    result = ClaimAdmissionEvaluator().evaluate(candidate=_inputs()[0], packet=_inputs()[1], contract=_inputs()[2], sample_policy=_inputs()[3], policy_snapshot=_inputs()[4], selected_source_count=10, independent_author_count=10)
    assert result.record.decision == "admitted"
    assert result.record.payload["claim_evidence_state"] == "repeated_observation"
    assert result.record.payload["decision_fingerprint"] == result.fingerprint


def test_admission_rejects_prohibited_type_and_downgrades_missing_evidence():
    candidate, packet, contract, policy, snapshot = _inputs(claim_type="causal_claim")
    assert ClaimAdmissionEvaluator().evaluate(candidate=candidate, packet=packet, contract=contract, sample_policy=policy, policy_snapshot=snapshot, selected_source_count=1, independent_author_count=1).record.decision == "rejected"
    candidate, packet, contract, policy, snapshot = _inputs(available=False)
    result = ClaimAdmissionEvaluator().evaluate(candidate=candidate, packet=packet, contract=contract, sample_policy=policy, policy_snapshot=snapshot, selected_source_count=0, independent_author_count=0)
    assert result.record.decision == "downgraded"
    assert "blocking_field_missing" in result.record.payload["reason_codes"]
