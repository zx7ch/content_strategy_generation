"""Product-marketing's note-only candidate boundary.

This direction reports what a sampled note explicitly expresses.  It does not
turn engagement metadata or comments into a claim about preference, conversion,
or marketing effect.
"""

from __future__ import annotations

import re
from typing import Any

from app.content_research.admission.candidates import (
    ExtractedFact,
    build_claim_candidate,
    extract_facts,
)
from app.content_research.admission.quote_fields import quote_fields_for_claim
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

_PROHIBITED_OUTCOME_TERMS = ("偏好", "转化", "购买", "因果", "效果提升", "表现更好")
_MAX_DIRECT_OBSERVATION_CHARS = 280
_SCENE_TERMS = ("春季", "夏季", "秋季", "冬季", "通勤", "运动", "徒步", "睡眠", "居家", "户外")
_AUDIENCE_TERMS = ("儿童", "宝宝", "学生", "上班族", "孕妇", "老人", "男士", "女士")
_COUNTER_TERMS = (
    "不凉",
    "不透气",
    "不舒服",
    "不推荐",
    "没有凉感",
    "没感觉",
    "闷热",
    "有点闷",
    "太闷",
    "刺痒",
)
_ATOMIC_BOUNDARY = re.compile(r"[。！？!?；;\r\n]+")


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
    if fact.field_path not in quote_fields_for_claim("product_marketing", claim_type):
        raise ValueError("product-marketing claim type cannot use this evidence field")
    quote, text_start = _direct_observation(fact.text)
    return _build_product_marketing_candidate_for_span(
        workflow_run_id=workflow_run_id,
        direction_id=direction_id,
        claim_type=claim_type,
        fact=fact,
        quote=quote,
        text_start=text_start,
        scope=scope,
    )


def _build_product_marketing_candidate_for_span(
    *,
    workflow_run_id: str,
    direction_id: str,
    claim_type: str,
    fact: ExtractedFact,
    quote: str,
    text_start: int,
    scope: dict[str, Any] | None = None,
) -> ClaimCandidateRecord:
    if direction_id != "product_marketing":
        raise ValueError("product-marketing factory requires product_marketing direction")
    if claim_type not in PRODUCT_MARKETING_CLAIM_INTENTS:
        raise ValueError("product-marketing claim type is not allowed")
    if fact.field_path not in quote_fields_for_claim("product_marketing", claim_type):
        raise ValueError("product-marketing claim type cannot use this evidence field")
    if any(term in quote for term in _PROHIBITED_OUTCOME_TERMS):
        raise ValueError("product-marketing observation cannot claim preference, conversion, or effect")
    qualifiers = _qualifiers(quote)
    return build_claim_candidate(
        workflow_run_id=workflow_run_id,
        direction_id=direction_id,
        intent_id=PRODUCT_MARKETING_CLAIM_INTENTS[claim_type],
        claim_type=claim_type,
        statement=quote,
        scope={
            "sample": "selected_packets",
            "qualifiers": qualifiers,
            "polarity": _polarity(quote),
            **dict(scope or {}),
        },
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
    for excerpt, offset in _atomic_observations(text):
        return excerpt, offset
    raise ValueError("product-marketing observation requires non-empty text")


def _atomic_observations(text: str) -> list[tuple[str, int]]:
    """Return bounded Chinese sentence/short-clause spans from the exact source."""
    spans: list[tuple[str, int]] = []
    cursor = 0
    for boundary in _ATOMIC_BOUNDARY.finditer(text):
        spans.extend(_bounded_atomic_segment(text, cursor, boundary.start()))
        cursor = boundary.end()
    spans.extend(_bounded_atomic_segment(text, cursor, len(text)))
    return spans


def extract_atomic_marketing_spans(text: str) -> tuple[tuple[str, int, int], ...]:
    """Public deterministic extraction contract used by the quality gate."""
    return tuple(
        (quote, start, start + len(quote))
        for quote, start in _atomic_observations(text)
    )


def infer_atomic_marketing_metadata(
    quote: str, *, field_path: str
) -> dict[str, object]:
    qualifiers = _qualifiers(quote)
    if field_path == "title":
        tracks = ["message"]
    elif field_path == "content_text":
        tracks = ["value"]
        if qualifiers["scenes"] or qualifiers["audiences"]:
            tracks.append("need")
    else:
        tracks = []
    return {
        "tracks": sorted(tracks),
        "qualifiers": qualifiers,
        "polarity": _polarity(quote),
    }


def _bounded_atomic_segment(text: str, start: int, end: int) -> list[tuple[str, int]]:
    raw = text[start:end]
    stripped = raw.strip()
    if not stripped:
        return []
    offset = start + raw.index(stripped)
    results: list[tuple[str, int]] = []
    remaining = stripped
    remaining_offset = offset
    while remaining:
        excerpt = remaining[:_MAX_DIRECT_OBSERVATION_CHARS].strip()
        if not excerpt:
            break
        excerpt_offset = remaining_offset + remaining.index(excerpt)
        results.append((excerpt, excerpt_offset))
        consumed = remaining.index(excerpt) + len(excerpt)
        remaining_offset += consumed
        remaining = remaining[consumed:].strip()
    return results


def _qualifiers(text: str) -> dict[str, list[str]]:
    return {
        "scenes": [term for term in _SCENE_TERMS if term in text],
        "audiences": [term for term in _AUDIENCE_TERMS if term in text],
    }


def _polarity(text: str) -> str:
    return "counter" if any(term in text for term in _COUNTER_TERMS) else "support"


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
        for quote, text_start in _atomic_observations(fact.text):
            claim_types: list[str] = []
            if fact.field_path == "content_text":
                claim_types.append("product_value_expression")
                qualifiers = _qualifiers(quote)
                if qualifiers["scenes"]:
                    claim_types.append("use_context")
                if qualifiers["audiences"]:
                    claim_types.append("target_audience_framing")
            elif fact.field_path == "title":
                claim_types.append("message_angle")
            for claim_type in claim_types:
                try:
                    candidates.append(
                        _build_product_marketing_candidate_for_span(
                            workflow_run_id=packet.workflow_run_id,
                            direction_id=packet.research_direction_id,
                            claim_type=claim_type,
                            fact=fact,
                            quote=quote,
                            text_start=text_start,
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
    if field_path not in quote_fields_for_claim("product_marketing", candidate.claim_type):
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
