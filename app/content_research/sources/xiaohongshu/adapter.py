"""Xiaohongshu source adapter for Content Research source collection."""

from __future__ import annotations

import asyncio

from app.content_research.sources.base import (
    CollectCommentsRequest,
    CollectNoteDetailRequest,
    DiscoverCandidatesRequest,
    ProviderCapability,
    SourceOperationResult,
)
from app.content_research.sources.xiaohongshu.normalizer import XiaohongshuSourceNormalizer
from app.content_research.sources.xiaohongshu.types import (
    COOKIE_STATUS_INVALID,
    COOKIE_STATUS_UNKNOWN,
    COOKIE_STATUS_VALID,
    FAILURE_AUTH_REQUIRED,
    FAILURE_EMPTY_RESULT,
    FAILURE_PARSER_ERROR,
    FAILURE_PROVIDER_ACCESS_REJECTED,
    FAILURE_RATE_LIMITED,
    FAILURE_TIMEOUT,
    FAILURE_TRANSIENT_ERROR,
    FAILURE_UNSUPPORTED_SOURCE_KIND,
    SOURCE_KIND_COMMENT,
    SOURCE_KIND_NOTE_DETAIL,
    SOURCE_KIND_SEARCH_RESULT,
    SOURCE_PROVIDER,
    STATUS_COMPLETED,
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_PARTIAL_COMPLETED,
)
from app.services.xhs_spider import SpiderPermanentError, SpiderTransientError, XHSSpiderClient


class XiaohongshuSourceAdapter:
    def __init__(
        self,
        *,
        spider_client=None,
        normalizer: XiaohongshuSourceNormalizer | None = None,
    ) -> None:
        self._spider_client = spider_client or XHSSpiderClient()
        self._normalizer = normalizer or XiaohongshuSourceNormalizer()
        self._operation_timeout_seconds = 15.0

    def capabilities(self) -> tuple[ProviderCapability, ...]:
        return (
            ProviderCapability("discover_candidates", "supported", ("title", "author", "metrics"), {"max_limit": 50}),
            ProviderCapability("collect_note_detail", "supported", ("title", "content_text", "tags", "note_type", "metrics", "metrics_observed_at")),
            ProviderCapability("collect_comments", "supported", ("content_text", "author", "parent_note_id"), {"top_level_only": True}),
        )

    async def discover_candidates(self, request: DiscoverCandidatesRequest) -> SourceOperationResult:
        try:
            posts = await asyncio.wait_for(self._spider_client.search_with_retry(request.query, num=max(1, request.limit), sort=_sort_value(request.sort)), timeout=self._operation_timeout_seconds)
        except asyncio.TimeoutError:
            return self._operation_failure("discover_candidates", SOURCE_KIND_SEARCH_RESULT, FAILURE_TIMEOUT)
        except SpiderTransientError:
            return self._operation_failure("discover_candidates", SOURCE_KIND_SEARCH_RESULT, FAILURE_TRANSIENT_ERROR)
        except SpiderPermanentError as exc:
            return self._operation_failure("discover_candidates", SOURCE_KIND_SEARCH_RESULT, _classify_permanent_error(str(exc)))
        if not posts:
            return SourceOperationResult(SOURCE_PROVIDER, "discover_candidates", SOURCE_KIND_SEARCH_RESULT, STATUS_EMPTY, [], FAILURE_EMPTY_RESULT, COOKIE_STATUS_VALID, completeness="complete")
        try:
            items = [
                {**self._normalizer.normalize_search_result(post, query=request.query, source_kind=SOURCE_KIND_SEARCH_RESULT), "retrieval_context": {"query": request.query, "rank": index + 1, "sort": request.sort}}
                for index, post in enumerate(posts[:request.limit])
            ]
        except Exception:
            return self._operation_failure("discover_candidates", SOURCE_KIND_SEARCH_RESULT, FAILURE_PARSER_ERROR, cookie_status=COOKIE_STATUS_VALID)
        return SourceOperationResult(SOURCE_PROVIDER, "discover_candidates", SOURCE_KIND_SEARCH_RESULT, STATUS_COMPLETED, items, None, COOKIE_STATUS_VALID, completeness="complete", metadata={"item_count": len(items), "operation_fingerprint": _operation_fingerprint(request)})

    async def collect_note_detail(self, request: CollectNoteDetailRequest) -> SourceOperationResult:
        try:
            post = await asyncio.wait_for(self._spider_client.collect_note_detail(
                note_id=request.note_id, note_url=request.note_url,
            ), timeout=self._operation_timeout_seconds)
        except asyncio.TimeoutError:
            return self._operation_failure("collect_note_detail", SOURCE_KIND_NOTE_DETAIL, FAILURE_TIMEOUT)
        except SpiderTransientError:
            return self._operation_failure("collect_note_detail", SOURCE_KIND_NOTE_DETAIL, FAILURE_TRANSIENT_ERROR)
        except SpiderPermanentError as exc:
            return self._operation_failure("collect_note_detail", SOURCE_KIND_NOTE_DETAIL, _classify_permanent_error(str(exc)))
        try:
            item = self._normalizer.normalize_note_detail(post, required_fields=request.required_fields)
        except Exception:
            return self._operation_failure("collect_note_detail", SOURCE_KIND_NOTE_DETAIL, FAILURE_PARSER_ERROR, cookie_status=COOKIE_STATUS_VALID)
        return SourceOperationResult(
            SOURCE_PROVIDER, "collect_note_detail", SOURCE_KIND_NOTE_DETAIL, STATUS_COMPLETED, [item],
            cookie_status=COOKIE_STATUS_VALID, completeness="complete",
            field_availability=item["field_availability"],
            metadata={"note_id": request.note_id, "operation_fingerprint": _operation_fingerprint(request)},
        )

    async def collect_comments(self, request: CollectCommentsRequest) -> SourceOperationResult:
        if not request.top_level_only or request.reply_depth_limit != 0:
            return self._operation_failure("collect_comments", SOURCE_KIND_COMMENT, FAILURE_UNSUPPORTED_SOURCE_KIND)
        try:
            comments, next_cursor, has_more = await asyncio.wait_for(
                self._spider_client.collect_comment_page(
                    note_id=request.parent_note_id, note_url=request.note_url, cursor=request.cursor,
                ),
                timeout=self._operation_timeout_seconds,
            )
        except asyncio.TimeoutError:
            return self._operation_failure("collect_comments", SOURCE_KIND_COMMENT, FAILURE_TIMEOUT)
        except SpiderTransientError:
            return self._operation_failure("collect_comments", SOURCE_KIND_COMMENT, FAILURE_TRANSIENT_ERROR)
        except SpiderPermanentError as exc:
            return self._operation_failure("collect_comments", SOURCE_KIND_COMMENT, _classify_permanent_error(str(exc)))
        try:
            items = [self._normalizer.normalize_comment(item, parent_note_id=request.parent_note_id) for item in comments[:request.limit]]
        except Exception:
            return self._operation_failure("collect_comments", SOURCE_KIND_COMMENT, FAILURE_PARSER_ERROR, cookie_status=COOKIE_STATUS_VALID)
        if not items:
            return SourceOperationResult(SOURCE_PROVIDER, "collect_comments", SOURCE_KIND_COMMENT, STATUS_EMPTY, [], FAILURE_EMPTY_RESULT, COOKIE_STATUS_VALID, next_cursor=next_cursor, completeness="complete")
        truncated = has_more or len(comments) > request.limit
        return SourceOperationResult(SOURCE_PROVIDER, "collect_comments", SOURCE_KIND_COMMENT, STATUS_PARTIAL_COMPLETED if truncated else STATUS_COMPLETED, items, cookie_status=COOKIE_STATUS_VALID, next_cursor=next_cursor, completeness="truncated_by_cap" if truncated else "complete", metadata={"parent_note_id": request.parent_note_id, "operation_fingerprint": _operation_fingerprint(request)})

    def _operation_failure(self, operation: str, source_kind: str, reason: str, *, cookie_status: str = COOKIE_STATUS_UNKNOWN) -> SourceOperationResult:
        return SourceOperationResult(SOURCE_PROVIDER, operation, source_kind, STATUS_FAILED, [], reason, COOKIE_STATUS_INVALID if reason == FAILURE_AUTH_REQUIRED else cookie_status, completeness="unavailable", retryable=reason in {FAILURE_TIMEOUT, FAILURE_TRANSIENT_ERROR, FAILURE_RATE_LIMITED})


def _classify_permanent_error(message: str) -> str:
    lower = message.lower()
    if any(token in lower for token in (
        "auth", "cookie", "login", "unauthorized", "forbidden",
        "登录已过期", "未登录", "请先登录", "身份验证", "无登录信息", "登录信息为空",
    )):
        return FAILURE_AUTH_REQUIRED
    if any(token in lower for token in ("rate limit", "too many requests", "429")):
        return FAILURE_RATE_LIMITED
    if lower.strip() in {"-1", "provider rejected detail request"} or "browser_session_provider:" in lower:
        return FAILURE_PROVIDER_ACCESS_REJECTED
    return FAILURE_TRANSIENT_ERROR


def _sort_value(sort: str) -> int:
    return {
        "general": 0,
        "latest": 1,
        "likes": 2,
        "comments": 3,
        "collections": 4,
    }.get(sort, 2)


def _operation_fingerprint(request: object) -> str:
    import hashlib
    import json
    return hashlib.sha256(json.dumps(request.__dict__, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
