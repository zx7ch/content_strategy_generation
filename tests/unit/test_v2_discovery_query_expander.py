from __future__ import annotations

import pytest

from app.v2.discovery.query_expander import DiscoveryQueryExpander, DiscoveryQueryExpansionFailure


class FakeLLM:
    def __init__(self, response):
        self.response = response

    async def chat(self, *, system: str, user: str, max_tokens: int = 1024, temperature: float = 0.7) -> str:
        del system, user, max_tokens, temperature
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.mark.asyncio
async def test_discovery_query_expander_uses_llm_json_and_preserves_category_order() -> None:
    expander = DiscoveryQueryExpander(
        llm_client=FakeLLM(
            """
            {
              "queries": [
                {"category": "problem", "query_text": "敏感肌修护怎么选"},
                {"category": "core", "query_text": "敏感肌修护"},
                {"category": "scenario", "query_text": "换季敏感肌修护"},
                {"category": "crowd", "query_text": "油皮敏感肌修护"},
                {"category": "decision", "query_text": "敏感肌修护避坑"},
                {"category": "compare", "query_text": "敏感肌修护平替"}
              ]
            }
            """
        )
    )

    queries = await expander.expand_topic("敏感肌修护")

    assert queries.source == "llm"
    assert [item.category for item in queries.queries] == ["core", "crowd", "scenario", "problem", "compare", "decision"]
    assert queries.queries[0].query_text == "敏感肌修护"
    assert queries.queries[3].query_text == "敏感肌修护怎么选"


@pytest.mark.asyncio
async def test_discovery_query_expander_filters_disallowed_fragments_and_duplicate_topic() -> None:
    expander = DiscoveryQueryExpander(
        llm_client=FakeLLM(
            """
            {
              "queries": [
                {"category": "core", "query_text": "通勤穿搭"},
                {"category": "scenario", "query_text": "通勤穿搭 日常场景"},
                {"category": "problem", "query_text": "通勤穿搭"},
                {"category": "compare", "query_text": "通勤穿搭平替"}
              ]
            }
            """
        )
    )

    queries = await expander.expand_topic("通勤穿搭")

    assert queries.source == "llm"
    assert [item.category for item in queries.queries] == ["core", "compare"]
    assert all("日常场景" not in item.query_text for item in queries.queries)


@pytest.mark.asyncio
async def test_discovery_query_expander_raises_when_llm_fails() -> None:
    expander = DiscoveryQueryExpander(llm_client=FakeLLM(RuntimeError("llm down")))

    with pytest.raises(DiscoveryQueryExpansionFailure):
        await expander.expand_topic("租房收纳")
