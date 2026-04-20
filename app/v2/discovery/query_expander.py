"""LLM-assisted query expansion for the V2 discovery workspace."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from app.llm.client import LLMClient
from app.prompts.discovery import DISCOVERY_QUERY_EXPANSION_PROMPT


CATEGORY_ORDER = ("core", "crowd", "scenario", "problem", "compare", "decision")
DISALLOWED_FRAGMENTS = ("新手入门", "日常场景", "教程大全", "干货")


class QueryExpansionLLM(Protocol):
    async def chat(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class DiscoveryExpandedQuery:
    category: str
    query_text: str


@dataclass(frozen=True, slots=True)
class DiscoveryExpansionResult:
    queries: list[DiscoveryExpandedQuery]
    source: str


class DiscoveryQueryExpansionFailure(RuntimeError):
    """Raised when query expansion cannot produce a valid LLM-backed result."""


class DiscoveryQueryExpander:
    """Generate discovery queries with an LLM and a deterministic sanitizer."""

    def __init__(self, *, llm_client: QueryExpansionLLM | None = None) -> None:
        self._llm = llm_client or LLMClient()

    async def expand_topic(self, topic: str) -> DiscoveryExpansionResult:
        normalized_topic = _normalize_text(topic)
        if not normalized_topic:
            raise DiscoveryQueryExpansionFailure("请先填写一个有效的搜索主题。")

        try:
            response = await self._llm.chat(
                system="你是小红书搜索拓展词规划助手。",
                user=DISCOVERY_QUERY_EXPANSION_PROMPT.format(topic=normalized_topic),
                max_tokens=400,
                temperature=0.4,
            )
            parsed = _parse_response(response)
            sanitized = _sanitize_queries(normalized_topic, parsed)
            if sanitized:
                return DiscoveryExpansionResult(queries=sanitized, source="llm")
            raise DiscoveryQueryExpansionFailure("LLM 没有返回可用的拓展搜索词，请稍后重试。")
        except DiscoveryQueryExpansionFailure:
            raise
        except Exception as exc:
            raise DiscoveryQueryExpansionFailure("当前无法生成拓展搜索词，请检查 LLM 配置后重试。") from exc


def _parse_response(response: str) -> list[DiscoveryExpandedQuery]:
    payload = json.loads(_extract_json_object(response))
    items = payload.get("queries")
    if not isinstance(items, list):
        return []

    result: list[DiscoveryExpandedQuery] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        category = item.get("category")
        query_text = item.get("query_text")
        if not isinstance(category, str) or not isinstance(query_text, str):
            continue
        result.append(DiscoveryExpandedQuery(category=category, query_text=query_text))
    return result


def _extract_json_object(response: str) -> str:
    text = response.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in LLM response")
    return text[start : end + 1]


def _sanitize_queries(topic: str, queries: Iterable[DiscoveryExpandedQuery]) -> list[DiscoveryExpandedQuery]:
    seen_texts: set[str] = set()
    by_category: dict[str, str] = {}

    for item in queries:
        category = item.category.strip()
        if category not in CATEGORY_ORDER:
            continue
        if category in by_category:
            continue
        normalized = _normalize_text(item.query_text)
        if not normalized:
            continue
        if any(fragment in normalized for fragment in DISALLOWED_FRAGMENTS):
            continue
        if category != "core" and normalized == topic:
            continue

        text_key = normalized.lower()
        if text_key in seen_texts:
            continue

        by_category[category] = normalized
        seen_texts.add(text_key)

    if "core" not in by_category:
        by_category["core"] = topic

    ordered: list[DiscoveryExpandedQuery] = []
    seen_output: set[str] = set()
    for category in CATEGORY_ORDER:
        query_text = by_category.get(category)
        if not query_text:
            continue
        text_key = query_text.lower()
        if text_key in seen_output:
            continue
        seen_output.add(text_key)
        ordered.append(DiscoveryExpandedQuery(category=category, query_text=query_text))
    return ordered


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())
