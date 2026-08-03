"""Read-only trace aggregation for Content Research workflows."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from app.content_research.api_schemas import ContentResearchTraceResponse
from app.content_research.models import ObservationEventRecord, ResearchBriefRecord, TraceRecord
from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.workflow_store import WorkflowStore
from app.services.llm.usage_tracker import LLMUsageSummary, LLMUsageTracker


class ContentResearchTraceService:
    """Build a frontend-friendly trace view from persisted runtime sources."""

    def __init__(self, *, store: SQLiteContentResearchStore, db_path: str) -> None:
        self._store = store
        self._db_path = db_path

    async def build_trace(
        self,
        *,
        workflow_run_id: str,
        brief: ResearchBriefRecord,
    ) -> ContentResearchTraceResponse:
        traces = self._store.list_traces_for_workflow(workflow_run_id)
        observation_events = [
            event
            for trace in traces
            for event in self._store.list_observation_events(trace.id)
        ]

        # Trace is a query-only projection: opening it must not DDL/commit or
        # join a writer's lock queue while a provider is collecting evidence.
        async with WorkflowStore(self._db_path, read_only=True) as workflow_store:
            run = await workflow_store.get_run(workflow_run_id)
            runtime_steps = await workflow_store.list_steps(workflow_run_id)
            runtime_child_tasks = await workflow_store.list_child_tasks(workflow_run_id)
            workflow_events = await workflow_store.list_events(workflow_run_id)

        async with LLMUsageTracker(self._db_path, read_only=True) as usage_tracker:
            usage_steps = await usage_tracker.summarize_job_steps(workflow_run_id)
            usage_events = await usage_tracker.list_job_events(workflow_run_id)

        current_stage = _derive_current_stage(
            run=_json_dict(run),
            steps=[_json_dict(step) for step in runtime_steps],
            traces=traces,
        )
        run_status = str(run.status.value if run and hasattr(run.status, "value") else run.status) if run else brief.status
        duration_ms = _duration_ms(
            traces=traces,
            observation_events=observation_events,
            workflow_events=[_json_dict(event) for event in workflow_events],
            run=_json_dict(run),
        )
        usage_steps_dicts = [_json_dict(step) for step in usage_steps]
        usage_events_dicts = [_json_dict(event) for event in usage_events]
        workflow_event_dicts = [_json_dict(event) for event in workflow_events]
        # Aggregate from the durable records before narrowing their public
        # representation.  The records contain provider payloads and must
        # never cross the Lite Trace API boundary.
        provider_operations = _provider_operations(self._store, workflow_run_id)
        observation_event_dicts = [_safe_observation_event_dict(event) for event in observation_events]
        source_operation_events = [_source_operation_event_dict(event) for event in observation_events]

        return ContentResearchTraceResponse(
            workflow_run_id=workflow_run_id,
            thread_id=brief.thread_id,
            current_stage=current_stage,
            run_status=run_status,
            recoverable=_is_recoverable(run_status, brief.status),
            duration_ms=duration_ms,
            error_count=_error_count(
                observation_events=observation_event_dicts,
                workflow_events=workflow_event_dicts,
                usage_events=usage_events_dicts,
            ),
            retry_count=_retry_count(workflow_events=workflow_event_dicts, usage_steps=usage_steps_dicts),
            traces=[_safe_trace_dict(trace) for trace in traces],
            observation_events=observation_event_dicts,
            workflow_events=[_safe_workflow_event_dict(event) for event in workflow_event_dicts],
            runtime_steps=[_safe_runtime_step_dict(step) for step in runtime_steps],
            runtime_child_tasks=[_safe_runtime_child_task_dict(task) for task in runtime_child_tasks],
            usage_summary={},
            external_api_summary=_external_api_summary(
                source_operation_events, provider_operations
            ),
            provider_operations=[_safe_provider_operation_dict(item) for item in provider_operations],
            usage_steps=[],
            usage_events=[],
            llm_recovery=_llm_recovery_projection(
                run_status=run_status, current_stage=current_stage,
                runtime_steps=[_json_dict(step) for step in runtime_steps], brief=brief,
            ),
        )


def _json_dict(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return _json_safe(value)
    return {}


def _safe_trace_dict(trace: TraceRecord) -> dict:
    return _json_safe({
        "id": trace.id,
        "workflow_run_id": trace.workflow_run_id,
        "thread_id": trace.thread_id,
        "schema_version": trace.schema_version,
        "status": trace.status,
        "started_at": trace.started_at,
        "created_at": trace.created_at,
        "updated_at": trace.updated_at,
    })


def _safe_observation_event_dict(event: ObservationEventRecord) -> dict:
    return _json_safe({
        "id": event.id,
        "trace_id": event.trace_id,
        "workflow_run_id": event.workflow_run_id,
        "thread_id": event.thread_id,
        "schema_version": event.schema_version,
        "status": event.status,
        "sequence_no": event.sequence_no,
        "event_type": event.event_type,
        "event_name": event.event_name,
        "timestamp": event.timestamp,
        "created_at": event.created_at,
        "updated_at": event.updated_at,
    })


def _source_operation_event_dict(event: ObservationEventRecord) -> dict:
    """Retain only the provider/operation labels needed for aggregate counts."""
    payload = event.payload if isinstance(event.payload, dict) else {}
    return {
        "event_name": event.event_name,
        "payload": {
            "provider": payload.get("provider"),
            "operation": payload.get("operation"),
        },
    }


def _safe_workflow_event_dict(event: dict) -> dict:
    return _select_safe_fields(event, {
        "id", "run_id", "thread_id", "step_id", "job_id", "event_type",
        "event_level", "created_at",
    })


def _safe_runtime_step_dict(step: Any) -> dict:
    return _select_safe_fields(_json_dict(step), {
        "step_id", "step_name", "phase", "status", "attempt_count",
        "max_attempts", "started_at", "completed_at", "error_code",
    })


def _safe_runtime_child_task_dict(task: Any) -> dict:
    value = _json_dict(task)
    safe = _select_safe_fields(value, {
        "child_task_id", "step_id", "task_type", "status", "attempt_count",
        "max_attempts", "started_at", "completed_at", "error_code",
    })
    recovery_count = max(int(value.get("attempt_count") or 0), 0)
    max_attempts = max(int(value.get("max_attempts") or 3), 1)
    safe["retry_counters"] = {
        "specialist_user_recovery": {
            "used": recovery_count,
            "limit": max(max_attempts - 1, 0),
        },
        "workflow_child_attempt": {
            "used": min(recovery_count + 1, max_attempts),
            "limit": max_attempts,
        },
    }
    return safe


def _llm_recovery_projection(
    *, run_status: str, current_stage: str | None, runtime_steps: list[dict], brief: ResearchBriefRecord
) -> dict:
    presearch_step = next((step for step in runtime_steps if step.get("step_name") == "presearch"), {})
    required = run_status == "waiting_user" and current_stage == "presearch"
    error_code = presearch_step.get("error_code")
    if not isinstance(error_code, str) or not error_code.startswith("llm_"):
        error_code = None
    payload = brief.payload
    return {
        "required": required,
        "error_code": error_code,
        "configuration_source": payload.get("configuration_source")
        if payload.get("configuration_source") in {"user", "system_default"} else None,
        "model": payload.get("model") if isinstance(payload.get("model"), str) else None,
    }


def _safe_provider_operation_dict(operation: dict) -> dict:
    safe = _select_safe_fields(operation, {
        "operation_id", "operation_fingerprint", "operation", "provider", "provider_operation",
        "source_kind", "result_status", "status", "started_at", "finished_at",
        "failure_code", "retryable", "candidate_dispositions",
    })
    if not safe.get("candidate_dispositions"):
        safe.pop("candidate_dispositions", None)
    retry_count = operation.get("automatic_retry_count")
    retry_limit = operation.get("automatic_retry_limit")
    if (
        isinstance(retry_count, int)
        and retry_count >= 0
        and isinstance(retry_limit, int)
        and retry_limit >= 0
    ):
        safe["retry_counters"] = {
            "provider_automatic": {"used": retry_count, "limit": retry_limit}
        }
    return safe


def _safe_candidate_dispositions(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        key: int(value[key])
        for key in ("invalid_candidate", "eligible")
        if isinstance(value.get(key), int) and value[key] >= 0
    }


def _select_safe_fields(value: dict, allowed: set[str]) -> dict:
    return {key: _json_safe(value[key]) for key in allowed if key in value}


def _usage_summary_dict(summary: LLMUsageSummary) -> dict:
    return _json_safe(asdict(summary))


def _external_api_summary(
    observation_events: list[dict], provider_operations: list[dict]
) -> dict:
    """Summarize source-adapter calls for UI observability, never for recovery."""
    started = [event for event in observation_events if event.get("event_name") == "source_collection_started"]
    completed = [event for event in observation_events if event.get("event_name") == "source_collection_completed"]
    failed = [event for event in observation_events if event.get("event_name") == "source_collection_failed"]
    by_provider: dict[str, int] = {}
    by_operation: dict[str, int] = {}
    for event in started:
        payload = dict(event.get("payload") or {})
        provider = str(payload.get("provider") or "unknown")
        operation = str(payload.get("operation") or "collect")
        by_provider[provider] = by_provider.get(provider, 0) + 1
        by_operation[operation] = by_operation.get(operation, 0) + 1
    for operation in provider_operations:
        provider = str(operation.get("provider") or "unknown")
        operation_name = str(operation.get("provider_operation") or operation.get("operation") or "collect")
        by_provider[provider] = by_provider.get(provider, 0) + 1
        by_operation[operation_name] = by_operation.get(operation_name, 0) + 1
    return {
        "call_count": len(started) + len(provider_operations),
        "completed_count": len(completed) + sum(item.get("status") == "completed" for item in provider_operations),
        "failed_count": len(failed) + sum(item.get("status") not in {"completed", "running"} for item in provider_operations),
        "by_provider": by_provider,
        "by_operation": by_operation,
    }


def _provider_operations(store: SQLiteContentResearchStore, workflow_run_id: str) -> list[dict]:
    """Return safe, durable provider-operation outcomes for Trace projection."""
    records = [
        item for item in store.list_typed_records(StageCheckpointRecord)
        if item.workflow_run_id == workflow_run_id and item.stage_name == "operation"
    ]
    latest: dict[tuple[str, str], Any] = {}
    for record in sorted(records, key=lambda item: (item.created_at, item.id)):
        fingerprint = str(record.payload.get("operation_fingerprint") or "")
        if fingerprint:
            latest[(record.subagent_task_id, fingerprint)] = record
    return [
        {
            "operation_id": "op_"
            + hashlib.sha256(f"{task_id}:{fingerprint}".encode()).hexdigest()[:24],
            "operation_fingerprint": fingerprint,
            "operation": record.payload.get("operation"),
            "provider": (record.payload.get("completion") or {}).get("provider"),
            "provider_operation": (record.payload.get("completion") or {}).get("provider_operation"),
            "source_kind": (record.payload.get("completion") or {}).get("source_kind"),
            "result_status": (record.payload.get("completion") or {}).get("result_status"),
            "item_count": (record.payload.get("completion") or {}).get("item_count"),
            "completeness": (record.payload.get("completion") or {}).get("completeness"),
            "cookie_status": (record.payload.get("completion") or {}).get("cookie_status"),
            "status": record.status,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "failure_code": (record.payload.get("completion") or {}).get("failure_code"),
            "failure_reason": (record.payload.get("completion") or {}).get("failure_reason"),
            "retryable": bool((record.payload.get("completion") or {}).get("retryable")),
            "recovery_action": (record.payload.get("completion") or {}).get("recovery_action"),
            "candidate_dispositions": _safe_candidate_dispositions(
                (record.payload.get("completion") or {}).get("candidate_dispositions")
            ),
            "automatic_retry_count": (record.payload.get("completion") or {}).get(
                "automatic_retry_count"
            ),
            "automatic_retry_limit": (record.payload.get("completion") or {}).get(
                "automatic_retry_limit"
            ),
        }
        for (task_id, fingerprint), record in sorted(latest.items())
    ]


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    return value


def _derive_current_stage(*, run: dict, steps: list[dict], traces: list[TraceRecord]) -> str | None:
    if run.get("current_step"):
        return str(run["current_step"])
    for step in steps:
        if step.get("status") == "running":
            return str(step.get("step_name") or "")
    return None


def _duration_ms(
    *,
    traces: list[TraceRecord],
    observation_events: list[ObservationEventRecord],
    workflow_events: list[dict],
    run: dict,
) -> int:
    times: list[datetime] = []
    for key in ("started_at", "created_at", "updated_at", "completed_at", "failed_at", "cancelled_at"):
        parsed = _parse_dt(run.get(key))
        if parsed:
            times.append(parsed)
    for trace in traces:
        times.extend([trace.started_at, trace.created_at, trace.updated_at])
    for event in observation_events:
        times.extend([event.timestamp, event.created_at, event.updated_at])
    for event in workflow_events:
        parsed = _parse_dt(event.get("created_at"))
        if parsed:
            times.append(parsed)
    if len(times) < 2:
        return 0
    start = min(times)
    end = max(times)
    return max(0, int((end - start).total_seconds() * 1000))


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _error_count(*, observation_events: list[dict], workflow_events: list[dict], usage_events: list[dict]) -> int:
    count = 0
    for event in observation_events:
        if _event_has_error(event):
            count += 1
    for event in workflow_events:
        if _event_has_error(event):
            count += 1
    for event in usage_events:
        if str(event.get("status") or "").lower() != "success" or event.get("error_message"):
            count += 1
    return count


def _retry_count(*, workflow_events: list[dict], usage_steps: list[dict]) -> int:
    workflow_retry_count = sum(1 for event in workflow_events if "retry" in str(event.get("event_type") or ""))
    failed_usage_calls = sum(int(step.get("failed_calls") or 0) for step in usage_steps)
    return workflow_retry_count + failed_usage_calls


def _event_has_error(event: dict) -> bool:
    event_type = str(event.get("event_type") or "").lower()
    event_level = str(event.get("event_level") or "").lower()
    status = str(event.get("status") or "").lower()
    payload = event.get("payload") or event.get("payload_json") or {}
    return (
        "failed" in event_type
        or event_level == "error"
        or status in {"failed", "error"}
        or bool(payload.get("error_code"))
        or bool(payload.get("error_message"))
    )


def _is_recoverable(run_status: str | None, brief_status: str) -> bool:
    status_values = {str(run_status or "").lower(), brief_status.lower()}
    return not bool(status_values & {"final_timeout", "failed", "cancelled"})
