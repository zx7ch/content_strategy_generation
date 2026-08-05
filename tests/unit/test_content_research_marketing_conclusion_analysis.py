from __future__ import annotations

import json
from dataclasses import replace

import pytest

from app.content_research.admission.candidates import source_text_hash
from app.content_research.marketing_conclusion_analysis import (
    MarketingConclusionAnalysisService,
)
from app.content_research.persistence_models import (
    ClaimAdmissionDecisionRecord,
    ClaimCandidateRecord,
)
from app.services.llm.types import LLMResponse, TokenUsage


def marketing_policy() -> dict:
    return {
        "marketing_conclusion_policy": {
            "primary_marketing_goal": "content_seeding",
            "tracks": ["need", "value", "message"],
            "minimum_notes_per_conclusion": 3,
            "minimum_independent_authors_per_conclusion": 2,
            "require_core_and_first_intent_support": True,
            "maximum_primary_conclusions_per_track": 1,
        }
    }


def admitted_claim(claim_id: str, *, quote: str = "样本明确提到轻量透气"):
    claim = ClaimCandidateRecord(
        claim_id,
        "claim",
        {
            "quote_refs": [
                {
                    "field_path": "content_text",
                    "quote": quote,
                    "text_start": 0,
                    "text_end": len(quote),
                    "source_text_hash": source_text_hash(quote),
                    "source_url": f"https://example.test/{claim_id}",
                }
            ],
            "scope": {"sample": "selected_packets"},
            "note_id": f"private-note-{claim_id}",
            "raw_payload": {"private": True},
            "ranking": 999,
        },
        workflow_run_id="run_1",
        research_direction_id="product_marketing",
        evidence_packet_id=f"packet_{claim_id}",
        statement=quote,
        intent_id="value_proposition",
        claim_type="product_value_expression",
    )
    decision = ClaimAdmissionDecisionRecord(
        f"decision_{claim_id}",
        "admission",
        {"policy_snapshot_hash": "frozen", "reason_codes": []},
        research_direction_id="product_marketing",
        claim_candidate_id=claim_id,
        decision="admitted",
        policy_snapshot_id="snapshot_1",
    )
    return decision, claim


class RecordingLLM:
    def __init__(self, response: dict | str) -> None:
        self.response = response
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        content = self.response if isinstance(self.response, str) else json.dumps(self.response)
        return LLMResponse(
            content=content,
            provider="fake",
            model="fake",
            usage=TokenUsage(total_tokens=1),
            latency_ms=1,
        )


@pytest.mark.asyncio
async def test_conclusion_analysis_receives_only_safe_admitted_claim_fields():
    llm = RecordingLLM(
        {
            "candidates": [
                {
                    "track": "need",
                    "statement": "样本明确表达轻量透气需求",
                    "supporting_claim_ids": ["claim_1", "claim_2", "claim_3"],
                }
            ]
        }
    )
    service = MarketingConclusionAnalysisService(llm=llm)

    records = await service.generate(
        workflow_run_id="run_1",
        research_plan_id="plan_1",
        policy=marketing_policy(),
        admitted_claims=[
            admitted_claim("claim_1"),
            admitted_claim("claim_2"),
            admitted_claim("claim_3"),
        ],
    )

    request = llm.requests[-1]
    payload = json.loads(request.messages[-1].content)
    assert payload == {
        "primary_marketing_goal": "content_seeding",
        "tracks": ["need", "value", "message"],
        "claims": [
            {
                "claim_id": "claim_1",
                "quote": "样本明确提到轻量透气",
                "field_path": "content_text",
            },
            {
                "claim_id": "claim_2",
                "quote": "样本明确提到轻量透气",
                "field_path": "content_text",
            },
            {
                "claim_id": "claim_3",
                "quote": "样本明确提到轻量透气",
                "field_path": "content_text",
            },
        ],
    }
    request_text = "\n".join(message.content for message in request.messages)
    for forbidden in (
        "note_id",
        "author_id",
        "raw_payload",
        "ranking",
        "source_url",
        "evidence_packet_id",
    ):
        assert forbidden not in request_text
    assert len(records) == 1
    assert records[0].workflow_run_id == "run_1"
    assert records[0].research_plan_id == "plan_1"
    assert records[0].track == "need"
    assert records[0].payload == {
        "statement": "样本明确表达轻量透气需求",
        "supporting_claim_ids": ["claim_1", "claim_2", "claim_3"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error"),
    [
        ("not-json", "valid JSON"),
        ({"candidates": [{"track": "outcome", "statement": "x", "supporting_claim_ids": ["claim_1"]}]}, "unknown track"),
        ({"candidates": [{"track": "need", "statement": "x", "supporting_claim_ids": ["invented"]}]}, "unknown claim"),
        ({"candidates": [{"track": "need", "statement": "x", "supporting_claim_ids": ["claim_1", "claim_1"]}]}, "duplicate supporting claim"),
        ({"candidates": [{"track": "need", "statement": " ", "supporting_claim_ids": ["claim_1"]}]}, "empty statement"),
    ],
)
async def test_conclusion_analysis_rejects_untrusted_model_lineage(response, error):
    service = MarketingConclusionAnalysisService(llm=RecordingLLM(response))

    with pytest.raises(ValueError, match=error):
        await service.generate(
            workflow_run_id="run_1",
            research_plan_id="plan_1",
            policy=marketing_policy(),
            admitted_claims=[admitted_claim("claim_1")],
        )


@pytest.mark.asyncio
async def test_conclusion_analysis_never_sends_identity_fields_from_forged_admission():
    llm = RecordingLLM({"candidates": []})
    decision, claim = admitted_claim("claim_private")
    forged_payload = dict(claim.payload)
    forged_payload["quote_refs"] = [
        {
            **claim.payload["quote_refs"][0],
            "field_path": "author_id",
            "quote": "private-author-7",
        }
    ]
    forged_claim = replace(claim, payload=forged_payload)
    service = MarketingConclusionAnalysisService(llm=llm)

    with pytest.raises(ValueError, match="safe quote reference"):
        await service.generate(
            workflow_run_id="run_1",
            research_plan_id="plan_1",
            policy=marketing_policy(),
            admitted_claims=[(decision, forged_claim)],
        )

    assert llm.requests == []
