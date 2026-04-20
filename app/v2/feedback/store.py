"""Store protocol and in-memory implementation for V2 publish, performance, and evaluation."""

from __future__ import annotations

from typing import Protocol

from app.v2.feedback.models import (
    EvaluationRunRecord,
    EvaluationRunSliceRecord,
    FeedbackEventRecord,
    PerformanceSnapshotRecord,
    PublishRecordRecord,
)


class FeedbackStore(Protocol):
    def save_publish_record(self, record: PublishRecordRecord) -> PublishRecordRecord: ...

    def get_publish_record(self, publish_record_id: str) -> PublishRecordRecord | None: ...

    def list_publish_records(self, brand_id: str) -> list[PublishRecordRecord]: ...

    def save_performance_snapshot(self, snapshot: PerformanceSnapshotRecord) -> PerformanceSnapshotRecord: ...

    def list_performance_snapshots(self, brand_id: str) -> list[PerformanceSnapshotRecord]: ...

    def save_feedback_event(self, event: FeedbackEventRecord) -> FeedbackEventRecord: ...

    def list_feedback_events(self, brand_id: str) -> list[FeedbackEventRecord]: ...

    def save_evaluation_run(self, run: EvaluationRunRecord) -> EvaluationRunRecord: ...

    def get_evaluation_run(self, evaluation_run_id: str) -> EvaluationRunRecord | None: ...

    def list_evaluation_runs(self, brand_id: str) -> list[EvaluationRunRecord]: ...

    def save_evaluation_run_slice(self, slice_record: EvaluationRunSliceRecord) -> EvaluationRunSliceRecord: ...

    def list_evaluation_run_slices(self, evaluation_run_id: str) -> list[EvaluationRunSliceRecord]: ...


class InMemoryFeedbackStore:
    def __init__(self) -> None:
        self._publish_records: dict[str, PublishRecordRecord] = {}
        self._performance_snapshots: dict[str, PerformanceSnapshotRecord] = {}
        self._feedback_events: dict[str, FeedbackEventRecord] = {}
        self._evaluation_runs: dict[str, EvaluationRunRecord] = {}
        self._evaluation_run_slices: dict[str, EvaluationRunSliceRecord] = {}

    def save_publish_record(self, record: PublishRecordRecord) -> PublishRecordRecord:
        self._publish_records[record.id] = record
        return record

    def get_publish_record(self, publish_record_id: str) -> PublishRecordRecord | None:
        return self._publish_records.get(publish_record_id)

    def list_publish_records(self, brand_id: str) -> list[PublishRecordRecord]:
        rows = [row for row in self._publish_records.values() if row.brand_id == brand_id]
        rows.sort(key=lambda item: (item.published_at or item.created_at, item.created_at), reverse=True)
        return rows

    def save_performance_snapshot(self, snapshot: PerformanceSnapshotRecord) -> PerformanceSnapshotRecord:
        self._performance_snapshots[snapshot.id] = snapshot
        return snapshot

    def list_performance_snapshots(self, brand_id: str) -> list[PerformanceSnapshotRecord]:
        rows = [row for row in self._performance_snapshots.values() if row.brand_id == brand_id]
        rows.sort(key=lambda item: (item.snapshot_at, item.created_at), reverse=True)
        return rows

    def save_feedback_event(self, event: FeedbackEventRecord) -> FeedbackEventRecord:
        self._feedback_events[event.id] = event
        return event

    def list_feedback_events(self, brand_id: str) -> list[FeedbackEventRecord]:
        rows = [row for row in self._feedback_events.values() if row.brand_id == brand_id]
        rows.sort(key=lambda item: item.created_at, reverse=True)
        return rows

    def save_evaluation_run(self, run: EvaluationRunRecord) -> EvaluationRunRecord:
        self._evaluation_runs[run.id] = run
        return run

    def get_evaluation_run(self, evaluation_run_id: str) -> EvaluationRunRecord | None:
        return self._evaluation_runs.get(evaluation_run_id)

    def list_evaluation_runs(self, brand_id: str) -> list[EvaluationRunRecord]:
        rows = [row for row in self._evaluation_runs.values() if row.brand_id == brand_id]
        rows.sort(key=lambda item: item.created_at, reverse=True)
        return rows

    def save_evaluation_run_slice(self, slice_record: EvaluationRunSliceRecord) -> EvaluationRunSliceRecord:
        self._evaluation_run_slices[slice_record.id] = slice_record
        return slice_record

    def list_evaluation_run_slices(self, evaluation_run_id: str) -> list[EvaluationRunSliceRecord]:
        rows = [
            row for row in self._evaluation_run_slices.values() if row.evaluation_run_id == evaluation_run_id
        ]
        rows.sort(key=lambda item: (item.slice_key, item.slice_value))
        return rows
