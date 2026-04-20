from __future__ import annotations

import asyncio
import difflib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional

from pydantic import BaseModel

from app.config import settings
from app.llm.client import LLMClient
from app.logging_config import get_logger, log_event
from app.memory.session_state import SessionManager
from app.models.schemas import ContentGeneratorRequest
from app.models.session import ContentStrategy, GeneratedNote, PlatformPreference, Proposal, SessionError, SessionStage
from app.prompts.generation import (
    build_language_instruction,
    get_temperature_hint,
    render_note_generation_prompts,
    render_proposal_generation_prompts,
)
from app.services.rag_service import RAGService, SimilarPost
from app.services.engagement_analyzer import EngagementAnalyzer


class ContentGenerationResult(BaseModel):
    title: str
    content: str
    tags: List[str]
    cover_design_prompt: Optional[str] = None
    designed_update_time: Optional[str] = None


class ContentGenerationError(RuntimeError):
    """Structured generation error for proposal parsing and validation."""


class BudgetExceededError(ContentGenerationError):
    """Raised when generation exceeds the session token budget."""


@dataclass(slots=True)
class SimilarityCheckResult:
    embedding_similarity: float
    lexical_overlap: float
    should_retry: bool
    status: str


class ProposalPool:
    """Concurrency-safe proposal pool with high-risk eviction."""

    def __init__(self, proposals: list[Proposal], *, slot_limit: Optional[int] = None) -> None:
        limit = slot_limit or settings.GENERATION_PARALLEL_SLOTS
        self._available = list(proposals[:limit])
        self._remaining = list(proposals[limit:])
        self._used: set[str] = set()
        self._high_risk: set[str] = set()
        self._lock = asyncio.Lock()

    async def select_proposal(self, slot_id: int) -> Optional[Proposal]:
        del slot_id
        async with self._lock:
            for proposal in self._available:
                if proposal.proposal_id in self._used or proposal.proposal_id in self._high_risk:
                    continue
                self._used.add(proposal.proposal_id)
                proposal.is_used = True
                return proposal

            while self._remaining:
                proposal = self._remaining.pop(0)
                if proposal.proposal_id in self._high_risk:
                    continue
                self._used.add(proposal.proposal_id)
                proposal.is_used = True
                return proposal
            return None

    async def mark_high_risk(self, proposal: Proposal) -> None:
        async with self._lock:
            self._high_risk.add(proposal.proposal_id)
            proposal.is_high_risk = True


@dataclass(slots=True)
class GenerationExecutionResult:
    success: bool
    status: str
    notes: list[GeneratedNote]
    similarity_report: dict[str, Any]
    message: str
    error_code: Optional[str] = None


class SessionTokenBudget:
    def __init__(self, session_budget: int):
        self.session_budget = session_budget
        self.used_tokens = 0
        self.usage_estimated = False
        self._lock = asyncio.Lock()

    async def consume(self, *texts: str) -> int:
        estimate = max(1, sum(max(1, len(text or "")) for text in texts) // 4)
        async with self._lock:
            self.used_tokens += estimate
            self.usage_estimated = True
            if self.used_tokens > self.session_budget:
                raise BudgetExceededError("SESSION_TOKEN_BUDGET exceeded")
            return self.used_tokens

    @property
    def remaining(self) -> int:
        return max(0, self.session_budget - self.used_tokens)


class ContentGenerationAgent:
    def __init__(
        self,
        *,
        llm_client: Optional[LLMClient] = None,
        engagement_analyzer: Optional[EngagementAnalyzer] = None,
        rag_service: Optional[RAGService] = None,
        session_manager: Optional[SessionManager] = None,
    ) -> None:
        self.llm = llm_client or LLMClient()
        self.analyzer = engagement_analyzer or EngagementAnalyzer()
        self.rag = rag_service
        self.session_manager = session_manager or SessionManager()
        self._logger = get_logger(__name__, component="generation")

    @staticmethod
    def resolve_language_instruction(request: ContentGeneratorRequest) -> str:
        return build_language_instruction(request.output_language)

    async def generate_proposals(
        self,
        *,
        content_strategy: ContentStrategy | dict[str, Any] | str,
        target_audience: str,
        output_language: str = "zh-CN",
        n: Optional[int] = None,
        budget: Optional[SessionTokenBudget] = None,
    ) -> list[Proposal]:
        proposal_count = n or settings.NUM_PROPOSALS
        system_prompt, user_prompt = render_proposal_generation_prompts(
            content_strategy=self._stringify_content_strategy(content_strategy),
            target_audience=target_audience,
            n=proposal_count,
            language_instruction=build_language_instruction(output_language),
        )
        response = await self.llm.chat(
            system=system_prompt,
            user=user_prompt,
            max_tokens=3000,
            temperature=0.7,
        )
        if budget is not None:
            await budget.consume(system_prompt, user_prompt, response)

        try:
            raw_items = json.loads(response)
        except json.JSONDecodeError as exc:
            raise ContentGenerationError("Proposal response is not valid JSON.") from exc

        if not isinstance(raw_items, list):
            raise ContentGenerationError("Proposal response must be a JSON array.")
        if len(raw_items) != proposal_count:
            raise ContentGenerationError(
                f"Expected {proposal_count} proposals, got {len(raw_items)}."
            )

        pillars = self._extract_content_pillars(content_strategy)
        proposals: list[Proposal] = []
        for index, item in enumerate(raw_items, start=1):
            if not isinstance(item, dict):
                raise ContentGenerationError("Each proposal item must be a JSON object.")
            proposals.append(self._normalize_proposal_item(item, index=index, content_pillars=pillars))
        return proposals

    def score_proposals(
        self,
        proposals: list[Proposal],
        preferences: PlatformPreference,
    ) -> list[Proposal]:
        return self.analyzer.score_proposals(proposals, preferences)

    def select_top_k(
        self,
        proposals: list[Proposal],
        k: Optional[int] = None,
    ) -> list[Proposal]:
        limit = k or settings.NUM_FINAL_NOTES
        ranked = sorted(proposals, key=lambda proposal: proposal.score, reverse=True)
        return ranked[:limit]

    def temperature_to_hint(self, temperature: float) -> str:
        return get_temperature_hint(temperature)

    async def _generate_single(
        self,
        *,
        slot_id: int,
        proposal: Proposal,
        content_strategy: ContentStrategy | dict[str, Any] | str,
        target_audience: str,
        temperature: float,
        output_language: str = "zh-CN",
        semaphore: Optional[asyncio.Semaphore] = None,
        budget: Optional[SessionTokenBudget] = None,
    ) -> GeneratedNote:
        system_prompt, user_prompt = render_note_generation_prompts(
            content_strategy=self._stringify_content_strategy(content_strategy),
            proposal=json.dumps(proposal.model_dump(), ensure_ascii=False),
            target_audience=target_audience,
            temperature=temperature,
            target_emotion=proposal.target_emotion,
            angle=proposal.angle,
            title_concept=proposal.hook,
            content_outline=proposal.outline,
            language_instruction=build_language_instruction(output_language),
        )

        if semaphore is None:
            response = await self.llm.chat(
                system=system_prompt,
                user=user_prompt,
                max_tokens=3000,
                temperature=temperature,
            )
            if budget is not None:
                await budget.consume(system_prompt, user_prompt, response)
        else:
            async with semaphore:
                response = await self.llm.chat(
                    system=system_prompt,
                    user=user_prompt,
                    max_tokens=3000,
                    temperature=temperature,
                )
                if budget is not None:
                    await budget.consume(system_prompt, user_prompt, response)

        try:
            raw = json.loads(response)
        except json.JSONDecodeError as exc:
            raise ContentGenerationError("Generated note response is not valid JSON.") from exc

        if not isinstance(raw, dict):
            raise ContentGenerationError("Generated note response must be a JSON object.")

        title = str(raw.get("title") or "").strip()
        content = str(raw.get("content") or "").strip()
        cover_design_prompt = str(raw.get("cover_design_prompt") or "").strip()
        suggested_update_time = str(raw.get("suggested_update_time") or "").strip()
        tags = raw.get("tags") or []
        if not isinstance(tags, list):
            raise ContentGenerationError("Generated note tags must be a list.")
        tags = [str(tag) for tag in tags]

        if not title or not content or not cover_design_prompt or not suggested_update_time:
            raise ContentGenerationError("Generated note is missing required fields.")

        return GeneratedNote(
            note_id=f"note_{uuid.uuid4().hex[:12]}",
            title=title,
            content=content,
            tags=tags,
            cover_design_prompt=cover_design_prompt,
            suggested_update_time=suggested_update_time,
            similarity_check={"max_similarity": 0.0, "status": "safe"},
            generation_params={
                "temperature": temperature,
                "proposal_id": proposal.proposal_id,
                "slot_id": slot_id,
            },
        )

    async def _parallel_generate(
        self,
        *,
        proposals: list[Proposal],
        content_strategy: ContentStrategy | dict[str, Any] | str,
        target_audience: str,
        output_language: str = "zh-CN",
        session_id: Optional[str] = None,
        max_slots: Optional[int] = None,
        budget: Optional[SessionTokenBudget] = None,
    ) -> list[GeneratedNote]:
        slot_count = min(max_slots or settings.GENERATION_PARALLEL_SLOTS, len(proposals))
        temperatures = settings.PARALLEL_TEMPERATURES[:slot_count]
        semaphore = asyncio.Semaphore(4)
        pool = ProposalPool(proposals, slot_limit=slot_count)

        tasks = [
            self._generate_with_retry(
                slot_id=index,
                proposal_pool=pool,
                content_strategy=content_strategy,
                target_audience=target_audience,
                temperature=temperatures[index],
                output_language=output_language,
                session_id=session_id,
                semaphore=semaphore,
                budget=budget,
            )
            for index in range(slot_count)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [result for result in results if isinstance(result, GeneratedNote)]

    async def _generate_with_retry(
        self,
        *,
        slot_id: int,
        proposal_pool: ProposalPool,
        content_strategy: ContentStrategy | dict[str, Any] | str,
        target_audience: str,
        temperature: float,
        output_language: str,
        session_id: Optional[str],
        semaphore: asyncio.Semaphore,
        budget: Optional[SessionTokenBudget],
    ) -> GeneratedNote:
        proposal = await self._select_next_proposal(proposal_pool, slot_id)
        if proposal is None:
            raise ContentGenerationError("Proposal pool exhausted before generation.")

        for _attempt in range(settings.GENERATION_MAX_RETRIES + 1):
            note = await self._generate_single(
                slot_id=slot_id,
                proposal=proposal,
                content_strategy=content_strategy,
                target_audience=target_audience,
                temperature=temperature,
                output_language=output_language,
                semaphore=semaphore,
                budget=budget,
            )
            similarity = await self._check_similarity(note, session_id=session_id)
            note.similarity_check = {
                "max_similarity": similarity.embedding_similarity,
                "status": similarity.status,
                "lexical_overlap": similarity.lexical_overlap,
            }
            if not similarity.should_retry:
                return note

            next_proposal = await self._handle_high_similarity(proposal_pool, proposal, slot_id=slot_id)
            if next_proposal is None:
                raise ContentGenerationError("Proposal pool exhausted after high-similarity retry.")
            proposal = next_proposal

        raise ContentGenerationError("Generation retry limit exceeded for slot.")

    async def execute(self, session_id: str) -> GenerationExecutionResult:
        async with self.session_manager as manager:
            session = await manager.get_session(session_id)
            if session is None:
                return GenerationExecutionResult(
                    success=False,
                    status="failed",
                    notes=[],
                    similarity_report={},
                    message="Session not found",
                    error_code="SESSION_NOT_FOUND",
                )

            if session.content_strategy is None or session.platform_preference is None:
                await manager.update_session(
                    session_id,
                    stage=SessionStage.FAILED,
                    error=SessionError(
                        code="INVALID_STAGE",
                        message="Generation requires completed strategy data",
                        stage=SessionStage.GENERATION,
                    ),
                )
                return GenerationExecutionResult(
                    success=False,
                    status="failed",
                    notes=[],
                    similarity_report={},
                    message="Generation requires completed strategy data",
                    error_code="INVALID_STAGE",
                )

            await manager.update_session(session_id, stage=SessionStage.GENERATION)

            budget = SessionTokenBudget(settings.SESSION_TOKEN_BUDGET)
            try:
                proposals = session.proposals or await self.generate_proposals(
                    content_strategy=session.content_strategy,
                    target_audience=session.content_strategy.target_audience,
                    output_language="zh-CN",
                    budget=budget,
                )
            except BudgetExceededError:
                log_event(
                    self._logger,
                    event_name="budget_exceeded",
                    level="error",
                    component="generation",
                    session_id=session_id,
                    stage=SessionStage.GENERATION.value,
                    token_used=budget.used_tokens,
                    token_budget=budget.session_budget,
                    budget_remaining=budget.remaining,
                    usage_estimated=budget.usage_estimated,
                )
                await manager.update_session(
                    session_id,
                    stage=SessionStage.FAILED,
                    error=SessionError(
                        code="BUDGET_EXCEEDED",
                        message="生成预算已用尽，请缩小范围或重试",
                        stage=SessionStage.GENERATION,
                    ),
                )
                return GenerationExecutionResult(
                    success=False,
                    status="failed",
                    notes=[],
                    similarity_report=self._collect_results(
                        total_proposals=0,
                        selected_count=0,
                        notes=[],
                        failed_count=0,
                        budget=budget,
                    ),
                    message="生成预算已用尽，请缩小范围或重试",
                    error_code="BUDGET_EXCEEDED",
                )

            scored = self.score_proposals(proposals, session.platform_preference)
            selected = self.select_top_k(scored)
            parallel_slots = self._resolve_parallel_slots(budget.remaining, len(selected))
            if parallel_slots < min(len(selected), settings.GENERATION_PARALLEL_SLOTS):
                log_event(
                    self._logger,
                    event_name="budget_degrade_applied",
                    level="warning",
                    component="generation",
                    session_id=session_id,
                    stage=SessionStage.GENERATION.value,
                    degrade_action=f"parallel_slots:{len(selected)}->{parallel_slots}",
                    budget_remaining=budget.remaining,
                    token_budget=budget.session_budget,
                )
            notes = await self._parallel_generate(
                proposals=selected,
                content_strategy=session.content_strategy,
                target_audience=session.content_strategy.target_audience,
                output_language="zh-CN",
                session_id=session_id,
                max_slots=parallel_slots,
                budget=budget,
            )
            failed_count = max(0, parallel_slots - len(notes))
            similarity_report = self._collect_results(
                total_proposals=len(proposals),
                selected_count=parallel_slots,
                notes=notes,
                failed_count=failed_count,
                budget=budget,
            )

            if not notes:
                await manager.update_session(
                    session_id,
                    proposals=scored,
                    similarity_report=similarity_report,
                    stage=SessionStage.FAILED,
                    error=SessionError(
                        code="GENERATION_PARTIAL_FAILURE",
                        message="All generation slots failed",
                        stage=SessionStage.GENERATION,
                    ),
                )
                return GenerationExecutionResult(
                    success=False,
                    status="failed",
                    notes=[],
                    similarity_report=similarity_report,
                    message="All generation slots failed",
                    error_code="GENERATION_PARTIAL_FAILURE",
                )

            result_status = "success"
            error_code = None
            message = "Generation completed successfully"
            error_value = None

            if similarity_report["budget_exceeded"]:
                log_event(
                    self._logger,
                    event_name="budget_exceeded",
                    level="error",
                    component="generation",
                    session_id=session_id,
                    stage=SessionStage.GENERATION.value,
                    token_used=similarity_report["token_used"],
                    token_budget=similarity_report["token_budget"],
                    budget_remaining=similarity_report["budget_remaining"],
                    usage_estimated=similarity_report["usage_estimated"],
                )
                result_status = "partial"
                error_code = "BUDGET_EXCEEDED"
                message = "生成预算已用尽，请缩小范围或重试"
                error_value = SessionError(
                    code="BUDGET_EXCEEDED",
                    message=message,
                    stage=SessionStage.GENERATION,
                )
            elif failed_count > 0:
                result_status = "partial"
                error_code = "GENERATION_PARTIAL_FAILURE"
                message = "Some generation slots failed"
                error_value = SessionError(
                    code="GENERATION_PARTIAL_FAILURE",
                    message=message,
                    stage=SessionStage.GENERATION,
                )

            await manager.update_session(
                session_id,
                proposals=scored,
                generated_notes=notes,
                similarity_report=similarity_report,
                stage=SessionStage.COMPLETED,
                error=error_value,
            )
            return GenerationExecutionResult(
                success=True,
                status=result_status,
                notes=notes,
                similarity_report=similarity_report,
                message=message,
                error_code=error_code,
            )

    async def generate(self, request: ContentGeneratorRequest) -> ContentGenerationResult:
        strategy = self._resolve_request_strategy(request)
        preference = self._resolve_request_preference(request)
        budget = SessionTokenBudget(settings.SESSION_TOKEN_BUDGET)

        proposals = await self.generate_proposals(
            content_strategy=strategy,
            target_audience=strategy.target_audience,
            output_language=request.output_language,
            budget=budget,
        )
        selected = self.select_top_k(self.score_proposals(proposals, preference))
        notes = await self._parallel_generate(
            proposals=selected,
            content_strategy=strategy,
            target_audience=strategy.target_audience,
            output_language=request.output_language,
            budget=budget,
        )
        if not notes:
            raise ContentGenerationError("Generation produced no notes.")
        note = notes[0]
        return ContentGenerationResult(
            title=note.title,
            content=note.content,
            tags=note.tags,
            cover_design_prompt=note.cover_design_prompt,
            designed_update_time=note.suggested_update_time,
        )

    async def _check_similarity(
        self,
        note: GeneratedNote,
        *,
        session_id: Optional[str],
        similar_posts: Optional[list[SimilarPost]] = None,
    ) -> SimilarityCheckResult:
        candidates = similar_posts
        if candidates is None and self.rag is not None and session_id:
            candidates = await self.rag.query_similar(
                session_id,
                f"{note.title}\n{note.content}",
                top_k=3,
            )
        candidates = candidates or []

        embedding_similarity = max((float(post.similarity) for post in candidates), default=0.0)
        lexical_overlap = max(
            (
                difflib.SequenceMatcher(None, note.content, getattr(post, "content", "")).ratio()
                for post in candidates
            ),
            default=0.0,
        )

        if embedding_similarity > settings.EMBEDDING_REWRITE_THRESHOLD:
            return SimilarityCheckResult(
                embedding_similarity=embedding_similarity,
                lexical_overlap=lexical_overlap,
                should_retry=True,
                status="rewritten",
            )
        if embedding_similarity > settings.EMBEDDING_WARNING_THRESHOLD or lexical_overlap > settings.LEXICAL_WARNING_THRESHOLD:
            return SimilarityCheckResult(
                embedding_similarity=embedding_similarity,
                lexical_overlap=lexical_overlap,
                should_retry=False,
                status="warning",
            )
        return SimilarityCheckResult(
            embedding_similarity=embedding_similarity,
            lexical_overlap=lexical_overlap,
            should_retry=False,
            status="safe",
        )

    async def _handle_high_similarity(
        self,
        proposal_pool: ProposalPool,
        proposal: Proposal,
        *,
        slot_id: int,
    ) -> Optional[Proposal]:
        await proposal_pool.mark_high_risk(proposal)
        return await self._select_next_proposal(proposal_pool, slot_id)

    async def _select_next_proposal(
        self,
        proposal_pool: ProposalPool,
        slot_id: int,
    ) -> Optional[Proposal]:
        return await proposal_pool.select_proposal(slot_id)

    @staticmethod
    def _resolve_parallel_slots(budget_remaining: int, selected_count: int) -> int:
        estimated_per_note = 1200
        if selected_count > settings.GENERATION_DEGRADED_SLOTS and budget_remaining < estimated_per_note * selected_count:
            return settings.GENERATION_DEGRADED_SLOTS
        return min(selected_count, settings.GENERATION_PARALLEL_SLOTS)

    @staticmethod
    def _collect_results(
        *,
        total_proposals: int,
        selected_count: int,
        notes: list[GeneratedNote],
        failed_count: int,
        budget: SessionTokenBudget,
    ) -> dict[str, Any]:
        warnings = [
            note.title
            for note in notes
            if note.similarity_check.get("status") == "warning"
        ]
        rewritten = sum(1 for note in notes if note.similarity_check.get("status") == "rewritten")
        return {
            "status": "partial" if failed_count > 0 or budget.used_tokens >= budget.session_budget else "success",
            "total_proposals": total_proposals,
            "selected_count": selected_count,
            "notes_generated": len(notes),
            "notes_rewritten": rewritten,
            "failed_count": failed_count,
            "similarity_warnings": warnings,
            "token_used": budget.used_tokens,
            "token_budget": budget.session_budget,
            "budget_remaining": budget.remaining,
            "budget_degraded": selected_count < min(total_proposals, settings.GENERATION_PARALLEL_SLOTS),
            "budget_exceeded": budget.used_tokens >= budget.session_budget,
            "usage_estimated": budget.usage_estimated,
        }

    @staticmethod
    def _resolve_request_strategy(request: ContentGeneratorRequest) -> ContentStrategy:
        requirements = request.requirements or {}
        raw = requirements.get("content_strategy")
        if isinstance(raw, ContentStrategy):
            return raw
        if isinstance(raw, dict):
            return ContentStrategy.model_validate(raw)
        topic = request.topic or "通用内容"
        return ContentStrategy(
            positioning=str(request.brand_preference or topic),
            target_audience="大众用户",
            content_pillars=[str(topic)],
            key_messaging="真实可执行",
            content_types=[request.content_type or "图文"],
            posting_strategy="晚间",
            data_source_quality=0.0,
        )

    @staticmethod
    def _resolve_request_preference(request: ContentGeneratorRequest) -> PlatformPreference:
        requirements = request.requirements or {}
        raw = requirements.get("platform_preference")
        if isinstance(raw, PlatformPreference):
            return raw
        if isinstance(raw, dict):
            return PlatformPreference.model_validate(raw)
        return PlatformPreference(
            avg_title_length=16,
            popular_tags=[],
            optimal_posting_times=["20:00"],
            content_patterns=["中等长度文案"],
        )

    @staticmethod
    def _stringify_content_strategy(content_strategy: ContentStrategy | dict[str, Any] | str) -> str:
        if isinstance(content_strategy, ContentStrategy):
            return content_strategy.model_dump_json(ensure_ascii=False)
        if isinstance(content_strategy, dict):
            return json.dumps(content_strategy, ensure_ascii=False)
        return str(content_strategy)

    @staticmethod
    def _extract_content_pillars(
        content_strategy: ContentStrategy | dict[str, Any] | str,
    ) -> list[str]:
        if isinstance(content_strategy, ContentStrategy):
            return list(content_strategy.content_pillars)
        if isinstance(content_strategy, dict):
            pillars = content_strategy.get("content_pillars")
            if isinstance(pillars, list):
                return [str(item) for item in pillars]
        return []

    @staticmethod
    def _normalize_proposal_item(
        item: dict[str, Any],
        *,
        index: int,
        content_pillars: list[str],
    ) -> Proposal:
        proposal_id = str(item.get("proposal_id") or f"prop_{index}")
        angle = str(item.get("angle") or "").strip()
        hook = str(item.get("title_concept") or "").strip()
        outline = ContentGenerationAgent._stringify_outline(item.get("content_outline"))
        target_emotion = str(item.get("target_emotion") or "practical_value").strip()
        expected_engagement = item.get("expected_engagement", 0.0)
        suggested_tags = ContentGenerationAgent._extract_suggested_tags(item, content_pillars)

        if not angle or not hook or not outline:
            raise ContentGenerationError(
                f"Proposal {proposal_id} is missing required fields."
            )

        try:
            initial_score = float(expected_engagement)
        except (TypeError, ValueError):
            initial_score = 0.0

        return Proposal(
            proposal_id=proposal_id,
            angle=angle,
            hook=hook,
            outline=outline,
            target_emotion=target_emotion,
            content_pillars=list(content_pillars),
            suggested_tags=suggested_tags,
            score=initial_score,
        )

    @staticmethod
    def _extract_suggested_tags(item: dict[str, Any], fallback_pillars: list[str]) -> list[str]:
        raw_tags = item.get("suggested_tags")
        if isinstance(raw_tags, list) and raw_tags:
            return [str(tag) for tag in raw_tags]
        return list(dict.fromkeys(fallback_pillars[:3]))

    @staticmethod
    def _stringify_outline(content_outline: Any) -> str:
        if isinstance(content_outline, str):
            return content_outline.strip()
        if isinstance(content_outline, Iterable):
            parts = [str(part).strip() for part in content_outline if str(part).strip()]
            return "\n".join(parts)
        return ""
