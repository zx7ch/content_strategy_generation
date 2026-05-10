"""Store protocol and in-memory implementation for V2 topic pool items."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from app.v2.topic_pool.models import TopicPoolItemRecord


class TopicPoolStore(Protocol):
    def save_topic_pool_item(self, item: TopicPoolItemRecord) -> TopicPoolItemRecord: ...

    def get_topic_pool_item(self, item_id: str) -> TopicPoolItemRecord | None: ...

    def get_topic_pool_item_by_topic(self, *, brand_id: str, topic_id: str) -> TopicPoolItemRecord | None: ...

    def list_topic_pool_items(
        self,
        brand_id: str,
        *,
        include_archived: bool = False,
    ) -> list[TopicPoolItemRecord]: ...

    def delete_topic_pool_items(self, brand_id: str) -> int: ...


class InMemoryTopicPoolStore:
    def __init__(self) -> None:
        self._items: dict[str, TopicPoolItemRecord] = {}
        self._by_brand_topic: dict[tuple[str, str], str] = {}

    def save_topic_pool_item(self, item: TopicPoolItemRecord) -> TopicPoolItemRecord:
        key = (item.brand_id, item.topic_id)
        existing_id = self._by_brand_topic.get(key)
        if existing_id is not None and existing_id != item.id:
            existing = self._items[existing_id]
            item = replace(
                item,
                id=existing.id,
                created_at=existing.created_at,
            )
        self._items[item.id] = item
        self._by_brand_topic[key] = item.id
        return item

    def get_topic_pool_item(self, item_id: str) -> TopicPoolItemRecord | None:
        return self._items.get(item_id)

    def get_topic_pool_item_by_topic(self, *, brand_id: str, topic_id: str) -> TopicPoolItemRecord | None:
        item_id = self._by_brand_topic.get((brand_id, topic_id))
        if item_id is None:
            return None
        return self._items.get(item_id)

    def list_topic_pool_items(
        self,
        brand_id: str,
        *,
        include_archived: bool = False,
    ) -> list[TopicPoolItemRecord]:
        items = [item for item in self._items.values() if item.brand_id == brand_id]
        if not include_archived:
            items = [item for item in items if item.status != "archived"]
        items.sort(key=lambda item: (-item.final_score, item.updated_at.isoformat(), item.title))
        return items

    def delete_topic_pool_items(self, brand_id: str) -> int:
        to_delete = [iid for iid, item in self._items.items() if item.brand_id == brand_id]
        for iid in to_delete:
            item = self._items.pop(iid)
            self._by_brand_topic.pop((item.brand_id, item.topic_id), None)
        return len(to_delete)
