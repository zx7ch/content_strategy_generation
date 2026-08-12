from __future__ import annotations

import json

import httpx
import pytest

from app.api.routes.router import app
from app.content_research.persistence_models import (
    DirectionalEvidencePacketRecord,
    DirectionSourceProjectionRecord,
)
from app.content_research.presearch.service import PresearchService
from app.content_research.service import ContentResearchService, WorkflowRunManagerRuntime
from app.content_research.sources import SourceAdapterRegistry
from app.content_research.sources.base import SourceOperationResult
from app.content_research.sources.canonical_registry import CanonicalSourceRegistry
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.content_research.workflow.directional_pipeline import DirectionalEvidencePipeline
from app.services.llm.types import LLMResponse, TokenUsage
from tests.e2e.test_content_research_source_collection_api import _confirmed_workflow


class FakeLLM:
    async def generate(self, _request):
        return LLMResponse(
            content=json.dumps({"subject_confirmation": "徒步短裤", "competitor_tags": ["迪卡侬"], "research_directions": ["产品营销"]}),
            provider="fake", model="fake", usage=TokenUsage(total_tokens=1), latency_ms=1,
        )


class FakeSourceAdapter:
    def __init__(self, result: SourceOperationResult) -> None:
        self._result = result

    async def discover_candidates(self, _request):
        return self._result


@pytest.fixture()
async def client_factory(tmp_path):
    original = getattr(app.state, "content_research_service", None)
    db_path = str(tmp_path / "direction-evidence.db")

    async def make_client(result: SourceOperationResult):
        app.state.content_research_service = ContentResearchService(
            store=SQLiteContentResearchStore(db_path),
            presearch=PresearchService(FakeLLM(), first_feedback_timeout_seconds=0.05, hard_cutoff_seconds=0.1),
            workflow_runtime=WorkflowRunManagerRuntime(db_path),
            source_registry=SourceAdapterRegistry({"xiaohongshu": FakeSourceAdapter(result)}),
        )
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")

    client = None
    yield make_client
    if client:
        await client.aclose()
    if original is None:
        delattr(app.state, "content_research_service")
    else:
        app.state.content_research_service = original


@pytest.mark.asyncio
async def test_direction_evidence_api_exposes_minimal_paginated_read_model(client_factory):
    client = await client_factory(SourceOperationResult(
        provider="xiaohongshu", operation="discover_candidates", source_kind="search_result_minimal", status="empty", items=[],
    ))
    presearch = await _confirmed_workflow(client)
    service = app.state.content_research_service
    pipeline = DirectionalEvidencePipeline(service._store)

    async def discover(group):
        return [{
            "provider": "xiaohongshu", "canonical_id": "note-1", "canonical_source_id": "note-1",
            "author_id": "author-1", "source_kind": "note_detail", "title": "标题", "content_text": "正文", "raw_payload": {"secret": True},
            "access_token": "secret", "field_availability": {"content_text": "present"},
        }]

    await pipeline.execute(
        workflow_run_id=presearch["workflow_run_id"],
        subagent_task_id="sat-api", direction_id="product_marketing", subject="徒步短裤",
        questions=["产品营销"], competitors=["迪卡侬"], author_cap=1, discover=discover,
    )
    response = await client.get(
        f"/content-research/workflows/{presearch['workflow_run_id']}/directions/product_marketing/evidence?limit=1"
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["counts"] == {"selected_source_count": 1, "eligible_source_count": 1, "independent_source_count": 1}
    assert payload["selections"][0]["selected"] is True
    assert payload["packets"][0]["field_projection"]["title"] == "标题"
    assert "raw_payload" not in str(payload)
    assert "secret" not in str(payload)
    await client.aclose()


@pytest.mark.asyncio
async def test_direction_evidence_api_exposes_safe_comment_packet_and_collection_scope(client_factory):
    client = await client_factory(SourceOperationResult(provider="xiaohongshu", operation="discover_candidates", source_kind="search_result_minimal", status="empty", items=[]))
    presearch = await _confirmed_workflow(client)
    service = app.state.content_research_service
    pipeline = DirectionalEvidencePipeline(service._store)

    async def discover(group):
        return [{"provider": "xiaohongshu", "canonical_id": "note-1", "source_kind": "note_detail", "content_text": "正文"}]

    async def comments(_candidate):
        return SourceOperationResult(provider="xiaohongshu", operation="collect_comments", source_kind="comment", status="partial_completed", items=[{"provider": "xiaohongshu", "canonical_id": "comment-1", "source_kind": "comment", "content_text": "尺码偏小", "author": "用户", "raw_payload": {"secret": True}, "access_token": "secret", "field_availability": {"comment_text": "present", "parent_note_id": "present"}}], completeness="truncated_by_cap", next_cursor="cursor-2")

    await pipeline.execute(workflow_run_id=presearch["workflow_run_id"], subagent_task_id="sat-comment-api", direction_id="product_marketing", subject="徒步短裤", questions=["评论"], competitors=[], author_cap=1, discover=discover, collect_comments=comments, required_comment_fields=("comment_text", "parent_note_id"))
    response = await client.get(f"/content-research/workflows/{presearch['workflow_run_id']}/directions/product_marketing/evidence?limit=10")

    assert response.status_code == 200, response.text
    payload = response.json()
    packet = next(item for item in payload["packets"] if item["retrieval_context"]["source_kind"] == "comment")
    assert packet["field_projection"]["comment_text"] == "尺码偏小"
    assert packet["retrieval_context"]["collection"]["completeness"] == "truncated_by_cap"
    assert payload["comment_collection"]["parents"][0]["next_cursor"] == "cursor-2"
    assert "secret" not in str(payload)
    assert "raw_payload" not in str(payload)
    await client.aclose()


@pytest.mark.asyncio
async def test_direction_evidence_api_exposes_safe_selection_revision_history(client_factory):
    client = await client_factory(SourceOperationResult(provider="xiaohongshu", operation="discover_candidates", source_kind="search_result_minimal", status="empty", items=[]))
    presearch = await _confirmed_workflow(client)
    pipeline = DirectionalEvidencePipeline(app.state.content_research_service._store)

    async def discover(group):
        return [
            {"canonical_id": "note-1", "source_kind": "search_result_minimal", "relevance": 2, "raw_payload": {"secret": True}},
            {"canonical_id": "note-2", "source_kind": "search_result_minimal", "relevance": 1, "access_token": "secret"},
        ]

    async def detail(candidate):
        if candidate["canonical_id"] == "note-1":
            return None
        return {"canonical_id": "note-2", "source_kind": "note_detail", "content_text": "正文", "author": "作者"}

    await pipeline.execute(workflow_run_id=presearch["workflow_run_id"], subagent_task_id="sat-revision-api", direction_id="product_marketing", subject="徒步短裤", questions=["产品营销"], competitors=[], author_cap=1, minimum_samples=1, minimum_independent_authors=1, detail_fetch_cap=2, discover=discover, collect_detail=detail)
    response = await client.get(f"/content-research/workflows/{presearch['workflow_run_id']}/directions/product_marketing/evidence?limit=10")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [item["trigger"]["candidate_id"] for item in payload["selection_revisions"]] == ["note-1", "note-2"]
    assert "blocking_field_unavailable" in payload["selection_revisions"][0]["trigger"]["reasons"]
    assert "secret" not in str(payload)
    assert "raw_payload" not in str(payload)
    await client.aclose()


@pytest.mark.asyncio
async def test_two_directions_keep_independent_projections_and_share_run_canonical_union(client_factory):
    client = await client_factory(SourceOperationResult(provider="xiaohongshu", operation="discover_candidates", source_kind="search_result_minimal", status="empty", items=[]))
    presearch_response = await client.post("/content-research/presearch", json={"seed_text": "徒步短裤", "thread_id": "thread-two-directions"})
    assert presearch_response.status_code == 201
    presearch = presearch_response.json()
    confirm_response = await client.post(
        f"/content-research/briefs/{presearch['brief_id']}/confirm",
        json={
            "confirmed_subject": "徒步短裤",
            "subject_type": "category",
            "selected_competitors": ["迪卡侬"],
            "custom_competitors": [],
            "selected_directions": ["product_marketing", "competitor_discovery"],
        },
    )
    assert confirm_response.status_code == 200, confirm_response.text
    pipeline = DirectionalEvidencePipeline(app.state.content_research_service._store)

    async def discover(group):
        return [{
            "provider": "xiaohongshu", "canonical_id": "shared-note-1", "source_kind": "note_detail",
            "title": "徒步短裤", "content_text": "轻量透气", "author": "作者", "raw_payload": {"secret": True}, "access_token": "secret",
        }]

    await pipeline.execute(workflow_run_id=presearch["workflow_run_id"], subagent_task_id="sat-product-shared", direction_id="product_marketing", subject="徒步短裤", questions=["卖点"], competitors=[], author_cap=1, discover=discover)
    await pipeline.execute(workflow_run_id=presearch["workflow_run_id"], subagent_task_id="sat-competitor-shared", direction_id="competitor_discovery", subject="徒步短裤", questions=["竞品"], competitors=[], author_cap=1, discover=discover)

    product = (await client.get(f"/content-research/workflows/{presearch['workflow_run_id']}/directions/product_marketing/evidence?limit=10")).json()
    competitor = (await client.get(f"/content-research/workflows/{presearch['workflow_run_id']}/directions/competitor_discovery/evidence?limit=10")).json()

    assert product["counts"]["independent_source_count"] == competitor["counts"]["independent_source_count"] == 1
    assert product["packets"][0]["canonical_source_id"] == competitor["packets"][0]["canonical_source_id"]
    assert product["packets"][0]["selection"]["query_group_ids"] != competitor["packets"][0]["selection"]["query_group_ids"]
    assert product["direction_id"] != competitor["direction_id"]
    assert "secret" not in str(product)
    assert "raw_payload" not in str(competitor)
    await client.aclose()


@pytest.mark.asyncio
async def test_direction_evidence_api_counts_complete_run_union_beyond_page_limit(client_factory):
    client = await client_factory(SourceOperationResult(
        provider="xiaohongshu", operation="discover_candidates", source_kind="search_result_minimal", status="empty", items=[],
    ))
    presearch_response = await client.post("/content-research/presearch", json={"seed_text": "徒步短裤", "thread_id": "thread-union-pages"})
    presearch = presearch_response.json()
    confirm_response = await client.post(
        f"/content-research/briefs/{presearch['brief_id']}/confirm",
        json={
            "confirmed_subject": "徒步短裤", "subject_type": "category",
            "selected_competitors": [], "custom_competitors": [],
            "selected_directions": ["product_marketing", "competitor_discovery"],
        },
    )
    assert confirm_response.status_code == 200, confirm_response.text
    store = app.state.content_research_service._store
    canonical_sources = CanonicalSourceRegistry(store)

    def save_projection(direction_id: str, note_id: str, suffix: str) -> None:
        source = canonical_sources.resolve_note(provider="xiaohongshu", note_id=note_id)
        packet_id = f"dep_union_{suffix}"
        store.save_directional_evidence_packet(DirectionalEvidencePacketRecord(
            packet_id, "v1", {"field_projection": {"content_text": note_id}, "retrieval_context": {"source_kind": "note_detail"}},
            workflow_run_id=presearch["workflow_run_id"], research_direction_id=direction_id,
            canonical_source_id=source.id, field_projection_hash=f"hash_{suffix}",
        ))
        store.save_direction_source_projection(DirectionSourceProjectionRecord(
            f"dsp_union_{suffix}", "v1", {}, workflow_run_id=presearch["workflow_run_id"],
            research_direction_id=direction_id, canonical_source_id=source.id, evidence_packet_id=packet_id,
        ))

    for index in range(51):
        save_projection("product_marketing", f"note-{index}", f"product-{index}")
    save_projection("competitor_discovery", "note-0", "competitor-shared")
    save_projection("competitor_discovery", "note-extra", "competitor-extra")

    first_page = await client.get(f"/content-research/workflows/{presearch['workflow_run_id']}/directions/product_marketing/evidence?limit=50")
    later_page = await client.get(f"/content-research/workflows/{presearch['workflow_run_id']}/directions/product_marketing/evidence?offset=50&limit=1")
    competitor = await client.get(f"/content-research/workflows/{presearch['workflow_run_id']}/directions/competitor_discovery/evidence?limit=1")

    assert first_page.status_code == later_page.status_code == competitor.status_code == 200
    assert len(first_page.json()["packets"]) == 50
    assert len(later_page.json()["packets"]) == 1
    assert first_page.json()["counts"]["independent_source_count"] == 52
    assert later_page.json()["counts"]["independent_source_count"] == 52
    assert competitor.json()["counts"]["independent_source_count"] == 52
    await client.aclose()
