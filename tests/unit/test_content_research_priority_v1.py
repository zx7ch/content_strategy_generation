from app.content_research.decision_policy import DecisionPolicyService, default_priority_policy
from app.content_research.evidence.models import EvidenceBundleRecord


def _bundle(**overrides):
    payload = {
        "id": "eb_priority",
        "workflow_run_id": "wfr_1",
        "schema_version": "content_research_evidence_bundle_v1",
        "status": "ready",
        "bundle_type": "finding",
        "bundle_version": "v1",
        "summary": "Commuting users discuss packability as a practical jacket concern.",
        "coverage": {"source_count": 3, "accepted_evidence_count": 3},
        "citation_coverage": {"citation_coverage_score": 0.8},
        "metadata": {"decision_value": "high"},
    }
    payload.update(overrides)
    return EvidenceBundleRecord(**payload)


def test_default_priority_policy_has_versioned_contract():
    policy = default_priority_policy()

    assert policy.id == "pp_content_research_default_v1"
    assert policy.profile_version == "priority_v1"
    assert "high_potential_needs_more_evidence" in policy.labels


def test_decision_card_returns_priority_label_and_evidence_policy_metadata():
    card = DecisionPolicyService().build_decision_card(_bundle())

    assert card["schema_version"] == "content_research_decision_card_v1"
    assert card["priority"]["label"] == "high_priority"
    assert card["priority"]["priority_policy_id"] == "pp_content_research_default_v1"
    assert card["evidence"]["evidence_boundary_policy_id"] == "ebp_content_research_default_v1"


def test_high_value_thin_evidence_is_promising_signal_not_conclusion():
    card = DecisionPolicyService().build_decision_card(
        _bundle(
            coverage={"source_count": 1, "accepted_evidence_count": 1},
            citation_coverage={"citation_coverage_score": 0.8},
        )
    )

    assert card["evidence"]["state"] == "case_only"
    assert card["priority"]["label"] == "high_potential_needs_more_evidence"
    assert "Cannot predict viral probability." in card["claim_scope"]["not_allowed"]
