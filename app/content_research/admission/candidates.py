"""Deterministic packet-to-fact and fact-to-candidate contracts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from app.content_research.persistence_models import (
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
)


def source_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _field_text(projection: dict[str, Any], field_path: str) -> str | None:
    value = projection.get(field_path)
    if isinstance(value, str):
        return value
    if field_path == "tags" and isinstance(value, list) and all(isinstance(item, str) for item in value):
        return "\n".join(value)
    return None


@dataclass(frozen=True)
class ExtractedFact:
    workflow_run_id: str
    direction_id: str
    evidence_packet_id: str
    field_path: str
    text: str
    source_url: str


def extract_facts(packet: DirectionalEvidencePacketRecord) -> list[ExtractedFact]:
    projection = dict(packet.payload.get("field_projection") or {})
    facts: list[ExtractedFact] = []
    for field_path in ("content_text", "comment_text", "title", "tags"):
        text = _field_text(projection, field_path)
        if text and text.strip():
            facts.append(ExtractedFact(packet.workflow_run_id, packet.research_direction_id, packet.id, field_path, text, str(projection.get("source_url") or "")))
    return facts


def build_claim_candidate(
    *, workflow_run_id: str, direction_id: str, intent_id: str, claim_type: str,
    statement: str, scope: dict[str, Any], fact: ExtractedFact, quote: str,
    text_start: int, text_end: int, limitations: list[dict[str, Any]] | None = None,
) -> ClaimCandidateRecord:
    if not all((workflow_run_id, direction_id, intent_id, claim_type, statement, quote)):
        raise ValueError("claim candidate requires identity, statement, and quote")
    if (fact.workflow_run_id, fact.direction_id) != (workflow_run_id, direction_id):
        raise ValueError("fact belongs to a different workflow run or direction")
    if text_start < 0 or text_end <= text_start or text_end > len(fact.text) or fact.text[text_start:text_end] != quote:
        raise ValueError("quote span does not match source text")
    if fact.field_path == "comment_text" and not scope.get("parent_note_canonical_source_id"):
        raise ValueError("comment candidate requires parent-note lineage")
    candidate_id = "cc_" + hashlib.sha256(repr((workflow_run_id, direction_id, intent_id, claim_type, statement, fact.evidence_packet_id, fact.field_path, text_start, text_end)).encode()).hexdigest()[:24]
    payload = {
        "schema_version": "content_research_claim_candidate_v2",
        "scope": scope, "evidence_refs": [fact.evidence_packet_id],
        "quote_refs": [{"evidence_id": fact.evidence_packet_id, "field_path": fact.field_path, "quote": quote, "text_start": text_start, "text_end": text_end, "source_text_hash": source_text_hash(fact.text), "source_url": fact.source_url}],
        "proposed_metrics": {}, "limitation_refs": list(limitations or []),
    }
    return ClaimCandidateRecord(candidate_id, "content_research_claim_candidate_v2", payload, workflow_run_id=workflow_run_id, research_direction_id=direction_id, evidence_packet_id=fact.evidence_packet_id, statement=statement, intent_id=intent_id, claim_type=claim_type)


def validate_candidate_packet(candidate: ClaimCandidateRecord, packet: DirectionalEvidencePacketRecord) -> None:
    if (packet.workflow_run_id, packet.research_direction_id, packet.id) != (candidate.workflow_run_id, candidate.research_direction_id, candidate.evidence_packet_id):
        raise ValueError("candidate packet reference is outside its workflow run or direction")
    projection = dict(packet.payload.get("field_projection") or {})
    availability = dict(packet.payload.get("field_availability") or {})
    refs = list(candidate.payload.get("quote_refs") or [])
    if not refs:
        raise ValueError("claim candidate requires quote references")
    for ref in refs:
        field_path, quote = str(ref.get("field_path") or ""), str(ref.get("quote") or "")
        text = _field_text(projection, field_path)
        start, end = ref.get("text_start"), ref.get("text_end")
        if availability.get(field_path, "present") != "present" or text is None or not isinstance(start, int) or not isinstance(end, int) or text[start:end] != quote or source_text_hash(text) != ref.get("source_text_hash") or str(projection.get("source_url") or "") != str(ref.get("source_url") or ""):
            raise ValueError("claim candidate quote reference does not match packet")
        if field_path == "comment_text" and not packet.payload.get("retrieval_context", {}).get("parent_note_canonical_source_id"):
            raise ValueError("comment candidate packet lacks parent-note lineage")
