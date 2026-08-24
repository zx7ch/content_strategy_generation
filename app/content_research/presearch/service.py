"""Lightweight presearch execution for Content Research."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Protocol

from app.content_research.presearch.prompts import (
    build_presearch_messages,
    build_presearch_repair_messages,
)
from app.content_research.subject_structure import (
    SubjectStructure,
    parse_subject_structure,
    subject_structure_fingerprint,
)
from app.services.llm.failures import LLMProviderFailure, classify_provider_exception
from app.services.llm.types import LLMCallContext, LLMRequest, LLMResponse


class PresearchLLM(Protocol):
    async def generate(self, request: LLMRequest) -> LLMResponse: ...


@dataclass(frozen=True)
class PresearchInput:
    seed_text: str
    user_note: str | None
    thread_id: str
    workflow_run_id: str
    user_id: str
    workspace_id: str = "default"


@dataclass(frozen=True)
class PresearchChecklist:
    subject_confirmation: str
    competitor_tags: list[str]
    research_directions: list[str]
    custom_competitor_input: str = ""
    subject_structure: SubjectStructure | None = None
    subject_structure_analysis_state: str = "unresolved"
    subject_structure_analysis_reason_codes: tuple[str, ...] = ()
    subject_structure_hash: str | None = None


@dataclass(frozen=True)
class PresearchOutcome:
    status: str
    checklist: PresearchChecklist
    timeout_status: str = "none"
    fallback_used: bool = False
    error_code: str | None = None
    error_message: str | None = None
    recoverable: bool = False
    provider: str | None = None
    model: str | None = None
    configuration_source: str | None = None


class PresearchService:
    def __init__(
        self,
        llm: PresearchLLM | None = None,
        *,
        first_feedback_timeout_seconds: float = 10.0,
        hard_cutoff_seconds: float = 20.0,
    ) -> None:
        self._llm = llm
        self.first_feedback_timeout_seconds = first_feedback_timeout_seconds
        self.hard_cutoff_seconds = hard_cutoff_seconds

    async def create_llm_task(self, request: PresearchInput) -> asyncio.Task[PresearchOutcome] | None:
        if self._llm is None:
            return None
        return asyncio.create_task(self._run_llm(request))

    async def wait_for_first_feedback(
        self,
        *,
        request: PresearchInput,
        task: asyncio.Task[PresearchOutcome] | None,
    ) -> PresearchOutcome:
        if task is None:
            return self.fallback(request, error_code="LLM_UNAVAILABLE")
        try:
            return await asyncio.wait_for(
                asyncio.shield(task),
                timeout=self.first_feedback_timeout_seconds,
            )
        except asyncio.TimeoutError:
            return self.fallback(request, timeout_status="first_timeout", error_code="PRESEARCH_FIRST_TIMEOUT")

    async def wait_for_hard_cutoff(
        self,
        *,
        request: PresearchInput,
        task: asyncio.Task[PresearchOutcome],
    ) -> PresearchOutcome | None:
        remaining = max(0.0, self.hard_cutoff_seconds - self.first_feedback_timeout_seconds)
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
        except asyncio.TimeoutError:
            task.cancel()
            return self.fallback(request, timeout_status="final_timeout", error_code="PRESEARCH_FINAL_TIMEOUT")
        except asyncio.CancelledError:
            return self.fallback(request, timeout_status="final_timeout", error_code="PRESEARCH_CANCELLED")

    def fallback(
        self,
        request: PresearchInput,
        *,
        timeout_status: str = "none",
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> PresearchOutcome:
        from app.content_research.presearch.fallback_templates import build_fallback_checklist

        return PresearchOutcome(
            status="fallback" if timeout_status != "final_timeout" else "final_timeout",
            checklist=build_fallback_checklist(request.seed_text, request.user_note),
            timeout_status=timeout_status,
            fallback_used=True,
            error_code=error_code,
            error_message=error_message,
        )

    async def _run_llm(self, request: PresearchInput) -> PresearchOutcome:
        assert self._llm is not None
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        try:
            # Provider SDK defaults can wait for minutes; presearch is an
            # interactive UI boundary and must fall back within its hard cutoff.
            response = await asyncio.wait_for(self._llm.generate(
                LLMRequest(
                    messages=build_presearch_messages(request.seed_text, request.user_note),
                    task_type="content_research.presearch",
                    model_policy="balanced",
                    temperature=1.0,
                    max_tokens=700,
                    context=LLMCallContext(
                        session_id=request.thread_id,
                        step_name="presearch",
                        agent_name="content_research_presearch",
                        tenant_id=request.workspace_id,
                        user_id=request.user_id,
                    ),
                )
            ), timeout=self.hard_cutoff_seconds)
            try:
                checklist = self._parse_checklist(
                    response.content, request.seed_text, request.user_note
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                from app.content_research.presearch.fallback_templates import (
                    build_fallback_checklist,
                )
                return PresearchOutcome(
                    status="waiting_model_config",
                    checklist=build_fallback_checklist(request.seed_text, request.user_note),
                    error_code="llm_structured_output_invalid",
                    error_message="模型服务返回格式不兼容",
                    recoverable=True,
                    provider=response.provider,
                    model=response.model,
                    configuration_source=response.configuration_source,
                )
            if checklist.subject_structure_analysis_state != "confirmed":
                try:
                    remaining_seconds = max(
                        0.001,
                        self.hard_cutoff_seconds - (loop.time() - started_at),
                    )
                    repaired_response = await asyncio.wait_for(
                        self._llm.generate(
                            LLMRequest(
                                messages=build_presearch_repair_messages(
                                    request.seed_text,
                                    request.user_note,
                                    invalid_response=response.content,
                                    reason_codes=(
                                        checklist.subject_structure_analysis_reason_codes
                                    ),
                                ),
                                task_type="content_research.presearch.repair",
                                model_policy="balanced",
                                temperature=0.2,
                                max_tokens=700,
                                context=LLMCallContext(
                                    session_id=request.thread_id,
                                    step_name="presearch_structure_repair",
                                    agent_name="content_research_presearch",
                                    tenant_id=request.workspace_id,
                                    user_id=request.user_id,
                                ),
                            )
                        ),
                        timeout=remaining_seconds,
                    )
                    repaired_checklist = self._parse_checklist(
                        repaired_response.content,
                        request.seed_text,
                        request.user_note,
                    )
                    if (
                        repaired_checklist.subject_structure_analysis_state
                        == "confirmed"
                    ):
                        checklist = repaired_checklist
                        response = repaired_response
                except Exception:  # noqa: BLE001
                    # Repair is best-effort and never creates another lifecycle
                    # stage. The original diagnosis remains visible for manual edit.
                    pass
            return PresearchOutcome(
                status="completed",
                checklist=checklist,
                fallback_used=False,
                provider=response.provider,
                model=response.model,
                configuration_source=response.configuration_source,
            )
        except asyncio.TimeoutError:
            # The caller owns the final-timeout state transition.
            raise
        except LLMProviderFailure as exc:
            from app.content_research.presearch.fallback_templates import build_fallback_checklist
            return PresearchOutcome(
                status="waiting_model_config",
                checklist=build_fallback_checklist(request.seed_text, request.user_note),
                fallback_used=False, error_code=exc.code, error_message=exc.public_message,
                recoverable=exc.recoverable, provider=exc.provider, model=exc.model,
                configuration_source=exc.configuration_source,
            )
        except Exception as exc:  # noqa: BLE001 - normalize the model boundary to a safe wait.
            failure = classify_provider_exception(exc)
            from app.content_research.presearch.fallback_templates import build_fallback_checklist
            return PresearchOutcome(
                status="waiting_model_config",
                checklist=build_fallback_checklist(request.seed_text, request.user_note),
                fallback_used=False,
                error_code=failure.code,
                error_message=failure.public_message,
                recoverable=failure.recoverable,
                provider=failure.provider,
                model=failure.model,
                configuration_source=failure.configuration_source,
            )

    def _parse_checklist(
        self, content: str, seed_text: str, user_note: str | None
    ) -> PresearchChecklist:
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("presearch response must be a JSON object")
        subject = str(data.get("subject_confirmation") or "").strip()
        if not subject:
            raise ValueError("presearch response missing subject_confirmation")
        structure_data = data.get("subject_structure")
        if not isinstance(structure_data, dict):
            raise ValueError("presearch response missing subject_structure")
        structure_decision = parse_subject_structure(
            structure_data,
            normalized_input=seed_text.strip(),
            require_term_mapping=True,
        )
        structure = structure_decision.structure
        return PresearchChecklist(
            subject_confirmation=subject,
            competitor_tags=self._string_list(data.get("competitor_tags")),
            research_directions=self._string_list(data.get("research_directions")),
            custom_competitor_input=str(data.get("custom_competitor_input") or ""),
            subject_structure=structure,
            subject_structure_analysis_state=structure_decision.state,
            subject_structure_analysis_reason_codes=structure_decision.reason_codes,
            subject_structure_hash=(
                subject_structure_fingerprint(structure) if structure else None
            ),
        )

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]
