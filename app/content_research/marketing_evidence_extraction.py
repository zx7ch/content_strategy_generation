"""Bounded structured LLM extraction over one immutable Evidence Snapshot."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Mapping, Sequence

from app.content_research.admission.candidates import source_text_hash
from app.content_research.analysis import DirectionalAnalysisLLM
from app.content_research.analysis_persistence import EvidenceSnapshot, FrozenEvidenceNote
from app.content_research.llm_scope import content_research_llm_context
from app.content_research.marketing_conclusion_analysis import MarketingConclusionAnalysisError
from app.content_research.marketing_evidence import AtomicMarketingEvidence
from app.content_research.persistence_models import (
    ClaimAdmissionDecisionRecord,
    ClaimCandidateRecord,
    CoverageManifest,
    DirectionalEvidencePacketRecord,
)
from app.content_research.runtime import canonical_fingerprint
from app.services.llm.failures import LLMProviderFailure
from app.services.llm.types import LLMCallContext, LLMRequest, Message

logger = logging.getLogger(__name__)

MAX_NOTES_PER_EXTRACTION_BATCH = 12
MAX_EXTRACTION_ATTEMPTS = 2
MARKETING_EVIDENCE_EXTRACTION_PROMPT = (
    "You extract exact product-marketing evidence from frozen Xiaohongshu notes. "
    "Return JSON only with exactly evidence, an array. Each item must contain exactly "
    "note_id, field_path, quote, text_start, text_end, track, aspect, evidence_type, "
    "polarity, scenes, audiences. field_path is title or content_text. quote must be an "
    "exact sentence or short-clause substring at the supplied offsets. track is need, "
    "value, or message; need/value use content_text and message uses title. aspect must "
    "be a non-empty label of at most 80 characters. evidence_type must be one of "
    "direct_expression, experience, need_context, message_expression, limitation. polarity is "
    "support or counter. scenes and audiences are arrays of explicit strings from the "
    "quote. Do not infer effects, causality, conversion, preference, or missing evidence."
)

_CLAIM_TYPES = {
    "need": ("use_context", "usage_context"),
    "value": ("product_value_expression", "value_proposition"),
    "message": ("message_angle", "message_angle"),
}
_EVIDENCE_TYPES = {
    "direct_expression",
    "experience",
    "need_context",
    "message_expression",
    "limitation",
}
MARKETING_EVIDENCE_EXTRACTION_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "marketing_evidence_extraction",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "evidence": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "note_id": {"type": "string", "minLength": 1},
                            "field_path": {"type": "string", "enum": ["title", "content_text"]},
                            "quote": {"type": "string", "minLength": 1},
                            "text_start": {"type": "integer", "minimum": 0},
                            "text_end": {"type": "integer", "minimum": 1},
                            "track": {"type": "string", "enum": ["need", "value", "message"]},
                            "aspect": {"type": "string", "minLength": 1, "maxLength": 80},
                            "evidence_type": {"type": "string", "enum": sorted(_EVIDENCE_TYPES)},
                            "polarity": {"type": "string", "enum": ["support", "counter"]},
                            "scenes": {"type": "array", "items": {"type": "string", "minLength": 1}},
                            "audiences": {"type": "array", "items": {"type": "string", "minLength": 1}},
                        },
                        "required": [
                            "note_id", "field_path", "quote", "text_start", "text_end",
                            "track", "aspect", "evidence_type", "polarity", "scenes", "audiences",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["evidence"],
            "additionalProperties": False,
        },
    },
}


def _collapse_whitespace_with_source_spans(
    value: str,
) -> tuple[str, tuple[tuple[int, int], ...]]:
    characters: list[str] = []
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(value):
        if value[index].isspace():
            start = index
            while index < len(value) and value[index].isspace():
                index += 1
            if characters and characters[-1] != " ":
                characters.append(" ")
                spans.append((start, index))
            continue
        characters.append(value[index])
        spans.append((index, index + 1))
        index += 1
    if characters and characters[-1] == " ":
        characters.pop()
        spans.pop()
    return "".join(characters), tuple(spans)


def _whitespace_equivalent_source_span(
    source: str, quote: str
) -> tuple[int, int] | None:
    normalized_source, source_spans = _collapse_whitespace_with_source_spans(source)
    normalized_quote, _ = _collapse_whitespace_with_source_spans(quote)
    if not normalized_quote:
        return None
    normalized_start = normalized_source.find(normalized_quote)
    if normalized_start < 0:
        return None
    normalized_end = normalized_start + len(normalized_quote)
    return (
        source_spans[normalized_start][0],
        source_spans[normalized_end - 1][1],
    )


class MarketingEvidenceExtractionService:
    """Extract exact, typed evidence without reading mutable packet/claim tables."""

    def __init__(
        self,
        *,
        llm: DirectionalAnalysisLLM,
        llm_scope: Mapping[str, object] | None = None,
        timeout_seconds: float = 90.0,
    ) -> None:
        self._llm = llm
        self._llm_scope = llm_scope
        self._timeout_seconds = timeout_seconds

    async def extract(self, snapshot: EvidenceSnapshot) -> tuple[AtomicMarketingEvidence, ...]:
        atoms: list[AtomicMarketingEvidence] = []
        for offset in range(0, len(snapshot.notes), MAX_NOTES_PER_EXTRACTION_BATCH):
            batch = snapshot.notes[offset : offset + MAX_NOTES_PER_EXTRACTION_BATCH]
            atoms.extend(await self._extract_batch(snapshot, batch, offset // MAX_NOTES_PER_EXTRACTION_BATCH))
        identities = [atom.atom_id for atom in atoms]
        if len(set(identities)) != len(identities):
            raise MarketingConclusionAnalysisError(
                "marketing evidence extraction returned duplicate evidence",
                detail_code="duplicate_evidence",
            )
        return tuple(sorted(atoms, key=lambda item: item.atom_id))

    async def _extract_batch(
        self,
        snapshot: EvidenceSnapshot,
        notes: Sequence[FrozenEvidenceNote],
        batch_no: int,
    ) -> tuple[AtomicMarketingEvidence, ...]:
        messages = [
            Message(role="system", content=MARKETING_EVIDENCE_EXTRACTION_PROMPT),
            Message(
                role="user",
                content=json.dumps(
                    {
                        "snapshot_id": snapshot.id,
                        "batch_no": batch_no,
                        "notes": [
                            {"note_id": note.note_id, "title": note.title, "content_text": note.body}
                            for note in notes
                        ],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ),
        ]
        context = (
            content_research_llm_context(
                self._llm_scope,
                session_id=snapshot.workflow_run_id,
                workflow_run_id=snapshot.workflow_run_id,
                step_name="marketing_evidence_extraction",
                agent_name="marketing_evidence_extractor",
            )
            if self._llm_scope is not None
            else LLMCallContext(
                session_id=snapshot.workflow_run_id,
                job_id=snapshot.workflow_run_id,
                step_name="marketing_evidence_extraction",
                agent_name="marketing_evidence_extractor",
            )
        )
        last_error: MarketingConclusionAnalysisError | None = None
        payload: object = None
        for attempt_no in range(MAX_EXTRACTION_ATTEMPTS):
            request = LLMRequest(
                messages=messages,
                task_type="content_research.marketing_evidence_extraction",
                model_policy="quality",
                temperature=0,
                max_tokens=6000,
                response_format=MARKETING_EVIDENCE_EXTRACTION_RESPONSE_FORMAT,
                context=context,
            )
            try:
                response = await asyncio.wait_for(
                    self._llm.generate(request), timeout=self._timeout_seconds
                )
            except asyncio.TimeoutError as exc:
                raise LLMProviderFailure(
                    "llm_service_unavailable", "模型服务响应超时", True, None
                ) from exc
            if response.finish_reason == "length":
                raise MarketingConclusionAnalysisError(
                    "marketing evidence extraction was truncated",
                    detail_code="extraction_truncated",
                )
            try:
                payload = json.loads(response.content)
                return self._parse_batch(snapshot, notes, payload)
            except (TypeError, json.JSONDecodeError) as exc:
                last_error = MarketingConclusionAnalysisError(
                    "marketing evidence extraction must be valid JSON",
                    detail_code="extraction_invalid_json",
                )
                last_error.__cause__ = exc
            except MarketingConclusionAnalysisError as exc:
                last_error = exc
            if attempt_no + 1 < MAX_EXTRACTION_ATTEMPTS:
                messages = [
                    *messages,
                    Message(role="assistant", content=response.content),
                    Message(
                        role="user",
                        content=(
                            "Your previous JSON failed validation ("
                            f"{last_error.detail_code}: {last_error}). Regenerate the complete JSON from the "
                            "same frozen notes. Copy note_id and quote exactly; quote must be an "
                            "exact substring of the selected field, and aspect must be non-empty "
                            "and at most 80 characters. Do not explain the correction."
                        ),
                    ),
                ]
        assert last_error is not None
        if isinstance(payload, dict) and isinstance(payload.get("evidence"), list):
            admitted = self._parse_batch(
                snapshot, notes, payload, skip_invalid_items=True
            )
            dropped_count = len(payload["evidence"]) - len(admitted)
            if dropped_count:
                logger.warning(
                    "dropped ungrounded marketing evidence items after bounded rewrite",
                    extra={
                        "workflow_run_id": snapshot.workflow_run_id,
                        "batch_no": batch_no,
                        "dropped_count": dropped_count,
                    },
                )
            return admitted
        raise last_error

    @staticmethod
    def _parse_batch(
        snapshot: EvidenceSnapshot,
        notes: Sequence[FrozenEvidenceNote],
        payload: object,
        *,
        skip_invalid_items: bool = False,
    ) -> tuple[AtomicMarketingEvidence, ...]:
        if not isinstance(payload, dict) or set(payload) != {"evidence"} or not isinstance(payload["evidence"], list):
            raise MarketingConclusionAnalysisError(
                "marketing evidence extraction has invalid shape",
                detail_code="extraction_invalid_shape",
            )
        by_id = {note.note_id: note for note in notes}
        atoms: list[AtomicMarketingEvidence] = []
        expected_keys = {
            "note_id", "field_path", "quote", "text_start", "text_end", "track",
            "aspect", "evidence_type", "polarity", "scenes", "audiences",
        }
        for item in payload["evidence"]:
            if not isinstance(item, dict):
                if skip_invalid_items:
                    continue
                raise MarketingConclusionAnalysisError(
                    "marketing evidence item has invalid shape: non_object",
                    detail_code="extraction_item_invalid_shape",
                )
            item_keys = set(item)
            if item_keys != expected_keys:
                if skip_invalid_items:
                    continue
                missing = ",".join(sorted(expected_keys - item_keys)) or "none"
                extra = ",".join(sorted(item_keys - expected_keys)) or "none"
                raise MarketingConclusionAnalysisError(
                    "marketing evidence item has invalid shape: "
                    f"missing={missing};extra={extra}",
                    detail_code="extraction_item_invalid_shape",
                )
            note_id = item["note_id"]
            field_path, track = item["field_path"], item["track"]
            note = by_id.get(note_id) if isinstance(note_id, str) else None
            source = note.title if note is not None and field_path == "title" else note.body if note is not None and field_path == "content_text" else None
            start, end, quote = item["text_start"], item["text_end"], item["quote"]
            if (
                source is not None
                and isinstance(start, int)
                and isinstance(end, int)
                and isinstance(quote, str)
                and quote
                and (
                    start < 0
                    or end <= start
                    or end > len(source)
                    or source[start:end] != quote
                )
            ):
                exact_start = source.find(quote)
                if exact_start >= 0:
                    start = exact_start
                    end = exact_start + len(quote)
                else:
                    equivalent_span = _whitespace_equivalent_source_span(source, quote)
                    if equivalent_span is not None:
                        start, end = equivalent_span
                        quote = source[start:end]
            qualifiers_valid = all(
                isinstance(item[name], list)
                and all(isinstance(value, str) and value.strip() for value in item[name])
                for name in ("scenes", "audiences")
            )
            failure_reason = None
            if note is None:
                failure_reason = "unknown_note_id"
            elif source is None:
                failure_reason = "invalid_field_path"
            elif not isinstance(track, str) or track not in _CLAIM_TYPES:
                failure_reason = "invalid_track"
            elif (track == "message") != (field_path == "title"):
                failure_reason = "track_field_mismatch"
            elif (
                not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end <= start
                or end > len(source)
            ):
                failure_reason = "invalid_offsets"
            elif not isinstance(quote, str) or source[start:end] != quote:
                failure_reason = "quote_not_exact"
            elif (
                not isinstance(item["aspect"], str)
                or not item["aspect"].strip()
                or len(item["aspect"]) > 80
            ):
                failure_reason = "invalid_aspect"
            elif (
                not isinstance(item["evidence_type"], str)
                or item["evidence_type"] not in _EVIDENCE_TYPES
            ):
                failure_reason = "invalid_evidence_type"
            elif (
                not isinstance(item["polarity"], str)
                or item["polarity"] not in {"support", "counter"}
            ):
                failure_reason = "invalid_polarity"
            elif not qualifiers_valid:
                failure_reason = "invalid_qualifiers"
            if failure_reason is not None:
                if skip_invalid_items:
                    continue
                raise MarketingConclusionAnalysisError(
                    "marketing evidence item is not grounded in the frozen snapshot: "
                    + failure_reason,
                    detail_code="extraction_not_grounded",
                )
            identity = canonical_fingerprint(
                {
                    "snapshot": snapshot.snapshot_fingerprint,
                    "note_id": note.note_id,
                    "field_path": field_path,
                    "start": start,
                    "end": end,
                    "track": track,
                    "polarity": item["polarity"],
                }
            )
            atoms.append(
                AtomicMarketingEvidence(
                    atom_id=f"mae_{identity[:24]}",
                    claim_id=f"cc_{identity[:24]}",
                    track=track,
                    note_id=note.note_id,
                    account_id=note.account_id,
                    field_path=field_path,
                    quote=quote,
                    text_start=start,
                    text_end=end,
                    polarity=item["polarity"],
                    scenes=tuple(sorted(set(item["scenes"]))),
                    audiences=tuple(sorted(set(item["audiences"]))),
                    aspect=item["aspect"].strip(),
                    evidence_type=item["evidence_type"],
                )
            )
        return tuple(atoms)


def project_snapshot_analysis_inputs(
    snapshot: EvidenceSnapshot,
    atoms: Sequence[AtomicMarketingEvidence],
    *,
    policy_snapshot_id: str,
    policy_snapshot_hash: str,
    manifest: CoverageManifest | None = None,
) -> tuple[
    tuple[tuple[ClaimAdmissionDecisionRecord, ClaimCandidateRecord], ...],
    dict[str, DirectionalEvidencePacketRecord],
]:
    """Build immutable in-memory compatibility records solely from Snapshot + atoms."""
    notes = {note.note_id: note for note in snapshot.notes}
    ownership = {
        "scope_contract_id": (
            manifest.scope_contract_id if manifest is not None else snapshot.scope_contract_id
        ),
        "execution_unit_id": (
            manifest.execution_unit_id
            if manifest is not None
            else snapshot.retrieval_execution_unit_id
        ),
        "attempt_no": (
            manifest.attempt_no
            if manifest is not None
            else snapshot.retrieval_attempt_no
        ),
        "execution_revision": (
            manifest.execution_revision if manifest is not None else 1
        ),
    }
    packets: dict[str, DirectionalEvidencePacketRecord] = {}
    admitted: list[tuple[ClaimAdmissionDecisionRecord, ClaimCandidateRecord]] = []
    for atom in atoms:
        note = notes[atom.note_id]
        packet_id = "sep_" + canonical_fingerprint(
            {"snapshot": snapshot.id, "note_id": note.note_id}
        )[:24]
        if packet_id not in packets:
            author_projection = (
                {"author_id": note.account_id[3:]}
                if note.account_id.startswith("id:")
                else {"author": note.account_id[5:]}
                if note.account_id.startswith("name:")
                else {"author_id": note.account_id}
            )
            projection = {
                "title": note.title,
                "content_text": note.body,
                "source_url": note.source_url,
                **author_projection,
            }
            packets[packet_id] = DirectionalEvidencePacketRecord(
                packet_id,
                "content_research_directional_evidence_packet_v2",
                {
                    "field_projection": projection,
                    "field_availability": {"title": "present", "content_text": "present"},
                    "retrieval_context": {"query_group_ids": list(note.query_provenance)},
                    "evidence_snapshot_id": snapshot.id,
                },
                workflow_run_id=snapshot.workflow_run_id,
                research_direction_id="product_marketing",
                canonical_source_id=note.note_id,
                field_projection_hash=source_text_hash(
                    json.dumps(projection, ensure_ascii=False, sort_keys=True)
                ),
                **ownership,
            )
        claim_type, intent_id = _CLAIM_TYPES[atom.track]
        source_text = note.title if atom.field_path == "title" else note.body
        candidate = ClaimCandidateRecord(
            atom.claim_id,
            "content_research_claim_candidate_v2",
            {
                "schema_version": "content_research_claim_candidate_v2",
                "scope": {
                    "sample": "selected_packets",
                    "qualifiers": {"scenes": list(atom.scenes), "audiences": list(atom.audiences)},
                    "polarity": atom.polarity,
                    "aspect": atom.aspect,
                    "evidence_type": atom.evidence_type,
                },
                "evidence_refs": [packet_id],
                "quote_refs": [{
                    "evidence_id": packet_id,
                    "field_path": atom.field_path,
                    "quote": atom.quote,
                    "text_start": atom.text_start,
                    "text_end": atom.text_end,
                    "source_text_hash": source_text_hash(source_text),
                    "source_url": note.source_url,
                }],
                "proposed_metrics": {},
                "limitation_refs": [],
                "evidence_snapshot_id": snapshot.id,
            },
            workflow_run_id=snapshot.workflow_run_id,
            research_direction_id="product_marketing",
            evidence_packet_id=packet_id,
            statement=atom.quote,
            intent_id=intent_id,
            claim_type=claim_type,
            **ownership,
        )
        decision = ClaimAdmissionDecisionRecord(
            "cad_" + hashlib.sha256(atom.claim_id.encode()).hexdigest()[:24],
            "content_research_claim_admission_decision_v2",
            {"reason_codes": [], "policy_snapshot_hash": policy_snapshot_hash, "evidence_snapshot_id": snapshot.id},
            research_direction_id="product_marketing",
            claim_candidate_id=candidate.id,
            decision="admitted",
            policy_snapshot_id=policy_snapshot_id,
        )
        admitted.append((decision, candidate))
    return tuple(sorted(admitted, key=lambda item: item[1].id)), packets


def serialize_atoms(atoms: Sequence[AtomicMarketingEvidence]) -> dict[str, object]:
    return {"atoms": [dict(atom.__dict__) for atom in atoms]}


def deserialize_atoms(payload: Mapping[str, object]) -> tuple[AtomicMarketingEvidence, ...]:
    raw = payload.get("atoms")
    if not isinstance(raw, list):
        raise ValueError("analysis extraction checkpoint is missing atoms")
    return tuple(
        AtomicMarketingEvidence(
            **{
                **item,
                "scenes": tuple(item.get("scenes") or ()),
                "audiences": tuple(item.get("audiences") or ()),
            }
        )
        for item in raw
        if isinstance(item, dict)
    )
