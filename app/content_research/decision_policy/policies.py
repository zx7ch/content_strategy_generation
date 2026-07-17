"""Versioned priority and evidence-boundary policies."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PriorityPolicy:
    id: str
    schema_version: str
    profile_name: str
    profile_version: str
    scope: str
    top_k: int = 8
    labels: tuple[str, ...] = (
        "high_priority",
        "high_potential_needs_more_evidence",
        "useful_but_lower_priority",
        "evidence_backed_reference",
        "do_not_prioritize",
    )
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceBoundaryPolicy:
    id: str
    schema_version: str
    policy_name: str
    policy_version: str
    scope: str
    minimum_evidence_count: int = 1
    minimum_verified_evidence_count: int = 3
    minimum_independent_source_count: int = 2
    required_citation_coverage: float = 0.5
    states: tuple[str, ...] = ("invalid", "case_only", "signal", "partially_supported", "verified")
    forbidden_claims: tuple[str, ...] = ("viral_probability", "purchase_conversion", "causal_content_effect")
    metadata: dict[str, Any] = field(default_factory=dict)


def default_priority_policy() -> PriorityPolicy:
    return PriorityPolicy(
        id="pp_content_research_default_v1",
        schema_version="content_research_priority_policy_v1",
        profile_name="content_research_priority",
        profile_version="priority_v1",
        scope="evidence_bundle",
    )


def default_evidence_boundary_policy() -> EvidenceBoundaryPolicy:
    return EvidenceBoundaryPolicy(
        id="ebp_content_research_default_v1",
        schema_version="content_research_evidence_boundary_policy_v1",
        policy_name="content_research_evidence_boundary",
        policy_version="evidence_boundary_v1",
        scope="evidence_bundle",
    )
