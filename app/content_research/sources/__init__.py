"""Source adapter layer for Content Research."""

from app.content_research.sources.base import SourceAdapter, SourceCollectionRequest, SourceCollectionResult
from app.content_research.sources.registry import SourceAdapterRegistry

__all__ = [
    "SourceAdapter",
    "SourceAdapterRegistry",
    "SourceCollectionRequest",
    "SourceCollectionResult",
]
