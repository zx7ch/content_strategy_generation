"""Source adapter registry for Content Research."""

from __future__ import annotations

from app.content_research.sources.base import SourceAdapter
from app.content_research.sources.xiaohongshu.adapter import XiaohongshuSourceAdapter


class SourceAdapterRegistry:
    def __init__(self, adapters: dict[str, SourceAdapter] | None = None) -> None:
        self._adapters = dict(adapters or {"xiaohongshu": XiaohongshuSourceAdapter()})

    def get(self, provider: str) -> SourceAdapter:
        try:
            return self._adapters[provider]
        except KeyError as exc:
            raise ValueError(f"Unsupported content research source provider: {provider}") from exc
