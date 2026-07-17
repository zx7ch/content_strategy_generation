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
            "raw_payload_hash": _stable_hash(raw_payload),
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
            "raw_payload": raw_payload,
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
            "raw_payload": raw_payload,
        }


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _failure_canonical_id(payload: dict[str, Any]) -> str:
    return f"xhs_failure:{_stable_hash(payload)[:24]}"
