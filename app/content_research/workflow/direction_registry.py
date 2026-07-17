"""Stable direction registry for Content Research P0 planning."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResearchDirectionDefinition:
    id: str
    label: str
    direction_type: str
    agent_name: str
    task_type: str
    default_questions: list[str]
    expected_evidence_types: list[str]
    source_scope: list[str]
    priority: int


class ResearchDirectionRegistry:
    def __init__(self, definitions: list[ResearchDirectionDefinition] | None = None) -> None:
        items = definitions or _default_definitions()
        self._definitions = {item.id: item for item in items}

    def list_directions(self) -> list[ResearchDirectionDefinition]:
        return sorted(self._definitions.values(), key=lambda item: item.priority)

    def get(self, direction_id: str) -> ResearchDirectionDefinition | None:
        return self._definitions.get(direction_id)

    def require_many(self, direction_ids: list[str]) -> list[ResearchDirectionDefinition]:
        missing = [item for item in direction_ids if item not in self._definitions]
        if missing:
            raise ValueError(f"Unknown research directions: {', '.join(missing)}")
        return [self._definitions[item] for item in direction_ids]


def _default_definitions() -> list[ResearchDirectionDefinition]:
    return [
        ResearchDirectionDefinition(
            id="product_marketing",
            label="产品营销",
            direction_type="content_pattern",
            agent_name="ProductMarketingResearchAgent",
            task_type="product_marketing_research",
            default_questions=["提炼小红书产品卖点表达", "识别营销话术和内容角度"],
            expected_evidence_types=["post", "search_result", "metric_snapshot"],
            source_scope=["search_result"],
            priority=10,
        ),
        ResearchDirectionDefinition(
            id="competitor_discovery",
            label="竞品品牌",
            direction_type="competitor_scan",
            agent_name="CompetitorDiscoveryAgent",
            task_type="competitor_discovery",
            default_questions=["识别相关竞品品牌", "判断竞品内容表达和热度信号"],
            expected_evidence_types=["search_result", "profile", "post"],
            source_scope=["search_result"],
            priority=20,
        ),
        ResearchDirectionDefinition(
            id="ugc_community",
            label="UGC 社群互动",
            direction_type="brand_fit",
            agent_name="UGCCommunityResearchAgent",
            task_type="ugc_community_research",
            default_questions=["识别用户自发讨论场景", "总结社群互动和生活方式表达"],
            expected_evidence_types=["post", "comment", "agent_observation"],
            source_scope=["search_result"],
            priority=30,
        ),
        ResearchDirectionDefinition(
            id="comment_insight",
            label="用户评论痛点",
            direction_type="comment_signal",
            agent_name="CommentInsightAgent",
            task_type="comment_insight_research",
            default_questions=["发现评论中的需求点", "识别阻塞性缺失证据"],
            expected_evidence_types=["comment", "post"],
            source_scope=["search_result"],
            priority=40,
        ),
        ResearchDirectionDefinition(
            id="brand_activity",
            label="品牌活动",
            direction_type="market_trend",
            agent_name="BrandActivityResearchAgent",
            task_type="brand_activity_research",
            default_questions=["发现品牌活动、联名或新品信号", "判断活动传播方式"],
            expected_evidence_types=["post", "metric_snapshot", "search_result"],
            source_scope=["search_result"],
            priority=50,
        ),
        ResearchDirectionDefinition(
            id="keyword_growth",
            label="高增长关键词",
            direction_type="market_trend",
            agent_name="KeywordGrowthResearchAgent",
            task_type="keyword_growth_research",
            default_questions=["发现增长关键词和搜索表达", "整理关键词簇"],
            expected_evidence_types=["search_result", "metric_snapshot"],
            source_scope=["search_result"],
            priority=60,
        ),
        ResearchDirectionDefinition(
            id="content_performance",
            label="小红书内容表现",
            direction_type="content_pattern",
            agent_name="ContentPerformanceResearchAgent",
            task_type="content_performance_research",
            default_questions=["识别高表现内容样本", "分析标题、封面和互动表现"],
            expected_evidence_types=["post", "metric_snapshot"],
            source_scope=["search_result"],
            priority=70,
        ),
    ]
