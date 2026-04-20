"""Unit tests for P2-3 Strategy Prompts.

Validation criteria from dev_spec §6.2, §6.3:
- Query expansion generates 3-5 valid alternative queries
- Data-driven strategy output conforms to ContentStrategy schema
- Generic strategy produces usable strategy without data dependency
"""

from __future__ import annotations

from app.prompts.strategy import (
    CONTENT_STRATEGY_DATA_DRIVEN,
    CONTENT_STRATEGY_GENERIC,
    QUERY_EXPANSION_PROMPT,
)


def test_query_expansion_prompt_requires_3_to_5_and_line_output():
    prompt = QUERY_EXPANSION_PROMPT.format(
        query="抹茶拿铁",
        doc_count=5,
        quality_score=0.25,
        expansion_count=0,
        existing_queries="无"
    )
    assert "3-5" in prompt or "3到5" in prompt
    assert "每行一个查询" in prompt
    assert "不要编号" in prompt
    assert "抹茶拿铁" in prompt
    assert "阈值: 0.35" in prompt


def test_query_expansion_prompt_includes_expansion_rules():
    """Verify expansion trigger/stop conditions are documented in prompt."""
    prompt = QUERY_EXPANSION_PROMPT.format(
        query="测试",
        doc_count=5,
        quality_score=0.20,
        expansion_count=0,
        existing_queries="无"
    )
    assert "触发条件" in prompt
    assert "停止条件" in prompt
    assert "quality_score < 0.35" in prompt
    assert "新增 unique 文档 < 3" in prompt or "new_unique_docs" in prompt
    assert "quality_gain < 0.05" in prompt


def test_data_driven_prompt_has_strict_json_contract():
    prompt = CONTENT_STRATEGY_DATA_DRIVEN.format(
        user_query="抹茶拿铁",
        platform_summary="平均标题长度18，热门标签：抹茶, 自制",
        top_posts="标题: 抹茶教程\n标签: 抹茶, 自制\n互动分: 92",
        top_k=10
    )
    assert "只输出" in prompt and "JSON" in prompt
    assert "positioning" in prompt
    assert "target_audience" in prompt
    assert "content_pillars" in prompt
    assert "key_messaging" in prompt
    assert "content_types" in prompt
    assert "posting_strategy" in prompt
    assert "字符串数组" in prompt or "数组" in prompt


def test_generic_prompt_has_strict_json_contract():
    prompt = CONTENT_STRATEGY_GENERIC.format(query="护肤")
    assert "只输出" in prompt and "JSON" in prompt
    assert "positioning" in prompt
    assert "target_audience" in prompt
    assert "content_pillars" in prompt
    assert "key_messaging" in prompt
    assert "content_types" in prompt
    assert "posting_strategy" in prompt
    assert "护肤" in prompt


def test_data_driven_prompt_includes_runtime_inputs():
    prompt = CONTENT_STRATEGY_DATA_DRIVEN.format(
        user_query="咖啡",
        platform_summary="平均标题长度16，热门标签：咖啡, 拉花",
        top_posts="标题: 咖啡入门\n标签: 咖啡\n互动分: 88",
        top_k=10
    )
    assert "咖啡" in prompt
    assert "平均标题长度16" in prompt
    assert "标题: 咖啡入门" in prompt


def test_generic_prompt_covers_platform_rules():
    """Verify generic prompt includes platform general rules."""
    prompt = CONTENT_STRATEGY_GENERIC.format(query="穿搭")
    assert "内容分发机制" in prompt or "冷启动" in prompt
    assert "收藏率" in prompt or "高互动" in prompt
    assert "禁止" in prompt or "安全红线" in prompt
