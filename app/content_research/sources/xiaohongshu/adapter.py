"""Xiaohongshu source adapter for Content Research source collection."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Awaitable, Callable
from typing import TypeVar
from urllib.parse import parse_qs, urlparse

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
    FAILURE_INVALID_CANDIDATE,
    FAILURE_NOTE_UNAVAILABLE,
    FAILURE_PARSER_ERROR,
    FAILURE_PROVIDER_ACCESS_REJECTED,
    FAILURE_PROVIDER_PERMANENT_ERROR,
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

_T = TypeVar("_T")


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
        self._automatic_retry_limit = max(
            int(getattr(self._spider_client, "max_retries", 0) or 0), 0
        )
        self._retry_backoff_base = max(
            int(getattr(self._spider_client, "backoff_base", 1) or 1), 1
        )

    def capabilities(self) -> tuple[ProviderCapability, ...]:
        return (
            ProviderCapability("discover_candidates", "supported", ("title", "author", "metrics"), {"max_limit": 50}),
            ProviderCapability("collect_note_detail", "supported", ("title", "content_text", "tags", "note_type", "metrics", "metrics_observed_at")),
            ProviderCapability("collect_comments", "supported", ("content_text", "author", "parent_note_id"), {"top_level_only": True}),
        )

    def authentication_ready(self) -> bool:
        checker = getattr(self._spider_client, "authentication_ready", None)
        return bool(checker()) if callable(checker) else False

    async def discover_candidates(self, request: DiscoverCandidatesRequest) -> SourceOperationResult:
        automatic_retry_count = 0
        try:
            search_with_stats = getattr(
                self._spider_client, "search_with_retry_result", None
            )
            if callable(search_with_stats):
                posts, automatic_retry_count = await asyncio.wait_for(
                    search_with_stats(
                        request.query,
                        num=max(1, request.limit),
                        sort=_sort_value(request.sort),
                    ),
                    timeout=(
                        self._operation_timeout_seconds
                        * (self._automatic_retry_limit + 1)
                        + sum(
                            self._retry_backoff_base ** retry_no
                            for retry_no in range(1, self._automatic_retry_limit + 1)
                        )
                    ),
                )
            else:
                posts = await asyncio.wait_for(
                    self._spider_client.search_with_retry(
                        request.query,
                        num=max(1, request.limit),
                        sort=_sort_value(request.sort),
                    ),
                    timeout=self._operation_timeout_seconds,
                )
        except asyncio.TimeoutError as exc:
            return self._operation_failure(
                "discover_candidates",
                SOURCE_KIND_SEARCH_RESULT,
                FAILURE_TIMEOUT,
                automatic_retry_count=_retry_count(exc),
            )
        except SpiderTransientError as exc:
            return self._operation_failure(
                "discover_candidates",
                SOURCE_KIND_SEARCH_RESULT,
                _classify_transient_error(str(exc)),
                automatic_retry_count=_retry_count(exc),
                diagnostic_message=str(exc),
            )
        except SpiderPermanentError as exc:
            return self._operation_failure(
                "discover_candidates",
                SOURCE_KIND_SEARCH_RESULT,
                _classify_permanent_error(str(exc)),
                automatic_retry_count=_retry_count(exc),
                diagnostic_message=str(exc),
            )
        if not posts:
            return SourceOperationResult(
                SOURCE_PROVIDER,
                "discover_candidates",
                SOURCE_KIND_SEARCH_RESULT,
                STATUS_EMPTY,
                [],
                FAILURE_EMPTY_RESULT,
                COOKIE_STATUS_VALID,
                completeness="complete",
                metadata={
                    "candidate_dispositions": {
                        FAILURE_INVALID_CANDIDATE: 0,
                        "eligible": 0,
                    },
                    "automatic_retry_count": automatic_retry_count,
                    "automatic_retry_limit": self._automatic_retry_limit,
                    "operation_fingerprint": _operation_fingerprint(request),
                },
            )
        items = []
        invalid_candidate_count = 0
        for index, post in enumerate(posts[:request.limit]):
            try:
                item = self._normalizer.normalize_search_result(
                    post,
                    query=request.query,
                    source_kind=SOURCE_KIND_SEARCH_RESULT,
                )
            except Exception:
                invalid_candidate_count += 1
                continue
            if not _is_detail_eligible_candidate(
                str(item.get("canonical_id") or ""),
                str(item.get("source_url") or ""),
                provider_item_type=str(item.get("provider_item_type") or ""),
            ):
                invalid_candidate_count += 1
                continue
            items.append(
                {
                    **item,
                    "retrieval_context": {
                        "query": request.query,
                        "rank": index + 1,
                        "sort": request.sort,
                    },
                }
            )
        metadata = {
            "item_count": len(items),
            "candidate_dispositions": {
                FAILURE_INVALID_CANDIDATE: invalid_candidate_count,
                "eligible": len(items),
            },
            "automatic_retry_count": automatic_retry_count,
            "automatic_retry_limit": self._automatic_retry_limit,
            "operation_fingerprint": _operation_fingerprint(request),
        }
        return SourceOperationResult(
            SOURCE_PROVIDER,
            "discover_candidates",
            SOURCE_KIND_SEARCH_RESULT,
            STATUS_COMPLETED if items else STATUS_EMPTY,
            items,
            None if items else FAILURE_EMPTY_RESULT,
            COOKIE_STATUS_VALID,
            completeness="complete",
            metadata=metadata,
        )

    async def collect_note_detail(self, request: CollectNoteDetailRequest) -> SourceOperationResult:
        if not _is_detail_eligible_candidate(request.note_id, request.note_url):
            return self._operation_failure(
                "collect_note_detail",
                SOURCE_KIND_NOTE_DETAIL,
                FAILURE_INVALID_CANDIDATE,
            )
        try:
            post, automatic_retry_count = await _run_with_automatic_retries(
                lambda: asyncio.wait_for(
                    self._spider_client.collect_note_detail(
                        note_id=request.note_id,
                        note_url=request.note_url,
                    ),
                    timeout=self._operation_timeout_seconds,
                ),
                retry_limit=self._automatic_retry_limit,
                backoff_base=self._retry_backoff_base,
            )
        except asyncio.TimeoutError as exc:
            return self._operation_failure(
                "collect_note_detail",
                SOURCE_KIND_NOTE_DETAIL,
                FAILURE_TIMEOUT,
                automatic_retry_count=_retry_count(exc),
            )
        except SpiderTransientError as exc:
            return self._operation_failure(
                "collect_note_detail",
                SOURCE_KIND_NOTE_DETAIL,
                _classify_transient_error(str(exc)),
                automatic_retry_count=_retry_count(exc),
                diagnostic_message=str(exc),
            )
        except SpiderPermanentError as exc:
            return self._operation_failure(
                "collect_note_detail",
                SOURCE_KIND_NOTE_DETAIL,
                _classify_permanent_error(str(exc)),
                automatic_retry_count=_retry_count(exc),
                diagnostic_message=str(exc),
            )
        try:
            item = self._normalizer.normalize_note_detail(post, required_fields=request.required_fields)
        except Exception:
            return self._operation_failure(
                "collect_note_detail",
                SOURCE_KIND_NOTE_DETAIL,
                FAILURE_PARSER_ERROR,
                cookie_status=COOKIE_STATUS_VALID,
                automatic_retry_count=automatic_retry_count,
            )
        return SourceOperationResult(
            SOURCE_PROVIDER, "collect_note_detail", SOURCE_KIND_NOTE_DETAIL, STATUS_COMPLETED, [item],
            cookie_status=COOKIE_STATUS_VALID, completeness="complete",
            field_availability=item["field_availability"],
            metadata={
                "note_id": request.note_id,
                "operation_fingerprint": _operation_fingerprint(request),
                "automatic_retry_count": automatic_retry_count,
                "automatic_retry_limit": self._automatic_retry_limit,
            },
        )

    async def collect_comments(self, request: CollectCommentsRequest) -> SourceOperationResult:
        if not request.top_level_only or request.reply_depth_limit != 0:
            return self._operation_failure("collect_comments", SOURCE_KIND_COMMENT, FAILURE_UNSUPPORTED_SOURCE_KIND)
        try:
            (comments, next_cursor, has_more), automatic_retry_count = (
                await _run_with_automatic_retries(
                    lambda: asyncio.wait_for(
                        self._spider_client.collect_comment_page(
                            note_id=request.parent_note_id,
                            note_url=request.note_url,
                            cursor=request.cursor,
                        ),
                        timeout=self._operation_timeout_seconds,
                    ),
                    retry_limit=self._automatic_retry_limit,
                    backoff_base=self._retry_backoff_base,
                )
            )
        except asyncio.TimeoutError as exc:
            return self._operation_failure(
                "collect_comments",
                SOURCE_KIND_COMMENT,
                FAILURE_TIMEOUT,
                automatic_retry_count=_retry_count(exc),
            )
        except SpiderTransientError as exc:
            return self._operation_failure(
                "collect_comments",
                SOURCE_KIND_COMMENT,
                _classify_transient_error(str(exc)),
                automatic_retry_count=_retry_count(exc),
                diagnostic_message=str(exc),
            )
        except SpiderPermanentError as exc:
            return self._operation_failure(
                "collect_comments",
                SOURCE_KIND_COMMENT,
                _classify_permanent_error(str(exc)),
                automatic_retry_count=_retry_count(exc),
                diagnostic_message=str(exc),
            )
        try:
            items = [self._normalizer.normalize_comment(item, parent_note_id=request.parent_note_id) for item in comments[:request.limit]]
        except Exception:
            return self._operation_failure(
                "collect_comments",
                SOURCE_KIND_COMMENT,
                FAILURE_PARSER_ERROR,
                cookie_status=COOKIE_STATUS_VALID,
                automatic_retry_count=automatic_retry_count,
            )
        if not items:
            return SourceOperationResult(
                SOURCE_PROVIDER,
                "collect_comments",
                SOURCE_KIND_COMMENT,
                STATUS_EMPTY,
                [],
                FAILURE_EMPTY_RESULT,
                COOKIE_STATUS_VALID,
                next_cursor=next_cursor,
                completeness="complete",
                metadata={
                    "parent_note_id": request.parent_note_id,
                    "automatic_retry_count": automatic_retry_count,
                    "automatic_retry_limit": self._automatic_retry_limit,
                    "operation_fingerprint": _operation_fingerprint(request),
                },
            )
        truncated = has_more or len(comments) > request.limit
        return SourceOperationResult(SOURCE_PROVIDER, "collect_comments", SOURCE_KIND_COMMENT, STATUS_PARTIAL_COMPLETED if truncated else STATUS_COMPLETED, items, cookie_status=COOKIE_STATUS_VALID, next_cursor=next_cursor, completeness="truncated_by_cap" if truncated else "complete", metadata={"parent_note_id": request.parent_note_id, "operation_fingerprint": _operation_fingerprint(request), "automatic_retry_count": automatic_retry_count, "automatic_retry_limit": self._automatic_retry_limit})

    def _operation_failure(
        self,
        operation: str,
        source_kind: str,
        reason: str,
        *,
        cookie_status: str = COOKIE_STATUS_UNKNOWN,
        automatic_retry_count: int = 0,
        diagnostic_message: str | None = None,
    ) -> SourceOperationResult:
        metadata = {
            "automatic_retry_count": automatic_retry_count,
            "automatic_retry_limit": self._automatic_retry_limit,
        }
        if diagnostic_message:
            metadata["failure_diagnostic"] = _failure_diagnostic(diagnostic_message)
        return SourceOperationResult(
            SOURCE_PROVIDER,
            operation,
            source_kind,
            STATUS_FAILED,
            [],
            reason,
            COOKIE_STATUS_INVALID if reason == FAILURE_AUTH_REQUIRED else cookie_status,
            completeness="unavailable",
            retryable=reason in {FAILURE_TIMEOUT, FAILURE_TRANSIENT_ERROR, FAILURE_RATE_LIMITED},
            metadata=metadata,
        )


def _classify_permanent_error(message: str) -> str:
    lower = message.lower()
    if (
        lower.strip() in {"-1", "provider rejected detail request"}
        or "browser_session_provider:" in lower
        or any(token in lower for token in ("risk control", "access denied", "风控", "访问受限"))
    ):
        return FAILURE_PROVIDER_ACCESS_REJECTED
    if any(token in lower for token in (
        "auth", "cookie", "login", "unauthorized", "forbidden",
        "登录已过期", "未登录", "请先登录", "身份验证", "无登录信息", "登录信息为空",
    )):
        return FAILURE_AUTH_REQUIRED
    if any(token in lower for token in ("rate limit", "too many requests", "429")):
        return FAILURE_RATE_LIMITED
    if any(token in lower for token in (
        "笔记不存在", "笔记已删除", "note not found", "note unavailable",
        "note detail response did not include an item",
    )):
        return FAILURE_NOTE_UNAVAILABLE
    return FAILURE_PROVIDER_PERMANENT_ERROR


def _failure_diagnostic(message: str) -> dict[str, str]:
    """Keep a correlatable provider error signal without retaining provider text."""
    normalized = " ".join(message.split())
    return {
        "kind": "provider_message_fingerprint",
        "value": hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16],
    }


async def _run_with_automatic_retries(
    operation: Callable[[], Awaitable[_T]],
    *,
    retry_limit: int,
    backoff_base: int,
) -> tuple[_T, int]:
    for retry_count in range(retry_limit + 1):
        try:
            return await operation(), retry_count
        except (asyncio.TimeoutError, SpiderTransientError) as exc:
            if retry_count >= retry_limit:
                try:
                    exc.retry_count = retry_count
                except AttributeError:
                    pass
                raise
            await asyncio.sleep(backoff_base ** (retry_count + 1))
        except SpiderPermanentError as exc:
            exc.retry_count = retry_count
            raise
    raise RuntimeError("automatic retry loop did not terminate")


def _retry_count(error: BaseException) -> int:
    value = getattr(error, "retry_count", 0)
    return max(int(value), 0) if isinstance(value, int) else 0


def _classify_transient_error(message: str) -> str:
    lower = message.lower()
    if any(token in lower for token in ("rate limit", "too many requests", "429", "限流")):
        return FAILURE_RATE_LIMITED
    return FAILURE_TRANSIENT_ERROR


_NOTE_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{24}$")


def _is_detail_eligible_candidate(
    note_id: str,
    note_url: str,
    *,
    provider_item_type: str = "note",
) -> bool:
    if provider_item_type.strip().lower() != "note":
        return False
    normalized_id = str(note_id or "").strip()
    if _NOTE_ID_PATTERN.fullmatch(normalized_id) is None:
        return False
    try:
        parsed = urlparse(str(note_url or "").strip())
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.hostname not in {
        "www.xiaohongshu.com",
        "xiaohongshu.com",
    }:
        return False
    if parsed.fragment or parsed.path.rstrip("/") != f"/explore/{normalized_id}":
        return False
    return bool((parse_qs(parsed.query).get("xsec_token") or [""])[0].strip())


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
