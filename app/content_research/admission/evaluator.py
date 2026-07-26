"""Deterministic, packet-only claim admission."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.content_research.admission.registry import DEFAULT_ADMISSION_STRATEGIES
from app.content_research.contracts import DirectionContract, RunPolicySnapshot, SamplePolicy
from app.content_research.persistence_models import (
    ClaimAdmissionDecisionRecord,
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
)

ALGORITHM_VERSION = "claim_admission_v1"


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class AdmissionResult:
    record: ClaimAdmissionDecisionRecord
    fingerprint: str


class ClaimAdmissionEvaluator:
    def evaluate(self, *, candidate: ClaimCandidateRecord, packet: DirectionalEvidencePacketRecord, contract: DirectionContract, sample_policy: SamplePolicy, policy_snapshot: RunPolicySnapshot, selected_source_count: int, independent_author_count: int) -> AdmissionResult:
        if (candidate.workflow_run_id, candidate.research_direction_id) != (packet.workflow_run_id, packet.research_direction_id):
            raise ValueError("candidate and packet must share workflow run and direction")
        if candidate.research_direction_id != contract.direction_id:
            raise ValueError("candidate and contract direction differ")
        fingerprint = _hash({"candidate": candidate.id, "packet": packet.field_projection_hash, "policy": policy_snapshot.effective_policy_hash, "contract": contract.schema_version, "algorithm": ALGORITHM_VERSION})
        availability = dict(packet.payload.get("field_availability") or {})
        scope = dict(candidate.payload.get("scope") or {})
        required_fields = (
            contract.required_comment_fields
            if scope.get("sample") == "selected_comment_packets"
            else contract.required_note_fields
        )
        missing = [field for field in required_fields if availability.get(field) != "present"]
        reasons: list[str] = []
        if candidate.claim_type not in contract.claim_rules:
            reasons.append("claim_type_not_allowed")
        strategy = DEFAULT_ADMISSION_STRATEGIES.get(contract.direction_id)
        if strategy is not None:
            boundary_reason = strategy.boundary_reason(candidate)
            if boundary_reason:
                reasons.append(boundary_reason)
        if missing:
            reasons.append("blocking_field_missing")
        if selected_source_count < sample_policy.minimum_samples or independent_author_count < sample_policy.minimum_independent_authors:
            reasons.append("sample_threshold_unmet")
        if reasons:
            decision = "rejected" if any(
                reason in {
                    "claim_type_not_allowed",
                    "product_marketing_evidence_boundary_violation",
                    "content_performance_evidence_boundary_violation",
                    "competitor_discovery_evidence_boundary_violation",
                    "brand_activity_evidence_boundary_violation",
                    "keyword_growth_evidence_boundary_violation",
                    "ugc_comment_sample_insufficient",
                    "comment_insight_evidence_boundary_violation",
                }
                for reason in reasons
            ) else "downgraded"
            state = "insufficient_evidence" if "sample_threshold_unmet" in reasons else "provisional"
        else:
            decision, state = "admitted", "repeated_observation" if selected_source_count > 1 else "case_level"
        payload = {"schema_version": "content_research_claim_admission_decision_v1", "decision_fingerprint": fingerprint, "algorithm_version": ALGORITHM_VERSION, "claim_evidence_state": state, "reason_codes": reasons, "computed_metrics": {"selected_source_count": selected_source_count, "independent_author_count": independent_author_count, "missing_required_fields": missing}, "evidence_refs": [packet.id], "required_disclosures": ["sample_threshold_unmet"] if "sample_threshold_unmet" in reasons else [], "recovery_action": "collect_required_fields" if "blocking_field_missing" in reasons else ("collect_more_independent_samples" if "sample_threshold_unmet" in reasons else None), "policy_snapshot_hash": policy_snapshot.effective_policy_hash}
        record = ClaimAdmissionDecisionRecord("cad_" + fingerprint[:24], "content_research_claim_admission_decision_v1", payload, research_direction_id=candidate.research_direction_id, claim_candidate_id=candidate.id, decision=decision, policy_snapshot_id=policy_snapshot.id)
        return AdmissionResult(record, fingerprint)
