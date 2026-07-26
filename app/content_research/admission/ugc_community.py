"""UGC-community comment-only admission boundary."""
from __future__ import annotations

from collections.abc import Mapping

from app.content_research.admission.candidates import build_claim_candidate, extract_facts
from app.content_research.admission.strategy import AdmissionStrategy
from app.content_research.persistence_models import (
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
)

UGC_CLAIM_INTENTS = {"observed_discussion_scenario": "connection_mechanism", "interaction_pattern": "connection_mechanism", "sampled_language": "member_profile"}


def _complete_collection(collection: Mapping[str, object]) -> bool:
    return (
        isinstance(collection.get("sort"), str)
        and bool(collection.get("sort"))
        and int(collection.get("target_comment_count") or 0) >= 30
        and int(collection.get("actual_comment_count") or 0) >= 30
        and isinstance(collection.get("completeness"), str)
        and bool(collection.get("completeness"))
        and int(collection.get("deduplicated_comment_count") or 0) >= 30
        and int(collection.get("deduplicated_author_count") or 0) >= 5
    )


def build_ugc_candidates(packet: DirectionalEvidencePacketRecord) -> list[ClaimCandidateRecord]:
    context = dict(packet.payload.get("retrieval_context") or {})
    collection = context.get("collection")
    if not context.get("parent_note_canonical_source_id") or not isinstance(collection, Mapping):
        return []
    if not _complete_collection(collection):
        return []
    candidates = []
    for fact in extract_facts(packet):
        if fact.field_path != "comment_text":
            continue
        for claim_type, intent in UGC_CLAIM_INTENTS.items():
            reply_relation = packet.payload.get("field_projection", {}).get("reply_depth")
            if reply_relation is None:
                continue
            candidates.append(build_claim_candidate(workflow_run_id=packet.workflow_run_id, direction_id=packet.research_direction_id, intent_id=intent, claim_type=claim_type, statement=fact.text, scope={"sample": "selected_comment_packets", "parent_note_canonical_source_id": context["parent_note_canonical_source_id"], "reply_relation": reply_relation, "collection": dict(collection)}, fact=fact, quote=fact.text, text_start=0, text_end=len(fact.text)))
    return candidates


def ugc_boundary_reason(candidate: ClaimCandidateRecord) -> str | None:
    scope = dict(candidate.payload.get("scope") or {})
    collection = scope.get("collection")
    refs = list(candidate.payload.get("quote_refs") or [])
    if candidate.claim_type not in UGC_CLAIM_INTENTS or len(refs) != 1 or refs[0].get("field_path") != "comment_text" or not scope.get("parent_note_canonical_source_id") or scope.get("reply_relation") is None or not isinstance(collection, Mapping) or not _complete_collection(collection):
        return "ugc_comment_sample_insufficient"
    return None


class UgcCommunityAdmissionStrategy(AdmissionStrategy):
    def __init__(self) -> None:
        super().__init__("ugc_community")

    def build_candidates(self, packet: DirectionalEvidencePacketRecord) -> list[ClaimCandidateRecord]:
        return build_ugc_candidates(packet)

    def boundary_reason(self, candidate: ClaimCandidateRecord) -> str | None:
        return ugc_boundary_reason(candidate)


STRATEGY = UgcCommunityAdmissionStrategy()
