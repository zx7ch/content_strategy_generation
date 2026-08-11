from __future__ import annotations

import asyncio

import pytest

from app.content_research.sources.base import (
    CollectCommentsRequest,
    CollectNoteDetailRequest,
    DiscoverCandidatesRequest,
)
from app.content_research.sources.xiaohongshu.adapter import XiaohongshuSourceAdapter
from app.services.xhs_spider import SpiderPermanentError, SpiderTransientError, XHSPost

VALID_NOTE_ID = "64b64b0d000000001201abcd"
VALID_NOTE_URL = (
    f"https://www.xiaohongshu.com/explore/{VALID_NOTE_ID}"
    "?xsec_token=token&xsec_source=pc_search"
)


class FakeSpiderClient:
    async def search_with_retry(self, query: str, num: int = 50, sort: int = 2):
        return [XHSPost(note_id=VALID_NOTE_ID, title="标题", content="搜索卡内容", author="作者", tags=[], liked_count=1, collected_count=2, comment_count=3, share_count=4, note_url=VALID_NOTE_URL, images=[])]

    async def collect_comment_page(self, **_kwargs):
        return ([{"id": "comment_1", "content": "尺码偏小", "user_info": {"nickname": "用户"}}], "cursor_2", True)

    async def collect_note_detail(self, *, note_id: str, note_url: str):
        assert note_id == VALID_NOTE_ID
        assert note_url == VALID_NOTE_URL
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


class NoteUnavailableSpider(FakeSpiderClient):
    async def collect_note_detail(self, **_kwargs):
        raise SpiderPermanentError("笔记不存在")


class UnknownPermanentFailureSpider(FakeSpiderClient):
    async def collect_note_detail(self, **_kwargs):
        raise SpiderPermanentError("recognized upstream permanent failure")


class RateLimitedSpider(FakeSpiderClient):
    async def collect_note_detail(self, **_kwargs):
        raise SpiderTransientError("429 too many requests")


class RetryingDetailSpider(FakeSpiderClient):
    max_retries = 3
    backoff_base = 1

    def __init__(self):
        self.detail_attempts = 0

    async def collect_note_detail(self, **kwargs):
        self.detail_attempts += 1
        if self.detail_attempts < 3:
            raise SpiderTransientError("temporary connection failure")
        return await super().collect_note_detail(**kwargs)


class TransientThenAuthDetailSpider(FakeSpiderClient):
    max_retries = 3
    backoff_base = 1

    def __init__(self):
        self.detail_attempts = 0

    async def collect_note_detail(self, **_kwargs):
        self.detail_attempts += 1
        if self.detail_attempts == 1:
            raise SpiderTransientError("temporary connection failure")
        raise SpiderPermanentError("Auth error: cookie expired")


class EmptySearchStatsSpider(FakeSpiderClient):
    max_retries = 3

    async def search_with_retry_result(self, *_args, **_kwargs):
        return [], 2


class InvalidCandidateSpider(FakeSpiderClient):
    def __init__(self):
        self.detail_called = False

    async def collect_note_detail(self, **_kwargs):
        self.detail_called = True
        raise AssertionError("invalid candidates must not reach the provider")


class MixedDiscoverySpider(FakeSpiderClient):
    async def search_with_retry(self, query: str, num: int = 50, sort: int = 2):
        return [
            XHSPost(
                note_id="550e8400-e29b-41d4-a716-446655440000-1722574012",
                title="错误卡片",
                content="",
                author="",
                tags=[],
                liked_count=0,
                collected_count=0,
                comment_count=0,
                share_count=0,
                note_url=(
                    "https://www.xiaohongshu.com/explore/"
                    "550e8400-e29b-41d4-a716-446655440000-1722574012"
                    "?xsec_token=token"
                ),
                images=[],
            ),
            XHSPost(
                note_id="64b64b0d000000001201abce",
                title="带 fragment 的卡片",
                content="",
                author="",
                tags=[],
                liked_count=0,
                collected_count=0,
                comment_count=0,
                share_count=0,
                note_url=(
                    "https://www.xiaohongshu.com/explore/64b64b0d000000001201abce"
                    "?xsec_token=token#fragment"
                ),
                images=[],
            ),
            XHSPost(
                note_id="64b64b0d000000001201abcf",
                title="搜索建议卡片",
                content="",
                author="",
                tags=[],
                liked_count=0,
                collected_count=0,
                comment_count=0,
                share_count=0,
                note_url=(
                    "https://www.xiaohongshu.com/explore/64b64b0d000000001201abcf"
                    "?xsec_token=token"
                ),
                images=[],
                provider_item_type="hot_query",
            ),
            (await super().search_with_retry(query, num, sort))[0],
        ]


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
async def test_discovery_filters_invalid_candidates_before_detail_scheduling():
    result = await XiaohongshuSourceAdapter(
        spider_client=MixedDiscoverySpider()
    ).discover_candidates(
        DiscoverCandidatesRequest(
            workflow_run_id="run_1", query="徒步短裤", limit=4
        )
    )

    assert [item["canonical_id"] for item in result.items] == [VALID_NOTE_ID]
    assert result.metadata["candidate_dispositions"] == {
        "invalid_candidate": 3,
        "eligible": 1,
    }


@pytest.mark.asyncio
async def test_detail_uses_provider_detail_not_search_card_and_supplies_frozen_fields():
    result = await XiaohongshuSourceAdapter(spider_client=FakeSpiderClient()).collect_note_detail(
        CollectNoteDetailRequest(
            workflow_run_id="run_1",
            note_id=VALID_NOTE_ID,
            note_url=VALID_NOTE_URL,
            required_fields=("title", "content_text", "tags", "note_type", "metrics", "metrics_observed_at"),
        )
    )
    assert result.status == "completed"
    assert result.source_kind == "note_detail"
    assert result.items[0]["content_text"] == "详情正文，不是搜索卡内容"
    assert result.items[0]["note_type"] == "video"
    assert "xsec_token=token" in result.items[0]["source_url"]
    assert result.field_availability == {
        "title": "present", "content_text": "present", "tags": "present",
        "note_type": "present", "metrics": "present", "metrics_observed_at": "present",
    }


@pytest.mark.asyncio
async def test_detail_auth_failure_is_typed_and_does_not_return_a_search_card():
    result = await XiaohongshuSourceAdapter(spider_client=DetailAuthFailureSpider()).collect_note_detail(
        CollectNoteDetailRequest(workflow_run_id="run_1", note_id=VALID_NOTE_ID, note_url=VALID_NOTE_URL)
    )
    assert result.status == "failed"
    assert result.failure_reason == "auth_required"
    assert result.items == []


@pytest.mark.asyncio
async def test_localized_missing_login_information_is_typed_as_auth_required():
    result = await XiaohongshuSourceAdapter(spider_client=MissingLoginInformationSpider()).collect_note_detail(
        CollectNoteDetailRequest(workflow_run_id="run_1", note_id=VALID_NOTE_ID, note_url=VALID_NOTE_URL)
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
            workflow_run_id="run_1", note_id=VALID_NOTE_ID, note_url=VALID_NOTE_URL
        )
    )
    assert result.failure_reason == "provider_access_rejected"
    assert result.retryable is False


@pytest.mark.asyncio
async def test_invalid_detail_candidate_is_rejected_before_provider_call():
    spider = InvalidCandidateSpider()

    result = await XiaohongshuSourceAdapter(spider_client=spider).collect_note_detail(
        CollectNoteDetailRequest(
            workflow_run_id="run_1",
            note_id="550e8400-e29b-41d4-a716-446655440000-1722574012",
            note_url=(
                "https://www.xiaohongshu.com/explore/"
                "550e8400-e29b-41d4-a716-446655440000-1722574012"
                "?xsec_token=token#fragment"
            ),
        )
    )

    assert result.status == "failed"
    assert result.failure_reason == "invalid_candidate"
    assert result.retryable is False
    assert spider.detail_called is False


@pytest.mark.asyncio
async def test_missing_note_is_non_retryable_candidate_outcome():
    result = await XiaohongshuSourceAdapter(
        spider_client=NoteUnavailableSpider()
    ).collect_note_detail(
        CollectNoteDetailRequest(
            workflow_run_id="run_1", note_id=VALID_NOTE_ID, note_url=VALID_NOTE_URL
        )
    )

    assert result.failure_reason == "note_unavailable"
    assert result.retryable is False


@pytest.mark.asyncio
async def test_unknown_permanent_error_does_not_fall_back_to_transient():
    result = await XiaohongshuSourceAdapter(
        spider_client=UnknownPermanentFailureSpider()
    ).collect_note_detail(
        CollectNoteDetailRequest(
            workflow_run_id="run_1", note_id=VALID_NOTE_ID, note_url=VALID_NOTE_URL
        )
    )

    assert result.failure_reason == "provider_permanent_error"
    assert result.retryable is False
    assert result.metadata["failure_diagnostic"] == {
        "kind": "provider_message_fingerprint",
        "value": "f807a292b36d989e",
    }
    assert "recognized upstream permanent failure" not in repr(result.metadata)


@pytest.mark.asyncio
async def test_rate_limit_is_distinct_from_transport_failure():
    result = await XiaohongshuSourceAdapter(
        spider_client=RateLimitedSpider()
    ).collect_note_detail(
        CollectNoteDetailRequest(
            workflow_run_id="run_1", note_id=VALID_NOTE_ID, note_url=VALID_NOTE_URL
        )
    )

    assert result.failure_reason == "rate_limited"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_detail_reports_actual_automatic_retry_count(monkeypatch):
    spider = RetryingDetailSpider()

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    result = await XiaohongshuSourceAdapter(spider_client=spider).collect_note_detail(
        CollectNoteDetailRequest(
            workflow_run_id="run_1", note_id=VALID_NOTE_ID, note_url=VALID_NOTE_URL
        )
    )

    assert result.status == "completed"
    assert spider.detail_attempts == 3
    assert result.metadata["automatic_retry_count"] == 2
    assert result.metadata["automatic_retry_limit"] == 3


@pytest.mark.asyncio
async def test_permanent_detail_outcome_keeps_prior_automatic_retry_count(monkeypatch):
    spider = TransientThenAuthDetailSpider()

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    result = await XiaohongshuSourceAdapter(spider_client=spider).collect_note_detail(
        CollectNoteDetailRequest(
            workflow_run_id="run_1", note_id=VALID_NOTE_ID, note_url=VALID_NOTE_URL
        )
    )

    assert result.failure_reason == "auth_required"
    assert spider.detail_attempts == 2
    assert result.metadata["automatic_retry_count"] == 1
    assert result.metadata["automatic_retry_limit"] == 3


@pytest.mark.asyncio
async def test_empty_discovery_keeps_actual_automatic_retry_count():
    result = await XiaohongshuSourceAdapter(
        spider_client=EmptySearchStatsSpider()
    ).discover_candidates(
        DiscoverCandidatesRequest(workflow_run_id="run_1", query="徒步短裤", limit=1)
    )

    assert result.status == "empty"
    assert result.metadata["automatic_retry_count"] == 2
    assert result.metadata["automatic_retry_limit"] == 3


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
