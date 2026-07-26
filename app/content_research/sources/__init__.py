"""Source adapter layer for Content Research."""

from app.content_research.sources.base import SourceAdapter
from app.content_research.sources.registry import SourceAdapterRegistry

__all__ = [
    "SourceAdapter",
    "SourceAdapterRegistry",
]
