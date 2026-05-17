"""Configuration-driven orchestration for pluggable web search providers."""

from __future__ import annotations

from typing import Any, Callable, Iterable, Optional

from app.services.web_search.models import CapabilityRequest, Evidence, EvidenceBatch, SearchIntent
from app.services.web_search.providers.xhs_spider import XhsSpiderDiscoverProvider


class SearchOrchestrator:
    """Select providers and merge evidence into a unified response."""

    def __init__(
        self,
        *,
        providers: Optional[Iterable[Any]] = None,
    ):
        self.providers = list(providers or [])
        self._provider_map = {provider.describe().provider_name: provider for provider in self.providers}

    async def discover(self, intent: SearchIntent, *, limit: int = 50, on_page: Optional[Callable] = None) -> EvidenceBatch:
        traces = []
        batch_items: list[Evidence] = []
        failure_reason: Optional[str] = "empty_result"
        for provider_name in self._provider_map:
            provider = self._provider_map[provider_name]
            if not provider.supports("discover", intent):
                continue
            result = await provider.execute(
                CapabilityRequest(capability="discover", intent=intent, limit=limit),
                on_page=on_page,
            )
            traces.extend(result.trace)
            if result.items:
                batch_items.extend(result.items)
                failure_reason = None
                break
            if result.failure_reason:
                failure_reason = result.failure_reason

        return EvidenceBatch(
            items=batch_items,
            trace=traces,
            status="success" if batch_items else "empty",
            failure_reason=failure_reason,
        )


def build_default_search_orchestrator(*, spider_client: Optional[Any] = None, db_path: Optional[str] = None) -> SearchOrchestrator:
    del db_path
    return SearchOrchestrator(
        providers=[XhsSpiderDiscoverProvider(spider_client=spider_client)],
    )
