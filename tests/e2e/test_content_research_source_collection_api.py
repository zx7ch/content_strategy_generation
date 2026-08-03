from __future__ import annotations

import json

import httpx
import pytest

from app.api.routes.router import app
from app.content_research.presearch.service import PresearchService
from app.content_research.service import ContentResearchService, WorkflowRunManagerRuntime
from app.content_research.sources import SourceAdapterRegistry
from app.content_research.sources.base import SourceOperationResult
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.services.llm.types import LLMResponse, TokenUsage


class FakeLLM:
    async def generate(self, _request):
        return LLMResponse(
            content=json.dumps(
                {
                    "subject_confirmation": "徒步短裤更可能是户外服饰品类，请确认。",
                    "competitor_tags": ["迪卡侬", "凯乐石"],
                    "research_directions": ["产品营销", "用户评论痛点"],
                    "custom_research_question": "",
                    "custom_competitor_input": "",
                    "subject_structure": {
                        "schema_version": "content_research_subject_structure_v1",
                        "canonical_subject": "徒步短裤",
                        "subject_type": "category",
                        "core_entities": [{"canonical_name": "徒步短裤", "raw_mentions": ["徒步短裤"]}],
                        "research_intents": ["产品营销"],
                        "context_modifiers": [],
                        "synonym_groups": {"徒步短裤": ["户外短裤"]},
                        "ambiguities": [],
                        "resolution_state": "resolved",
                    },
                },
                ensure_ascii=False,
            ),
            provider="fake",
            model="fake-model",
            usage=TokenUsage(total_tokens=10),
            latency_ms=1,
        )


class FakeSourceAdapter:
    def __init__(self, result: SourceOperationResult) -> None:
        self.result = result
        self.requests = []

    async def discover_candidates(self, request):
        self.requests.append(request)
        return self.result


@pytest.fixture()
async def client_factory(tmp_path):
    clients = []

    async def make_client(result: SourceOperationResult):
        original = getattr(app.state, "content_research_service", None)
        db_path = str(tmp_path / f"content_research_{len(clients)}.db")
        app.state.content_research_service = ContentResearchService(
            store=SQLiteContentResearchStore(db_path),
            presearch=PresearchService(FakeLLM(), first_feedback_timeout_seconds=0.05, hard_cutoff_seconds=0.1),
            workflow_runtime=WorkflowRunManagerRuntime(db_path),
            source_registry=SourceAdapterRegistry({"xiaohongshu": FakeSourceAdapter(result)}),
        )
        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
        clients.append((client, original))
        return client

    yield make_client

    for client, original in clients:
        await client.aclose()
        if original is None:
            if hasattr(app.state, "content_research_service"):
                delattr(app.state, "content_research_service")
        else:
            app.state.content_research_service = original


async def _confirmed_workflow(client):
    presearch_response = await client.post(
        "/content-research/presearch",
        json={"seed_text": "徒步短裤", "thread_id": "thread-source-api"},
    )
    assert presearch_response.status_code == 201
    presearch = presearch_response.json()
    confirm_response = await client.post(
        f"/content-research/briefs/{presearch['brief_id']}/confirm",
        json={
            "confirmed_subject": "徒步短裤",
            "subject_type": "category",
            "selected_competitors": ["迪卡侬"],
            "custom_competitors": ["凯乐石"],
            "selected_directions": ["product_marketing"],
        },
    )
    assert confirm_response.status_code == 200
    return presearch


@pytest.mark.asyncio
async def test_source_collection_api_returns_payload_and_records_observation(client_factory):
    result = SourceOperationResult(
        provider="xiaohongshu",
        operation="discover_candidates",
        source_kind="search_result_minimal",
        status="completed",
        items=[
            {
                "schema_version": "content_research_source_payload_v1",
                "source_url": "https://www.xiaohongshu.com/explore/note-1",
                "canonical_id": "note-1",
                "source_kind": "search_result_minimal",
                "captured_at": "2026-07-04T00:00:00+00:00",
                "raw_payload_hash": "hash-1",
                "cookie_status": "valid",
                "failure_reason": None,
            }
        ],
        cookie_status="valid",
        metadata={"item_count": 1},
    )
    client = await client_factory(result)
    presearch = await _confirmed_workflow(client)

    response = await client.post(
        f"/content-research/workflows/{presearch['workflow_run_id']}/source-collections",
        json={"query": "徒步短裤", "limit": 5, "sort": "likes"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["cookie_status"] == "valid"
    assert payload["items"][0]["canonical_id"] == "note-1"

    trace_response = await client.get(f"/content-research/workflows/{presearch['workflow_run_id']}/trace")
    assert trace_response.status_code == 200
    event_names = [event["event_name"] for event in trace_response.json()["observation_events"]]
    assert "source_collection_started" in event_names
    assert "source_collection_completed" in event_names
    started = next(event for event in trace_response.json()["observation_events"] if event["event_name"] == "source_collection_started")
    assert "payload" not in started
    assert trace_response.json()["external_api_summary"] == {
        "call_count": 1,
        "completed_count": 1,
        "failed_count": 0,
        "by_provider": {"xiaohongshu": 1},
        "by_operation": {"discover_candidates": 1},
    }

    summary = (await client.get(f"/content-research/workflows/{presearch['workflow_run_id']}")).json()
    assert summary["runtime_run"]["status"] == "running"
    assert summary["subagent_tasks"][0]["status"] == "queued"


@pytest.mark.asyncio
async def test_source_collection_api_auth_failure_is_observable(client_factory):
    result = SourceOperationResult(
        provider="xiaohongshu",
        operation="discover_candidates",
        source_kind="search_result_minimal",
        status="failed",
        items=[],
        failure_reason="auth_required",
        cookie_status="invalid",
    )
    client = await client_factory(result)
    presearch = await _confirmed_workflow(client)

    response = await client.post(
        f"/content-research/workflows/{presearch['workflow_run_id']}/source-collections",
        json={"query": "徒步短裤"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["failure_reason"] == "auth_required"
    assert payload["cookie_status"] == "invalid"

    trace_response = await client.get(f"/content-research/workflows/{presearch['workflow_run_id']}/trace")
    events = trace_response.json()["observation_events"]
    failed = [event for event in events if event["event_name"] == "source_collection_failed"]
    assert failed
    assert "payload" not in failed[0]
    started = next(event for event in events if event["event_name"] == "source_collection_started")
    assert "payload" not in started
    assert trace_response.json()["external_api_summary"] == {
        "call_count": 1,
        "completed_count": 0,
        "failed_count": 1,
        "by_provider": {"xiaohongshu": 1},
        "by_operation": {"discover_candidates": 1},
    }
