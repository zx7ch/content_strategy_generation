"""Keyword-growth candidate boundary with explicit comparable-window gate."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.content_research.admission.candidates import (
    ExtractedFact,
    build_claim_candidate,
    extract_facts,
)
from app.content_research.admission.quote_fields import quote_fields_for_direction
from app.content_research.admission.strategy import AdmissionStrategy
from app.content_research.persistence_models import (
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
)

KEYWORD_GROWTH_CLAIM_INTENTS = {"sampled_keyword_pattern": "keyword_discovery", "keyword_growth_with_comparable_baseline": "relative_window_comparison"}
_FIELDS = quote_fields_for_direction("keyword_growth")


def _context(packet: DirectionalEvidencePacketRecord) -> dict[str, Any] | None:
    projection = dict(packet.payload.get("field_projection") or {})
    patterns = projection.get("keyword_patterns")
    published = projection.get("source_published_at")
    if not isinstance(patterns, list) or not all(isinstance(item, str) and item for item in patterns) or not isinstance(published, str) or not published:
        return None
    return {"keyword_patterns": tuple(dict.fromkeys(patterns)), "source_published_at": published, "reference_window": projection.get("reference_window")}


def _comparable(window: Any) -> bool:
    if not isinstance(window, Mapping):
        return False
    return bool(window.get("non_overlapping") and window.get("comparable") and window.get("bias_disclosure") and all(isinstance(window.get(key), int) and window[key] > 0 for key in ("recent_eligible", "reference_eligible", "recent_keyword_count", "reference_keyword_count")))


def build_keyword_growth_candidate(*, workflow_run_id: str, direction_id: str, claim_type: str, keyword: str, fact: ExtractedFact, context: Mapping[str, Any]) -> ClaimCandidateRecord:
    if direction_id != "keyword_growth" or claim_type not in KEYWORD_GROWTH_CLAIM_INTENTS:
        raise ValueError("keyword-growth claim type is not allowed")
    if fact.field_path not in _FIELDS or keyword not in fact.text or keyword not in context.get("keyword_patterns", ()):
        raise ValueError("keyword-growth candidate requires literal keyword quote")
    if claim_type == "keyword_growth_with_comparable_baseline" and not _comparable(context.get("reference_window")):
        raise ValueError("reference_window_insufficient")
    return build_claim_candidate(workflow_run_id=workflow_run_id, direction_id=direction_id, intent_id=KEYWORD_GROWTH_CLAIM_INTENTS[claim_type], claim_type=claim_type, statement=fact.text, scope={"sample": "selected_packets", "keyword": keyword, **dict(context)}, fact=fact, quote=fact.text, text_start=0, text_end=len(fact.text))


def build_keyword_growth_candidates(packet: DirectionalEvidencePacketRecord) -> list[ClaimCandidateRecord]:
    context = _context(packet)
    if context is None:
        return []
    candidates = []
    for fact in extract_facts(packet):
        if fact.field_path not in _FIELDS:
            continue
        for keyword in context["keyword_patterns"]:
            if keyword not in fact.text:
                continue
            for claim_type in KEYWORD_GROWTH_CLAIM_INTENTS:
                try:
                    candidates.append(build_keyword_growth_candidate(workflow_run_id=packet.workflow_run_id, direction_id=packet.research_direction_id, claim_type=claim_type, keyword=keyword, fact=fact, context=context))
                except ValueError:
                    continue
    return candidates


def keyword_growth_boundary_reason(candidate: ClaimCandidateRecord) -> str | None:
    scope = dict(candidate.payload.get("scope") or {})
    refs = list(candidate.payload.get("quote_refs") or [])
    if candidate.claim_type not in KEYWORD_GROWTH_CLAIM_INTENTS or len(refs) != 1 or str(refs[0].get("field_path") or "") not in _FIELDS or not scope.get("keyword") or scope["keyword"] not in candidate.statement:
        return "keyword_growth_evidence_boundary_violation"
    if candidate.claim_type == "keyword_growth_with_comparable_baseline" and not _comparable(scope.get("reference_window")):
        return "reference_window_insufficient"
    return None


class KeywordGrowthAdmissionStrategy(AdmissionStrategy):
    def __init__(self) -> None:
        super().__init__("keyword_growth")

    def build_candidates(self, packet: DirectionalEvidencePacketRecord) -> list[ClaimCandidateRecord]:
        return build_keyword_growth_candidates(packet)

    def boundary_reason(self, candidate: ClaimCandidateRecord) -> str | None:
        return keyword_growth_boundary_reason(candidate)


STRATEGY = KeywordGrowthAdmissionStrategy()
