"""Content-performance's visible-format, non-causal candidate boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.content_research.admission.candidates import (
    ExtractedFact,
    build_claim_candidate,
    extract_facts,
)
from app.content_research.admission.strategy import AdmissionStrategy
from app.content_research.persistence_models import (
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
)

CONTENT_PERFORMANCE_CLAIM_INTENTS = {
    "observed_high_engagement_sample": "engagement_cohort",
    "visible_content_format": "content_pattern",
}

_ALLOWED_FIELDS = frozenset({"title", "content_text"})
_PROHIBITED_OUTCOME_TERMS = ("表现更好", "互动更高", "点击", "转化", "因果", "导致", "提升效果")


def _engagement_context(packet: DirectionalEvidencePacketRecord) -> dict[str, Any] | None:
    projection = dict(packet.payload.get("field_projection") or {})
    metrics = projection.get("metrics")
    observed_at = projection.get("metrics_observed_at")
    if not isinstance(metrics, Mapping) or not metrics or not isinstance(observed_at, str) or not observed_at:
        return None
    return {"metrics": dict(metrics), "metrics_observed_at": observed_at}


def build_content_performance_candidate(
    *,
    workflow_run_id: str,
    direction_id: str,
    claim_type: str,
    fact: ExtractedFact,
    engagement_context: Mapping[str, Any],
) -> ClaimCandidateRecord:
    """Build a directly quoted, interaction-contextualized format observation."""
    if direction_id != "content_performance":
        raise ValueError("content-performance factory requires content_performance direction")
    if claim_type not in CONTENT_PERFORMANCE_CLAIM_INTENTS:
        raise ValueError("content-performance claim type is not allowed")
    if fact.field_path not in _ALLOWED_FIELDS:
        raise ValueError("content-performance claim must cite title or content text")
    if not engagement_context.get("metrics") or not engagement_context.get("metrics_observed_at"):
        raise ValueError("content-performance claim requires an observed interaction snapshot")
    if any(term in fact.text for term in _PROHIBITED_OUTCOME_TERMS):
        raise ValueError("content-performance observation cannot claim an interaction effect")
    return build_claim_candidate(
        workflow_run_id=workflow_run_id,
        direction_id=direction_id,
        intent_id=CONTENT_PERFORMANCE_CLAIM_INTENTS[claim_type],
        claim_type=claim_type,
        statement=fact.text,
        scope={"sample": "selected_packets", "engagement_context": dict(engagement_context)},
        fact=fact,
        quote=fact.text,
        text_start=0,
        text_end=len(fact.text),
    )


def build_content_performance_candidates(
    packet: DirectionalEvidencePacketRecord,
) -> list[ClaimCandidateRecord]:
    """Emit only visible format/cohort observations with recorded interaction context."""
    engagement_context = _engagement_context(packet)
    if engagement_context is None:
        return []
    candidates: list[ClaimCandidateRecord] = []
    for fact in extract_facts(packet):
        if fact.field_path not in _ALLOWED_FIELDS:
            continue
        for claim_type in CONTENT_PERFORMANCE_CLAIM_INTENTS:
            try:
                candidates.append(
                    build_content_performance_candidate(
                        workflow_run_id=packet.workflow_run_id,
                        direction_id=packet.research_direction_id,
                        claim_type=claim_type,
                        fact=fact,
                        engagement_context=engagement_context,
                    )
                )
            except ValueError:
                continue
    return candidates


def content_performance_boundary_reason(candidate: ClaimCandidateRecord) -> str | None:
    """Return a stable reason when a candidate exceeds the non-causal boundary."""
    if candidate.claim_type not in CONTENT_PERFORMANCE_CLAIM_INTENTS:
        return "content_performance_evidence_boundary_violation"
    if any(term in candidate.statement for term in _PROHIBITED_OUTCOME_TERMS):
        return "content_performance_evidence_boundary_violation"
    refs = list(candidate.payload.get("quote_refs") or [])
    if len(refs) != 1 or str(refs[0].get("field_path") or "") not in _ALLOWED_FIELDS:
        return "content_performance_evidence_boundary_violation"
    scope = dict(candidate.payload.get("scope") or {})
    context = scope.get("engagement_context")
    if scope.get("sample") != "selected_packets" or not isinstance(context, Mapping):
        return "content_performance_evidence_boundary_violation"
    if not context.get("metrics") or not context.get("metrics_observed_at"):
        return "content_performance_evidence_boundary_violation"
    return None


class ContentPerformanceAdmissionStrategy(AdmissionStrategy):
    def __init__(self) -> None:
        super().__init__("content_performance")

    def build_candidates(self, packet: DirectionalEvidencePacketRecord) -> list[ClaimCandidateRecord]:
        return build_content_performance_candidates(packet)

    def boundary_reason(self, candidate: ClaimCandidateRecord) -> str | None:
        return content_performance_boundary_reason(candidate)


STRATEGY = ContentPerformanceAdmissionStrategy()
