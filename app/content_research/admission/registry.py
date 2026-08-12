"""Direction-specific admission routing without central workflow branching."""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType

from app.content_research.admission.brand_activity import STRATEGY as BRAND_ACTIVITY_STRATEGY
from app.content_research.admission.comment_insight import STRATEGY as COMMENT_INSIGHT_STRATEGY
from app.content_research.admission.competitor_discovery import (
    STRATEGY as COMPETITOR_DISCOVERY_STRATEGY,
)
from app.content_research.admission.content_performance import (
    STRATEGY as CONTENT_PERFORMANCE_STRATEGY,
)
from app.content_research.admission.keyword_growth import STRATEGY as KEYWORD_GROWTH_STRATEGY
from app.content_research.admission.product_marketing import STRATEGY as PRODUCT_MARKETING_STRATEGY
from app.content_research.admission.strategy import AdmissionStrategy
from app.content_research.admission.ugc_community import STRATEGY as UGC_COMMUNITY_STRATEGY


class AdmissionStrategyRegistry:
    """Immutable lookup of specialist admission behavior by direction id."""

    def __init__(
        self,
        registrations: Iterable[tuple[str, AdmissionStrategy]],
    ) -> None:
        registered: dict[str, AdmissionStrategy] = {}
        for direction_id, strategy in registrations:
            direction_id = direction_id.strip()
            if not direction_id:
                raise ValueError("admission strategy direction_id cannot be empty")
            if direction_id != strategy.direction_id:
                raise ValueError("admission strategy registration key must match direction_id")
            if direction_id in registered:
                raise ValueError(f"duplicate admission strategy for {direction_id}")
            registered[direction_id] = strategy
        self._strategies = MappingProxyType(registered)

    def get(self, direction_id: str) -> AdmissionStrategy | None:
        return self._strategies.get(direction_id)


DEFAULT_ADMISSION_STRATEGIES = AdmissionStrategyRegistry((
    ("product_marketing", PRODUCT_MARKETING_STRATEGY),
    ("content_performance", CONTENT_PERFORMANCE_STRATEGY),
    ("competitor_discovery", COMPETITOR_DISCOVERY_STRATEGY),
    ("brand_activity", BRAND_ACTIVITY_STRATEGY),
    ("keyword_growth", KEYWORD_GROWTH_STRATEGY),
    ("ugc_community", UGC_COMMUNITY_STRATEGY),
    ("comment_insight", COMMENT_INSIGHT_STRATEGY),
))
