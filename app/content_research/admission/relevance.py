"""Frozen query-subject relevance evaluation shared by formal admission."""

from __future__ import annotations

from app.content_research.contracts import (
    DirectionContract,
    RunPolicySnapshot,
    frozen_query_relevance,
    normalize_relevance_text,
)
from app.content_research.persistence_models import (
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
)


def query_relevance_reason(
    *,
    candidate: ClaimCandidateRecord,
    packet: DirectionalEvidencePacketRecord,
    contract: DirectionContract,
    policy_snapshot: RunPolicySnapshot,
) -> str | None:
    """Return the mandatory relevance reason unless frozen provenance and quote anchors agree."""
    relevance = frozen_query_relevance(contract, policy_snapshot)
    if relevance is None:
        return "invalid_query_relevance_contract"
    reason = str(relevance["reason_code"])
    context = dict(packet.payload.get("retrieval_context") or {})
    packet_group_ids = [str(item) for item in context.get("query_group_ids", ()) if str(item)]
    frozen_group_ids = {str(item) for item in relevance["query_group_ids"]}
    locked_direction = dict(
        (
            policy_snapshot.effective_policy.get("locked_query_plan", {})
            .get("directions", {})
            .get(contract.direction_id, {})
        )
        or {}
    )
    query_plan_hash = str(locked_direction.get("query_plan_hash") or "")
    query_hits = context.get("query_hits")
    if (
        not isinstance(query_hits, list)
        or not query_hits
        or not query_plan_hash
        or context.get("query_plan_hash") != query_plan_hash
    ):
        return "invalid_query_provenance"
    normalized_hits: list[tuple[str, int]] = []
    for hit in query_hits:
        if not isinstance(hit, dict):
            return "invalid_query_provenance"
        group_id = str(hit.get("query_group_id") or "")
        rank = hit.get("rank")
        if (
            not group_id
            or group_id not in frozen_group_ids
            or not isinstance(rank, int)
            or isinstance(rank, bool)
            or rank < 1
        ):
            return "invalid_query_provenance"
        normalized_hits.append((group_id, rank))
    if (
        not packet_group_ids
        or packet_group_ids != sorted(packet_group_ids)
        or len(set(packet_group_ids)) != len(packet_group_ids)
        or packet_group_ids != [group_id for group_id, _ in normalized_hits]
        or normalized_hits != sorted(normalized_hits)
    ):
        return "invalid_query_provenance"
    refs = list(candidate.payload.get("quote_refs") or [])
    if len(refs) != 1:
        return "invalid_quote_reference"
    ref = refs[0]
    allowed_fields = set(relevance.get("claim_quote_fields", {}).get(candidate.claim_type, ()))
    if str(ref.get("field_path") or "") not in allowed_fields:
        return "invalid_quote_reference"
    quote = normalize_relevance_text(str(ref.get("quote") or ""))
    if not quote:
        return "invalid_quote_reference"
    first_intent_anchor = str(relevance.get("first_intent_anchor") or "")
    if contract.direction_id == "product_marketing" and first_intent_anchor:
        if not any(
            str(anchor) in quote
            for anchor in relevance.get("core_entity_anchors", ())
            if anchor
        ):
            return "core_entity_not_supported"
        if first_intent_anchor not in quote:
            return "first_intent_not_supported"
        return None
    anchors = [
        *relevance.get("core_entity_anchors", ()),
        *relevance.get("subject_anchors", ()),
        *relevance.get("category_anchors", ()),
        *[
            synonym
            for synonyms in relevance.get("allowed_synonyms", {}).values()
            for synonym in synonyms
        ],
    ]
    if not any(str(anchor) in quote for anchor in anchors if anchor):
        return reason
    return None
