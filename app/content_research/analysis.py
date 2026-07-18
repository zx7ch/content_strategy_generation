"""Bounded LLM analysis for one directional research task."""

from __future__ import annotations

import json
from typing import Any, Protocol

from app.content_research.runtime import LLMCostLedger
from app.services.llm.pricing import PricingCalculator, UsageCost
from app.services.llm.types import LLMCallContext, LLMRequest, LLMResponse, Message, TokenUsage
from app.services.llm.usage_tracker import LLMUsageEventInput, LLMUsageTracker


class DirectionalAnalysisLLM(Protocol):
    async def generate(self, request: LLMRequest) -> LLMResponse: ...


class DirectionalAnalysisService:
    def __init__(self, *, llm: DirectionalAnalysisLLM, db_path: str) -> None:
        self._llm = llm
        self._db_path = db_path

    async def analyze(self, *, task, direction: dict[str, Any], query: str, facts: list[dict[str, Any]]) -> dict[str, Any] | None:
        evidence_ids = {str(fact["evidence_id"]) for fact in facts if fact.get("evidence_id")}
        evidence = [
            {"evidence_id": fact.get("evidence_id"), "claim": fact.get("claim"), "metrics": fact.get("metrics", {})}
            for fact in facts[:30]
        ]
        request = LLMRequest(
            messages=[
                Message(role="system", content=(
                    "You are a research analyst. Return JSON only: summary, observations (array of short strings), "
                    "evidence_refs (array of supplied evidence_id), missing_evidence (array of {reason,message}). "
                    "Do not claim causality or facts not in the supplied evidence."
                )),
                Message(role="user", content=json.dumps({
                    "direction": direction, "query": query, "evidence": evidence,
                }, ensure_ascii=False)),
            ],
            task_type="content_research.directional_analysis",
            model_policy="quality",
            temperature=0.2,
            max_tokens=900,
            context=LLMCallContext(
                session_id=task.thread_id,
                job_id=task.workflow_run_id,
                step_name="formal_research",
                agent_name=str(task.payload.get("agent_name") or "directional_research"),
            ),
        )
        try:
            response = await self._llm.generate(request)
            await self._record(task, request.context, response.provider, response.model, response.usage, response.latency_ms, "success", None)
            payload = json.loads(response.content)
            refs = [str(item) for item in payload.get("evidence_refs") or []]
            if not refs or not set(refs).issubset(evidence_ids):
                return None
            return {
                "summary": str(payload.get("summary") or "").strip(),
                "observations": [str(item) for item in payload.get("observations") or [] if str(item).strip()],
                "evidence_refs": refs,
                "missing_evidence": list(payload.get("missing_evidence") or []),
            }
        except Exception as exc:  # analysis failure must remain an explicit evidence boundary.
            await self._record(task, request.context, "unknown", "unknown", TokenUsage(), None, "failed", str(exc))
            return None

    async def _record(self, task, context, provider, model, usage, latency_ms, status, error_message) -> None:
        cost = PricingCalculator().calculate(
            provider=provider, model=model,
            prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens,
        ) if status == "success" else UsageCost()
        async with LLMUsageTracker(self._db_path) as tracker:
            usage_event_id = await tracker.record(LLMUsageEventInput(
                context=context, provider=provider, model=model, model_policy="quality",
                usage=usage, cost=cost, latency_ms=latency_ms, status=status, error_message=error_message,
            ))
        if not task.plan_id:
            return
        ledger = LLMCostLedger(self._db_path)
        if status == "success":
            ledger.record_actual(
                research_plan_id=task.plan_id, research_direction_id=task.direction_id,
                usage_event_id=usage_event_id, amount=cost.total_cost,
            )
        else:
            ledger.record_unknown(
                research_plan_id=task.plan_id, research_direction_id=task.direction_id,
                usage_event_id=usage_event_id, reason=error_message or "provider usage unavailable",
            )
