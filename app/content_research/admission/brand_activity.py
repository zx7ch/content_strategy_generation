"""Brand-activity's dated, directly quoted signal boundary."""

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

BRAND_ACTIVITY_CLAIM_INTENTS = {
    "campaign_signal": "activity_identification",
    "launch_signal": "activity_identification",
    "collaboration_signal": "activity_identification",
    "dissemination_signal": "dissemination_expression",
}
_ALLOWED_FIELDS = frozenset({"title", "content_text", "tags"})
_PROHIBITED_TERMS = ("触达", "销量", "销售提升", "活动成功", "带来增长", "因果")


def _context(packet: DirectionalEvidencePacketRecord) -> dict[str, Any] | None:
    projection = dict(packet.payload.get("field_projection") or {})
    signals, published_at, metrics, observed_at = (
        projection.get("activity_signals"), projection.get("source_published_at"),
        projection.get("metrics"), projection.get("metrics_observed_at"),
    )
    if (
        not isinstance(signals, list)
        or not all(signal in BRAND_ACTIVITY_CLAIM_INTENTS for signal in signals)
        or not isinstance(published_at, str) or not published_at
        or not isinstance(metrics, Mapping) or not metrics
        or not isinstance(observed_at, str) or not observed_at
    ):
        return None
    return {
        "activity_signals": tuple(dict.fromkeys(signals)),
        "source_published_at": published_at,
        "engagement_context": {"metrics": dict(metrics), "metrics_observed_at": observed_at},
    }


def build_brand_activity_candidate(*, workflow_run_id: str, direction_id: str, claim_type: str, fact: ExtractedFact, context: Mapping[str, Any]) -> ClaimCandidateRecord:
    if direction_id != "brand_activity":
        raise ValueError("brand-activity factory requires brand_activity direction")
    if claim_type not in BRAND_ACTIVITY_CLAIM_INTENTS or claim_type not in context.get("activity_signals", ()):
        raise ValueError("brand-activity signal type is not allowed")
    if fact.field_path not in _ALLOWED_FIELDS:
        raise ValueError("brand-activity claim must cite title, content text, or tags")
    if not context.get("source_published_at"):
        raise ValueError("brand-activity claim requires publication date")
    if any(term in fact.text for term in _PROHIBITED_TERMS):
        raise ValueError("brand-activity observation cannot claim outcome or causal success")
    return build_claim_candidate(
        workflow_run_id=workflow_run_id, direction_id=direction_id,
        intent_id=BRAND_ACTIVITY_CLAIM_INTENTS[claim_type], claim_type=claim_type,
        statement=fact.text, scope={"sample": "selected_packets", **dict(context)}, fact=fact,
        quote=fact.text, text_start=0, text_end=len(fact.text),
    )


def build_brand_activity_candidates(packet: DirectionalEvidencePacketRecord) -> list[ClaimCandidateRecord]:
    context = _context(packet)
    if context is None:
        return []
    candidates: list[ClaimCandidateRecord] = []
    for fact in extract_facts(packet):
        if fact.field_path not in _ALLOWED_FIELDS:
            continue
        for claim_type in context["activity_signals"]:
            try:
                candidates.append(build_brand_activity_candidate(
                    workflow_run_id=packet.workflow_run_id, direction_id=packet.research_direction_id,
                    claim_type=claim_type, fact=fact, context=context,
                ))
            except ValueError:
                continue
    return candidates


def brand_activity_boundary_reason(candidate: ClaimCandidateRecord) -> str | None:
    if candidate.claim_type not in BRAND_ACTIVITY_CLAIM_INTENTS or any(term in candidate.statement for term in _PROHIBITED_TERMS):
        return "brand_activity_evidence_boundary_violation"
    scope = dict(candidate.payload.get("scope") or {})
    refs = list(candidate.payload.get("quote_refs") or [])
    context = scope.get("engagement_context")
    if (
        len(refs) != 1 or str(refs[0].get("field_path") or "") not in _ALLOWED_FIELDS
        or scope.get("sample") != "selected_packets" or candidate.claim_type not in scope.get("activity_signals", ())
        or not scope.get("source_published_at") or not isinstance(context, Mapping)
        or not context.get("metrics") or not context.get("metrics_observed_at")
    ):
        return "brand_activity_evidence_boundary_violation"
    return None


class BrandActivityAdmissionStrategy(AdmissionStrategy):
    def __init__(self) -> None:
        super().__init__("brand_activity")

    def build_candidates(self, packet: DirectionalEvidencePacketRecord) -> list[ClaimCandidateRecord]:
        return build_brand_activity_candidates(packet)

    def boundary_reason(self, candidate: ClaimCandidateRecord) -> str | None:
        return brand_activity_boundary_reason(candidate)


STRATEGY = BrandActivityAdmissionStrategy()
