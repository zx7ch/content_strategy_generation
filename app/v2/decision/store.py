"""Store protocol and in-memory implementation for V2 decision data."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from app.v2.decision.models import (
    CandidateSetSnapshotRecord,
    DecisionBatchItemRecord,
    DecisionBatchRecord,
    DecisionEventRecord,
)


class DecisionStore(Protocol):
    def save_decision_batch(self, batch: DecisionBatchRecord) -> DecisionBatchRecord: ...

    def get_decision_batch(self, batch_id: str) -> DecisionBatchRecord | None: ...

    def list_decision_batches(self, brand_id: str) -> list[DecisionBatchRecord]: ...

    def save_decision_event(self, event: DecisionEventRecord) -> DecisionEventRecord: ...

    def get_decision_event(self, event_id: str) -> DecisionEventRecord | None: ...

    def list_decision_events(self, brand_id: str) -> list[DecisionEventRecord]: ...

    def save_decision_batch_item(self, item: DecisionBatchItemRecord) -> DecisionBatchItemRecord: ...

    def get_decision_batch_item_by_slot(
        self,
        *,
        batch_id: str,
        slot_index: int,
    ) -> DecisionBatchItemRecord | None: ...

    def list_decision_batch_items(self, batch_id: str) -> list[DecisionBatchItemRecord]: ...

    def save_candidate_set_snapshot(
        self,
        snapshot: CandidateSetSnapshotRecord,
    ) -> CandidateSetSnapshotRecord: ...

    def list_candidate_set_snapshots(
        self,
        *,
        brand_id: str,
        decision_batch_id: str | None = None,
    ) -> list[CandidateSetSnapshotRecord]: ...

    def delete_by_brand(self, brand_id: str) -> int: ...


class InMemoryDecisionStore:
    def __init__(self) -> None:
        self._batches: dict[str, DecisionBatchRecord] = {}
        self._events: dict[str, DecisionEventRecord] = {}
        self._batch_items: dict[tuple[str, int], DecisionBatchItemRecord] = {}
        self._snapshots: dict[str, CandidateSetSnapshotRecord] = {}

    def save_decision_batch(self, batch: DecisionBatchRecord) -> DecisionBatchRecord:
        self._batches[batch.id] = batch
        return batch

    def get_decision_batch(self, batch_id: str) -> DecisionBatchRecord | None:
        return self._batches.get(batch_id)

    def list_decision_batches(self, brand_id: str) -> list[DecisionBatchRecord]:
        batches = [batch for batch in self._batches.values() if batch.brand_id == brand_id]
        batches.sort(key=lambda item: item.created_at, reverse=True)
        return batches

    def save_decision_event(self, event: DecisionEventRecord) -> DecisionEventRecord:
        self._events[event.id] = event
        return event

    def get_decision_event(self, event_id: str) -> DecisionEventRecord | None:
        return self._events.get(event_id)

    def list_decision_events(self, brand_id: str) -> list[DecisionEventRecord]:
        events = [event for event in self._events.values() if event.brand_id == brand_id]
        events.sort(key=lambda item: (item.created_at, item.slot_index), reverse=True)
        return events

    def save_decision_batch_item(self, item: DecisionBatchItemRecord) -> DecisionBatchItemRecord:
        key = (item.batch_id, item.selected_slot_index)
        existing = self._batch_items.get(key)
        if existing is not None:
            item = replace(
                item,
                batch_id=existing.batch_id,
                selected_slot_index=existing.selected_slot_index,
            )
        self._batch_items[key] = item
        return item

    def get_decision_batch_item_by_slot(
        self,
        *,
        batch_id: str,
        slot_index: int,
    ) -> DecisionBatchItemRecord | None:
        return self._batch_items.get((batch_id, slot_index))

    def list_decision_batch_items(self, batch_id: str) -> list[DecisionBatchItemRecord]:
        items = [item for item in self._batch_items.values() if item.batch_id == batch_id]
        items.sort(key=lambda item: item.selected_slot_index)
        return items

    def save_candidate_set_snapshot(
        self,
        snapshot: CandidateSetSnapshotRecord,
    ) -> CandidateSetSnapshotRecord:
        self._snapshots[snapshot.id] = snapshot
        return snapshot

    def list_candidate_set_snapshots(
        self,
        *,
        brand_id: str,
        decision_batch_id: str | None = None,
    ) -> list[CandidateSetSnapshotRecord]:
        snapshots = [snapshot for snapshot in self._snapshots.values() if snapshot.brand_id == brand_id]
        if decision_batch_id is not None:
            snapshots = [snapshot for snapshot in snapshots if snapshot.decision_batch_id == decision_batch_id]
        snapshots.sort(key=lambda item: (item.created_at, item.slot_index or -1), reverse=True)
        return snapshots

    def delete_by_brand(self, brand_id: str) -> int:
        count = 0
        batch_ids = {bid for bid, b in self._batches.items() if b.brand_id == brand_id}
        item_keys = [k for k in self._batch_items if k[0] in batch_ids]
        for k in item_keys:
            del self._batch_items[k]
            count += 1
        snap_ids = [sid for sid, s in self._snapshots.items() if s.brand_id == brand_id]
        for sid in snap_ids:
            del self._snapshots[sid]
            count += 1
        event_ids = [eid for eid, e in self._events.items() if e.brand_id == brand_id]
        for eid in event_ids:
            del self._events[eid]
            count += 1
        for bid in batch_ids:
            del self._batches[bid]
            count += 1
        return count
