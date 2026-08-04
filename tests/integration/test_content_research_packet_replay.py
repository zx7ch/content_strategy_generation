from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.content_research.contracts import build_default_snapshot, policy_hash
from app.content_research.models import ResearchBriefRecord
from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.presearch.service import (
    PresearchChecklist,
    PresearchOutcome,
    PresearchService,
)
from app.content_research.subject_structure import parse_subject_structure
from app.content_research.service import (
    ContentResearchService,
    ContentResearchValidationError,
    WorkflowRunManagerRuntime,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.content_research.workflow.directional_pipeline import compile_query_groups


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
