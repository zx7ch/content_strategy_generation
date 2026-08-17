"""Frozen query-subject relevance evaluation shared by formal admission."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.content_research.contracts import (
    CandidateScopeMatch,
    DirectionContract,
    RunPolicySnapshot,
    ScopeConstraintMatch,
    frozen_query_relevance,
    normalize_relevance_text,
)
from app.content_research.persistence_models import (
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
)
from app.content_research.scope_contract import ResearchScopeContract


def evaluate_scope_match(
    *,
    source: Mapping[str, Any],
    contract: ResearchScopeContract,
) -> CandidateScopeMatch:
    """Evaluate a detailed source against every frozen Scope constraint."""
    searchable_fields = _scope_searchable_fields(source)
    constraint_matches: dict[str, ScopeConstraintMatch] = {}
    exclusion_reasons: list[str] = []
    for constraint in contract.constraints:
        terms = (constraint.value, *constraint.allowed_aliases)
        hits: list[tuple[str, str]] = []
        for field_name, field_value in searchable_fields:
            normalized_value = normalize_relevance_text(field_value)
            for term in terms:
                if (normalized_term := normalize_relevance_text(term)) and (
                    normalized_term in normalized_value
                ):
                    hits.append((field_name, term))
        evidence = tuple(dict.fromkeys(term for _field, term in hits))
        evidence_fields = tuple(dict.fromkeys(field for field, _term in hits))
        status = "matched" if evidence else "unmatched"
        constraint_matches[constraint.id] = ScopeConstraintMatch(
            status=status,
            evidence=evidence,
            evidence_fields=evidence_fields,
        )
        if constraint.mode == "required" and status == "unmatched":
            exclusion_reasons.append(f"required_constraint_unmatched:{constraint.id}")

    context = source.get("retrieval_context")
    query_group_hits = tuple(
        sorted(
            {
                str(item)
                for item in (
                    context.get("query_group_ids", ())
                    if isinstance(context, Mapping)
                    else source.get("query_group_ids", ())
                )
                if str(item)
            }
        )
    )
    return CandidateScopeMatch(
        scope_contract_version=contract.version,
        query_group_hits=query_group_hits,
        constraint_matches=constraint_matches,
        eligibility="excluded" if exclusion_reasons else "eligible",
        exclusion_reasons=tuple(exclusion_reasons),
    )


def scope_match_payload(
    match: CandidateScopeMatch, *, query_plan_hash: str | None = None
) -> dict[str, Any]:
    payload = {
        "scope_contract_version": match.scope_contract_version,
        "query_group_hits": list(match.query_group_hits),
        "constraint_matches": {
            constraint_id: {
                "status": value.status,
                "evidence": list(value.evidence),
                "evidence_fields": list(value.evidence_fields),
            }
            for constraint_id, value in match.constraint_matches.items()
        },
        "eligibility": match.eligibility,
        "exclusion_reasons": list(match.exclusion_reasons),
    }
    if query_plan_hash:
        payload["query_plan_hash"] = query_plan_hash
    return payload


def _scope_searchable_fields(source: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    fields: list[tuple[str, str]] = []
    for key in (
        "title",
        "content_text",
        "tags",
        "author",
        "author_id",
        "source_published_at",
        "source_collected_at",
        "ip_location",
        "note_type",
        "provider",
    ):
        value = source.get(key)
        if isinstance(value, (list, tuple, set)):
            fields.extend((key, str(item)) for item in value if str(item).strip())
        elif value is not None and str(value).strip():
            fields.append((key, str(value)))
    for metadata_key in ("source_metadata", "metadata"):
        metadata = source.get(metadata_key)
        if not isinstance(metadata, Mapping):
            continue
        for key, value in sorted(metadata.items(), key=lambda item: str(item[0])):
            if isinstance(value, (str, int, float)) and str(value).strip():
                fields.append((f"{metadata_key}.{key}", str(value)))
            elif isinstance(value, (list, tuple, set)):
                fields.extend(
                    (f"{metadata_key}.{key}", str(item))
                    for item in value
                    if str(item).strip()
                )
    return tuple(fields)


def query_relevance_reason(
    *,
    candidate: ClaimCandidateRecord,
    packet: DirectionalEvidencePacketRecord,
    contract: DirectionContract,
    policy_snapshot: RunPolicySnapshot,
    scope_contract: ResearchScopeContract | None = None,
    scope_query_plan_hash: str | None = None,
) -> str | None:
    """Return the mandatory relevance reason unless frozen provenance and quote anchors agree."""
    relevance = frozen_query_relevance(contract, policy_snapshot)
    if relevance is None:
        return "invalid_query_relevance_contract"
    reason = str(relevance["reason_code"])
    context = dict(packet.payload.get("retrieval_context") or {})
    packet_group_ids = [str(item) for item in context.get("query_group_ids", ()) if str(item)]
    persisted_scope_match = (
        dict(candidate.payload.get("scope_match") or {})
        if contract.direction_id == "product_marketing"
        else {}
    )
    if scope_contract is not None:
        frozen_group_ids = {group.id for group in scope_contract.query_groups}
    elif persisted_scope_match:
        frozen_group_ids = {
            str(item)
            for item in persisted_scope_match.get("query_group_hits", ())
            if str(item)
        }
    else:
        frozen_group_ids = {str(item) for item in relevance["query_group_ids"]}
    locked_direction = dict(
        (
            policy_snapshot.effective_policy.get("locked_query_plan", {})
            .get("directions", {})
            .get(contract.direction_id, {})
        )
        or {}
    )
    query_plan_hash = (
        str(
            scope_query_plan_hash
            or persisted_scope_match.get("query_plan_hash")
            or ""
        )
        if scope_contract is not None or persisted_scope_match
        else str(locked_direction.get("query_plan_hash") or "")
    )
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
    if contract.direction_id == "product_marketing" and scope_contract is not None:
        match = evaluate_scope_match(
            source={
                **dict(packet.payload.get("field_projection") or {}),
                "retrieval_context": context,
            },
            contract=scope_contract,
        )
        return match.exclusion_reasons[0] if match.exclusion_reasons else None
    if persisted_scope_match:
        exclusions = [
            str(item)
            for item in persisted_scope_match.get("exclusion_reasons", ())
            if str(item)
        ]
        return exclusions[0] if exclusions else None
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
