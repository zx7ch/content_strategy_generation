from __future__ import annotations

import asyncio

import pytest

from app.content_research.sources.base import (
    CollectCommentsRequest,
    CollectNoteDetailRequest,
    DiscoverCandidatesRequest,
)
from app.content_research.sources.xiaohongshu.adapter import XiaohongshuSourceAdapter
from app.services.xhs_spider import SpiderPermanentError, XHSPost


class FakeSpiderClient:
    async def search_with_retry(self, query: str, num: int = 50, sort: int = 2):
        return [XHSPost(note_id="note_1", title="标题", content="搜索卡内容", author="作者", tags=[], liked_count=1, collected_count=2, comment_count=3, share_count=4, note_url="https://www.xiaohongshu.com/explore/note_1", images=[])]

    async def collect_comment_page(self, **_kwargs):
        return ([{"id": "comment_1", "content": "尺码偏小", "user_info": {"nickname": "用户"}}], "cursor_2", True)

    async def collect_note_detail(self, *, note_id: str, note_url: str):
        assert note_id == "note_1"
        assert note_url == "https://www.xiaohongshu.com/explore/note_1?xsec_token=token"
        return XHSPost(
            note_id=note_id,
            title="详情标题",
            content="详情正文，不是搜索卡内容",
            author="作者",
            tags=["通勤", "短裤"],
            liked_count=10,
            collected_count=20,
            comment_count=30,
            share_count=40,
            note_url=note_url,
            images=[],
            note_type="video",
        )


class DetailAuthFailureSpider(FakeSpiderClient):
    async def collect_note_detail(self, **_kwargs):
        raise SpiderPermanentError("Auth error: cookie expired")


class MissingLoginInformationSpider(FakeSpiderClient):
    async def collect_note_detail(self, **_kwargs):
        raise SpiderPermanentError("无登录信息，或登录信息为空")


class ProviderAccessRejectedSpider(FakeSpiderClient):
    async def collect_note_detail(self, **_kwargs):
        raise SpiderPermanentError("-1")


class TimeoutSpider(FakeSpiderClient):
    async def search_with_retry(self, *_args, **_kwargs):
        await asyncio.sleep(60)


@pytest.mark.asyncio
async def test_discovery_returns_candidates_with_retrieval_context():
    result = await XiaohongshuSourceAdapter(spider_client=FakeSpiderClient()).discover_candidates(DiscoverCandidatesRequest(workflow_run_id="run_1", query="徒步短裤", limit=1))
    assert result.operation == "discover_candidates"
    assert result.source_kind == "search_result"
    assert result.items[0]["retrieval_context"]["rank"] == 1


@pytest.mark.asyncio
async def test_detail_uses_provider_detail_not_search_card_and_supplies_frozen_fields():
    result = await XiaohongshuSourceAdapter(spider_client=FakeSpiderClient()).collect_note_detail(
        CollectNoteDetailRequest(
            workflow_run_id="run_1",
            note_id="note_1",
            note_url="https://www.xiaohongshu.com/explore/note_1?xsec_token=token",
            required_fields=("title", "content_text", "tags", "note_type", "metrics", "metrics_observed_at"),
        )
    )
    assert result.status == "completed"
    assert result.source_kind == "note_detail"
    assert result.items[0]["content_text"] == "详情正文，不是搜索卡内容"
    assert result.items[0]["note_type"] == "video"
    assert result.items[0]["source_url"].endswith("xsec_token=token")
    assert result.field_availability == {
        "title": "present", "content_text": "present", "tags": "present",
        "note_type": "present", "metrics": "present", "metrics_observed_at": "present",
    }


@pytest.mark.asyncio
async def test_detail_auth_failure_is_typed_and_does_not_return_a_search_card():
    result = await XiaohongshuSourceAdapter(spider_client=DetailAuthFailureSpider()).collect_note_detail(
        CollectNoteDetailRequest(workflow_run_id="run_1", note_id="note_1", note_url="https://example.test/note_1")
    )
    assert result.status == "failed"
    assert result.failure_reason == "auth_required"
    assert result.items == []


@pytest.mark.asyncio
async def test_localized_missing_login_information_is_typed_as_auth_required():
    result = await XiaohongshuSourceAdapter(spider_client=MissingLoginInformationSpider()).collect_note_detail(
        CollectNoteDetailRequest(workflow_run_id="run_1", note_id="note_1", note_url="https://example.test/note_1")
    )
    assert result.status == "failed"
    assert result.failure_reason == "auth_required"
    assert result.retryable is False


@pytest.mark.asyncio
async def test_provider_detail_access_rejection_is_not_misclassified_as_transient():
    result = await XiaohongshuSourceAdapter(
        spider_client=ProviderAccessRejectedSpider()
    ).collect_note_detail(
        CollectNoteDetailRequest(
            workflow_run_id="run_1", note_id="note_1", note_url="https://example.test/note_1"
        )
    )
    assert result.failure_reason == "provider_access_rejected"
    assert result.retryable is False


@pytest.mark.asyncio
async def test_discovery_timeout_is_distinguished_from_transient_failure():
    adapter = XiaohongshuSourceAdapter(spider_client=TimeoutSpider())
    adapter._operation_timeout_seconds = 0.01

    result = await adapter.discover_candidates(
        DiscoverCandidatesRequest(workflow_run_id="run_1", query="徒步短裤", limit=1)
    )

    assert result.status == "failed"
    assert result.failure_reason == "timeout"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_comments_are_capped_and_keep_parent_lineage():
    result = await XiaohongshuSourceAdapter(spider_client=FakeSpiderClient()).collect_comments(CollectCommentsRequest(workflow_run_id="run_1", parent_note_id="note_1", note_url="https://www.xiaohongshu.com/explore/note_1?xsec_token=token", limit=1))
    assert result.status == "partial_completed"
    assert result.completeness == "truncated_by_cap"
    assert result.next_cursor == "cursor_2"
    assert result.items[0]["parent_note_id"] == "note_1"
