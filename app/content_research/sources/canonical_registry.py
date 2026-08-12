"""Stable platform identity resolution for source operations."""

from __future__ import annotations

import hashlib

from app.content_research.models import utcnow
from app.content_research.persistence_models import CanonicalSourceRecord
from app.content_research.stores.base import ContentResearchStore


class CanonicalSourceRegistry:
    def __init__(self, store: ContentResearchStore) -> None:
        self._store = store

    def resolve_note(self, *, provider: str, note_id: str, canonical_url: str = "") -> CanonicalSourceRecord:
        return self._resolve(provider=provider, kind="note", source_id=note_id, canonical_url=canonical_url)

    def resolve_comment(self, *, provider: str, comment_id: str, parent_note_canonical_source_id: str) -> CanonicalSourceRecord:
        return self._resolve(provider=provider, kind="comment", source_id=comment_id, payload={"parent_note_canonical_source_id": parent_note_canonical_source_id})

    def _resolve(self, *, provider: str, kind: str, source_id: str, canonical_url: str = "", payload: dict | None = None) -> CanonicalSourceRecord:
        if not source_id:
            raise ValueError("canonical source requires a platform source id")
        digest = hashlib.sha256(f"{provider}:{kind}:{source_id}".encode()).hexdigest()[:24]
        return self._store.resolve_canonical_source(CanonicalSourceRecord(
            id=f"cs_{digest}", schema_version="content_research_canonical_source_v1", platform=provider,
            platform_source_kind=kind, platform_source_id=source_id, canonical_url=canonical_url,
            payload=payload or {}, created_at=utcnow(),
        ))
