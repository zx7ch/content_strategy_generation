"""Provider protocol for web search integrations."""

from __future__ import annotations

from typing import Protocol

from app.services.web_search.models import CapabilityRequest, CapabilityResult, ProviderDescriptor, SearchIntent


class WebSearchProvider(Protocol):
    def describe(self) -> ProviderDescriptor:
        """Describe provider capabilities."""

    def supports(self, capability: str, intent: SearchIntent) -> bool:
        """Check whether provider can handle the request."""

    async def execute(self, request: CapabilityRequest) -> CapabilityResult:
        """Execute the capability request."""
