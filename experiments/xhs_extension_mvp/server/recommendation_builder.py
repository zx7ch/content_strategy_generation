from __future__ import annotations

import math
from collections.abc import Iterable

from experiments.xhs_extension_mvp.server.candidate_builder import NormalizedItem
from experiments.xhs_extension_mvp.server.models import RecommendedNote


INTENT_PRIORITY = ("decision", "compare", "scenario", "crowd", "general")
INTENT_KEYWORDS = {
    "decision": ("怎么选", "避坑", "推荐", "好物", "清单"),
    "compare": ("对比", "平替", "差异", "区别"),
    "scenario": ("上班", "日常", "换季", "场景", "通勤", "夏天"),
    "crowd": ("学生党", "小个子", "敏感肌", "油皮", "干皮", "新手", "上班族"),
}


def build_recommended_notes(
    topic: str,
    items: Iterable[NormalizedItem],
    *,
    query_hits_by_item_id: dict[str, set[str]] | None = None,
    limit: int = 5,
) -> list[RecommendedNote]:
    normalized_items = [item for item in items if item.note_id]
    if not normalized_items:
        return []

    hit_map = query_hits_by_item_id or {}
    dominant_intent = _infer_dominant_intent(topic, hit_map.values())
    ranked = sorted(
        (
            _to_recommended_note(
                item,
                dominant_intent=dominant_intent,
                query_hits=hit_map.get(item.item_id or "", set()),
            )
            for item in normalized_items
        ),
        key=lambda note: (
            note.score,
            note.likes + 1.8 * note.collections + 1.2 * note.comments,
            note.query_coverage_count,
        ),
        reverse=True,
    )
    return ranked[:limit]


def _infer_dominant_intent(topic: str, query_groups: Iterable[set[str]]) -> str:
    texts = [topic]
    for query_group in query_groups:
        texts.extend(query_group)
    joined = " ".join(text.strip() for text in texts if text.strip())
    for intent in INTENT_PRIORITY:
        if intent == "general":
            continue
        if any(keyword in joined for keyword in INTENT_KEYWORDS[intent]):
            return intent
    return "general"


def _to_recommended_note(
    item: NormalizedItem,
    *,
    dominant_intent: str,
    query_hits: set[str],
) -> RecommendedNote:
    excerpt = _build_excerpt(item.excerpt, item.title)
    query_coverage_count = len({query.strip() for query in query_hits if query.strip()})
    engagement_score = float(item.likes) + 1.8 * float(item.collections) + 1.2 * float(item.comments)
    query_coverage_bonus = min(query_coverage_count, 3) * 1.5
    intent_match_bonus = _compute_intent_match_bonus(item, query_hits, dominant_intent)
    content_quality_bonus = _compute_content_quality_bonus(item, excerpt)
    total_score = round(
        engagement_score + query_coverage_bonus + intent_match_bonus + content_quality_bonus,
        2,
    )

    return RecommendedNote(
        note_id=item.note_id or None,
        title=item.title or "未命名笔记",
        source_url=item.source_url,
        author=item.author,
        excerpt=excerpt,
        score=total_score,
        score_reason=_build_score_reason(
            engagement_score=engagement_score,
            query_coverage_count=query_coverage_count,
            intent_match_bonus=intent_match_bonus,
        ),
        why_recommended=_build_recommend_reason(
            title=item.title or "这条笔记",
            query_coverage_count=query_coverage_count,
            dominant_intent=dominant_intent,
            excerpt=excerpt,
        ),
        likes=item.likes,
        comments=item.comments,
        collections=item.collections,
        query_coverage_count=query_coverage_count,
    )


def _compute_intent_match_bonus(item: NormalizedItem, query_hits: set[str], dominant_intent: str) -> float:
    if dominant_intent == "general":
        return 1.5

    haystack = " ".join(
        part.strip()
        for part in [item.title, item.excerpt, *sorted(query_hits)]
        if part and part.strip()
    )
    keywords = INTENT_KEYWORDS[dominant_intent]
    match_count = sum(1 for keyword in keywords if keyword in haystack)
    if match_count >= 2:
        return 3.0
    if match_count >= 1:
        return 1.5
    return 0.0


def _compute_content_quality_bonus(item: NormalizedItem, excerpt: str) -> float:
    if item.title.strip() and excerpt.strip():
        return 1.0
    if item.title.strip() or excerpt.strip():
        return 0.75
    return 0.5


def _build_excerpt(excerpt: str, title: str) -> str:
    text = (excerpt or "").strip()
    if not text:
        return (title or "").strip()[:120]
    first_segment = text.splitlines()[0].strip()
    if len(first_segment) > 120:
        return first_segment[:117].rstrip() + "..."
    return first_segment


def _build_score_reason(*, engagement_score: float, query_coverage_count: int, intent_match_bonus: float) -> str:
    parts = [f"互动值 {math.floor(engagement_score)}"]
    if query_coverage_count > 1:
        parts.append(f"覆盖 {query_coverage_count} 个搜索词")
    if intent_match_bonus >= 3:
        parts.append("和当前搜索目的高度匹配")
    elif intent_match_bonus > 0:
        parts.append("和当前搜索目的有一定匹配")
    return "，".join(parts) + "。"


def _build_recommend_reason(
    *,
    title: str,
    query_coverage_count: int,
    dominant_intent: str,
    excerpt: str,
) -> str:
    coverage_text = (
        f"同时被 {query_coverage_count} 个搜索词命中，说明它不只是单一入口里的偶然结果。"
        if query_coverage_count > 1
        else "它已经在当前采集结果里形成了可直接查看的代表样本。"
    )
    intent_text = {
        "decision": "如果你现在更关心选型、避坑和最终判断，这条更值得先看。",
        "compare": "如果你更想看差异、平替和横向比较，这条会更有参考性。",
        "scenario": "如果你更偏向真实使用场景，这条更容易帮你快速判断适用性。",
        "crowd": "如果你想确认特定人群是否适配，这条的参考价值更高。",
        "general": "它能帮助你先建立对当前主题结果面的直觉。",
    }[dominant_intent]
    detail = f"首段内容是“{excerpt}”" if excerpt else f"标题“{title}”已经足够说明切入点"
    return f"{coverage_text}{intent_text}{detail}。"
