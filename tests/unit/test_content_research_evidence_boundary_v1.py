from app.content_research.decision_policy import DecisionPolicyService, default_evidence_boundary_policy
from app.content_research.evidence.models import EvidenceBundleRecord


def _bundle(**overrides):
    payload = {
        "id": "eb_boundary",
        "workflow_run_id": "wfr_1",
        "schema_version": "content_research_evidence_bundle_v1",
        "status": "ready",
        "bundle_type": "finding",
        "bundle_version": "v1",
        "summary": "Users repeatedly ask whether the jacket wrinkles after being packed.",
        "coverage": {"source_count": 3, "accepted_evidence_count": 3},
        "citation_coverage": {"citation_coverage_score": 0.75},
    }
    payload.update(overrides)
    return EvidenceBundleRecord(**payload)


def test_default_evidence_boundary_policy_has_versioned_contract():
    policy = default_evidence_boundary_policy()

    assert policy.id == "ebp_content_research_default_v1"
    assert policy.policy_version == "evidence_boundary_v1"
    assert policy.states == ("invalid", "case_only", "signal", "partially_supported", "verified")


def test_verified_state_requires_enough_sources_and_citation_coverage():
    card = DecisionPolicyService().build_decision_card(_bundle())

    assert card["evidence"]["state"] == "verified"
    assert card["evidence"]["grade"] == "A"


def test_missing_evidence_downgrades_to_signal():
    card = DecisionPolicyService().build_decision_card(
        _bundle(missing_evidence=[{"reason": "need_comment_details"}])
    )

    assert card["evidence"]["state"] == "signal"
    assert card["evidence"]["grade"] == "C"
    assert card["next_action"]["type"] == "collect_more_evidence"


def test_apply_to_bundle_persists_decision_fields_without_old_mechanism(tmp_path):
    bundle = DecisionPolicyService().apply_to_bundle(_bundle())

    assert bundle.priority_policy_id == "pp_content_research_default_v1"
    assert bundle.evidence_boundary_policy_id == "ebp_content_research_default_v1"
    assert bundle.decision_card["evidence"]["state"] == "verified"
    assert bundle.priority["label"] == "high_priority"
    assert bundle.evidence_grade == "A"
