"""Versioned, user-grounded subject structures for Content Research."""

from __future__ import annotations

import hashlib
import json
import unicodedata
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
) -> SubjectStructureDecision:
    """Parse an LLM proposal; backend code owns the collection trust decision."""
    raw_research_intents = data.get("research_intents")
    entities = tuple(
        SubjectEntity(
            canonical_name=_text(item.get("canonical_name")),
            raw_mentions=_strings(item.get("raw_mentions")),
        )
        for item in data.get("core_entities", ())
        if isinstance(item, dict)
    )
    synonym_groups = tuple(
        sorted(
            (
                _text(owner),
                _strings(values),
            )
            for owner, values in dict(data.get("synonym_groups") or {}).items()
        )
    )
    structure = SubjectStructure(
        schema_version=_text(data.get("schema_version"))
        or SUBJECT_STRUCTURE_SCHEMA_VERSION,
        canonical_subject=_text(data.get("canonical_subject")),
        subject_type=_text(data.get("subject_type")) or "unknown",
        core_entities=entities,
        research_intents=_strings(data.get("research_intents")),
        context_modifiers=_strings(data.get("context_modifiers")),
        synonym_groups=synonym_groups,
        ambiguities=_strings(data.get("ambiguities")),
        resolution_state=_text(data.get("resolution_state")) or "unresolved",
    )
    reason_codes: list[str] = []
    input_for_matching = _matching_text(normalized_input)
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

    if (
        not isinstance(raw_research_intents, (list, tuple))
        or not raw_research_intents
        or not _text(raw_research_intents[0])
    ):
        reason_codes.append("primary_research_intent_missing")

    role_terms = {
        _matching_text(value)
        for value in (*structure.research_intents, *structure.context_modifiers)
        if _matching_text(value)
    }
    raw_mentions = tuple(
        mention for entity in structure.core_entities for mention in entity.raw_mentions
    )
    if (
        len(structure.core_entities) == 1
        and len(raw_mentions) == 1
        and _matching_text(raw_mentions[0]) == input_for_matching
        and role_terms
    ):
        reason_codes.append("core_entity_is_complete_input")
    elif any(
        term in _matching_text(mention)
        for mention in raw_mentions
        for term in role_terms
    ):
        reason_codes.append("core_entity_overlaps_role")

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
