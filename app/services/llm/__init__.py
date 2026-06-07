"""LLM abstraction layer public API."""

from app.services.llm.credentials import CredentialResolver
from app.services.llm.pricing import DEFAULT_PRICING, ModelPricing, PricingCalculator, UsageCost
from app.services.llm.providers.openai_compatible import OpenAICompatibleAdapter
from app.services.llm.router import ModelRouter, build_default_policy_map
from app.services.llm.service import LLMService
from app.services.llm.usage_tracker import (
    LLMUsageEvent,
    LLMUsageEventInput,
    LLMUsageStepSummary,
    LLMUsageSummary,
    LLMUsageTracker,
)
from app.services.llm.types import (
    CredentialResolutionError,
    LLMCallContext,
    LLMRequest,
    LLMResponse,
    LLMServiceError,
    Message,
    ModelRoutingError,
    ProviderNotRegisteredError,
    ResolvedModel,
    TokenUsage,
)

__all__ = [
    "CredentialResolutionError",
    "CredentialResolver",
    "DEFAULT_PRICING",
    "LLMCallContext",
    "LLMRequest",
    "LLMResponse",
    "LLMService",
    "LLMServiceError",
    "LLMUsageEventInput",
    "LLMUsageEvent",
    "LLMUsageStepSummary",
    "LLMUsageSummary",
    "LLMUsageTracker",
    "Message",
    "ModelPricing",
    "ModelRouter",
    "ModelRoutingError",
    "OpenAICompatibleAdapter",
    "PricingCalculator",
    "ProviderNotRegisteredError",
    "ResolvedModel",
    "TokenUsage",
    "UsageCost",
    "build_default_policy_map",
]
