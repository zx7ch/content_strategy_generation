from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.content_research.admission.candidates import source_text_hash
from app.content_research.contracts import build_default_snapshot, policy_hash
from app.content_research.models import ResearchBriefRecord
from app.content_research.persistence_models import (
    CanonicalSourceRecord,
    ClaimAdmissionDecisionRecord,
    ClaimCandidateRecord,
    DirectionalEvidencePacketRecord,
    StageCheckpointRecord,
)
from app.content_research.presearch.service import (
    PresearchChecklist,
    PresearchOutcome,
    PresearchService,
)
from app.content_research.service import (
    ContentResearchService,
    ContentResearchValidationError,
    WorkflowRunManagerRuntime,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.content_research.subject_structure import parse_subject_structure
from app.content_research.workflow.directional_pipeline import compile_query_groups
from app.services.llm.failures import LLMProviderFailure
from app.services.llm.types import LLMResponse, TokenUsage


def _legacy_context(tmp_path):
    db_path = str(tmp_path / "legacy-replay.db")
    store = SQLiteContentResearchStore(db_path)
    frozen_at = datetime(2026, 8, 3, tzinfo=timezone.utc)
    groups = compile_query_groups(
        direction_id="product_marketing",
        subject="夏季防晒穿搭",
        questions=["穿搭"],
        competitors=[],
        run_as_of_at=frozen_at,
    )
    structure = {
        "schema_version": "content_research_subject_structure_v1",
        "canonical_subject": "夏季防晒穿搭",
        "subject_type": "category",
        "core_entities": [{"canonical_name": "防晒服饰", "raw_mentions": ["防晒"]}],
        "research_intents": ["穿搭"],
        "context_modifiers": ["夏季"],
        "synonym_groups": {"防晒服饰": ["防晒衣", "防晒服"]},
        "ambiguities": [],
        "resolution_state": "resolved",
    }
    snapshot, _policies, contracts = build_default_snapshot(
        snapshot_id="rps-legacy",
        workflow_run_id="run-legacy",
        brief_id="rb-legacy",
        plan_id="rp-legacy",
        run_as_of_at=frozen_at,
        direction_ids=("product_marketing",),
        confirmed_subject="夏季防晒穿搭",
        query_groups_by_direction={
            "product_marketing": tuple(
                {
                    "id": group.id,
                    "direction_id": group.direction_id,
                    "normalized_query": group.query,
                    "priority": group.priority,
                    "sort": group.sort,
                    "time_window": dict(group.time_window or {}),
                    "candidate_cap": group.candidate_limit,
                }
                for group in groups
            )
        },
        subject_structure=structure,
        subject_structure_hash="structure-hash",
    )
    legacy_relevance = {
        **snapshot.effective_policy["query_relevance"]["product_marketing"],
        "schema_version": "content_research_query_relevance_v1",
        "algorithm_version": "query_relevance_v1",
        "subject_anchors": ["夏季防晒穿搭"],
        "category_anchors": [],
    }
    legacy_relevance.pop("core_entity_anchors", None)
    legacy_policy = {
        **snapshot.effective_policy,
        "query_relevance": {"product_marketing": legacy_relevance},
    }
    legacy_snapshot = replace(
        snapshot,
        effective_policy=legacy_policy,
        effective_policy_hash=policy_hash(legacy_policy),
    )
    legacy_contract = replace(
        contracts[0],
        metadata={**contracts[0].metadata, "query_relevance": legacy_relevance},
    )
    brief = ResearchBriefRecord(
        id="rb-legacy",
        workflow_run_id="run-legacy",
        thread_id="thread-legacy",
        schema_version="content_research_brief_v1",
        status="confirmed",
        payload={
            "seed_text": "夏季防晒穿搭",
            "confirmed_subject": "夏季防晒穿搭",
            "subject_structure": structure,
        },
    )
    service = ContentResearchService(
        store=store,
        presearch=PresearchService(None),
        workflow_runtime=WorkflowRunManagerRuntime(db_path),
    )
    return service, store, brief, legacy_snapshot, legacy_contract


@pytest.mark.asyncio
async def test_legacy_relevance_revision_is_append_only_and_provider_free(tmp_path):
    service, store, brief, snapshot, contract = _legacy_context(tmp_path)
    operations_before = {
        item.id
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.stage_name == "operation"
    }

    revised_snapshot, revised_contracts = await service._replay_relevance_context(
        brief=brief,
        snapshot=snapshot,
        contracts={"product_marketing": contract},
    )

    relevance = revised_contracts["product_marketing"].metadata["query_relevance"]
    assert relevance["algorithm_version"] == "query_relevance_v2"
    assert relevance["core_entity_anchors"] == ["防晒服饰"]
    assert relevance["allowed_synonyms"] == {"防晒服饰": ["防晒服", "防晒衣"]}
    assert revised_snapshot.id == snapshot.id
    revisions = [
        item
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.stage_name == "relevance_revision"
    ]
    assert len(revisions) == 1
    assert revisions[0].payload["base_snapshot_hash"] == snapshot.effective_policy_hash
    assert {
        item.id
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.stage_name == "operation"
    } == operations_before


@pytest.mark.asyncio
async def test_legacy_revision_serializes_model_generated_synonym_groups(tmp_path):
    service, _store, brief, snapshot, contract = _legacy_context(tmp_path)
    structure_payload = {
        "schema_version": "content_research_subject_structure_v1",
        "canonical_subject": "夏季防晒穿搭",
        "subject_type": "category",
        "core_entities": [{"canonical_name": "防晒服饰", "raw_mentions": ["防晒"]}],
        "research_intents": ["穿搭"],
        "context_modifiers": ["夏季"],
        "synonym_groups": {"防晒服饰": ["防晒衣", "防晒服"]},
        "ambiguities": [],
        "resolution_state": "resolved",
    }
    structure = parse_subject_structure(
        structure_payload,
        normalized_input="夏季防晒穿搭",
    ).structure
    assert structure is not None

    class GeneratedStructurePresearch:
        async def create_llm_task(self, _request):
            async def complete():
                return PresearchOutcome(
                    status="completed",
                    checklist=PresearchChecklist(
                        subject_confirmation="夏季防晒穿搭",
                        competitor_tags=[],
                        research_directions=["product_marketing"],
                        subject_structure=structure,
                        subject_structure_state="confirmed",
                    ),
                )

            return asyncio.create_task(complete())

    service._presearch = GeneratedStructurePresearch()
    legacy_brief = replace(
        brief,
        payload={key: value for key, value in brief.payload.items() if key != "subject_structure"},
    )

    _revised_snapshot, revised_contracts = await service._replay_relevance_context(
        brief=legacy_brief,
        snapshot=snapshot,
        contracts={"product_marketing": contract},
    )

    relevance = revised_contracts["product_marketing"].metadata["query_relevance"]
    assert relevance["allowed_synonyms"] == {"防晒服饰": ["防晒服", "防晒衣"]}


@pytest.mark.asyncio
async def test_legacy_revision_rejects_mismatched_query_groups(tmp_path):
    service, _store, brief, snapshot, contract = _legacy_context(tmp_path)
    malformed = replace(
        contract,
        metadata={
            **contract.metadata,
            "query_relevance": {
                **contract.metadata["query_relevance"],
                "query_group_ids": ["qg-wrong"],
            },
        },
    )

    with pytest.raises(
        ContentResearchValidationError,
        match="query groups do not match",
    ):
        await service._replay_relevance_context(
            brief=brief,
            snapshot=snapshot,
            contracts={"product_marketing": malformed},
        )


class _ConclusionLLM:
    def __init__(self, *, failures_remaining: int = 0) -> None:
        self.calls = 0
        self.failures_remaining = failures_remaining

    async def generate(self, request):
        self.calls += 1
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise LLMProviderFailure(
                "llm_service_unavailable",
                "模型服务暂时不可用",
                True,
                None,
            )
        payload = json.loads(request.messages[-1].content)
        return LLMResponse(
            content=json.dumps(
                {
                    "candidates": [
                        {
                            "track": "need",
                            "statement": "样本明确表达轻量透气需求",
                            "supporting_claim_ids": [
                                item["claim_id"] for item in payload["claims"]
                            ],
                        },
                        {
                            "track": "need",
                            "statement": "另一条较弱但合格的需求结论",
                            "supporting_claim_ids": [
                                item["claim_id"] for item in payload["claims"][:3]
                            ],
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            provider="fake",
            model="fake",
            usage=TokenUsage(total_tokens=1),
            latency_ms=1,
        )


@pytest.mark.asyncio
async def test_conclusion_packet_replay_reuses_checkpoint_without_collection_delta(
    tmp_path,
):
    db_path = str(tmp_path / "marketing-conclusion-replay.db")
    store = SQLiteContentResearchStore(db_path)
    frozen_at = datetime(2026, 8, 5, tzinfo=timezone.utc)
    snapshot, _policies, _contracts = build_default_snapshot(
        snapshot_id="rps-conclusion-replay",
        workflow_run_id="run-conclusion-replay",
        brief_id="rb-conclusion-replay",
        plan_id="rp-conclusion-replay",
        run_as_of_at=frozen_at,
        direction_ids=("product_marketing",),
        primary_marketing_goal="content_seeding",
    )
    store.save_run_policy_snapshot(snapshot)
    store.save_brief(
        ResearchBriefRecord(
            id="rb-conclusion-replay",
            workflow_run_id="run-conclusion-replay",
            thread_id="thread-conclusion-replay",
            schema_version="content_research_brief_v1",
            status="ready",
            payload={
                "schema_version": "content_research_brief_v1",
                "workspace_id": "ws-1",
                "user_id": "user-1",
            },
        )
    )
    quote = "产品营销样本明确提到轻量透气"
    for index, author_id in enumerate(
        ("author-a", "author-a", "author-b", "author-c"), start=1
    ):
        source_id = f"source-{index}"
        packet_id = f"packet-{index}"
        claim_id = f"claim-{index}"
        store.save_canonical_source(
            CanonicalSourceRecord(
                source_id,
                "canonical-source-v1",
                {},
                platform="xiaohongshu",
                platform_source_kind="note",
                platform_source_id=f"note-{index}",
            )
        )
        store.save_directional_evidence_packet(
            DirectionalEvidencePacketRecord(
                packet_id,
                "directional-packet-v1",
                {
                    "field_projection": {
                        "content_text": quote,
                        "source_url": f"https://example.test/{index}",
                        "author_id": author_id,
                    },
                    "field_availability": {"content_text": "present"},
                },
                workflow_run_id="run-conclusion-replay",
                research_direction_id="product_marketing",
                canonical_source_id=source_id,
                field_projection_hash=f"projection-{index}",
            )
        )
        store.save_claim_candidate(
            ClaimCandidateRecord(
                claim_id,
                "claim-candidate-v1",
                {
                    "quote_refs": [
                        {
                            "field_path": "content_text",
                            "quote": quote,
                            "text_start": 0,
                            "text_end": len(quote),
                            "source_text_hash": source_text_hash(quote),
                            "source_url": f"https://example.test/{index}",
                        }
                    ],
                    "scope": {"sample": "selected_packets"},
                },
                workflow_run_id="run-conclusion-replay",
                research_direction_id="product_marketing",
                evidence_packet_id=packet_id,
                statement=quote,
                intent_id="value_proposition",
                claim_type="product_value_expression",
            )
        )
        store.save_claim_admission_decision(
            ClaimAdmissionDecisionRecord(
                f"decision-{index}",
                "admission-decision-v1",
                {
                    "policy_snapshot_hash": snapshot.effective_policy_hash,
                    "reason_codes": [],
                },
                research_direction_id="product_marketing",
                claim_candidate_id=claim_id,
                decision="admitted",
                policy_snapshot_id=snapshot.id,
            )
        )

    llm = _ConclusionLLM(failures_remaining=2)
    service = ContentResearchService(
        store=store,
        presearch=PresearchService(None),
        workflow_runtime=WorkflowRunManagerRuntime(db_path),
        analysis_llm=llm,
    )
    operation_ids_before = {
        item.id
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.stage_name == "operation"
    }
    packet_ids_before = {
        item.id for item in store.list_typed_records(DirectionalEvidencePacketRecord)
    }

    for _ in range(2):
        with pytest.raises(LLMProviderFailure, match="llm_service_unavailable"):
            await service._govern_marketing_conclusions(
                workflow_run_id="run-conclusion-replay",
                research_plan_id="rp-conclusion-replay",
            )
    unavailable = store.list_marketing_conclusion_decisions(
        "run-conclusion-replay", "rp-conclusion-replay"
    )
    assert {(item.track, item.state) for item in unavailable} == {
        ("message", "analysis_unavailable"),
        ("need", "analysis_unavailable"),
        ("value", "analysis_unavailable"),
    }

    first = await service._govern_marketing_conclusions(
        workflow_run_id="run-conclusion-replay",
        research_plan_id="rp-conclusion-replay",
    )
    candidates_after_first = store.list_marketing_conclusion_candidates(
        "run-conclusion-replay", "rp-conclusion-replay"
    )
    decisions_after_first = store.list_marketing_conclusion_decisions(
        "run-conclusion-replay", "rp-conclusion-replay"
    )
    selected_decision = next(
        item for item in decisions_after_first if item.state == "selected"
    )
    assert selected_decision.payload["additional_qualified_count"] == 1
    replay = await service._govern_marketing_conclusions(
        workflow_run_id="run-conclusion-replay",
        research_plan_id="rp-conclusion-replay",
    )

    assert replay.id == first.id
    assert llm.calls == 3
    assert store.list_marketing_conclusion_candidates(
        "run-conclusion-replay", "rp-conclusion-replay"
    ) == candidates_after_first
    assert store.list_marketing_conclusion_decisions(
        "run-conclusion-replay", "rp-conclusion-replay"
    ) == decisions_after_first
    assert len(
        [
            item
            for item in store.list_typed_records(StageCheckpointRecord)
            if item.stage_name == "marketing_conclusion"
        ]
    ) == 1
    assert {
        item.id
        for item in store.list_typed_records(StageCheckpointRecord)
        if item.stage_name == "operation"
    } == operation_ids_before
    assert {
        item.id for item in store.list_typed_records(DirectionalEvidencePacketRecord)
    } == packet_ids_before
