"""Normalize Xiaohongshu spider posts into Content Research source payloads."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.content_research.sources.xiaohongshu.types import (
    COOKIE_STATUS_UNKNOWN,
    SOURCE_KIND_SEARCH_RESULT_MINIMAL,
    SOURCE_PROVIDER,
)
from app.services.xhs_spider import XHSPost


class XiaohongshuSourceNormalizer:
    def normalize_search_result(
        self,
        post: XHSPost,
        *,
        query: str,
        source_kind: str = SOURCE_KIND_SEARCH_RESULT_MINIMAL,
    ) -> dict[str, Any]:
        raw_payload = post.model_dump()
        note_id = str(raw_payload.get("note_id") or "").strip()
        note_url = str(raw_payload.get("note_url") or "").strip()
        if not note_id and not note_url:
            raise ValueError("XHS search result must include note_id or note_url")

        captured_at = datetime.now(timezone.utc).isoformat()
        return {
            "schema_version": "content_research_source_payload_v1",
            "provider": SOURCE_PROVIDER,
            "source_kind": source_kind,
            "source_url": note_url,
            "canonical_id": note_id or note_url,
            "captured_at": captured_at,
            "source_collected_at": captured_at,
            "metrics_observed_at": captured_at,
            "raw_payload_hash": _stable_hash(raw_payload),
            "field_availability": _availability(raw_payload, ("title", "content_text", "author", "metrics")),
            "cookie_status": "valid",
            "failure_reason": None,
            "query_used": query,
            "title": post.title,
            "content_text": post.content,
            "author": post.author,
            "tags": list(post.tags),
            "metrics": {
                "liked_count": post.liked_count,
                "collected_count": post.collected_count,
                "comment_count": post.comment_count,
                "share_count": post.share_count,
            },
            "media": list(post.images),
        }

    def normalize_comment(
        self, comment: dict[str, Any], *, parent_note_id: str, source_collected_at: str | None = None
    ) -> dict[str, Any]:
        comment_id = str(comment.get("id") or comment.get("comment_id") or "").strip()
        if not comment_id:
            raise ValueError("XHS comment must include an id")
        collected_at = source_collected_at or datetime.now(timezone.utc).isoformat()
        content = str(comment.get("content") or comment.get("content_text") or "")
        author = comment.get("user_info") or comment.get("user") or {}
        author_name = str(author.get("nickname") or comment.get("user_nickname") or "") if isinstance(author, dict) else ""
        raw_payload = dict(comment)
        return {
            "schema_version": "content_research_source_payload_v2",
            "provider": SOURCE_PROVIDER,
            "source_kind": "comment",
            "source_url": "",
            "canonical_id": comment_id,
            "parent_note_id": parent_note_id,
            "captured_at": collected_at,
            "source_collected_at": collected_at,
            "source_published_at": comment.get("create_time") or comment.get("create_time_text"),
            "raw_payload_hash": _stable_hash(raw_payload),
            "field_availability": _availability(
                {"comment_text": content, "author": author_name, "parent_note_id": parent_note_id},
                ("comment_text", "author", "parent_note_id"),
            ),
            "cookie_status": "valid",
            "failure_reason": None,
            "content_text": content,
            "author": author_name,
            "reply_depth": 0,
        }

    def normalize_note_detail(
        self, post: XHSPost, *, required_fields: tuple[str, ...]
    ) -> dict[str, Any]:
        """Create a governed detail projection from an actual detail response."""
        raw_payload = post.model_dump()
        note_id = str(post.note_id or "").strip()
        note_url = str(post.note_url or "").strip()
        if not note_id or not note_url:
            raise ValueError("XHS note detail must include note_id and note_url")

        captured_at = datetime.now(timezone.utc).isoformat()
        metrics = {
            "liked_count": post.liked_count,
            "collected_count": post.collected_count,
            "comment_count": post.comment_count,
            "share_count": post.share_count,
        }
        availability_payload = {
            "title": post.title,
            "content_text": post.content,
            "tags": post.tags,
            "note_type": post.note_type,
            "metrics": metrics,
            "metrics_observed_at": captured_at,
        }
        return {
            "schema_version": "content_research_source_payload_v2",
            "provider": SOURCE_PROVIDER,
            "source_kind": "note_detail",
            "source_url": note_url,
            "canonical_id": note_id,
            "captured_at": captured_at,
            "source_collected_at": captured_at,
            "source_published_at": post.source_published_at,
            "metrics_observed_at": captured_at,
            "raw_payload_hash": _stable_hash(raw_payload),
            "field_availability": _availability(availability_payload, required_fields),
            "cookie_status": "valid",
            "failure_reason": None,
            "title": post.title,
            "content_text": post.content,
            "author": post.author,
            "tags": list(post.tags),
            "note_type": post.note_type,
            "metrics": metrics,
            "media": list(post.images),
        }

    def build_failure_payload(
        self,
        *,
        workflow_run_id: str,
        query: str,
        source_kind: str,
        failure_reason: str,
        cookie_status: str = COOKIE_STATUS_UNKNOWN,
    ) -> dict[str, Any]:
        raw_payload = {
            "provider": SOURCE_PROVIDER,
            "workflow_run_id": workflow_run_id,
            "query": query,
            "source_kind": source_kind,
            "failure_reason": failure_reason,
            "cookie_status": cookie_status,
        }
        return {
            "schema_version": "content_research_source_payload_v1",
            "provider": SOURCE_PROVIDER,
            "source_kind": source_kind,
            "source_url": "",
            "canonical_id": _failure_canonical_id(raw_payload),
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "raw_payload_hash": _stable_hash(raw_payload),
            "cookie_status": cookie_status,
            "failure_reason": failure_reason,
            "query_used": query,
            "title": "",
            "content_text": "",
            "author": "",
            "tags": [],
            "metrics": {},
            "media": [],
        }


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _failure_canonical_id(payload: dict[str, Any]) -> str:
    return f"xhs_failure:{_stable_hash(payload)[:24]}"


def _availability(payload: dict[str, Any], fields: tuple[str, ...]) -> dict[str, str]:
    return {
        field: "present" if payload.get(field) not in (None, "", [], {}) else "missing"
        for field in fields
    }
