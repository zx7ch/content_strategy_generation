"""Comment-insight admission from directly quoted comment evidence only."""

from __future__ import annotations

from collections.abc import Mapping

from app.content_research.admission.candidates import build_claim_candidate, extract_facts
from app.content_research.admission.strategy import AdmissionStrategy
from app.content_research.persistence_models import (
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
)

COMMENT_INSIGHT_CLAIM_INTENTS = {
    "explicit_question": "explicit_question",
    "objection_or_failure": "objection_or_failure",
    "repeated_need_language": "repeated_need_language",
}
_OBJECTION_TERMS = ("不行", "失败", "问题", "麻烦", "太贵", "不好用", "不适合")
_NEED_TERMS = ("需要", "希望", "想要", "能不能")


def _complete_collection(collection: Mapping[str, object]) -> bool:
    return all((
        bool(collection.get("sort")), int(collection.get("target_comment_count") or 0) >= 30,
        int(collection.get("actual_comment_count") or 0) >= 30, collection.get("completeness") == "complete",
        int(collection.get("deduplicated_comment_count") or 0) >= 30,
        int(collection.get("deduplicated_author_count") or 0) >= 5,
    ))


def build_comment_insight_candidates(packet: DirectionalEvidencePacketRecord) -> list[ClaimCandidateRecord]:
    context = dict(packet.payload.get("retrieval_context") or {})
    collection = context.get("collection")
    if not context.get("parent_note_canonical_source_id") or not isinstance(collection, Mapping) or not _complete_collection(collection):
        return []
    reply_relation = packet.payload.get("field_projection", {}).get("reply_depth")
    if reply_relation is None:
        return []
    candidates = []
    for fact in extract_facts(packet):
        if fact.field_path != "comment_text":
            continue
        phrase = " ".join(fact.text.split()).lower()
        repeated = dict(collection.get("repeated_need_phrases") or {}).get(phrase) or {}
        claim_type = "explicit_question" if "?" in fact.text or "？" in fact.text else (
            "objection_or_failure" if any(term in fact.text for term in _OBJECTION_TERMS) else (
                "repeated_need_language" if any(term in fact.text for term in _NEED_TERMS) and int(repeated.get("comment_count") or 0) >= 3 and int(repeated.get("independent_author_count") or 0) >= 2 else None
            )
        )
        if claim_type:
            candidates.append(build_claim_candidate(workflow_run_id=packet.workflow_run_id, direction_id=packet.research_direction_id, intent_id=COMMENT_INSIGHT_CLAIM_INTENTS[claim_type], claim_type=claim_type, statement=fact.text, scope={"sample": "selected_comment_packets", "parent_note_canonical_source_id": context["parent_note_canonical_source_id"], "reply_relation": reply_relation, "collection": dict(collection)}, fact=fact, quote=fact.text, text_start=0, text_end=len(fact.text)))
    return candidates


def comment_insight_boundary_reason(candidate: ClaimCandidateRecord) -> str | None:
    scope = dict(candidate.payload.get("scope") or {})
    refs = list(candidate.payload.get("quote_refs") or [])
    collection = scope.get("collection")
    if candidate.claim_type not in COMMENT_INSIGHT_CLAIM_INTENTS or len(refs) != 1 or refs[0].get("field_path") != "comment_text" or not scope.get("parent_note_canonical_source_id") or scope.get("reply_relation") is None or not isinstance(collection, Mapping) or not _complete_collection(collection):
        return "comment_insight_evidence_boundary_violation"
    return None


class CommentInsightAdmissionStrategy(AdmissionStrategy):
    def __init__(self) -> None:
        super().__init__("comment_insight")

    def build_candidates(self, packet: DirectionalEvidencePacketRecord) -> list[ClaimCandidateRecord]:
        return build_comment_insight_candidates(packet)

    def boundary_reason(self, candidate: ClaimCandidateRecord) -> str | None:
        return comment_insight_boundary_reason(candidate)


STRATEGY = CommentInsightAdmissionStrategy()
