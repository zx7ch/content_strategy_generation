from app.content_research.admission.results import build_direction_result
from app.content_research.persistence_models import ClaimAdmissionDecisionRecord


def _decision(id: str, decision: str) -> ClaimAdmissionDecisionRecord:
    return ClaimAdmissionDecisionRecord(id, "v1", {"reason_codes": ["sample_threshold_unmet"], "recovery_action": "collect_more_independent_samples"}, research_direction_id="product_marketing", claim_candidate_id=f"cc_{id}", decision=decision, policy_snapshot_id="rps_1")


def test_direction_result_only_exposes_admitted_claims_and_retains_weak_signals():
    output = build_direction_result(direction_id="product_marketing", policy_snapshot_id="rps_1", decisions=[_decision("cad_ok", "admitted"), _decision("cad_low", "downgraded")])
    assert output.direction_result.payload["admitted_claim_ids"] == ["cc_cad_ok"]
    assert output.direction_result.payload["weak_signal_ids"] == [output.weak_signals[0].id]
    assert output.weak_signals[0].payload["recovery_action"] == "collect_more_independent_samples"


def test_no_admitted_claim_produces_insufficient_direction_result():
    output = build_direction_result(direction_id="product_marketing", policy_snapshot_id="rps_1", decisions=[_decision("cad_no", "rejected")])
    assert output.direction_result.payload["state"] == "insufficient_evidence"
