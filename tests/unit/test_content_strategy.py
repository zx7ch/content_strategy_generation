from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.agents.content_strategy_agent import ContentStrategyAgent, compute_engagement_score
from app.models.session import PlatformPreference
from app.services.xhs_spider import XHSPost


def _post(note_id: str, liked: int = 100, collected: int = 50) -> XHSPost:
    return XHSPost(
        note_id=note_id,
        title=f"title-{note_id}",
        content=f"content-{note_id}",
        author="author",
        tags=["tag-a", "tag-b"],
        liked_count=liked,
        collected_count=collected,
        comment_count=10,
        share_count=2,
        note_url=f"https://xhs.com/{note_id}",
        images=["img1"],
    )


class _FakeAnalyzer:
    def __init__(self) -> None:
        self.score_posts_calls: list[list[XHSPost]] = []

    def score_posts(self, posts: list[XHSPost]):
        self.score_posts_calls.append(posts)
        return [
            type("ScoredPost", (), {"post": post, "raw_score": index + 1})()
            for index, post in enumerate(posts)
        ]

    def analyze_platform_preferences(self, posts: list[XHSPost]) -> PlatformPreference:
        del posts
        return PlatformPreference(
            avg_title_length=18,
            popular_tags=["tag-a"],
            optimal_posting_times=["20:00"],
            content_patterns=["listicle"],
        )


@pytest.fixture
def agent() -> ContentStrategyAgent:
    return ContentStrategyAgent(
        spider_client=AsyncMock(),
        rag_service=AsyncMock(),
        engagement_analyzer=_FakeAnalyzer(),
        llm_client=AsyncMock(),
    )


class TestComputeEngagementScore:
    def test_highest_likes_and_collects_scores_one(self):
        score = compute_engagement_score(1000, 500, [0, 500, 1000], [0, 250, 500])
        assert score == pytest.approx(1.0)

    def test_single_note_dataset_returns_zero(self):
        score = compute_engagement_score(500, 250, [500], [250])
        assert score == pytest.approx(0.0)

    def test_score_always_between_zero_and_one(self):
        score = compute_engagement_score(5000, 800, [100, 200, 5000], [10, 300, 800], lambda_weight=1.5)
        assert 0.0 <= score <= 1.0


class TestExpandedQueries:
    @pytest.mark.asyncio
    async def test_generate_expanded_queries_deduplicates_and_filters_original(self, agent: ContentStrategyAgent):
        agent.llm.chat.return_value = "抹茶自制\n- 抹茶自制\n# 注释\n原始查询\n抹茶避坑\n"

        queries = await agent._generate_expanded_queries(
            original_query="原始查询",
            doc_count=3,
            quality_score=0.2,
            existing_queries=["原始查询"],
        )

        assert queries == ["抹茶自制", "抹茶避坑"]

    @pytest.mark.asyncio
    async def test_generate_expanded_queries_returns_empty_on_llm_error(self, agent: ContentStrategyAgent):
        agent.llm.chat.side_effect = RuntimeError("llm down")
        queries = await agent._generate_expanded_queries("原始查询")
        assert queries == []


class TestStrategyGenerationHelpers:
    @pytest.mark.asyncio
    async def test_llm_generate_strategy_parses_embedded_json(self, agent: ContentStrategyAgent):
        agent.llm.chat.return_value = (
            "这里是策略说明\n"
            + json.dumps(
                {
                    "positioning": "专业博主",
                    "target_audience": "成分党",
                    "content_pillars": ["成分", "实测"],
                    "key_messaging": "科学解释",
                    "content_types": ["图文"],
                    "posting_strategy": "晚间",
                },
                ensure_ascii=False,
            )
        )

        strategy = await agent._llm_generate_strategy("prompt", user_query="护肤", default_quality=0.0)

        assert strategy.positioning == "专业博主"
        assert strategy.target_audience == "成分党"
        assert strategy.content_pillars == ["成分", "实测"]

    @pytest.mark.asyncio
    async def test_llm_generate_strategy_falls_back_when_json_invalid(self, agent: ContentStrategyAgent):
        agent.llm.chat.return_value = "not json"

        strategy = await agent._llm_generate_strategy("prompt", user_query="护肤", default_quality=0.0)

        assert strategy.positioning == "生活方式分享者"
        assert "护肤" in strategy.content_pillars

    @pytest.mark.asyncio
    async def test_generate_data_driven_strategy_scores_top_posts_and_uses_top_five_context(
        self, agent: ContentStrategyAgent
    ):
        posts = [_post(f"n{i}", liked=100 + i, collected=50 + i) for i in range(8)]
        platform_pref = PlatformPreference(
            avg_title_length=16,
            popular_tags=["护肤", "成分"],
            optimal_posting_times=["20:00"],
            content_patterns=["清单式"],
        )
        agent.llm.chat.return_value = json.dumps(
            {
                "positioning": "护肤顾问",
                "target_audience": "油敏肌",
                "content_pillars": ["成分", "避坑"],
                "key_messaging": "温和有效",
                "content_types": ["图文"],
                "posting_strategy": "20:00",
            },
            ensure_ascii=False,
        )

        strategy = await agent._generate_data_driven_strategy(
            user_query="护肤",
            posts=posts,
            platform_pref=platform_pref,
        )

        assert strategy.positioning == "护肤顾问"
        assert len(agent.analyzer.score_posts_calls) == 1
        assert len(agent.analyzer.score_posts_calls[0]) == 8
        llm_user_prompt = agent.llm.chat.await_args.kwargs["user"]
        assert "top_k=5" not in llm_user_prompt
        assert llm_user_prompt.count("标题:") == 5

    @pytest.mark.asyncio
    async def test_generate_generic_strategy_uses_query_in_fallback(self, agent: ContentStrategyAgent):
        agent.llm.chat.side_effect = RuntimeError("provider down")

        strategy = await agent._generate_generic_strategy("露营")

        assert strategy.target_audience == "25-35岁对该主题感兴趣的用户"
        assert strategy.content_pillars == ["露营", "实用经验"]
