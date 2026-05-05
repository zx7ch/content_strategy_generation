from __future__ import annotations

import math
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable

from experiments.xhs_extension_mvp.server.models import Candidate, EvidenceRef


STOPWORDS = {
    "小红书",
    "真的",
    "这个",
    "那个",
    "我们",
    "你们",
    "自己",
    "一下",
    "一个",
    "什么",
    "怎么",
    "可以",
    "就是",
    "还是",
    "日常场景",
    "新手入门",
}


@dataclass(slots=True)
class NormalizedItem:
    note_id: str
    title: str
    author: str
    source_url: str
    raw_href: str
    xsec_token: str
    xsec_source: str
    debug_url_source: str
    query_text: str
    excerpt: str
    tags: list[str]
    likes: int
    comments: int
    collections: int
    item_id: str = ""

    @property
    def engagement_score(self) -> float:
        return float(self.likes) + 1.8 * float(self.collections) + 1.2 * float(self.comments)


def build_candidates(topic: str, items: Iterable[NormalizedItem], *, limit: int = 5) -> list[Candidate]:
    normalized_items = list(items)
    if not normalized_items:
        return []

    term_stats: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "count": 0,
            "engagement": 0.0,
            "queries": set(),
            "items": [],
            "secondary_terms": Counter(),
        }
    )

    for item in normalized_items:
        primary_terms = _extract_terms(item.title, item.excerpt, item.tags)
        if not primary_terms:
            fallback = item.query_text.strip() or topic.strip()
            primary_terms = [fallback] if fallback else []

        unique_terms = list(dict.fromkeys(primary_terms))
        for term in unique_terms[:4]:
            stat = term_stats[term]
            stat["count"] = int(stat["count"]) + 1
            stat["engagement"] = float(stat["engagement"]) + item.engagement_score
            query_text = item.query_text.strip()
            if query_text:
                cast_queries = stat["queries"]
                assert isinstance(cast_queries, set)
                cast_queries.add(query_text)
            cast_items = stat["items"]
            assert isinstance(cast_items, list)
            cast_items.append(item)

            secondary = stat["secondary_terms"]
            assert isinstance(secondary, Counter)
            for related in unique_terms[1:4]:
                if related != term:
                    secondary[related] += 1

    ranked_terms = sorted(
        term_stats.items(),
        key=lambda pair: _score_term(pair[1]),
        reverse=True,
    )

    chosen_terms: list[str] = []
    candidates: list[Candidate] = []
    for term, stat in ranked_terms:
        if _is_redundant(term, chosen_terms):
            continue
        chosen_terms.append(term)

        supporting_items = sorted(
            stat["items"],  # type: ignore[index]
            key=lambda item: item.engagement_score,
            reverse=True,
        )[:3]
        query_count = len(stat["queries"]) if isinstance(stat["queries"], set) else 0
        frequency = int(stat["count"])
        score = _score_term(stat)
        evidence_refs = [
            EvidenceRef(
                note_id=item.note_id or None,
                title=item.title,
                source_url=item.source_url,
                raw_href=item.raw_href,
                xsec_token=item.xsec_token,
                xsec_source=item.xsec_source,
                debug_url_source=item.debug_url_source,
                query_text=item.query_text,
                author=item.author,
                likes=item.likes,
                comments=item.comments,
                collections=item.collections,
            )
            for item in supporting_items
        ]

        secondary_counter = stat["secondary_terms"]
        assert isinstance(secondary_counter, Counter)
        secondary_terms = [value for value, _ in secondary_counter.most_common(2)]
        angle = _build_angle(term, secondary_terms, topic)
        query_coverage_count = max(query_count, 1) if frequency > 0 else 0
        why_now = _build_why_now(
            term,
            frequency=frequency,
            query_coverage_count=query_coverage_count,
            supporting_items=supporting_items,
        )
        score_explanation = _build_score_explanation(
            frequency=frequency,
            query_coverage_count=query_coverage_count,
            supporting_items=supporting_items,
        )
        candidates.append(
            Candidate(
                candidate_id=str(uuid.uuid4()),
                title=term,
                why_now=why_now,
                angle=angle,
                score=round(score, 2),
                supporting_note_count=frequency,
                query_coverage_count=query_coverage_count,
                score_explanation=score_explanation,
                evidence_refs=evidence_refs,
            )
        )
        if len(candidates) >= limit:
            break

    if candidates:
        return candidates

    top_items = sorted(normalized_items, key=lambda item: item.engagement_score, reverse=True)[: min(3, len(normalized_items))]
    return [
        Candidate(
            candidate_id=str(uuid.uuid4()),
            title=topic.strip() or "候选方向",
            why_now="当前样本词汇较分散，先围绕原始主题聚合出一个基础候选方向。",
            angle="优先从最常出现的提问、对比和避坑表达切入，再根据后续样本继续细化。",
            score=1.0,
            supporting_note_count=len(top_items),
            query_coverage_count=1 if top_items else 0,
            score_explanation="推荐指数先按基础样本量给出初步排序，等补充更多页面后会更准确地比较不同方向。",
            evidence_refs=[
                EvidenceRef(
                    note_id=item.note_id or None,
                    title=item.title,
                    source_url=item.source_url,
                    raw_href=item.raw_href,
                    xsec_token=item.xsec_token,
                    xsec_source=item.xsec_source,
                    debug_url_source=item.debug_url_source,
                    query_text=item.query_text,
                    author=item.author,
                    likes=item.likes,
                    comments=item.comments,
                    collections=item.collections,
                )
                for item in top_items
            ],
        )
    ]


def _extract_terms(title: str, excerpt: str, tags: list[str]) -> list[str]:
    raw_segments = list(tags)
    raw_segments.extend(_split_fragments(title))
    raw_segments.extend(_split_fragments(excerpt))

    result: list[str] = []
    for segment in raw_segments:
        normalized = _normalize_term(segment)
        if normalized:
            result.append(normalized)
    return result


def _split_fragments(text: str) -> list[str]:
    if not text:
        return []
    fragments = re.split(r"[\s,，。！？!?:：;；、/|【】\[\]()（）\n\r]+", text)
    return [fragment.strip() for fragment in fragments if fragment.strip()]


def _normalize_term(value: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff#+-]", "", value).strip().lower()
    if not cleaned or cleaned in STOPWORDS:
        return ""
    if cleaned.isdigit():
        return ""
    if len(cleaned) < 2:
        return ""
    if len(cleaned) > 18:
        return ""
    return cleaned


def _score_term(stat: dict[str, object]) -> float:
    count = int(stat["count"])
    engagement = float(stat["engagement"])
    queries = len(stat["queries"]) if isinstance(stat["queries"], set) else 0
    return count * 3.0 + queries * 2.0 + math.log1p(max(engagement, 0.0))


def _is_redundant(term: str, chosen_terms: list[str]) -> bool:
    for chosen in chosen_terms:
        if term in chosen or chosen in term:
            return True
    return False


def _build_angle(term: str, secondary_terms: list[str], topic: str) -> str:
    if secondary_terms:
        joined = "、".join(secondary_terms)
        return f"可先围绕“{term}”切入，再把 {joined} 这些高频细节组织成更具体的选题、对比或避坑内容。"
    if topic.strip() and term != topic.strip():
        return f"可以把“{term}”收窄成一个更具体的子话题，优先验证它是否比原始主题更容易承接真实需求。"
    return "先从这个方向切入做第一轮内容验证，再继续补更多页面样本，判断是否值得拆成更细的子选题。"


def _build_why_now(
    term: str,
    *,
    frequency: int,
    query_coverage_count: int,
    supporting_items: list[NormalizedItem],
) -> str:
    engagement_signal = _summarize_engagement_signal(supporting_items)
    if query_coverage_count > 1:
        return (
            f"“{term}”在当前样本里反复出现，已经有 {frequency} 条代表样本支撑，"
            f"而且分布在 {query_coverage_count} 个拓展搜索视角里，说明它不是某个单一搜索词偶然冒出来的结果。"
            f"{engagement_signal}"
        )
    return (
        f"“{term}”在当前样本里已经形成稳定聚集，现有 {frequency} 条代表样本都指向这个方向，"
        f"可以先把它当作优先验证的内容切口。{engagement_signal}"
    )


def _build_score_explanation(
    *,
    frequency: int,
    query_coverage_count: int,
    supporting_items: list[NormalizedItem],
) -> str:
    total_engagement = sum(item.engagement_score for item in supporting_items)
    if frequency >= 3 and query_coverage_count >= 2 and total_engagement > 0:
        return "推荐指数较高，因为这个方向出现得更频繁、覆盖了更多拓展搜索，而且代表样本也拿到了更明显的互动反馈。"
    if frequency >= 2 and query_coverage_count >= 2:
        return "推荐指数靠前，因为这个方向不只重复出现，还同时覆盖了多个拓展搜索视角。"
    if frequency >= 2:
        return "推荐指数主要来自样本里的重复出现，说明这个方向已经不是零散个例。"
    if total_engagement > 0:
        return "推荐指数主要来自少量但互动更强的代表样本，值得先观察它能否继续放大。"
    return "推荐指数暂时只基于当前样本量做初步排序，补充更多页面后会更容易看出差异。"


def _summarize_engagement_signal(items: list[NormalizedItem]) -> str:
    peak_engagement = max((item.engagement_score for item in items), default=0.0)
    if peak_engagement <= 0:
        return ""
    return " 代表样本里也能看到一定互动反馈，值得优先关注。"
