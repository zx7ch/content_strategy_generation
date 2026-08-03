"""Compile selected directions into P0 subagent task specs."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.content_research.agents.base import (
    ContentResearchSubagent,
    SubagentExecutionResult,
)
from app.content_research.models import (
    ObservationEventRecord,
    SubagentTaskRecord,
    TraceRecord,
    utcnow,
)
from app.content_research.sources import SourceAdapterRegistry
from app.content_research.sources.base import (
    CollectCommentsRequest,
    CollectNoteDetailRequest,
    DiscoverCandidatesRequest,
    SourceOperationResult,
)
from app.content_research.stores.base import ContentResearchStore
from app.content_research.workflow.direction_registry import ResearchDirectionDefinition
from app.content_research.workflow.directional_pipeline import (
    DirectionalExecutionPipeline,
    OperationOutcomeUnknownError,
    QueryGroup,
)


class SubagentTaskRouter:
    def __init__(
        self,
        *,
        store: ContentResearchStore | None = None,
        source_registry: SourceAdapterRegistry | None = None,
        agents: dict[str, ContentResearchSubagent] | None = None,
    ) -> None:
        self._store = store
        self._source_registry = source_registry or SourceAdapterRegistry()
        self._agents = dict(agents or {})

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
        workspace_id: str,
        user_id: str,
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
                "llm_scope": {
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                },
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
        source_result: SourceOperationResult | None = None,
    ) -> SubagentTaskRecord:
        if self._store is None:
            raise ValueError("SubagentTaskRouter requires a store to execute tasks")
        agent_name = "DirectionalExecutionPipeline"
        trace = self._ensure_trace(task, trace_id)
        sequence_no = self._next_observation_sequence(trace.id)
        started = replace(task, status="running", updated_at=utcnow())
        self._store.save_subagent_task(started)
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
            result = await self._execute_direction_pipeline(
                task=started, provider=provider, limit=limit, source_result=source_result,
            )
        except OperationOutcomeUnknownError as exc:
            pending = self._terminal_task(
                started,
                status="outcome_unknown",
                result=SubagentExecutionResult(
                    status="outcome_unknown",
                    findings=[],
                    evidence_records=[],
                    missing_evidence=[{"reason": "collection_outcome_pending_confirmation", "operation": exc.operation}],
                    failure_reason="collection_outcome_pending_confirmation",
                    metadata={
                        "recovery_action": "confirm_collection_outcome_before_retry",
                        "operation": exc.operation,
                    },
                ),
            )
            self._append_event(
                trace,
                sequence_no,
                "task_pending_confirmation",
                "subagent_task_outcome_unknown",
                pending,
                {"error_message": str(exc), "recoverable": True, "recovery_action": "confirm_collection_outcome_before_retry"},
            )
            return pending
        except Exception as exc:
            failed = self._terminal_task(started, status="failed", result=None, error=str(exc))
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
            },
        )
        return terminal

    async def _execute_direction_pipeline(
        self, *, task: SubagentTaskRecord, provider: str, limit: int, source_result: SourceOperationResult | None,
    ) -> SubagentExecutionResult:
        input_payload = dict(task.payload.get("input_payload") or {})
        direction = dict(input_payload.get("direction") or {})
        direction_id = str(direction.get("id") or task.direction_id or "")
        if not direction_id:
            raise ValueError("direction task requires direction id")
        snapshot = self._store.get_run_policy_snapshot_for_workflow(task.workflow_run_id)
        if snapshot is None:
            raise ValueError("direction task requires run policy snapshot")
        contracts = {item.direction_id: item for item in self._store.list_direction_contracts(snapshot.id)}
        contract = contracts.get(direction_id)
        if contract is None:
            raise ValueError(f"direction contract not found: {direction_id}")
        policy = self._store.get_sample_policy(contract.sample_policy_id)
        if policy is None:
            raise ValueError(f"sample policy not found: {contract.sample_policy_id}")
        adapter = self._source_registry.get(provider)

        async def discover(group: QueryGroup) -> SourceOperationResult:
            if source_result is not None:
                result = source_result
            else:
                result = await adapter.discover_candidates(DiscoverCandidatesRequest(
                    workflow_run_id=task.workflow_run_id, query=group.query, limit=min(limit, group.candidate_limit), sort=group.sort, cursor=group.cursor,
                    context={"subagent_task_id": task.id, "direction_id": direction_id, "query_group_id": group.id},
                ))
            return SourceOperationResult(
                provider=result.provider,
                operation=result.operation,
                source_kind=result.source_kind,
                status=result.status,
                items=[{**item, "query_priority": group.priority} for item in result.items if isinstance(item, dict)],
                failure_reason=result.failure_reason,
                cookie_status=result.cookie_status,
                next_cursor=result.next_cursor,
                completeness=result.completeness,
                field_availability=result.field_availability,
                retryable=result.retryable,
                metadata=result.metadata,
            )

        async def collect_detail(candidate: dict[str, Any]) -> SourceOperationResult:
            return await adapter.collect_note_detail(CollectNoteDetailRequest(
                workflow_run_id=task.workflow_run_id,
                note_id=str(candidate.get("canonical_id") or ""), note_url=str(candidate.get("source_url") or ""),
                required_fields=contract.required_note_fields,
                context={"subagent_task_id": task.id, "direction_id": direction_id},
            ))

        async def collect_comments(candidate: dict[str, Any]) -> SourceOperationResult:
            return await adapter.collect_comments(CollectCommentsRequest(
                workflow_run_id=task.workflow_run_id,
                parent_note_id=str(candidate.get("canonical_id") or ""),
                note_url=str(candidate.get("source_url") or ""),
                limit=int(candidate.get("_collection_limit") or policy.comment_limit),
                cursor=candidate.get("_collection_cursor"),
                top_level_only=bool(candidate.get("_collection_top_level_only", policy.comment_top_level_only)),
                reply_depth_limit=int(candidate.get("_collection_reply_depth_limit", policy.comment_reply_depth_limit)),
                context={"subagent_task_id": task.id, "direction_id": direction_id, "sample_policy_id": policy.id},
            ))

        run = await (
            await DirectionalExecutionPipeline.open_async(
                self._store._db_path, workflow_run_id=task.workflow_run_id
            )
        ).execute(
            workflow_run_id=task.workflow_run_id, subagent_task_id=task.id, direction_id=direction_id,
            subject=str(input_payload.get("confirmed_subject") or ""),
            questions=[str(item) for item in direction.get("questions") or []],
            competitors=[str(item) for item in input_payload.get("competitors") or []],
            author_cap=policy.author_cap, minimum_samples=policy.minimum_samples,
            minimum_independent_authors=policy.minimum_independent_authors, detail_fetch_cap=policy.detail_fetch_cap, snapshot_id=snapshot.id,
            discover=discover, collect_detail=collect_detail,
            collect_comments=collect_comments if contract.required_comment_fields else None,
            required_comment_fields=contract.required_comment_fields,
            comment_limit=policy.comment_limit,
            comment_top_level_only=policy.comment_top_level_only,
            comment_reply_depth_limit=policy.comment_reply_depth_limit,
            comment_policy_id=policy.id,
            run_as_of_at=snapshot.run_as_of_at,
            admission_contract=contract, admission_policy=policy, policy_snapshot=snapshot,
        )
        status = (
            "failed"
            if run.blocking_failure_code
            else "completed" if run.selection.status == "complete" else "partial_completed"
        )
        return SubagentExecutionResult(
            status=status,
            findings=[], evidence_records=[],
            missing_evidence=[] if run.selection.status == "complete" else [{"reason": run.selection.status}],
            failure_reason=run.blocking_failure_code,
            metadata={
                "direction_id": direction_id,
                "packet_ids": list(run.packet_ids),
                "selection_status": run.selection.status,
                "blocking_failure_code": run.blocking_failure_code,
            },
        )

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
