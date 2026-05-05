from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.services.xhs_spider import SpiderSearchSortOption, XHSSpiderClient
from experiments.xhs_extension_mvp.server.models import HotspotItem, HotspotList, HotspotSnapshotResponse


@dataclass(slots=True)
class _AggregatePost:
    note_id: str
    title: str
    source_url: str
    author: str
    excerpt: str
    likes: int
    comments: int
    collections: int
    query_sources: set[str] = field(default_factory=set)


async def build_hotspot_snapshot(
    *,
    task_id: str,
    topic: str,
    queries: list[str],
    spider_client: Any | None = None,
    per_query_limit: int = 10,
) -> HotspotSnapshotResponse:
    normalized_queries = _normalize_queries(topic, queries)
    if not normalized_queries:
        return HotspotSnapshotResponse(task_id=task_id, status="empty")

    client = spider_client or XHSSpiderClient()
    sort_options = _resolve_hotspot_sort_options(client)
    lists: list[HotspotList] = []

    for sort_option in sort_options:
        aggregated: dict[str, _AggregatePost] = {}
        for query in normalized_queries:
            posts = await client.search_with_retry(query, num=per_query_limit, sort=sort_option.value)
            for post in posts:
                if not getattr(post, "title_is_explicit", False):
                    continue
                key = post.note_id.strip() or post.note_url.strip() or f"{post.title.strip()}|{post.author.strip()}"
                if not key:
                    continue
                if key not in aggregated:
                    aggregated[key] = _AggregatePost(
                        note_id=post.note_id.strip(),
                        title=post.title.strip() or "无标题",
                        source_url=post.note_url.strip(),
                        author=post.author.strip(),
                        excerpt=_trim_excerpt(post.content),
                        likes=max(0, int(post.liked_count)),
                        comments=max(0, int(post.comment_count)),
                        collections=max(0, int(post.collected_count)),
                    )
                aggregate = aggregated[key]
                aggregate.query_sources.add(query)
                aggregate.likes = max(aggregate.likes, max(0, int(post.liked_count)))
                aggregate.comments = max(aggregate.comments, max(0, int(post.comment_count)))
                aggregate.collections = max(aggregate.collections, max(0, int(post.collected_count)))
                if not aggregate.excerpt:
                    aggregate.excerpt = _trim_excerpt(post.content)
                if not aggregate.source_url:
                    aggregate.source_url = post.note_url.strip()

        ranked_items = sorted(
            aggregated.values(),
            key=lambda item: (
                getattr(item, sort_option.key),
                item.likes,
                item.collections,
                item.comments,
                item.title,
            ),
            reverse=True,
        )[:5]
        lists.append(
            HotspotList(
                metric=sort_option.key,  # type: ignore[arg-type]
                items=[
                    HotspotItem(
                        note_id=item.note_id or None,
                        title=item.title,
                        source_url=item.source_url,
                        author=item.author,
                        excerpt=item.excerpt,
                        likes=item.likes,
                        comments=item.comments,
                        collections=item.collections,
                        query_sources=sorted(item.query_sources),
                    )
                    for item in ranked_items
                ],
            )
        )

    status = "ready" if any(hotspot_list.items for hotspot_list in lists) else "empty"
    return HotspotSnapshotResponse(
        task_id=task_id,
        status=status,
        generated_at=datetime.now(timezone.utc),
        stale_seconds=0,
        lists=lists,
    )


def _resolve_hotspot_sort_options(client: Any) -> tuple[SpiderSearchSortOption, ...]:
    getter = getattr(client, "get_hotspot_sort_options", None)
    if callable(getter):
        options = tuple(getter())
        if options:
            return options
    return XHSSpiderClient.get_hotspot_sort_options()


def _normalize_queries(topic: str, queries: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for query in [topic, *queries]:
        normalized = " ".join(query.strip().split())
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _trim_excerpt(text: str) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    first_line = value.splitlines()[0].strip()
    if len(first_line) > 120:
        return first_line[:117].rstrip() + "..."
    return first_line
