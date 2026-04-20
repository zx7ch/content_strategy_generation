from __future__ import annotations

from app.prompts.discovery import DISCOVERY_QUERY_EXPANSION_PROMPT


def test_discovery_query_expansion_prompt_requires_json_and_fixed_categories() -> None:
    prompt = DISCOVERY_QUERY_EXPANSION_PROMPT.format(topic="敏感肌修护")

    assert "只输出 JSON" in prompt
    assert '"queries"' in prompt
    assert '"category": "core"' in prompt
    assert "`core`, `crowd`, `scenario`, `problem`, `compare`, `decision`" in prompt
    assert "敏感肌修护" in prompt


def test_discovery_query_expansion_prompt_rejects_generic_fragments() -> None:
    prompt = DISCOVERY_QUERY_EXPANSION_PROMPT.format(topic="通勤穿搭")

    assert "新手入门" in prompt
    assert "日常场景" in prompt
    assert "真实搜索" in prompt
