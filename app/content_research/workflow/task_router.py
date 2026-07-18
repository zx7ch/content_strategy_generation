"""Compile selected directions into P0 subagent task specs."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.content_research.agents import build_default_subagent_registry
from app.content_research.agents.base import (
    ContentResearchSubagent,
    SubagentExecutionContext,
    SubagentExecutionResult,
)
from app.content_research.analysis import DirectionalAnalysisService
from app.content_research.evidence import EvidenceBundleService, EvidenceService
from app.content_research.models import (
    ObservationEventRecord,
    SubagentTaskRecord,
    TraceRecord,
    utcnow,
)
from app.content_research.runtime import CheckpointRuntime, canonical_fingerprint
from app.content_research.sources import SourceAdapterRegistry
from app.content_research.sources.base import SourceCollectionResult
from app.content_research.stores.base import ContentResearchStore
from app.content_research.workflow.direction_registry import ResearchDirectionDefinition


class SubagentTaskRouter:
    def __init__(
        self,
        *,
        store: ContentResearchStore | None = None,
        source_registry: SourceAdapterRegistry | None = None,
        evidence_service: EvidenceService | None = None,
        bundle_service: EvidenceBundleService | None = None,
        agents: dict[str, ContentResearchSubagent] | None = None,
        analysis_service: DirectionalAnalysisService | None = None,
    ) -> None:
        self._store = store
        self._source_registry = source_registry or SourceAdapterRegistry()
        self._evidence_service = evidence_service
        self._bundle_service = bundle_service
        if agents is not None:
            self._agents = agents
        elif evidence_service is not None and bundle_service is not None:
            self._agents = build_default_subagent_registry(
                evidence_service=evidence_service,
                bundle_service=bundle_service,
                analysis_service=analysis_service,
            )
        else:
            self._agents = {}

    def build_task_specs(
        self,
        *,
        workflow_run_id: str,
        brief_id: str,
        plan_id: str,
        confirmed_subject: str,
        selected_competitors: list[str],
        custom_competitors: list[str],
        custom_research_question: str,
        directions: list[ResearchDirectionDefinition],
    ) -> list[dict]:
        competitors = [*selected_competitors, *custom_competitors]
        return [
            {
                "schema_version": "content_research_subagent_task_v1",
                "workflow_run_id": workflow_run_id,
                "research_brief_id": brief_id,
                "research_plan_id": plan_id,
                "research_direction_id": direction.id,
                "agent_name": direction.agent_name,
                "agent_version": "p0_spec_v1",
                "task_type": direction.task_type,
                "input_payload": {
                    "schema_version": "content_research_subagent_input_v1",
                    "confirmed_subject": confirmed_subject,
                    "competitors": competitors,
                    "custom_research_question": custom_research_question,
                    "direction": {
                        "id": direction.id,
                        "label": direction.label,
                        "direction_type": direction.direction_type,
                        "questions": direction.default_questions,
                        "source_scope": direction.source_scope,
                    },
                },
                "expected_output_schema": {
                    "schema_version": "content_research_subagent_output_schema_v1",
                    "required": ["finding", "evidence_refs", "missing_evidence"],
                },
                "status": "queued",
            }
            for direction in directions
        ]

    async def execute_task(
        self,
        task: SubagentTaskRecord,
        *,
        trace_id: str | None = None,
        provider: str = "xiaohongshu",
        source_kind: str = "search_result",
        limit: int = 10,
        source_result: SourceCollectionResult | None = None,
    ) -> SubagentTaskRecord:
        if self._store is None:
            raise ValueError("SubagentTaskRouter requires a store to execute tasks")
        agent_name = str(task.payload.get("agent_name") or task.payload.get("input_payload", {}).get("agent_name") or "")
        if not agent_name:
            agent_name = str(task.payload.get("agent_type") or "")
        agent = self._agents.get(agent_name)
        if agent is None:
            raise ValueError(f"Unsupported content research subagent: {agent_name}")
        db_path = getattr(self._store, "_db_path", None)
        checkpoint_runtime = CheckpointRuntime(db_path) if db_path else None
        collect_fingerprint = canonical_fingerprint({
            "task_id": task.id, "stage": "collect", "input": task.payload.get("input_payload", {}),
        })
        if checkpoint_runtime is not None and checkpoint_runtime.is_completed(
            subagent_task_id=task.id, stage_name="collect", input_fingerprint=collect_fingerprint,
        ):
            return self._store.get_subagent_task(task.id) or task

        trace = self._ensure_trace(task, trace_id)
        sequence_no = self._next_observation_sequence(trace.id)
        started = replace(task, status="running", updated_at=utcnow())
        self._store.save_subagent_task(started)
        if checkpoint_runtime is not None:
            checkpoint_runtime.checkpoint(
                subagent_task_id=task.id, stage_name="collect", input_fingerprint=collect_fingerprint, status="running",
            )
        self._append_event(trace, sequence_no, "task_started", "subagent_task_started", started, {"agent_name": agent_name})
        sequence_no += 1
        self._append_event(
            trace,
            sequence_no,
            "heartbeat",
            "subagent_stage_changed",
            started,
            {"current_stage": "collect_sources", "progress_percent": 20, "agent_name": agent_name},
        )
        sequence_no += 1

        try:
            result = await agent.execute(
                SubagentExecutionContext(
                    task=started,
                    source_registry=self._source_registry,
                    provider=provider,
                    source_kind=source_kind,
                    query=self._query_for_task(started),
                    limit=limit,
                    source_result=source_result,
                )
            )
        except Exception as exc:
            failed = self._terminal_task(started, status="failed", result=None, error=str(exc))
            if checkpoint_runtime is not None:
                checkpoint_runtime.checkpoint(
                    subagent_task_id=task.id, stage_name="collect", input_fingerprint=collect_fingerprint,
                    status="failed_recoverable", failure={"code": "agent_execution_failed", "message": str(exc), "recoverable": True},
                    retry_count=1,
                )
            self._append_event(
                trace,
                sequence_no,
                "task_failed",
                "subagent_task_failed",
                failed,
                {"error_message": str(exc), "recoverable": False},
            )
            return failed

        sequence_no = self._append_result_events(trace, sequence_no, started, result)
        terminal = self._terminal_task(started, status=result.status, result=result)
        if checkpoint_runtime is not None and result.status in {"completed", "partial_completed"}:
            refs = (result.evidence_bundle.id,) if result.evidence_bundle else ()
            checkpoint_runtime.checkpoint(
                subagent_task_id=task.id, stage_name="collect", input_fingerprint=collect_fingerprint,
                status="completed", output_refs=refs,
            )
        terminal_event = "subagent_task_completed" if result.status in {"completed", "partial_completed"} else "subagent_task_failed"
        self._append_event(
            trace,
            sequence_no,
            "task_completed" if result.status in {"completed", "partial_completed"} else "task_failed",
            terminal_event,
            terminal,
            {
                "status": result.status,
                "evidence_count": len(result.evidence_records),
                "finding_count": len(result.findings),
                "missing_evidence_count": len(result.missing_evidence),
                "evidence_bundle_id": result.evidence_bundle.id if result.evidence_bundle else None,
            },
        )
        return terminal

    def _ensure_trace(self, task: SubagentTaskRecord, trace_id: str | None) -> TraceRecord:
        assert self._store is not None
        if trace_id:
            existing = self._store.get_trace(trace_id)
            if existing is not None:
                return existing
        trace = TraceRecord(
            id=trace_id or f"trc_{task.id}",
            workflow_run_id=task.workflow_run_id,
            thread_id=task.thread_id,
            schema_version="content_research_trace_v1",
            status="running",
            started_at=utcnow(),
            payload={
                "schema_version": "content_research_trace_payload_v1",
                "trace_type": "subagent_task",
                "subagent_task_id": task.id,
            },
        )
        self._store.save_trace(trace)
        return trace

    def _next_observation_sequence(self, trace_id: str) -> int:
        assert self._store is not None
        events = self._store.list_observation_events(trace_id)
        if not events:
            return 1
        return max(event.sequence_no for event in events) + 1

    def _append_event(
        self,
        trace: TraceRecord,
        sequence_no: int,
        event_type: str,
        event_name: str,
        task: SubagentTaskRecord,
        payload: dict[str, Any],
    ) -> None:
        assert self._store is not None
        now = utcnow()
        self._store.append_observation_event(
            ObservationEventRecord(
                id=f"obs_{trace.id}_{sequence_no}",
                trace_id=trace.id,
                workflow_run_id=task.workflow_run_id,
                thread_id=task.thread_id,
                schema_version="content_research_observation_event_v1",
                status="recorded",
                sequence_no=sequence_no,
                event_type=event_type,
                event_name=event_name,
                timestamp=now,
                payload={
                    "schema_version": "content_research_observation_event_v1",
                    "subagent_task_id": task.id,
                    **payload,
                },
            )
        )

    def _append_result_events(
        self,
        trace: TraceRecord,
        sequence_no: int,
        task: SubagentTaskRecord,
        result: SubagentExecutionResult,
    ) -> int:
        if result.evidence_records:
            self._append_event(
                trace,
                sequence_no,
                "task_progress",
                "evidence_ingested",
                task,
                {"evidence_ids": [record.id for record in result.evidence_records]},
            )
            sequence_no += 1
        if result.findings:
            self._append_event(
                trace,
                sequence_no,
                "task_progress",
                "finding_extracted",
                task,
                {"finding_ids": [finding.finding_id for finding in result.findings]},
            )
            sequence_no += 1
        if result.missing_evidence and not result.evidence_records:
            self._append_event(
                trace,
                sequence_no,
                "task_progress",
                "subagent_stage_changed",
                task,
                {"current_stage": "summarize", "missing_evidence": result.missing_evidence},
            )
            sequence_no += 1
        return sequence_no

    def _terminal_task(
        self,
        task: SubagentTaskRecord,
        *,
        status: str,
        result: SubagentExecutionResult | None,
        error: str | None = None,
    ) -> SubagentTaskRecord:
        assert self._store is not None
        output_payload: dict[str, Any]
        if result is None:
            output_payload = {
                "schema_version": "content_research_subagent_output_v1",
                "findings": [],
                "evidence_refs": [],
                "missing_evidence": [],
                "error_message": error,
            }
        else:
            output_payload = {
                "schema_version": "content_research_subagent_output_v1",
                "findings": [finding.payload for finding in result.findings],
                "evidence_refs": [record.id for record in result.evidence_records],
                "missing_evidence": result.missing_evidence,
                "evidence_bundle_id": result.evidence_bundle.id if result.evidence_bundle else None,
                "failure_reason": result.failure_reason,
                "metadata": result.metadata,
            }
        updated_payload = {
            **task.payload,
            "status": status,
            "output_payload": output_payload,
        }
        terminal = replace(task, status=status, payload=updated_payload, updated_at=utcnow())
        self._store.save_subagent_task(terminal)
        return terminal

    @staticmethod
    def _query_for_task(task: SubagentTaskRecord) -> str:
        input_payload = dict(task.payload.get("input_payload") or {})
        direction = dict(input_payload.get("direction") or {})
        subject = str(input_payload.get("confirmed_subject") or "").strip()
        question = str(input_payload.get("custom_research_question") or "").strip()
        label = str(direction.get("label") or "").strip()
        questions = [str(item).strip() for item in direction.get("questions") or [] if str(item).strip()]
        competitors = [str(item).strip() for item in input_payload.get("competitors") or [] if str(item).strip()]
        direction_focus = " ".join(questions[:2]) or label
        competitor_focus = " ".join(competitors[:2]) if str(direction.get("id") or "") == "competitor_discovery" else ""
        return " ".join(item for item in [subject, question, label, direction_focus, competitor_focus] if item).strip()
