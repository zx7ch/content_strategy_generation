"""Append-only human decision handling for Content Research workflows."""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from app.content_research.api_schemas import (
    HumanDecisionRequest,
    HumanDecisionResponse,
    HumanDecisionsResponse,
)
from app.content_research.models import (
    HumanDecisionRecord,
    ObservationEventRecord,
    ResearchBriefRecord,
    utcnow,
)
from app.content_research.stores.base import ContentResearchStore

DECISION_STATUSES = {"selected", "watchlist", "rejected"}
TARGET_TYPES = {"brand_candidate", "recommended_content"}


class DecisionWorkflowRuntime(Protocol):
    async def append_event(
        self,
        *,
        workflow_run_id: str,
        thread_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None: ...


class ResearchDecisionService:
    def __init__(self, *, store: ContentResearchStore, workflow_runtime: DecisionWorkflowRuntime) -> None:
        self._store = store
        self._workflow_runtime = workflow_runtime

    async def submit_decision(
        self,
        *,
        brief: ResearchBriefRecord,
        target_type: str,
        request: HumanDecisionRequest,
        user_id: str,
    ) -> HumanDecisionResponse:
        self._validate(target_type=target_type, request=request)
        existing = self._store.get_human_decision_by_request(
            workflow_run_id=brief.workflow_run_id,
            target_type=target_type,
            target_id=request.target_id,
            decision_request_id=request.decision_request_id,
        )
        if existing is not None:
            return self._response(existing, idempotent_replay=True)

        traces = self._store.list_traces_for_workflow(brief.workflow_run_id)
        if not traces:
            raise ValueError(f"Content research trace not found for workflow: {brief.workflow_run_id}")
        trace = traces[0]
        plans = self._store.list_plans_for_brief(brief.id)
        plan = plans[-1] if plans else None
        created_at = utcnow()
        decision = HumanDecisionRecord(
            id=_new_id("hd"),
            workflow_run_id=brief.workflow_run_id,
            thread_id=brief.thread_id,
            schema_version="content_research_human_decision_v1",
            target_type=target_type,
            target_id=request.target_id.strip(),
            decision_request_id=request.decision_request_id.strip(),
            decision_status=request.decision_status.strip(),
            decision_payload={
                "schema_version": "content_research_human_decision_payload_v1",
                **request.decision_payload,
            },
            rationale=request.rationale.strip(),
            created_by_type=request.created_by_type.strip() or "user",
            created_by_id=request.created_by_id or user_id,
            research_brief_id=brief.id,
            research_plan_id=plan.id if plan else None,
            research_result_snapshot_id=request.research_result_snapshot_id,
            metadata=request.metadata,
            created_at=created_at,
        )
        saved = self._store.append_human_decision(decision)
        observation = self._append_observation_event(saved, trace_id=trace.id)
        await self._workflow_runtime.append_event(
            workflow_run_id=brief.workflow_run_id,
            thread_id=brief.thread_id,
            event_type="human_decision_submitted",
            payload={
                "schema_version": "content_research_workflow_event_payload_v1",
                "decision_id": saved.id,
                "target_type": saved.target_type,
                "target_id": saved.target_id,
                "decision_status": saved.decision_status,
                "decision_request_id": saved.decision_request_id,
                "observation_event_id": observation.id,
                "advancement": _advancement(saved.target_type, saved.decision_status),
            },
        )
        return self._response(saved)

    def list_decisions(self, workflow_run_id: str) -> HumanDecisionsResponse:
        decisions = self._store.list_human_decisions_for_workflow(workflow_run_id)
        current_keys = {
            (decision.target_type, decision.target_id): decision.id
            for decision in self._store.list_current_human_decisions_for_workflow(workflow_run_id)
        }
        responses = [
            self._response(decision, is_current=current_keys.get((decision.target_type, decision.target_id)) == decision.id)
            for decision in decisions
        ]
        return HumanDecisionsResponse(
            workflow_run_id=workflow_run_id,
            decisions=responses,
            current_decisions=[response for response in responses if response.is_current],
        )

    def _append_observation_event(self, decision: HumanDecisionRecord, *, trace_id: str) -> ObservationEventRecord:
        sequence_no = self._next_observation_sequence(trace_id)
        return self._store.append_observation_event(
            ObservationEventRecord(
                id=_new_id("obs"),
                trace_id=trace_id,
                workflow_run_id=decision.workflow_run_id,
                thread_id=decision.thread_id,
                schema_version="content_research_observation_event_v1",
                status="completed",
                sequence_no=sequence_no,
                event_type="human_decision",
                event_name="human_decision_submitted",
                timestamp=decision.created_at,
                payload={
                    "schema_version": "content_research_observation_event_v1",
                    "decision_id": decision.id,
                    "target_type": decision.target_type,
                    "target_id": decision.target_id,
                    "decision_status": decision.decision_status,
                    "decision_request_id": decision.decision_request_id,
                    "advancement": _advancement(decision.target_type, decision.decision_status),
                },
            )
        )

    def _next_observation_sequence(self, trace_id: str) -> int:
        events = self._store.list_observation_events(trace_id)
        return (events[-1].sequence_no + 1) if events else 1

    def _response(
        self,
        decision: HumanDecisionRecord,
        *,
        idempotent_replay: bool = False,
        is_current: bool | None = None,
    ) -> HumanDecisionResponse:
        history = [
            item
            for item in self._store.list_human_decisions_for_workflow(decision.workflow_run_id)
            if item.target_type == decision.target_type and item.target_id == decision.target_id
        ]
        if is_current is None:
            is_current = bool(history and history[-1].id == decision.id)
        return HumanDecisionResponse(
            decision_id=decision.id,
            workflow_run_id=decision.workflow_run_id,
            target_type=decision.target_type,
            target_id=decision.target_id,
            decision_request_id=decision.decision_request_id,
            decision_status=decision.decision_status,
            decision_payload=decision.decision_payload,
            rationale=decision.rationale,
            created_by_type=decision.created_by_type,
            created_by_id=decision.created_by_id,
            research_brief_id=decision.research_brief_id,
            research_plan_id=decision.research_plan_id,
            research_result_snapshot_id=decision.research_result_snapshot_id,
            metadata=decision.metadata,
            advancement=_advancement(decision.target_type, decision.decision_status),
            is_current=is_current,
            idempotent_replay=idempotent_replay,
            history_count=len(history),
            created_at=decision.created_at.isoformat(),
        )

    @staticmethod
    def _validate(*, target_type: str, request: HumanDecisionRequest) -> None:
        if target_type not in TARGET_TYPES:
            raise ValueError(f"Unsupported decision target type: {target_type}")
        if not request.target_id.strip():
            raise ValueError("target_id is required")
        if not request.decision_request_id.strip():
            raise ValueError("decision_request_id is required")
        if request.decision_status.strip() not in DECISION_STATUSES:
            raise ValueError(f"Unsupported decision_status: {request.decision_status}")


def _advancement(target_type: str, decision_status: str) -> dict[str, str]:
    if target_type == "brand_candidate":
        return {
            "selected": {
                "next_step": "brand_content_deep_research",
                "resource_policy": "full_deep_research",
            },
            "watchlist": {
                "next_step": "observation_pool",
                "resource_policy": "lightweight_or_deferred",
            },
            "rejected": {
                "next_step": "no_advance",
                "resource_policy": "none",
            },
        }[decision_status]
    return {
        "selected": {
            "next_step": "final_content_pool",
            "resource_policy": "include_in_final_focus",
        },
        "watchlist": {
            "next_step": "content_observation_pool",
            "resource_policy": "deferred",
        },
        "rejected": {
            "next_step": "exclude_from_final_focus",
            "resource_policy": "none",
        },
    }[decision_status]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
