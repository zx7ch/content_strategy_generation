"""Versioned, user-grounded subject structures for Content Research."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Any

SUBJECT_STRUCTURE_SCHEMA_VERSION = "content_research_subject_structure_v1"


@dataclass(frozen=True)
class SubjectEntity:
    canonical_name: str
    raw_mentions: tuple[str, ...]


@dataclass(frozen=True)
class SubjectStructure:
    schema_version: str
    canonical_subject: str
    subject_type: str
    source_terms: tuple[str, ...]
    term_roles: tuple[tuple[str, tuple[str, ...]], ...]
    core_entities: tuple[SubjectEntity, ...]
    research_intents: tuple[str, ...]
    context_modifiers: tuple[str, ...]
    synonym_groups: tuple[tuple[str, tuple[str, ...]], ...]
    ambiguities: tuple[str, ...]
    resolution_state: str


@dataclass(frozen=True)
class SubjectStructureDecision:
    state: str
    structure: SubjectStructure | None
    reason_codes: tuple[str, ...]


def subject_structure_payload(structure: SubjectStructure) -> dict[str, Any]:
    return {
        "schema_version": structure.schema_version,
        "canonical_subject": structure.canonical_subject,
        "subject_type": structure.subject_type,
        "source_terms": list(structure.source_terms),
        "term_roles": {role: list(terms) for role, terms in structure.term_roles},
        "core_entities": [
            {
                "canonical_name": entity.canonical_name,
                "raw_mentions": list(entity.raw_mentions),
            }
            for entity in structure.core_entities
        ],
        "research_intents": list(structure.research_intents),
        "context_modifiers": list(structure.context_modifiers),
        "synonym_groups": {
            owner: list(values) for owner, values in structure.synonym_groups
        },
        "ambiguities": list(structure.ambiguities),
        "resolution_state": structure.resolution_state,
    }


def subject_structure_fingerprint(structure: SubjectStructure) -> str:
    encoded = json.dumps(
        subject_structure_payload(structure),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_subject_structure(
    data: dict[str, Any],
    *,
    normalized_input: str,
    require_term_mapping: bool = False,
) -> SubjectStructureDecision:
    """Parse an LLM proposal; backend code owns the collection trust decision."""
    legacy_entities = tuple(
        SubjectEntity(
            canonical_name=_text(item.get("canonical_name")),
            raw_mentions=_strings(item.get("raw_mentions")),
        )
        for item in data.get("core_entities", ())
        if isinstance(item, dict)
    )
    legacy_synonym_groups = tuple(
        sorted(
            (
                _text(owner),
                _strings(values),
            )
            for owner, values in dict(data.get("synonym_groups") or {}).items()
        )
    )
    source_terms = _strings(data.get("source_terms"))
    raw_term_roles = data.get("term_roles")
    has_term_mapping = bool(source_terms) or isinstance(raw_term_roles, dict)
    term_roles = {
        "core_object": _strings(raw_term_roles.get("core_object")),
        "product_experience": _strings(raw_term_roles.get("product_experience")),
        "context_audience": _strings(raw_term_roles.get("context_audience")),
    } if isinstance(raw_term_roles, dict) else {
        "core_object": (),
        "product_experience": (),
        "context_audience": (),
    }
    if source_terms:
        term_roles = _normalize_term_roles(source_terms, term_roles)

    input_for_matching = _matching_text(normalized_input)
    input_segments = _strings(_text(normalized_input).split())
    source_terms_valid = bool(source_terms) and (
        source_terms == input_segments
        if len(input_segments) > 1
        else "".join(_matching_text(term) for term in source_terms) == input_for_matching
    )
    assigned_terms = tuple(
        term
        for role in ("core_object", "product_experience", "context_audience")
        for term in term_roles[role]
    )
    mapping_valid = bool(
        source_terms
        and term_roles["core_object"]
        and Counter(_matching_text(term) for term in assigned_terms)
        == Counter(_matching_text(term) for term in source_terms)
    )
    # Never project an invented system term into the executable structure. Even
    # when the complete mapping is invalid, exact grounded assignments remain
    # useful as editable suggestions while the diagnosis stays visible.
    term_roles = _grounded_term_roles(
        source_terms,
        term_roles,
        input_for_matching=input_for_matching,
    )
    mapped_core = term_roles["core_object"]
    mapped_projection_required = has_term_mapping or require_term_mapping
    entities = (
        (
            SubjectEntity(
                canonical_name=" ".join(mapped_core),
                raw_mentions=mapped_core,
            ),
        )
        if mapped_core
        else ()
    ) if mapped_projection_required else legacy_entities
    research_intents = (
        (" ".join(term_roles["product_experience"]),)
        if term_roles["product_experience"]
        else ()
    ) if mapped_projection_required else _strings(data.get("research_intents"))
    context_modifiers = (
        (" ".join(term_roles["context_audience"]),)
        if term_roles["context_audience"]
        else ()
    ) if mapped_projection_required else _strings(data.get("context_modifiers"))
    structure = SubjectStructure(
        schema_version=_text(data.get("schema_version"))
        or SUBJECT_STRUCTURE_SCHEMA_VERSION,
        canonical_subject=_text(data.get("canonical_subject")),
        subject_type=_text(data.get("subject_type")) or "unknown",
        source_terms=source_terms,
        term_roles=tuple((role, term_roles[role]) for role in (
            "core_object",
            "product_experience",
            "context_audience",
        )),
        core_entities=entities,
        research_intents=research_intents,
        context_modifiers=context_modifiers,
        synonym_groups=() if has_term_mapping else legacy_synonym_groups,
        ambiguities=_strings(data.get("ambiguities")),
        resolution_state=_text(data.get("resolution_state")) or "unresolved",
    )
    reason_codes: list[str] = []
    if require_term_mapping and not has_term_mapping:
        reason_codes.append("source_terms_missing")
    if has_term_mapping:
        if len(input_segments) > 1:
            if source_terms != input_segments:
                reason_codes.append("source_terms_do_not_match_spaced_input")
        elif "".join(_matching_text(term) for term in source_terms) != input_for_matching:
            reason_codes.append("source_terms_do_not_reconstruct_input")
        if not source_terms_valid or not mapping_valid:
            reason_codes.append("source_term_mapping_invalid")
    if not structure.canonical_subject:
        reason_codes.append("canonical_subject_missing")
    if not structure.core_entities:
        reason_codes.append("core_entity_missing")
    elif any(
        not entity.canonical_name
        or not entity.raw_mentions
        or any(
            _matching_text(mention) not in input_for_matching
            for mention in entity.raw_mentions
        )
        for entity in structure.core_entities
    ):
        reason_codes.append("core_entity_ungrounded")
    if len(structure.core_entities) > 1:
        reason_codes.append("multiple_primary_entities")
    if structure.resolution_state != "resolved" or structure.ambiguities:
        reason_codes.append("unresolved_ambiguity")

    entity_names = {
        _matching_text(entity.canonical_name) for entity in structure.core_entities
    }
    if any(
        _matching_text(owner) not in entity_names
        for owner, _values in structure.synonym_groups
    ):
        reason_codes.append("orphan_synonym_group")
    normalized_synonyms = [
        _matching_text(value)
        for _owner, values in structure.synonym_groups
        for value in values
    ]
    if len(normalized_synonyms) != len(set(normalized_synonyms)):
        reason_codes.append("duplicate_synonym")

    return SubjectStructureDecision(
        state="needs_confirmation" if reason_codes else "confirmed",
        structure=structure,
        reason_codes=tuple(reason_codes),
    )


def _text(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in (_text(raw) for raw in value) if item)


def _matching_text(value: Any) -> str:
    return "".join(_text(value).casefold().split())


def _normalize_term_roles(
    source_terms: tuple[str, ...],
    term_roles: dict[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    """Reduce compound system core labels to their unassigned source terms."""
    auxiliary_terms = (
        *term_roles["product_experience"],
        *term_roles["context_audience"],
    )
    source_counter = Counter(_matching_text(term) for term in source_terms)
    auxiliary_counter = Counter(_matching_text(term) for term in auxiliary_terms)
    if auxiliary_counter - source_counter:
        return term_roles

    remaining_counter = source_counter - auxiliary_counter
    remaining_terms: list[str] = []
    for term in source_terms:
        normalized = _matching_text(term)
        if remaining_counter[normalized] > 0:
            remaining_terms.append(term)
            remaining_counter[normalized] -= 1
    if not remaining_terms:
        return term_roles

    expanded_core: list[str] = []
    for core_term in term_roles["core_object"]:
        expansion = _contiguous_source_expansion(core_term, source_terms)
        if expansion is None:
            return term_roles
        expanded_core.extend(expansion)
    expanded_counter = Counter(_matching_text(term) for term in expanded_core)
    required_counter = Counter(_matching_text(term) for term in remaining_terms)
    if required_counter - expanded_counter:
        return term_roles

    return {**term_roles, "core_object": tuple(remaining_terms)}


def _grounded_term_roles(
    source_terms: tuple[str, ...],
    term_roles: dict[str, tuple[str, ...]],
    *,
    input_for_matching: str,
) -> dict[str, tuple[str, ...]]:
    """Keep exact source assignments, ordered as the user's source terms."""
    role_names = ("core_object", "product_experience", "context_audience")
    remaining = {
        role: Counter(_matching_text(term) for term in term_roles[role])
        for role in role_names
    }
    grounded: dict[str, list[str]] = {role: [] for role in role_names}
    for source_term in source_terms:
        normalized = _matching_text(source_term)
        if not normalized or normalized not in input_for_matching:
            continue
        assigned_roles = [role for role in role_names if remaining[role][normalized] > 0]
        if len(assigned_roles) != 1:
            continue
        role = assigned_roles[0]
        grounded[role].append(source_term)
        remaining[role][normalized] -= 1
    return {role: tuple(grounded[role]) for role in role_names}


def _contiguous_source_expansion(
    value: str,
    source_terms: tuple[str, ...],
) -> tuple[str, ...] | None:
    target = _matching_text(value)
    for start in range(len(source_terms)):
        for end in range(start + 1, len(source_terms) + 1):
            candidate = source_terms[start:end]
            if "".join(_matching_text(term) for term in candidate) == target:
                return candidate
    return None
