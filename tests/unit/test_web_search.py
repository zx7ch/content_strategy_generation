from __future__ import annotations

import uuid

import pytest

from app.services.web_search import SearchIntent, build_default_search_orchestrator
from app.services.web_search.models import CapabilityRequest
from app.services.web_search.providers.xhs_spider import XhsSpiderDiscoverProvider
from app.services.xhs_spider import SpiderPermanentError, XHSPost


def _post(note_id: str) -> XHSPost:
    return XHSPost(
        note_id=note_id,
        title=f"title-{note_id}",
        content=f"content-{note_id}",
        author="author",
        tags=["tag-a", "tag-b"],
        liked_count=10,
        collected_count=5,
        comment_count=1,
        share_count=0,
        note_url=f"https://xhs.com/{note_id}",
        images=["img"],
    )


class FakeSpider:
    def __init__(self, mapping=None, fail=False):
        self.mapping = mapping or {}
        self.fail = fail

    async def search_with_retry(self, query: str, num: int = 50):
        if self.fail:
            raise SpiderPermanentError("spider failed")
        return list(self.mapping.get(query, []))


@pytest.mark.asyncio
async def test_xhs_spider_provider_maps_posts_to_evidence():
    provider = XhsSpiderDiscoverProvider(spider_client=FakeSpider(mapping={"护肤": [_post("n1")]}))
    result = await provider.execute(
        request=CapabilityRequest(
            capability="discover",
            intent=SearchIntent(query="护肤"),
            limit=10,
        )
    )

    assert result.status == "success"
    assert len(result.items) == 1
    assert result.items[0].canonical_id == "n1"
    assert result.items[0].source_provider == "xhs_spider"


@pytest.mark.asyncio
async def test_orchestrator_discovers_via_spider_only(tmp_path):
    db_path = str(tmp_path / "web-search.db")
    session_id = str(uuid.uuid4())
    orchestrator = build_default_search_orchestrator(
        spider_client=FakeSpider(mapping={"护肤": [_post("n9")]}),
        db_path=db_path,
    )
    batch = await orchestrator.discover(
        SearchIntent(
            query="护肤",
            session_id=session_id,
            workflow_stage="strategy",
        )
    )

    assert batch.status == "success"
    assert len(batch.items) == 1
    assert batch.items[0].canonical_id == "n9"


@pytest.mark.asyncio
async def test_orchestrator_returns_empty_when_spider_fails_and_no_fallback():
    orchestrator = build_default_search_orchestrator(
        spider_client=FakeSpider(fail=True),
    )
    batch = await orchestrator.discover(
        SearchIntent(
            query="护肤",
            session_id=str(uuid.uuid4()),
            workflow_stage="strategy",
        )
    )

    assert batch.status == "empty"
    assert batch.failure_reason == "permanent_error"
