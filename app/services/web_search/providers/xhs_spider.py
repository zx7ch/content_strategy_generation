"""Spider-backed discover provider."""

from __future__ import annotations

from time import monotonic
from typing import Any, Callable, List, Optional

from app.services.web_search.models import CapabilityRequest, CapabilityResult, Evidence, ProviderDescriptor, SearchTraceEntry, SearchIntent
from app.services.xhs_spider import SpiderPermanentError, SpiderTransientError, XHSPost, XHSSpiderClient


class XhsSpiderDiscoverProvider:
    """Wrap the legacy spider client behind the web-search provider protocol."""

    def __init__(self, spider_client: Optional[Any] = None):
        self.spider_client = spider_client or XHSSpiderClient()

    def describe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_name="xhs_spider",
            supported_capabilities=["discover"],
            platforms=["xiaohongshu"],
            priority=10,
            enabled=True,
        )

    def supports(self, capability: str, intent: SearchIntent) -> bool:
        return capability == "discover" and intent.platform == "xiaohongshu"

    async def execute(self, request: CapabilityRequest, *, on_page: Optional[Callable] = None) -> CapabilityResult:
        started = monotonic()
        descriptor = self.describe()
        if not self.supports(request.capability, request.intent):
            trace = SearchTraceEntry(
                provider=descriptor.provider_name,
                capability="discover",
                status="unsupported",
                latency_ms=0,
                item_count=0,
                failure_reason="unsupported_capability",
            )
            return CapabilityResult(
                capability="discover",
                provider=descriptor.provider_name,
                status="unsupported",
                failure_reason="unsupported_capability",
                trace=[trace],
            )

        try:
            posts = await self.spider_client.search_with_retry(request.intent.query, num=request.limit, on_page=on_page)
            evidences = [self._post_to_evidence(post, request.intent.query, request.intent.session_id) for post in posts]
            status = "success" if evidences else "empty"
            reason = None if evidences else "empty_result"
        except SpiderTransientError:
            evidences = []
            status = "transient_error"
            reason = "transient_error"
        except SpiderPermanentError as exc:
            evidences = []
            status = "permanent_error"
            reason = self._classify_failure_reason(exc)

        trace = SearchTraceEntry(
            provider=descriptor.provider_name,
            capability="discover",
            status=status,
            latency_ms=int((monotonic() - started) * 1000),
            item_count=len(evidences),
            failure_reason=reason,
        )
        return CapabilityResult(
            capability="discover",
            provider=descriptor.provider_name,
            status=status,
            items=evidences,
            failure_reason=reason,
            trace=[trace],
        )

    @staticmethod
    def _classify_failure_reason(exc: Exception) -> str:
        error_lower = str(exc).lower()
        if "auth" in error_lower or "cookie" in error_lower or "login" in error_lower:
            return "auth_required"
        if "rate limit" in error_lower or "too many requests" in error_lower:
            return "rate_limited"
        return "permanent_error"

    @staticmethod
    def _post_to_evidence(post: XHSPost, query: str, session_id: Optional[str]) -> Evidence:
        return Evidence(
            session_id=session_id,
            platform="xiaohongshu",
            source_kind="spider_note",
            source_provider="xhs_spider",
            source_url=post.note_url,
            canonical_id=post.note_id,
            title=post.title,
            content_text=post.content,
            author=post.author,
            tags=list(post.tags),
            metrics={
                "liked_count": post.liked_count,
                "collected_count": post.collected_count,
                "comment_count": post.comment_count,
                "share_count": post.share_count,
            },
            media=list(post.images),
            query_used=query,
            raw_payload=post.model_dump(),
        )
