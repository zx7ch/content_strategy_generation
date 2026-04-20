"""Unified web search abstractions and orchestration."""

from app.services.web_search.models import (
    CapabilityRequest,
    CapabilityResult,
    Evidence,
    EvidenceBatch,
    ProviderDescriptor,
    SearchIntent,
    SearchTraceEntry,
)
from app.services.web_search.orchestrator import SearchOrchestrator, build_default_search_orchestrator

__all__ = [
    "CapabilityRequest",
    "CapabilityResult",
    "Evidence",
    "EvidenceBatch",
    "ProviderDescriptor",
    "SearchIntent",
    "SearchTraceEntry",
    "SearchOrchestrator",
    "build_default_search_orchestrator",
]
