from __future__ import annotations

import json

import httpx
import pytest

from app.api.routes.router import app
from app.content_research.evidence import EvidenceBundleItemRecord, EvidenceBundleRecord, EvidenceBundleService, EvidenceService
from app.content_research.presearch.service import PresearchService
from app.content_research.service import ContentResearchService, WorkflowRunManagerRuntime
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.services.llm.types import LLMResponse, TokenUsage


class FakeLLM:
    async def generate(self, _request):
        return LLMResponse(
            content=json.dumps(
                {
                    "subject_confirmation": "户外水壶品牌，请确认。",
                    "competitor_tags": ["Hydro Flask", "Owala"],
                    "research_directions": ["产品营销"],
                    "custom_research_question": "",
                    "custom_competitor_input": "",
                },
                ensure_ascii=False,
            ),
            provider="fake",
            model="fake-model",
            usage=TokenUsage(total_tokens=10),
            latency_ms=1,
        )


@pytest.fixture()
async def client_with_db(tmp_path):
    original = getattr(app.state, "content_research_service", None)
    db_path = str(tmp_path / "content_research_bundle.db")
    app.state.content_research_service = ContentResearchService(
        store=SQLiteContentResearchStore(db_path),
        presearch=PresearchService(FakeLLM(), first_feedback_timeout_seconds=0.05, hard_cutoff_seconds=0.1),
        workflow_runtime=WorkflowRunManagerRuntime(db_path),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, db_path
    if original is None:
        delattr(app.state, "content_research_service")
    else:
        app.state.content_research_service = original


async def _confirmed_workflow(client):
    presearch_response = await client.post(
        "/content-research/presearch",
        json={"seed_text": "户外水壶", "thread_id": "thread-bundle-api"},
    )
    assert presearch_response.status_code == 201
    presearch = presearch_response.json()
    confirm_response = await client.post(
        f"/content-research/briefs/{presearch['brief_id']}/confirm",
        json={
            "confirmed_subject": "户外水壶",
            "subject_type": "category",
            "selected_competitors": ["Hydro Flask"],
            "custom_competitors": [],
            "selected_directions": ["product_marketing"],
        },
    )
    assert confirm_response.status_code == 200
    return presearch, confirm_response.json()


def _source_payload(note_id: str) -> dict:
    return {
        "schema_version": "content_research_source_payload_v1",
        "provider": "xiaohongshu",
        "source_kind": "search_result",
        "source_url": f"https://www.xiaohongshu.com/explore/{note_id}",
        "canonical_id": note_id,
        "captured_at": "2026-07-06T00:00:00+08:00",
        "raw_payload_hash": f"hash_{note_id}",
        "cookie_status": "valid",
        "failure_reason": None,
        "query_used": "户外水壶",
        "title": "户外水壶测评",
        "content_text": "保冷和便携挂扣是高频讨论点。",
    }


def _persist_expanded_bundle(db_path: str, *, workflow_run_id: str, brief_id: str, plan_id: str) -> None:
    store = SQLiteContentResearchStore(db_path)
    evidence_service = EvidenceService(store)
    bundle_service = EvidenceBundleService(store)
    supporting = evidence_service.ingest_source_payload(
        workflow_run_id=workflow_run_id,
        research_brief_id=brief_id,
        research_plan_id=plan_id,
        source_payload=_source_payload("note_supporting"),
    )
    conflicting = evidence_service.ingest_source_payload(
        workflow_run_id=workflow_run_id,
        research_brief_id=brief_id,
        research_plan_id=plan_id,
        source_payload=_source_payload("note_conflicting"),
    )
    bundle = EvidenceBundleRecord(
        id="eb_expandable",
        workflow_run_id=workflow_run_id,
        research_brief_id=brief_id,
        research_plan_id=plan_id,
        schema_version="content_research_evidence_bundle_v1",
        status="ready",
        bundle_type="research_direction",
        bundle_version="p1_test_v1",
        summary="保冷和便携挂扣是户外水壶内容的核心信号。",
        coverage={"schema_version": "content_research_bundle_coverage_v1", "source_count": 2},
        missing_evidence=[
            {
                "schema_version": "content_research_missing_evidence_v1",
                "reason": "owned_history_missing",
            }
        ],
    )
    bundle_service.create_bundle(
        bundle,
        [
            EvidenceBundleItemRecord(
                id="ebi_supporting",
                bundle_id=bundle.id,
                evidence_record_id=supporting.id,
                role="supporting_fact",
                sort_order=1,
                schema_version="content_research_evidence_bundle_item_v1",
                payload={"schema_version": "content_research_evidence_bundle_item_payload_v1"},
            ),
            EvidenceBundleItemRecord(
                id="ebi_conflicting",
                bundle_id=bundle.id,
                evidence_record_id=conflicting.id,
                role="conflicting_fact",
                sort_order=2,
                schema_version="content_research_evidence_bundle_item_v1",
                payload={"schema_version": "content_research_evidence_bundle_item_payload_v1"},
            ),
            EvidenceBundleItemRecord(
                id="ebi_missing",
                bundle_id=bundle.id,
                evidence_record_id=None,
                role="missing_evidence",
                sort_order=3,
                schema_version="content_research_evidence_bundle_item_v1",
                payload={
                    "schema_version": "content_research_missing_evidence_v1",
                    "reason": "comment_depth_missing",
                },
            ),
        ],
    )


@pytest.mark.asyncio
async def test_evidence_bundle_api_expands_items_lineage_sources_and_scores(client_with_db):
    client, db_path = client_with_db
    presearch, summary = await _confirmed_workflow(client)
    _persist_expanded_bundle(
        db_path,
        workflow_run_id=presearch["workflow_run_id"],
        brief_id=presearch["brief_id"],
        plan_id=summary["plan"]["id"],
    )

    response = await client.get("/content-research/evidence-bundles/eb_expandable")

    assert response.status_code == 200
    payload = response.json()
    assert payload["bundle_id"] == "eb_expandable"
    assert payload["workflow_run_id"] == presearch["workflow_run_id"]
    assert [item["id"] for item in payload["items"]] == ["ebi_supporting", "ebi_conflicting", "ebi_missing"]
    assert payload["evidence_by_role"]["supporting_fact"][0]["source_id"] == "note_supporting"
    assert payload["evidence_by_role"]["conflicting_fact"][0]["source_id"] == "note_conflicting"
    assert payload["lineage_by_evidence_id"]
    assert payload["source_links"][0]["source_url"].startswith("https://www.xiaohongshu.com/explore/")
    assert len(payload["missing_evidence"]) == 2
    assert payload["priority"]["method"] == "p1_gate_and_top_k_ordering"
    assert payload["evidence_state"] == "signal"


@pytest.mark.asyncio
async def test_evidence_bundle_api_missing_bundle_returns_404(client_with_db):
    client, _db_path = client_with_db

    response = await client.get("/content-research/evidence-bundles/eb_missing")

    assert response.status_code == 404
    assert response.json()["error_code"] == "CONTENT_RESEARCH_PRESEARCH_NOT_FOUND"
