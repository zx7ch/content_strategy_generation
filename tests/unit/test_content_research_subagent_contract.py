from __future__ import annotations

import inspect

import pytest

from app.content_research.agents import (
    BrandActivityResearchAgent,
    CommentInsightAgent,
    CompetitorDiscoveryAgent,
    ContentPerformanceResearchAgent,
    KeywordGrowthResearchAgent,
    ProductMarketingResearchAgent,
    SubagentExecutionContext,
    UGCCommunityResearchAgent,
)
from app.content_research.evidence import EvidenceBundleService, EvidenceService
from app.content_research.models import SubagentTaskRecord
from app.content_research.sources import SourceAdapterRegistry, SourceCollectionResult
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore


class FakeSourceAdapter:
    async def collect(self, request):
        return SourceCollectionResult(
            provider="xiaohongshu",
            source_kind=request.source_kind,
            status="completed",
            items=[
                {
                    "schema_version": "content_research_source_payload_v1",
                    "provider": "xiaohongshu",
                    "source_kind": request.source_kind,
                    "source_url": "https://www.xiaohongshu.com/explore/note_1",
                    "canonical_id": "note_1",
                    "captured_at": "2026-07-05T00:00:00+00:00",
                    "raw_payload_hash": "hash_note_1",
                    "cookie_status": "valid",
                    "failure_reason": None,
                    "query_used": request.query,
                    "title": "通勤徒步短裤内容表现好",
                    "content_text": "轻量速干, 评论关注尺码。",
                    "author": "户外作者",
                    "metrics": {"liked_count": 42},
                }
            ],
            cookie_status="valid",
        )


class EmptySourceAdapter:
    async def collect(self, request):
        return SourceCollectionResult(
            provider="xiaohongshu",
            source_kind=request.source_kind,
            status="empty",
            items=[],
            failure_reason="empty_result",
            cookie_status="valid",
        )


@pytest.fixture()
def services(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "content_research.db"))
    evidence_service = EvidenceService(store)
    bundle_service = EvidenceBundleService(store)
    return store, evidence_service, bundle_service


def _task(agent_name: str, direction_id: str = "product_marketing") -> SubagentTaskRecord:
    return SubagentTaskRecord(
        id=f"sat_{direction_id}",
        workflow_run_id="wr_1",
        thread_id="thread_1",
        schema_version="content_research_subagent_task_v1",
        status="queued",
        plan_id="rp_1",
        direction_id=direction_id,
        payload={
            "schema_version": "content_research_subagent_task_v1",
            "agent_name": agent_name,
            "input_payload": {
                "schema_version": "content_research_subagent_input_v1",
                "confirmed_subject": "徒步短裤",
                "direction": {"id": direction_id, "label": "产品营销"},
            },
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent_cls", "direction_id"),
    [
        (ProductMarketingResearchAgent, "product_marketing"),
        (CompetitorDiscoveryAgent, "competitor_discovery"),
        (UGCCommunityResearchAgent, "ugc_community"),
        (CommentInsightAgent, "comment_insight"),
        (BrandActivityResearchAgent, "brand_activity"),
        (KeywordGrowthResearchAgent, "keyword_growth"),
        (ContentPerformanceResearchAgent, "content_performance"),
    ],
)
async def test_first_five_subagents_share_contract_and_return_evidence_backed_finding(services, agent_cls, direction_id):
    _store, evidence_service, bundle_service = services
    agent = agent_cls(evidence_service=evidence_service, bundle_service=bundle_service)

    result = await agent.execute(
        SubagentExecutionContext(
            task=_task(agent.agent_name, direction_id),
            source_registry=SourceAdapterRegistry({"xiaohongshu": FakeSourceAdapter()}),
            query="徒步短裤 产品营销",
        )
    )

    assert result.status == "completed"
    assert result.evidence_records
    assert result.evidence_bundle is not None
    finding = result.findings[0]
    assert finding.finding_id
    assert finding.summary
    assert finding.evidence_refs == [result.evidence_records[0].id]
    assert finding.supporting_fact_ids
    assert finding.evidence_refs or finding.missing_evidence


@pytest.mark.asyncio
async def test_subagent_empty_evidence_returns_missing_evidence(services):
    _store, evidence_service, bundle_service = services
    agent = ProductMarketingResearchAgent(evidence_service=evidence_service, bundle_service=bundle_service)

    result = await agent.execute(
        SubagentExecutionContext(
            task=_task(agent.agent_name),
            source_registry=SourceAdapterRegistry({"xiaohongshu": EmptySourceAdapter()}),
        )
    )

    assert result.status == "partial_completed"
    assert result.evidence_records == []
    assert result.findings[0].evidence_refs == []
    assert result.findings[0].missing_evidence[0]["reason"] == "empty_result"


def test_subagent_modules_do_not_import_spider_or_app_v2():
    import app.content_research.agents.directional as directional

    source = inspect.getsource(directional)

    assert "app.services.xhs_spider" not in source
    assert "app.v2" not in source
