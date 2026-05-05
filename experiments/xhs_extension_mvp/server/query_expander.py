from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


STYLE_KEYWORDS = {
    "穿搭",
    "穿衣",
    "搭配",
    "冲锋衣",
    "外套",
    "裤",
    "裙",
    "鞋",
    "包",
    "背包",
    "机能风",
    "山系",
    "通勤",
    "ootd",
    "风格",
}
BEAUTY_KEYWORDS = {
    "护肤",
    "底妆",
    "彩妆",
    "修护",
    "面霜",
    "精华",
    "防晒",
    "面膜",
    "粉底",
    "口红",
    "隔离",
    "敏感肌",
    "油皮",
    "干皮",
    "痘肌",
}
STYLE_CROWD_WORDS = ("小个子", "学生党")
BEAUTY_SKIN_WORDS = ("敏感肌", "油皮", "干皮", "混油", "混干", "痘肌")
GENERIC_HOME_WORDS = ("收纳", "整理", "出租屋", "租房", "宿舍", "改造")
OFFICE_WORDS = ("通勤", "上班", "办公", "工位")
INTENT_KEYWORDS = {
    "problem": ("怎么搭", "怎么选"),
    "compare": ("对比", "平替"),
    "decision": ("避坑", "公式", "推荐", "好物"),
}
CATEGORY_ORDER = ("core", "crowd", "scenario", "problem", "compare", "decision")


@dataclass(frozen=True, slots=True)
class QueryExpansion:
    category: str
    query_text: str


def _normalize_topic(topic: str) -> str:
    return " ".join(topic.strip().split())


def expand_topic(topic: str) -> list[QueryExpansion]:
    base = _normalize_topic(topic)
    if not base:
        return []

    topic_type = _classify_topic(base)
    expansions = [
        QueryExpansion(category=category, query_text=query_text)
        for category, query_text in _build_queries(base, topic_type)
    ]
    return _dedupe(expansions)


def _classify_topic(topic: str) -> str:
    lowered = topic.lower()
    if any(keyword in lowered for keyword in STYLE_KEYWORDS):
        return "style"
    if any(keyword in lowered for keyword in BEAUTY_KEYWORDS):
        return "beauty"
    return "generic"


def _build_queries(topic: str, topic_type: str) -> list[tuple[str, str]]:
    builders = {
        "core": lambda: [topic],
        "crowd": lambda: _build_crowd_candidates(topic, topic_type),
        "scenario": lambda: _build_scenario_candidates(topic, topic_type),
        "problem": lambda: _build_problem_candidates(topic, topic_type),
        "compare": lambda: _build_compare_candidates(topic, topic_type),
        "decision": lambda: _build_decision_candidates(topic, topic_type),
    }

    queries: list[tuple[str, str]] = []
    for category in CATEGORY_ORDER:
        candidates = builders[category]()
        selected = _pick_first_valid(topic, category, candidates)
        if selected:
            queries.append((category, selected))
    return queries


def _build_crowd_candidates(topic: str, topic_type: str) -> list[str]:
    if topic_type == "style":
        return [_prefix_query(prefix, topic) for prefix in ("小个子", "学生党")]
    if topic_type == "beauty":
        if "敏感肌" in topic:
            prefixes = ("学生党", "油皮")
        elif "油皮" in topic:
            prefixes = ("学生党", "敏感肌")
        else:
            prefixes = ("敏感肌", "油皮", "学生党")
        return [_prefix_query(prefix, topic) for prefix in prefixes]

    if any(word in topic for word in GENERIC_HOME_WORDS):
        prefixes = ("租房", "上班族", "新手")
    elif any(word in topic for word in OFFICE_WORDS):
        prefixes = ("上班族", "新手", "租房")
    else:
        prefixes = ("新手", "上班族", "租房")
    return [_prefix_query(prefix, topic) for prefix in prefixes]


def _build_scenario_candidates(topic: str, topic_type: str) -> list[str]:
    if topic_type == "style":
        return [_prefix_query(prefix, topic) for prefix in ("上班", "日常")]
    if topic_type == "beauty":
        return [_prefix_query(prefix, topic) for prefix in ("换季", "夏天")]

    if any(word in topic for word in GENERIC_HOME_WORDS):
        suffixes = ("技巧", "改造", "日常用")
    else:
        suffixes = ("日常用", "技巧", "改造")
    return [_suffix_query(topic, suffix) for suffix in suffixes]


def _build_problem_candidates(topic: str, topic_type: str) -> list[str]:
    if topic_type == "style":
        return [_suffix_query(topic, suffix) for suffix in ("怎么搭", "怎么选")]
    return [_suffix_query(topic, "怎么选")]


def _build_compare_candidates(topic: str, topic_type: str) -> list[str]:
    if topic_type == "style":
        return [_suffix_query(topic, suffix) for suffix in ("对比", "平替")]
    return [_suffix_query(topic, "对比")]


def _build_decision_candidates(topic: str, topic_type: str) -> list[str]:
    if topic_type == "style":
        return [_suffix_query(topic, suffix) for suffix in ("避坑", "公式")]
    if topic_type == "beauty":
        return [_suffix_query(topic, suffix) for suffix in ("避坑", "推荐")]
    return [_suffix_query(topic, suffix) for suffix in ("避坑", "好物")]


def _pick_first_valid(topic: str, category: str, candidates: list[str]) -> str:
    for candidate in candidates:
        normalized = _normalize_query_text(candidate)
        if not normalized:
            continue
        if normalized == topic and category != "core":
            continue
        if _conflicts_with_topic(topic, normalized, category):
            continue
        if _looks_awkward(normalized):
            continue
        return normalized
    return ""


def _prefix_query(prefix: str, topic: str) -> str:
    if not prefix or prefix in topic:
        return ""
    return f"{prefix}{topic}"


def _suffix_query(topic: str, suffix: str) -> str:
    if not suffix or suffix in topic:
        return ""
    return f"{topic}{suffix}"


def _normalize_query_text(text: str) -> str:
    normalized = _normalize_topic(text)
    if not normalized:
        return ""
    while True:
        collapsed = _collapse_repeated_prefix(normalized)
        if collapsed == normalized:
            break
        normalized = collapsed
    return normalized


def _collapse_repeated_prefix(text: str) -> str:
    for size in range(2, min(len(text) // 2, 8) + 1):
        prefix = text[:size]
        if text.startswith(prefix * 2):
            return text[size:]
    return text


def _conflicts_with_topic(topic: str, candidate: str, category: str) -> bool:
    if category == "crowd":
        if any(word in topic and candidate.startswith(word) for word in STYLE_CROWD_WORDS):
            return True
        if any(word in topic and candidate.startswith(word) for word in BEAUTY_SKIN_WORDS):
            return True
    if category in INTENT_KEYWORDS:
        return any(keyword in topic and keyword in candidate for keyword in INTENT_KEYWORDS[category])
    if category == "scenario":
        return any(keyword in topic and candidate.startswith(keyword) for keyword in ("上班", "日常", "换季", "夏天"))
    return False


def _looks_awkward(text: str) -> bool:
    awkward_fragments = ("新手入门", "日常场景", "怎么选避坑")
    return any(fragment in text for fragment in awkward_fragments)


def _dedupe(expansions: Iterable[QueryExpansion]) -> list[QueryExpansion]:
    seen: set[str] = set()
    result: list[QueryExpansion] = []
    for expansion in expansions:
        key = expansion.query_text.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(expansion)
    return result
