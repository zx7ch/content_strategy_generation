from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from experiments.xhs_extension_mvp.server.candidate_builder import NormalizedItem
from experiments.xhs_extension_mvp.server.llm_client import OpenAICompatibleLLMClient
from experiments.xhs_extension_mvp.server.llm_note_prompt import MVP_RECOMMENDED_NOTES_PROMPT
from experiments.xhs_extension_mvp.server.models import (
    RecommendedNote,
    RecommendedNotesDiagnostics,
    RecommendedNotesFilterReason,
)
from app.services.xhs_spider import XHSNoteDetail, XHSUserProfile, XHSSpiderClient


# Block claims we do not support in the payload.
FORBIDDEN_INFERRED_METRICS = ("近1天", "近3天", "7天", "热榜", "曝光", "粉丝", "千万粉", "万粉", "账号层级")

# Deterministic hard filters run before the LLM.
PROMO_KEYWORDS = ("直播间", "下单", "拍1发", "专拍", "点链接", "优惠券", "到手价", "返现", "私信领")
LOW_VALUE_KEYWORDS = ("抽奖", "福利", "点赞收藏", "冲冲冲", "姐妹们快看", "无脑入", "闭眼入", "买它")

# Screening thresholds for stale notes and head accounts.
HEAD_ACCOUNT_FANS_THRESHOLD = 600_000
STALE_DAYS_THRESHOLD = 30.0
RECENT_WINDOW_DAYS = 10.0
FILTER_REASON_LABELS = {
    "stale_note": "发布时间过旧",
    "promo_content": "硬广或带货信号过强",
    "low_value_content": "抽奖或低信息量内容",
    "head_account": "头部账号样本",
    "thin_content": "文本和互动信号偏弱",
}


class LLMRecommendedNotesFailure(RuntimeError):
    def __init__(self, stage: str, message: str, diagnostics: RecommendedNotesDiagnostics | None = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.diagnostics = diagnostics


@dataclass(slots=True)
class CandidateNote:
    item: NormalizedItem
    excerpt: str
    query_coverage_count: int
    baseline_score: float
    detail: XHSNoteDetail | None = None
    user_profile: XHSUserProfile | None = None


@dataclass(slots=True)
class RecommendedNotesAnalysis:
    notes: list[RecommendedNote]
    diagnostics: RecommendedNotesDiagnostics


class LLMRecommendedNoteAnalyzer:
    def __init__(
        self,
        *,
        llm_client: OpenAICompatibleLLMClient | None = None,
        spider_client: XHSSpiderClient | Any | None = None,
    ) -> None:
        self._llm_client = llm_client or OpenAICompatibleLLMClient()
        self._spider_client = spider_client or XHSSpiderClient()

    def analyze(
        self,
        topic: str,
        items: list[NormalizedItem],
        *,
        query_hits_by_item_id: dict[str, set[str]] | None = None,
        limit: int = 5,
    ) -> RecommendedNotesAnalysis:
        candidates, diagnostics = self._prepare_candidates(items, query_hits_by_item_id or {})
        if not candidates:
            return RecommendedNotesAnalysis(notes=[], diagnostics=diagnostics)

        response = self._llm_client.chat(
            system=MVP_RECOMMENDED_NOTES_PROMPT,
            user=self._build_user_payload(topic, candidates),
            max_tokens=1600,
            temperature=0.2,
        )
        payload = self._extract_json_payload(response)
        results, llm_excluded_count = self._sanitize_results(payload, candidates)
        if not results:
            diagnostics.llm_excluded_count = llm_excluded_count
            raise LLMRecommendedNotesFailure(
                "sanitize_empty",
                "no usable recommended note analyses returned by LLM",
                diagnostics=diagnostics,
            )
        diagnostics.llm_excluded_count = llm_excluded_count
        diagnostics.llm_recommended_count = len(results)
        diagnostics.analysis_source = "llm"
        ranked = sorted(
            results,
            key=lambda note: (note.score, note.likes + 1.8 * note.collections + 1.2 * note.comments, note.query_coverage_count),
            reverse=True,
        )
        return RecommendedNotesAnalysis(notes=ranked[:limit], diagnostics=diagnostics)

    def _prepare_candidates(
        self,
        items: list[NormalizedItem],
        query_hits_by_item_id: dict[str, set[str]],
    ) -> tuple[list[CandidateNote], RecommendedNotesDiagnostics]:
        note_items = [item for item in items if item.note_id]
        if not note_items:
            return [], RecommendedNotesDiagnostics(total_note_count=0)

        avg_engagement = sum(item.engagement_score for item in note_items) / max(len(note_items), 1)
        candidates: list[CandidateNote] = []
        filter_reason_counts: dict[str, int] = {}
        for item in note_items:
            detail = self._fetch_note_detail(item)
            user_profile = self._fetch_user_profile(detail)
            exclude_reason = self._get_exclude_reason(item, detail, user_profile, avg_engagement)
            if exclude_reason is not None:
                filter_reason_counts[exclude_reason] = filter_reason_counts.get(exclude_reason, 0) + 1
                continue
            excerpt = self._build_excerpt(item, detail)
            query_coverage_count = len({value.strip() for value in query_hits_by_item_id.get(item.item_id or "", set()) if value.strip()})
            # Pre-rank candidates before sending them to the LLM.
            baseline_score = round(
                item.engagement_score
                + min(query_coverage_count, 3) * 2.0
                + self._content_density_bonus(item, detail, excerpt),
                2,
            )
            candidates.append(
                CandidateNote(
                    item=item,
                    excerpt=excerpt,
                    query_coverage_count=query_coverage_count,
                    baseline_score=baseline_score,
                    detail=detail,
                    user_profile=user_profile,
                )
            )

        diagnostics = RecommendedNotesDiagnostics(
            total_note_count=len(note_items),
            hard_filter_pass_count=len(candidates),
            hard_filter_reasons=[
                RecommendedNotesFilterReason(
                    code=code,
                    label=FILTER_REASON_LABELS.get(code, code),
                    count=count,
                )
                for code, count in sorted(filter_reason_counts.items(), key=lambda item: (-item[1], item[0]))
            ],
        )
        return sorted(candidates, key=lambda entry: (entry.baseline_score, entry.item.engagement_score), reverse=True)[:8], diagnostics

    def _get_exclude_reason(
        self,
        item: NormalizedItem,
        detail: XHSNoteDetail | None,
        user_profile: XHSUserProfile | None,
        avg_engagement: float,
    ) -> str | None:
        # Keep low-value notes out of the LLM input.
        if detail is not None:
            upload_dt = detail.upload_datetime
            if upload_dt is not None:
                age_days = max((datetime.now() - upload_dt).total_seconds(), 0.0) / 86400.0
                if age_days > STALE_DAYS_THRESHOLD:
                    return "stale_note"
        combined = " ".join(
            part.strip().lower()
            for part in [
                item.title,
                item.excerpt,
                item.author,
                *(detail.tags if detail is not None else item.tags),
                (detail.desc if detail is not None else ""),
                (detail.nickname if detail is not None else ""),
                (user_profile.desc if user_profile is not None else ""),
            ]
            if part and part.strip()
        )
        if any(keyword.lower() in combined for keyword in PROMO_KEYWORDS):
            return "promo_content"
        if any(keyword.lower() in combined for keyword in LOW_VALUE_KEYWORDS):
            return "low_value_content"
        if user_profile is not None and int(user_profile.fans or 0) >= HEAD_ACCOUNT_FANS_THRESHOLD:
            return "head_account"

        text_length = len((item.title or "").strip()) + len((detail.desc if detail is not None else item.excerpt or "").strip())
        if text_length < 14 and item.engagement_score < max(12.0, avg_engagement * 0.25):
            return "thin_content"
        if item.engagement_score < max(8.0, avg_engagement * 0.12) and len((item.excerpt or "").strip()) < 18:
            return "thin_content"
        return None

    def _build_user_payload(self, topic: str, candidates: list[CandidateNote]) -> str:
        payload = {
            "topic": topic.strip(),
            "notes": [
                {
                    "note_id": candidate.item.note_id,
                    "title": candidate.item.title,
                    "author": candidate.item.author,
                    "visible_text_excerpt": candidate.excerpt,
                    "tags": candidate.detail.tags if candidate.detail is not None else candidate.item.tags,
                    "likes": candidate.item.likes,
                    "comments": candidate.item.comments,
                    "collections": candidate.item.collections,
                    "source_url": candidate.item.source_url,
                    "query_coverage_count": candidate.query_coverage_count,
                    "baseline_score": candidate.baseline_score,
                    "published_at": candidate.detail.upload_time if candidate.detail is not None else "",
                    "follower_count": int(candidate.user_profile.fans or 0) if candidate.user_profile is not None else None,
                    "note_type": candidate.detail.note_type if candidate.detail is not None else "",
                }
                for candidate in candidates
            ],
        }
        return json.dumps(payload, ensure_ascii=False)

    def _extract_json_payload(self, raw_text: str) -> dict[str, Any]:
        text = (raw_text or "").strip()
        if not text:
            raise LLMRecommendedNotesFailure("invalid_json", "empty LLM response for recommended notes")

        decoder = json.JSONDecoder()
        search_start = 0
        while True:
            brace_index = text.find("{", search_start)
            if brace_index < 0:
                break
            try:
                payload, _ = decoder.raw_decode(text[brace_index:])
                if isinstance(payload, dict):
                    return payload
            except json.JSONDecodeError:
                search_start = brace_index + 1
                continue
            search_start = brace_index + 1
        raise LLMRecommendedNotesFailure("invalid_json", "no JSON object found in recommended notes LLM response")

    def _sanitize_results(
        self,
        payload: dict[str, Any],
        candidates: list[CandidateNote],
    ) -> tuple[list[RecommendedNote], int]:
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise LLMRecommendedNotesFailure("invalid_json", "results must be a list")

        candidate_map = {candidate.item.note_id: candidate for candidate in candidates if candidate.item.note_id}
        results: list[RecommendedNote] = []
        seen: set[str] = set()
        llm_excluded_count = 0
        for entry in raw_results:
            if not isinstance(entry, dict):
                continue
            note_id = str(entry.get("note_id", "")).strip()
            if not note_id or note_id in seen or note_id not in candidate_map:
                continue
            seen.add(note_id)
            if bool(entry.get("excluded")):
                llm_excluded_count += 1
                continue

            candidate = candidate_map[note_id]
            score = entry.get("score", candidate.baseline_score)
            try:
                normalized_score = max(0.0, min(100.0, float(score)))
            except (TypeError, ValueError):
                normalized_score = max(0.0, min(100.0, candidate.baseline_score))

            why_recommended = self._sanitize_text(entry.get("worth_checking_reason", ""))
            score_reason = self._sanitize_text(entry.get("score_reason", ""))
            if not why_recommended:
                continue
            if self._mentions_unavailable_metrics(why_recommended) or self._mentions_unavailable_metrics(score_reason):
                raise LLMRecommendedNotesFailure("sanitize_empty", "recommended note analysis mentioned unavailable metrics")

            results.append(
                RecommendedNote(
                    note_id=candidate.item.note_id or None,
                    title=candidate.item.title or "未命名笔记",
                    source_url=candidate.item.source_url,
                    author=candidate.item.author,
                    excerpt=candidate.excerpt,
                    score=round(normalized_score, 2),
                    score_reason=score_reason or "标题与正文摘要能支撑明确的人群需求或内容切口。",
                    why_recommended=why_recommended,
                    likes=candidate.item.likes,
                    comments=candidate.item.comments,
                    collections=candidate.item.collections,
                    query_coverage_count=candidate.query_coverage_count,
                )
            )
        return results, llm_excluded_count

    def _build_excerpt(self, item: NormalizedItem, detail: XHSNoteDetail | None) -> str:
        text = ((detail.desc if detail is not None else "") or item.excerpt or "").strip()
        if not text:
            return (item.title or "").strip()[:120]
        first_segment = text.splitlines()[0].strip()
        if len(first_segment) > 120:
            return first_segment[:117].rstrip() + "..."
        return first_segment

    def _content_density_bonus(self, item: NormalizedItem, detail: XHSNoteDetail | None, excerpt: str) -> float:
        # Bias the pre-rank toward clearer and fresher notes.
        bonus = 0.5
        if len((item.title or "").strip()) >= 8:
            bonus += 0.5
        if len(excerpt) >= 24:
            bonus += 1.0
        if len((detail.tags if detail is not None else item.tags)) >= 2:
            bonus += 0.5
        if detail is not None and detail.upload_datetime is not None:
            age_days = max((datetime.now() - detail.upload_datetime).total_seconds(), 0.0) / 86400.0
            if age_days <= RECENT_WINDOW_DAYS:
                bonus += 1.0
        return bonus

    def _sanitize_text(self, value: Any) -> str:
        text = " ".join(str(value or "").split())
        return text[:240].strip()

    def _mentions_unavailable_metrics(self, text: str) -> bool:
        lowered = text.strip().lower()
        return any(token.lower() in lowered for token in FORBIDDEN_INFERRED_METRICS)

    def _fetch_note_detail(self, item: NormalizedItem) -> XHSNoteDetail | None:
        note_url = (item.source_url or "").strip()
        if not note_url:
            return None
        try:
            return self._spider_client.fetch_note_detail(note_url)
        except Exception:  # noqa: BLE001
            return None

    def _fetch_user_profile(self, detail: XHSNoteDetail | None) -> XHSUserProfile | None:
        if detail is None or not detail.user_id.strip():
            return None
        try:
            return self._spider_client.fetch_user_profile(detail.user_id)
        except Exception:  # noqa: BLE001
            return None
