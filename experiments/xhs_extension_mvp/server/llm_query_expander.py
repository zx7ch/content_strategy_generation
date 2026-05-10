from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from experiments.xhs_extension_mvp.server.llm_client import MVPLLMConfigError, OpenAICompatibleLLMClient
from experiments.xhs_extension_mvp.server.llm_prompt import MVP_QUERY_EXPANSION_PROMPT
from experiments.xhs_extension_mvp.server.query_expander import QueryExpansion


CATEGORY_ORDER = ("core", "crowd", "scenario", "problem", "compare", "decision")
DISALLOWED_FRAGMENTS = ("新手入门", "日常场景", "教程大全", "干货")


class QueryExpansionLLM(Protocol):
    def chat(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 400,
        temperature: float = 0.4,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class LLMExpansionFailure(RuntimeError):
    stage: str
    message: str

    def __str__(self) -> str:
        return self.message


class LLMQueryExpander:
    def __init__(self, *, llm_client: QueryExpansionLLM | None = None) -> None:
        self._llm = llm_client or OpenAICompatibleLLMClient()

    def expand_topic(self, topic: str) -> list[QueryExpansion]:
        normalized_topic = _normalize_text(topic)
        if not normalized_topic:
            raise LLMExpansionFailure("sanitize_empty", "Topic is empty after normalization")

        try:
            response = self._llm.chat(
                system="你是小红书搜索拓展词规划助手。",
                user=MVP_QUERY_EXPANSION_PROMPT.format(topic=normalized_topic),
                max_tokens=400,
                temperature=0.4,
            )
        except MVPLLMConfigError as exc:
            raise LLMExpansionFailure("config_missing", str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            stage = "timeout" if "timeout" in str(exc).lower() else "request_failed"
            raise LLMExpansionFailure(stage, str(exc)) from exc

        try:
            parsed = _parse_response(response)
        except Exception as exc:  # noqa: BLE001
            raise LLMExpansionFailure("invalid_json", str(exc)) from exc

        sanitized = _sanitize_queries(normalized_topic, parsed)
        if len(sanitized) != len(CATEGORY_ORDER):
            raise LLMExpansionFailure("sanitize_empty", "LLM result did not produce a complete usable query set")
        return sanitized


def _parse_response(response: str) -> list[QueryExpansion]:
    payload = json.loads(_extract_json_object(response))
    items = payload.get("queries")
    if not isinstance(items, list):
        return []

    result: list[QueryExpansion] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        category = item.get("category")
        query_text = item.get("query_text")
        if not isinstance(category, str) or not isinstance(query_text, str):
            continue
        result.append(QueryExpansion(category=category.strip(), query_text=query_text.strip()))
    return result


def _extract_json_object(response: str) -> str:
    text = response.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in LLM response")
    return text[start : end + 1]


def _sanitize_queries(topic: str, queries: Iterable[QueryExpansion]) -> list[QueryExpansion]:
    seen_texts: set[str] = set()
    by_category: dict[str, str] = {}

    for item in queries:
        category = item.category.strip()
        if category not in CATEGORY_ORDER or category in by_category:
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

    ordered: list[QueryExpansion] = []
    for category in CATEGORY_ORDER:
        query_text = by_category.get(category)
        if not query_text:
            continue
        ordered.append(QueryExpansion(category=category, query_text=query_text))
    return ordered


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())
