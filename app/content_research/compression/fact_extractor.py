"""Extract bounded facts from persisted evidence records."""

from __future__ import annotations

from typing import Any

from app.content_research.evidence.models import EvidenceRecord


class EvidenceFactExtractor:
    def extract(self, evidence_records: list[EvidenceRecord]) -> list[dict[str, Any]]:
        facts: list[dict[str, Any]] = []
        for index, record in enumerate(evidence_records, start=1):
            quality = _quality(record)
            if not quality["supports_directional_claim"]:
                continue
            quote = str(quality["best_quote"])
            claim = record.claim or _claim_from_record(record, quote)
            facts.append(
                {
                    "schema_version": "content_research_fact_v1",
                    "fact_id": f"fact_{record.id}_{index}",
                    "evidence_id": record.id,
                    "claim": claim,
                    "evidence_quote": quote,
                    "evidence_quality": quality,
                    "metrics": record.metrics,
                    "source_url": record.source_url,
                    "source_id": record.source_id,
                }
            )
        return facts

    def missing_evidence_for_records(self, evidence_records: list[EvidenceRecord]) -> list[dict[str, Any]]:
        missing: list[dict[str, Any]] = []
        for record in evidence_records:
            quality = _quality(record)
            if quality["supports_directional_claim"]:
                continue
            missing.append(
                {
                    "schema_version": "content_research_missing_evidence_v1",
                    "reason": quality["reason"],
                    "message": "该样本目前只有标题或弱摘要，只能作为候选线索，不能支撑策略结论。",
                    "evidence_id": record.id,
                    "evidence_title": record.title,
                }
            )
        return missing


def _quality(record: EvidenceRecord) -> dict[str, Any]:
    title = _clean(record.title)
    excerpt = _clean(record.text_excerpt)
    payload = record.normalized_payload or {}
    comments = _comments(payload)
    quote = _best_quote(title, excerpt, comments)
    has_body = bool(excerpt and excerpt != title and len(excerpt) >= 8)
    has_comment = bool(comments)
    has_metric = any(_positive_number(value) for value in (record.metrics or {}).values())
    supports = has_body or has_comment or (has_metric and bool(title))
    if has_body:
        reason = "body_excerpt_available"
    elif has_comment:
        reason = "comment_excerpt_available"
    elif has_metric:
        reason = "metric_signal_available"
    else:
        reason = "title_only_candidate"
    return {
        "schema_version": "content_research_evidence_quality_v1",
        "supports_directional_claim": supports,
        "reason": reason,
        "has_body_excerpt": has_body,
        "has_comment_excerpt": has_comment,
        "has_metric_signal": has_metric,
        "best_quote": quote,
    }


def _claim_from_record(record: EvidenceRecord, quote: str) -> str:
    title = _clean(record.title)
    if quote and title and quote != title:
        return f"{title}: {quote}"
    return quote or title or f"Evidence from {record.source_platform}"


def _best_quote(title: str, excerpt: str, comments: list[str]) -> str:
    if excerpt and excerpt != title:
        return excerpt[:240]
    if comments:
        return comments[0][:240]
    return title[:240]


def _comments(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("comments") or payload.get("comment_texts") or payload.get("top_comments") or []
    if isinstance(raw, str):
        return [_clean(raw)] if _clean(raw) else []
    if not isinstance(raw, list):
        return []
    comments: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            text = _clean(item.get("text") or item.get("content") or item.get("comment"))
        else:
            text = _clean(item)
        if text:
            comments.append(text)
    return comments


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _positive_number(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False
