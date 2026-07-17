"""Xiaohongshu source adapter for Content Research source collection."""

from __future__ import annotations

from app.content_research.sources.base import SourceCollectionRequest, SourceCollectionResult
from app.content_research.sources.xiaohongshu.normalizer import XiaohongshuSourceNormalizer
from app.content_research.sources.xiaohongshu.types import (
    COOKIE_STATUS_INVALID,
    COOKIE_STATUS_UNKNOWN,
    COOKIE_STATUS_VALID,
    FAILURE_AUTH_REQUIRED,
    FAILURE_EMPTY_RESULT,
    FAILURE_PARSER_ERROR,
    FAILURE_RATE_LIMITED,
    FAILURE_TRANSIENT_ERROR,
    FAILURE_UNSUPPORTED_SOURCE_KIND,
    SOURCE_PROVIDER,
    STATUS_COMPLETED,
    STATUS_EMPTY,
    STATUS_FAILED,
    SOURCE_KIND_SEARCH_RESULT,
    SOURCE_KIND_SEARCH_RESULT_MINIMAL,
    IMPLEMENTED_SOURCE_KINDS,
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

    async def collect(self, request: SourceCollectionRequest) -> SourceCollectionResult:
        # The bundled spider currently exposes search only. Do not label note
        # detail, comments, or topic pages as supported merely because their
        # payload schema already exists.
        if request.source_kind not in IMPLEMENTED_SOURCE_KINDS:
            return SourceCollectionResult(
                provider=SOURCE_PROVIDER,
                source_kind=request.source_kind,
                status=STATUS_FAILED,
                items=[],
                failure_reason=FAILURE_UNSUPPORTED_SOURCE_KIND,
                cookie_status=COOKIE_STATUS_UNKNOWN,
            )

        try:
            posts = await self._spider_client.search_with_retry(
                request.query,
                num=max(1, request.limit),
                sort=_sort_value(request.sort),
            )
        except SpiderTransientError:
            return self._failed(request, FAILURE_TRANSIENT_ERROR, cookie_status=COOKIE_STATUS_UNKNOWN)
        except SpiderPermanentError as exc:
            reason = _classify_permanent_error(str(exc))
            cookie_status = COOKIE_STATUS_INVALID if reason == FAILURE_AUTH_REQUIRED else COOKIE_STATUS_UNKNOWN
            return self._failed(request, reason, cookie_status=cookie_status)

        if not posts:
            return SourceCollectionResult(
                provider=SOURCE_PROVIDER,
                source_kind=request.source_kind,
                status=STATUS_EMPTY,
                items=[],
                failure_reason=FAILURE_EMPTY_RESULT,
                cookie_status=COOKIE_STATUS_VALID,
            )

        try:
            items = [
                self._normalizer.normalize_search_result(
                    post,
                    query=request.query,
                    source_kind=request.source_kind,
                )
                for post in posts
            ]
        except Exception:
            return self._failed(request, FAILURE_PARSER_ERROR, cookie_status=COOKIE_STATUS_VALID)

        return SourceCollectionResult(
            provider=SOURCE_PROVIDER,
            source_kind=request.source_kind,
            status=STATUS_COMPLETED,
            items=items,
            cookie_status=COOKIE_STATUS_VALID,
            metadata={"item_count": len(items)},
        )

    def _failed(
        self,
        request: SourceCollectionRequest,
        reason: str,
        *,
        cookie_status: str,
    ) -> SourceCollectionResult:
        return SourceCollectionResult(
            provider=SOURCE_PROVIDER,
            source_kind=request.source_kind,
            status=STATUS_FAILED,
            items=[
                self._normalizer.build_failure_payload(
                    workflow_run_id=request.workflow_run_id,
                    query=request.query,
                    source_kind=request.source_kind,
                    failure_reason=reason,
                    cookie_status=cookie_status,
                )
            ],
            failure_reason=reason,
            cookie_status=cookie_status,
        )


def _classify_permanent_error(message: str) -> str:
    lower = message.lower()
    if any(token in lower for token in ("auth", "cookie", "login", "unauthorized", "forbidden")):
        return FAILURE_AUTH_REQUIRED
    if any(token in lower for token in ("rate limit", "too many requests", "429")):
        return FAILURE_RATE_LIMITED
    return FAILURE_TRANSIENT_ERROR


def _sort_value(sort: str) -> int:
    return {
        "general": 0,
        "latest": 1,
        "likes": 2,
        "comments": 3,
        "collections": 4,
    }.get(sort, 2)
