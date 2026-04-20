"""Web search providers."""

from app.services.web_search.providers.base import WebSearchProvider
from app.services.web_search.providers.xhs_spider import XhsSpiderDiscoverProvider

__all__ = [
    "WebSearchProvider",
    "XhsSpiderDiscoverProvider",
]
