from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.runtime_write_coordinator import RuntimeWriteCoordinator
from app.core.sqlite_connection_roles import SQLiteConnectionOpened, observe_sqlite_connections
from app.services.xhs_spider import SpiderSearchSortOption, XHSPost
from app.v2.discovery.mutations import discovery_mutation_handlers
from app.v2.discovery.query_expander import DiscoveryExpandedQuery, DiscoveryExpansionResult
from app.v2.discovery.service import DiscoveryService


class _QueryExpander:
    async def expand_topic(self, topic: str) -> DiscoveryExpansionResult:
        return DiscoveryExpansionResult(
            queries=[
                DiscoveryExpandedQuery(category="core", query_text=topic),
                DiscoveryExpandedQuery(category="problem", query_text=f"{topic}怎么选"),
            ],
            source="test",
        )


class _SpiderClient:
    @staticmethod
    def get_hotspot_sort_options() -> tuple[SpiderSearchSortOption, ...]:
        return (SpiderSearchSortOption(key="likes", label="最多点赞", value=2),)

    async def search_with_retry(self, query: str, num: int = 50, sort: int = 2) -> list[XHSPost]:
        return [
            XHSPost(
                note_id=f"{query}-{sort}",
                title=f"{query} 热点",
                title_is_explicit=True,
                content="热点摘要",
                author="tester",
                tags=[],
                liked_count=10,
                collected_count=2,
                comment_count=1,
                share_count=0,
                note_url=f"https://example.test/{query}-{sort}",
                images=[],
            )
        ]


def test_discovery_public_mutations_share_runtime_writer(tmp_path: Path) -> None:
    database = tmp_path / "discovery.sqlite"
    DiscoveryService(database_path=database, secret="secret")
    opened: list[SQLiteConnectionOpened] = []

    async def exercise() -> None:
        writer = RuntimeWriteCoordinator(database, handlers=discovery_mutation_handlers())
        await writer.start()
        service = DiscoveryService(
            database_path=database,
            secret="secret",
            spider_client=_SpiderClient(),
            query_expander=_QueryExpander(),
            writer=writer,
        )
        created = await service.create_task(
            workspace_id="workspace-owned",
            brand_id="brand-owned",
            topic="通勤穿搭",
        )
        added = await service.add_custom_queries(
            workspace_id="workspace-owned",
            brand_id="brand-owned",
            task_id=created.task_snapshot.task_id,
            text="雨天通勤",
        )
        custom = next(query for query in added.task_snapshot.expanded_queries if query.category == "custom")
        await service.delete_custom_query(
            workspace_id="workspace-owned",
            brand_id="brand-owned",
            task_id=created.task_snapshot.task_id,
            query_id=custom.query_id,
        )
        refreshed = await service.refresh_hotspots(
            workspace_id="workspace-owned",
            brand_id="brand-owned",
            task_id=created.task_snapshot.task_id,
        )
        assert refreshed.hotspot_snapshot.status == "ready"
        assert refreshed.hotspot_snapshot.lists[0].items[0].title.startswith("通勤穿搭")
        await writer.close()

    with observe_sqlite_connections(opened.append):
        asyncio.run(exercise())

    writers = [event for event in opened if event.role == "writer"]
    assert len(writers) == 1
    assert {event.role for event in opened} == {"writer", "reader"}
