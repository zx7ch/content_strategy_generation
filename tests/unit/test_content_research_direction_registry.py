from __future__ import annotations

import pytest

from app.content_research.workflow.direction_registry import ResearchDirectionRegistry


def test_direction_registry_returns_stable_p0_directions():
    registry = ResearchDirectionRegistry()

    directions = registry.list_directions()

    assert [item.id for item in directions] == [
        "product_marketing",
        "competitor_discovery",
        "ugc_community",
        "comment_insight",
        "brand_activity",
        "keyword_growth",
        "content_performance",
    ]
    assert registry.get("brand_activity").agent_name == "DirectionalExecutionPipeline"


def test_direction_registry_requires_known_direction_ids():
    registry = ResearchDirectionRegistry()

    with pytest.raises(ValueError, match="unknown_direction"):
        registry.require_many(["product_marketing", "unknown_direction"])


def test_direction_registry_preserves_requested_order():
    registry = ResearchDirectionRegistry()

    directions = registry.require_many(["comment_insight", "product_marketing"])

    assert [item.id for item in directions] == ["comment_insight", "product_marketing"]


def test_direction_registry_canonicalizes_presearch_display_labels():
    registry = ResearchDirectionRegistry()

    assert registry.canonicalize_many(["产品卖点表达", "产品营销", "用户评论痛点"]) == [
        "product_marketing",
        "comment_insight",
    ]
