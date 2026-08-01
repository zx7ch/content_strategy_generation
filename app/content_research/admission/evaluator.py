"""Deterministic, packet-only claim admission."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.content_research.admission.candidates import validate_candidate_packet
from app.content_research.admission.registry import DEFAULT_ADMISSION_STRATEGIES
from app.content_research.admission.relevance import query_relevance_reason
from app.content_research.contracts import (
    ADMISSION_ALGORITHM_VERSION,
    DirectionContract,
    RunPolicySnapshot,
    SamplePolicy,
    frozen_query_relevance,
)
from app.content_research.persistence_models import (
    ClaimAdmissionDecisionRecord,
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
)

ALGORITHM_VERSION = ADMISSION_ALGORITHM_VERSION


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class AdmissionResult:
    record: ClaimAdmissionDecisionRecord
    fingerprint: str


def admission_identity_payload(
    *,
    candidate: ClaimCandidateRecord,
    packet: DirectionalEvidencePacketRecord,
    contract: DirectionContract,
    sample_policy: SamplePolicy,
    policy_snapshot: RunPolicySnapshot,
    relevance_contract: dict[str, Any] | None,
    admission_packet_identities: tuple[tuple[str, str], ...],
    selected_source_count: int,
    relevance_qualified_source_count: int,
    eligible_source_count: int,
    independent_author_count: int,
) -> dict[str, Any]:
    """Return every frozen input that can change an admission outcome."""
    return {
        "candidate": candidate.id,
        "candidate_packet": {
            "id": packet.id,
            "field_projection_hash": packet.field_projection_hash,
        },
        "admission_packets": [
            {"id": packet_id, "field_projection_hash": packet_hash}
            for packet_id, packet_hash in sorted(admission_packet_identities)
        ],
        "policy_snapshot_id": policy_snapshot.id,
        "policy_snapshot_hash": policy_snapshot.effective_policy_hash,
        "contract": {
            "id": contract.id,
            "schema_version": contract.schema_version,
        },
        "sample_policy": {
            "id": sample_policy.id,
            "schema_version": sample_policy.schema_version,
            "minimum_samples": sample_policy.minimum_samples,
            "minimum_independent_authors": (
                sample_policy.minimum_independent_authors
            ),
            "author_cap": sample_policy.author_cap,
            "metadata": sample_policy.metadata,
        },
        "metrics": {
            "selected_source_count": selected_source_count,
            "relevance_qualified_source_count": relevance_qualified_source_count,
            "eligible_source_count": eligible_source_count,
            "independent_author_count": independent_author_count,
        },
        "relevance_contract": relevance_contract,
        "relevance_algorithm_version": (
            relevance_contract.get("algorithm_version")
            if relevance_contract
            else None
        ),
        "admission_algorithm_version": ALGORITHM_VERSION,
    }


class ClaimAdmissionEvaluator:
    def evaluate(
        self,
        *,
        candidate: ClaimCandidateRecord,
        packet: DirectionalEvidencePacketRecord,
        contract: DirectionContract,
        sample_policy: SamplePolicy,
        policy_snapshot: RunPolicySnapshot,
        selected_source_count: int,
        relevance_qualified_source_count: int,
        eligible_source_count: int,
        independent_author_count: int,
        admission_packet_identities: tuple[tuple[str, str], ...],
    ) -> AdmissionResult:
        validate_candidate_packet(candidate, packet)
        if candidate.research_direction_id != contract.direction_id:
            raise ValueError("candidate and contract direction differ")
        if (
            policy_snapshot.effective_policy.get("admission_algorithm_version")
            != ALGORITHM_VERSION
        ):
            raise ValueError("run policy admission algorithm version is not current")
        packet_identity = (packet.id, packet.field_projection_hash)
        if packet_identity not in admission_packet_identities:
            raise ValueError("candidate packet is absent from admission packet identities")
        relevance = frozen_query_relevance(contract, policy_snapshot)
        relevance_qualified_count = relevance_qualified_source_count
        eligible_count = eligible_source_count
        fingerprint = _hash(
            admission_identity_payload(
                candidate=candidate,
                packet=packet,
                contract=contract,
                sample_policy=sample_policy,
                policy_snapshot=policy_snapshot,
                relevance_contract=relevance,
                admission_packet_identities=admission_packet_identities,
                selected_source_count=selected_source_count,
                relevance_qualified_source_count=relevance_qualified_source_count,
                eligible_source_count=eligible_source_count,
                independent_author_count=independent_author_count,
            )
        )
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
        relevance_reason = query_relevance_reason(
            candidate=candidate,
            packet=packet,
            contract=contract,
            policy_snapshot=policy_snapshot,
        )
        if relevance_reason:
            reasons.append(relevance_reason)
        if missing:
            reasons.append("blocking_field_missing")
        if (
            eligible_count < sample_policy.minimum_samples
            or independent_author_count
            < sample_policy.minimum_independent_authors
        ):
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
                    "query_subject_not_supported",
                }
                for reason in reasons
            ) else "downgraded"
            state = "insufficient_evidence" if "sample_threshold_unmet" in reasons else "provisional"
        else:
            decision, state = (
                "admitted",
                "repeated_observation" if eligible_count > 1 else "case_level",
            )
        projection = dict(packet.payload.get("field_projection") or {})
        payload = {
            "schema_version": "content_research_claim_admission_decision_v1",
            "decision_fingerprint": fingerprint,
            "algorithm_version": ALGORITHM_VERSION,
            "claim_evidence_state": state,
            "reason_codes": reasons,
            "computed_metrics": {
                "selected_source_count": selected_source_count,
                "relevance_qualified_source_count": relevance_qualified_count,
                "eligible_source_count": eligible_count,
                "independent_author_count": independent_author_count,
                "author_id": str(projection.get("author_id") or ""),
                "missing_required_fields": missing,
            },
            "evidence_refs": [packet.id],
            "required_disclosures": (
                ["sample_threshold_unmet"]
                if "sample_threshold_unmet" in reasons
                else []
            ),
            "recovery_action": (
                "collect_required_fields"
                if "blocking_field_missing" in reasons
                else (
                    "collect_more_independent_samples"
                    if "sample_threshold_unmet" in reasons
                    else None
                )
            ),
            "policy_snapshot_hash": policy_snapshot.effective_policy_hash,
        }
        record = ClaimAdmissionDecisionRecord("cad_" + fingerprint[:24], "content_research_claim_admission_decision_v1", payload, research_direction_id=candidate.research_direction_id, claim_candidate_id=candidate.id, decision=decision, policy_snapshot_id=policy_snapshot.id)
        return AdmissionResult(record, fingerprint)
