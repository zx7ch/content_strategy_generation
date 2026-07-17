"""Deterministic Decision Card generation for Content Research."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.content_research.decision_policy.policies import (
    EvidenceBoundaryPolicy,
    PriorityPolicy,
    default_evidence_boundary_policy,
    default_priority_policy,
)
from app.content_research.evidence.models import EvidenceBundleRecord
from app.content_research.models import utcnow
from app.content_research.stores.base import ContentResearchStore


class DecisionPolicyService:
    def __init__(
        self,
        store: ContentResearchStore | None = None,
        *,
        priority_policy: PriorityPolicy | None = None,
        evidence_boundary_policy: EvidenceBoundaryPolicy | None = None,
    ) -> None:
        self._store = store
        self.priority_policy = priority_policy or default_priority_policy()
        self.evidence_boundary_policy = evidence_boundary_policy or default_evidence_boundary_policy()

    def build_decision_card(self, bundle: EvidenceBundleRecord) -> dict[str, Any]:
        source_count = _source_count(bundle)
        missing_evidence = _normalize_missing_evidence(bundle.missing_evidence)
        evidence_state = self._evidence_state(bundle, source_count, missing_evidence)
        evidence_grade = _evidence_grade(evidence_state)
        priority_label = self._priority_label(bundle, evidence_state)
        supported_parts, unsupported_parts = _claim_parts(bundle, evidence_state)
        claim_scope = _claim_scope(bundle, evidence_state, unsupported_parts)
        next_action = _next_action(priority_label, evidence_state)
        return {
            "schema_version": "content_research_decision_card_v1",
            "finding_id": f"finding_{bundle.id}",
            "priority": {
                "label": priority_label,
                "rank": None,
                "method": "p1_gate_and_top_k_ordering",
                "priority_policy_id": self.priority_policy.id,
                "priority_policy_version": self.priority_policy.profile_version,
                "reasons": _priority_reasons(bundle, priority_label, evidence_state),
            },
            "evidence": {
                "state": evidence_state,
                "grade": evidence_grade,
                "evidence_boundary_policy_id": self.evidence_boundary_policy.id,
                "evidence_boundary_policy_version": self.evidence_boundary_policy.policy_version,
                "supported_parts": supported_parts,
                "unsupported_parts": unsupported_parts,
                "missing_evidence": missing_evidence,
                "source_count": source_count,
            },
            "claim_scope": claim_scope,
            "next_action": next_action,
        }

    def apply_to_bundle(self, bundle: EvidenceBundleRecord) -> EvidenceBundleRecord:
        card = self.build_decision_card(bundle)
        updated = replace(
            bundle,
            priority_policy_id=self.priority_policy.id,
            evidence_boundary_policy_id=self.evidence_boundary_policy.id,
            decision_card=card,
            priority=card["priority"],
            evidence_state=card["evidence"]["state"],
            evidence_grade=card["evidence"]["grade"],
            claim_scope=card["claim_scope"],
            next_action=card["next_action"],
            updated_at=utcnow(),
        )
        if self._store is not None:
            self._store.save_evidence_bundle(updated)
        return updated

    def _evidence_state(
        self,
        bundle: EvidenceBundleRecord,
        source_count: int,
        missing_evidence: list[dict[str, Any]],
    ) -> str:
        if not bundle.summary.strip() or source_count <= 0:
            return "invalid"
        if bundle.unsupported_claim_count > 0:
            return "partially_supported" if source_count > 0 else "invalid"
        if _has_unresolved_contradiction(bundle):
            return "partially_supported"
        if source_count < self.evidence_boundary_policy.minimum_evidence_count:
            return "invalid"
        if source_count < self.evidence_boundary_policy.minimum_independent_source_count:
            return "case_only"
        if missing_evidence or _citation_coverage(bundle) < self.evidence_boundary_policy.required_citation_coverage:
            return "signal"
        if source_count >= self.evidence_boundary_policy.minimum_verified_evidence_count:
            return "verified"
        return "partially_supported"

    def _priority_label(self, bundle: EvidenceBundleRecord, evidence_state: str) -> str:
        if evidence_state == "invalid":
            return "do_not_prioritize"
        high_decision_value = _decision_value(bundle) == "high"
        if evidence_state in {"verified", "partially_supported"} and high_decision_value:
            return "high_priority"
        if evidence_state in {"signal", "case_only"} and high_decision_value:
            return "high_potential_needs_more_evidence"
        if evidence_state in {"verified", "partially_supported"}:
            return "evidence_backed_reference"
        return "useful_but_lower_priority"


def _source_count(bundle: EvidenceBundleRecord) -> int:
    independent_count = bundle.cross_source_metrics.get("independent_source_count")
    if independent_count is None:
        independent_count = bundle.coverage.get("independent_source_count")
    return int(independent_count or bundle.coverage.get("source_count") or bundle.coverage.get("accepted_evidence_count") or 0)


def _citation_coverage(bundle: EvidenceBundleRecord) -> float:
    value = bundle.citation_coverage.get("citation_coverage_score", bundle.citation_coverage.get("coverage_score", 0.0))
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _has_unresolved_contradiction(bundle: EvidenceBundleRecord) -> bool:
    return bool(bundle.contradiction_summary.get("has_unresolved_contradiction"))


def _decision_value(bundle: EvidenceBundleRecord) -> str:
    value = str(bundle.metadata.get("decision_value") or bundle.metadata.get("actionability") or "").lower()
    if value in {"high", "medium", "low"}:
        return value
    if bundle.metadata.get("next_action") or bundle.metadata.get("actionability_score", 0) in {1, "1", "high"}:
        return "high"
    if _source_count(bundle) >= 2:
        return "high"
    return "medium"


def _evidence_grade(evidence_state: str) -> str:
    return {
        "verified": "A",
        "partially_supported": "B",
        "signal": "C",
        "case_only": "C",
        "invalid": "D",
    }.get(evidence_state, "C")


def _normalize_missing_evidence(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    normalized: list[dict[str, Any]] = []
    for index, value in enumerate(values, start=1):
        if isinstance(value, dict):
            normalized.append(value)
        else:
            normalized.append(
                {
                    "schema_version": "content_research_missing_evidence_v1",
                    "reason": "missing_evidence",
                    "message": str(value),
                    "sequence_no": index,
                }
            )
    return normalized


def _claim_parts(bundle: EvidenceBundleRecord, evidence_state: str) -> tuple[list[str], list[str]]:
    supported = [bundle.summary] if evidence_state != "invalid" and bundle.summary else []
    unsupported: list[str] = []
    if evidence_state in {"signal", "case_only"}:
        unsupported.append("The available evidence does not support treating this signal as a broad trend.")
    if evidence_state == "partially_supported":
        unsupported.append("Stronger causal, conversion, or viral-probability claims are not supported.")
    if bundle.unsupported_claim_count:
        unsupported.append("Some claims in the bundle were marked unsupported.")
    return supported, unsupported


def _claim_scope(bundle: EvidenceBundleRecord, evidence_state: str, unsupported_parts: list[str]) -> dict[str, Any]:
    allowed = ["Use as an evidence-backed finding."] if evidence_state == "verified" else ["Use as a bounded research signal."]
    if evidence_state == "case_only":
        allowed = ["Use as a single example or sample worth inspecting."]
    if evidence_state == "invalid":
        allowed = []
    not_allowed = [
        "Cannot predict viral probability.",
        "Cannot prove purchase conversion lift.",
        "Cannot claim causal content effect.",
        *unsupported_parts,
    ]
    confounders = list(bundle.metadata.get("confounders") or [])
    if "exposure_unknown" not in confounders:
        confounders.append("exposure_unknown")
    return {
        "allowed": allowed,
        "not_allowed": not_allowed,
        "confounders": confounders,
    }


def _next_action(priority_label: str, evidence_state: str) -> dict[str, Any]:
    if priority_label == "high_priority":
        return {
            "type": "content_experiment",
            "proposal": "Move this finding into the next content experiment or brief.",
        }
    if priority_label == "high_potential_needs_more_evidence":
        return {
            "type": "collect_more_evidence",
            "proposal": "Collect more independent notes or comments before treating this as a conclusion.",
        }
    if evidence_state == "invalid":
        return {"type": "do_not_use", "proposal": "Do not use this candidate as a displayed finding."}
    return {"type": "reference", "proposal": "Keep as supporting context for later decisions."}


def _priority_reasons(bundle: EvidenceBundleRecord, priority_label: str, evidence_state: str) -> list[str]:
    reasons = [f"Evidence state is {evidence_state}.", f"Priority label is {priority_label}."]
    if _source_count(bundle) > 0:
        reasons.append(f"Bundle has {_source_count(bundle)} source(s).")
    if bundle.missing_evidence:
        reasons.append("Missing evidence prevents stronger framing.")
    return reasons
