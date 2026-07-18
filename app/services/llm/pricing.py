"""Pricing calculation for LLM usage events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    input_per_1m_tokens: float
    output_per_1m_tokens: float
    currency: str = "USD"


@dataclass(frozen=True)
class UsageCost:
    input_cost: float = 0.0
    output_cost: float = 0.0
    total_cost: float = 0.0
    currency: str = "USD"


DEFAULT_PRICING: dict[str, ModelPricing] = {
    "openai:gpt-4.1-mini": ModelPricing(input_per_1m_tokens=0.40, output_per_1m_tokens=1.60),
    "openai:gpt-4o-mini": ModelPricing(input_per_1m_tokens=0.15, output_per_1m_tokens=0.60),
    "openai:gpt-4o": ModelPricing(input_per_1m_tokens=2.50, output_per_1m_tokens=10.00),
    "deepseek:deepseek-chat": ModelPricing(input_per_1m_tokens=0.14, output_per_1m_tokens=0.28),
    "anthropic:claude-haiku-4-5": ModelPricing(input_per_1m_tokens=1.00, output_per_1m_tokens=5.00),
    "anthropic:claude-sonnet-4-6": ModelPricing(input_per_1m_tokens=3.00, output_per_1m_tokens=15.00),
    "kimi:moonshot-v1-8k": ModelPricing(input_per_1m_tokens=0.20, output_per_1m_tokens=2.00),
    "kimi:kimi-k2.6": ModelPricing(input_per_1m_tokens=0.95, output_per_1m_tokens=4.00),
}


class PricingCalculator:
    def __init__(self, pricing_map: Mapping[str, ModelPricing] | None = None) -> None:
        self._pricing_map = dict(pricing_map or DEFAULT_PRICING)

    def calculate(
        self,
        *,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> UsageCost:
        pricing = self._pricing_map.get(f"{provider.lower()}:{model}")
        if pricing is None:
            return UsageCost()

        input_cost = prompt_tokens / 1_000_000 * pricing.input_per_1m_tokens
        output_cost = completion_tokens / 1_000_000 * pricing.output_per_1m_tokens
        return UsageCost(
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=input_cost + output_cost,
            currency=pricing.currency,
        )
