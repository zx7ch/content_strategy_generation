"""Product-marketing's note-only candidate boundary.

This direction reports what a sampled note explicitly expresses.  It does not
turn engagement metadata or comments into a claim about preference, conversion,
or marketing effect.
"""

from __future__ import annotations

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

PRODUCT_MARKETING_CLAIM_INTENTS = {
    "product_value_expression": "value_proposition",
    "use_context": "usage_context",
    "target_audience_framing": "target_audience",
    "message_angle": "message_angle",
}

_ALLOWED_FIELDS = {
    "product_value_expression": frozenset({"content_text"}),
    "use_context": frozenset({"content_text"}),
    "target_audience_framing": frozenset({"content_text"}),
    "message_angle": frozenset({"title", "content_text"}),
}
_PROHIBITED_OUTCOME_TERMS = ("偏好", "转化", "购买", "因果", "效果提升", "表现更好")
_MAX_DIRECT_OBSERVATION_CHARS = 280


def build_product_marketing_candidate(
    *,
    workflow_run_id: str,
    direction_id: str,
    claim_type: str,
    fact: ExtractedFact,
    scope: dict[str, Any] | None = None,
) -> ClaimCandidateRecord:
    """Build one direct, sample-scoped observation from an eligible note field."""
    if direction_id != "product_marketing":
        raise ValueError("product-marketing factory requires product_marketing direction")
    if claim_type not in PRODUCT_MARKETING_CLAIM_INTENTS:
        raise ValueError("product-marketing claim type is not allowed")
    if fact.field_path not in _ALLOWED_FIELDS[claim_type]:
        raise ValueError("product-marketing claim type cannot use this evidence field")
    quote, text_start = _direct_observation(fact.text)
    if any(term in quote for term in _PROHIBITED_OUTCOME_TERMS):
        raise ValueError("product-marketing observation cannot claim preference, conversion, or effect")
    return build_claim_candidate(
        workflow_run_id=workflow_run_id,
        direction_id=direction_id,
        intent_id=PRODUCT_MARKETING_CLAIM_INTENTS[claim_type],
        claim_type=claim_type,
        statement=quote,
        scope={"sample": "selected_packets", **dict(scope or {})},
        fact=fact,
        quote=quote,
        text_start=text_start,
        text_end=text_start + len(quote),
    )


def _direct_observation(text: str) -> tuple[str, int]:
    """Return one bounded verbatim observation and its source offset.

    A note body can contain several unrelated passages.  Product marketing
    reports what one sampled note explicitly says; it must not promote the
    entire body to a single claim.
    """
    for start, line in _nonempty_lines(text):
        excerpt = line[:_MAX_DIRECT_OBSERVATION_CHARS].strip()
        if excerpt:
            offset = start + line.index(excerpt)
            return excerpt, offset
    raise ValueError("product-marketing observation requires non-empty text")


def _nonempty_lines(text: str):
    cursor = 0
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        if body.strip():
            yield cursor, body
        cursor += len(line)
    if not text.splitlines(keepends=True) and text.strip():
        yield 0, text


def build_product_marketing_candidates(
    packet: DirectionalEvidencePacketRecord,
) -> list[ClaimCandidateRecord]:
    """Emit only direct body-value and title-angle observations for a packet.

    Use-context and audience framing remain available through the public factory
    once a direction-specific structured extractor can identify them without
    guessing.  The generic Foundation fallback must not invent those labels.
    """
    candidates: list[ClaimCandidateRecord] = []
    for fact in extract_facts(packet):
        claim_type = "product_value_expression" if fact.field_path == "content_text" else (
            "message_angle" if fact.field_path == "title" else None
        )
        if claim_type is None:
            continue
        try:
            candidates.append(
                build_product_marketing_candidate(
                    workflow_run_id=packet.workflow_run_id,
                    direction_id=packet.research_direction_id,
                    claim_type=claim_type,
                    fact=fact,
                )
            )
        except ValueError:
            continue
    return candidates


def product_marketing_boundary_reason(candidate: ClaimCandidateRecord) -> str | None:
    """Return the stable rejection reason when a candidate exceeds N1 scope."""
    if candidate.claim_type not in PRODUCT_MARKETING_CLAIM_INTENTS:
        return "product_marketing_evidence_boundary_violation"
    if any(term in candidate.statement for term in _PROHIBITED_OUTCOME_TERMS):
        return "product_marketing_evidence_boundary_violation"
    refs = list(candidate.payload.get("quote_refs") or [])
    if len(refs) != 1:
        return "product_marketing_evidence_boundary_violation"
    quote = str(refs[0].get("quote") or "")
    if (
        not quote
        or quote != candidate.statement
        or len(quote) > _MAX_DIRECT_OBSERVATION_CHARS
        or any(term in quote for term in _PROHIBITED_OUTCOME_TERMS)
    ):
        return "product_marketing_evidence_boundary_violation"
    field_path = str(refs[0].get("field_path") or "")
    if field_path not in _ALLOWED_FIELDS[candidate.claim_type]:
        return "product_marketing_evidence_boundary_violation"
    if candidate.payload.get("scope", {}).get("sample") != "selected_packets":
        return "product_marketing_evidence_boundary_violation"
    return None


class ProductMarketingAdmissionStrategy(AdmissionStrategy):
    def __init__(self) -> None:
        super().__init__("product_marketing")

    def build_candidates(self, packet: DirectionalEvidencePacketRecord) -> list[ClaimCandidateRecord]:
        return build_product_marketing_candidates(packet)

    def boundary_reason(self, candidate: ClaimCandidateRecord) -> str | None:
        return product_marketing_boundary_reason(candidate)


STRATEGY = ProductMarketingAdmissionStrategy()
