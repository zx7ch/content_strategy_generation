from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.content_research.analysis_persistence import (
    EVIDENCE_SNAPSHOT_SCHEMA_VERSION,
    EvidenceSnapshot,
    FrozenEvidenceNote,
)
from app.content_research.marketing_conclusion_analysis import MarketingConclusionAnalysisError
from app.content_research.marketing_evidence_extraction import (
    MarketingEvidenceExtractionService,
    project_snapshot_analysis_inputs,
)
from app.services.llm.types import LLMResponse, TokenUsage

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


class RecordingLLM:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        return LLMResponse(
            content=json.dumps(self.payload, ensure_ascii=False),
            provider="fake",
            model="fake",
            usage=TokenUsage(total_tokens=1),
            latency_ms=1,
        )


class SequencedLLM:
    def __init__(self, payloads: list[object]) -> None:
        self.payloads = list(payloads)
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        payload = self.payloads.pop(0)
        return LLMResponse(
            content=json.dumps(payload, ensure_ascii=False),
            provider="fake",
            model="fake",
            usage=TokenUsage(total_tokens=1),
            latency_ms=1,
        )


def _snapshot() -> EvidenceSnapshot:
    title = "夏季凉感T恤"
    body = "夏季通勤穿着凉爽透气"
    return EvidenceSnapshot(
        id="evs-test",
        schema_version=EVIDENCE_SNAPSHOT_SCHEMA_VERSION,
        workflow_run_id="run-test",
        scope_contract_id="scope-test",
        retrieval_execution_unit_id="unit-test",
        retrieval_attempt_no=1,
        snapshot_fingerprint="snapshot-fingerprint",
        query_groups=({"id": "query-test", "query": "凉感T恤"},),
        notes=(
            FrozenEvidenceNote(
                note_id="note-test",
                account_id="id:author-test",
                title=title,
                body=body,
                title_hash="title-hash",
                body_hash="body-hash",
                source_url="https://example.test/note-test",
                captured_at=NOW,
                query_provenance=("query-test",),
            ),
        ),
        created_at=NOW,
    )


@pytest.mark.asyncio
async def test_structured_extraction_is_exact_and_projects_only_snapshot_inputs() -> None:
    snapshot = _snapshot()
    body = snapshot.notes[0].body
    llm = RecordingLLM(
        {
            "evidence": [
                {
                    "note_id": "note-test",
                    "field_path": "content_text",
                    "quote": body,
                    "text_start": 0,
                    "text_end": len(body),
                    "track": "value",
                    "aspect": "凉感透气",
                    "evidence_type": "experience",
                    "polarity": "support",
                    "scenes": ["夏季", "通勤"],
                    "audiences": [],
                }
            ]
        }
    )

    atoms = await MarketingEvidenceExtractionService(llm=llm).extract(snapshot)
    admitted, packets = project_snapshot_analysis_inputs(
        snapshot,
        atoms,
        policy_snapshot_id="policy-test",
        policy_snapshot_hash="policy-hash",
    )

    assert llm.requests[0].task_type == "content_research.marketing_evidence_extraction"
    response_format = llm.requests[0].response_format
    assert response_format is not None
    assert response_format["type"] == "json_schema"
    schema = response_format["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    item_schema = schema["properties"]["evidence"]["items"]
    assert item_schema["additionalProperties"] is False
    assert item_schema["properties"]["evidence_type"]["enum"] == sorted(
        {
            "direct_expression",
            "experience",
            "need_context",
            "message_expression",
            "limitation",
        }
    )
    assert atoms[0].quote == body
    assert atoms[0].aspect == "凉感透气"
    assert atoms[0].evidence_type == "experience"
    assert admitted[0][1].payload["evidence_snapshot_id"] == snapshot.id
    assert admitted[0][1].payload["quote_refs"][0]["quote"] == body
    assert next(iter(packets.values())).payload["evidence_snapshot_id"] == snapshot.id


@pytest.mark.asyncio
async def test_structured_extraction_rejects_non_exact_quote() -> None:
    llm = RecordingLLM(
        {
            "evidence": [
                {
                    "note_id": "note-test",
                    "field_path": "content_text",
                    "quote": "模型编造的内容",
                    "text_start": 0,
                    "text_end": 7,
                    "track": "value",
                    "aspect": "凉感",
                    "evidence_type": "experience",
                    "polarity": "support",
                    "scenes": [],
                    "audiences": [],
                }
            ]
        }
    )

    snapshot = _snapshot()
    with pytest.raises(
        MarketingConclusionAnalysisError, match="quote_not_exact"
    ) as error:
        MarketingEvidenceExtractionService._parse_batch(
            snapshot, snapshot.notes, llm.payload
        )

    assert error.value.detail_code == "extraction_not_grounded"


def test_structured_extraction_repairs_offsets_past_frozen_source_end() -> None:
    snapshot = _snapshot()
    title = snapshot.notes[0].title

    atoms = MarketingEvidenceExtractionService._parse_batch(
        snapshot,
        snapshot.notes,
        {
            "evidence": [
                {
                    "note_id": "note-test",
                    "field_path": "title",
                    "quote": title,
                    "text_start": 0,
                    "text_end": len(title) + 2,
                    "track": "message",
                    "aspect": "选购表达",
                    "evidence_type": "message_expression",
                    "polarity": "support",
                    "scenes": [],
                    "audiences": [],
                }
            ]
        },
    )

    assert atoms[0].text_start == 0
    assert atoms[0].text_end == len(title)


def test_structured_extraction_repairs_offsets_only_when_quote_is_exact() -> None:
    snapshot = _snapshot()
    quote = "通勤穿着凉爽"

    atoms = MarketingEvidenceExtractionService._parse_batch(
        snapshot,
        snapshot.notes,
        {
            "evidence": [
                {
                    "note_id": "note-test",
                    "field_path": "content_text",
                    "quote": quote,
                    "text_start": 0,
                    "text_end": len(quote),
                    "track": "value",
                    "aspect": "凉爽",
                    "evidence_type": "experience",
                    "polarity": "support",
                    "scenes": ["通勤"],
                    "audiences": [],
                }
            ]
        },
    )

    assert atoms[0].text_start == snapshot.notes[0].body.index(quote)
    assert atoms[0].text_end == atoms[0].text_start + len(quote)


def test_structured_extraction_maps_whitespace_normalized_quote_back_to_frozen_text() -> None:
    original = _snapshot()
    body = "夏季通勤\n穿着凉爽  透气"
    snapshot = EvidenceSnapshot(
        **{
            **original.__dict__,
            "notes": (
                FrozenEvidenceNote(
                    **{**original.notes[0].__dict__, "body": body}
                ),
            ),
        }
    )
    normalized_quote = "夏季通勤 穿着凉爽 透气"

    atoms = MarketingEvidenceExtractionService._parse_batch(
        snapshot,
        snapshot.notes,
        {
            "evidence": [
                {
                    "note_id": "note-test",
                    "field_path": "content_text",
                    "quote": normalized_quote,
                    "text_start": 0,
                    "text_end": len(normalized_quote),
                    "track": "value",
                    "aspect": "凉感透气",
                    "evidence_type": "experience",
                    "polarity": "support",
                    "scenes": ["夏季", "通勤"],
                    "audiences": [],
                }
            ]
        },
    )

    assert atoms[0].quote == body
    assert atoms[0].text_start == 0
    assert atoms[0].text_end == len(body)


def test_structured_extraction_shape_error_names_only_missing_field_names() -> None:
    snapshot = _snapshot()
    item = {
        "note_id": "note-test",
        "field_path": "content_text",
        "quote": snapshot.notes[0].body,
        "text_start": 0,
        "text_end": len(snapshot.notes[0].body),
        "track": "value",
        "aspect": "凉感透气",
        "evidence_type": "experience",
        "polarity": "support",
        "audiences": [],
    }

    with pytest.raises(
        MarketingConclusionAnalysisError, match="missing=scenes"
    ):
        MarketingEvidenceExtractionService._parse_batch(
            snapshot, snapshot.notes, {"evidence": [item]}
        )


@pytest.mark.asyncio
async def test_structured_extraction_rewrites_one_ungrounded_response_once() -> None:
    snapshot = _snapshot()
    valid = {
        "note_id": "note-test",
        "field_path": "content_text",
        "quote": snapshot.notes[0].body,
        "text_start": 0,
        "text_end": len(snapshot.notes[0].body),
        "track": "value",
        "aspect": "凉感透气",
        "evidence_type": "experience",
        "polarity": "support",
        "scenes": ["夏季", "通勤"],
        "audiences": [],
    }
    llm = SequencedLLM(
        [
            {"evidence": [{**valid, "quote": "模型编造的内容", "text_end": 7}]},
            {"evidence": [valid]},
        ]
    )

    atoms = await MarketingEvidenceExtractionService(llm=llm).extract(snapshot)

    assert len(llm.requests) == 2
    assert atoms[0].quote == snapshot.notes[0].body
    assert "failed validation" in llm.requests[1].messages[-1].content


@pytest.mark.asyncio
async def test_structured_extraction_rewrite_names_the_safe_validation_reason() -> None:
    snapshot = _snapshot()
    valid = {
        "note_id": "note-test",
        "field_path": "content_text",
        "quote": snapshot.notes[0].body,
        "text_start": 0,
        "text_end": len(snapshot.notes[0].body),
        "track": "value",
        "aspect": "凉感透气",
        "evidence_type": "experience",
        "polarity": "support",
        "scenes": ["夏季", "通勤"],
        "audiences": [],
    }
    llm = SequencedLLM(
        [
            {"evidence": [{**valid, "aspect": ""}]},
            {"evidence": [valid]},
        ]
    )

    await MarketingEvidenceExtractionService(llm=llm).extract(snapshot)

    assert "invalid_aspect" in llm.requests[1].messages[-1].content


@pytest.mark.asyncio
async def test_structured_extraction_drops_only_invalid_items_after_bounded_rewrite() -> None:
    snapshot = _snapshot()
    valid = {
        "note_id": "note-test",
        "field_path": "content_text",
        "quote": snapshot.notes[0].body,
        "text_start": 0,
        "text_end": len(snapshot.notes[0].body),
        "track": "value",
        "aspect": "凉感透气",
        "evidence_type": "experience",
        "polarity": "support",
        "scenes": ["夏季", "通勤"],
        "audiences": [],
    }
    invalid = {**valid, "quote": "模型改写的内容", "text_end": 7}
    llm = SequencedLLM(
        [
            {"evidence": [valid, invalid]},
            {"evidence": [valid, invalid]},
        ]
    )

    atoms = await MarketingEvidenceExtractionService(llm=llm).extract(snapshot)

    assert len(llm.requests) == 2
    assert [atom.quote for atom in atoms] == [snapshot.notes[0].body]
