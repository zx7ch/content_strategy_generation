"""Compatibility chat client that records LLM usage events."""

from __future__ import annotations

from typing import Final

from app.config import settings
from app.services.llm.credentials import CredentialResolver
from app.services.llm.configuration_store import SQLiteLLMConfigurationStore
from app.services.llm.failures import LLMProviderFailure
from app.services.llm.pricing import PricingCalculator, UsageCost
from app.services.llm.providers.openai_compatible import OpenAICompatibleAdapter
from app.services.llm.router import ModelRouter
from app.services.llm.service import LLMService
from app.services.llm.types import LLMCallContext, LLMRequest, Message, TokenUsage
from app.services.llm.usage_tracker import LLMUsageEventInput, LLMUsageTracker


DEFAULT_OPENAI_COMPATIBLE_PROVIDERS: Final[tuple[str, ...]] = (
    "openai",
    "deepseek",
    "kimi",
    "moonshot",
    "qwen",
)


def build_default_llm_service(db_path: str | None = None) -> LLMService:
    providers = {
        provider: OpenAICompatibleAdapter(
            provider=provider,
            base_url=getattr(settings, f"{provider.upper()}_BASE_URL", "") or None,
        )
        for provider in DEFAULT_OPENAI_COMPATIBLE_PROVIDERS
    }
    providers["openai_compatible"] = OpenAICompatibleAdapter(provider="openai_compatible")
    return LLMService(
        router=ModelRouter(settings_obj=settings),
        credential_resolver=CredentialResolver(settings),
        providers=providers,
        configuration_reader=SQLiteLLMConfigurationStore(db_path or settings.SQLITE_DB_PATH),
    )


class TrackedLLMChatClient:
    """Old `LLMClient.chat()` shape backed by the new LLM abstraction layer."""

    def __init__(
        self,
        *,
        llm_service: LLMService,
        usage_tracker: LLMUsageTracker,
        pricing_calculator: PricingCalculator,
        context: LLMCallContext,
        model_policy: str = "balanced",
    ) -> None:
        self._llm_service = llm_service
        self._usage_tracker = usage_tracker
        self._pricing_calculator = pricing_calculator
        self._context = context
        self._model_policy = model_policy

    async def chat(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> str:
        request = LLMRequest(
            messages=[
                Message(role="system", content=system),
                Message(role="user", content=user),
            ],
            task_type="chat",
            model_policy=self._model_policy,
            temperature=temperature,
            max_tokens=max_tokens,
            context=self._context,
        )

        try:
            response = await self._llm_service.generate(request)
        except LLMProviderFailure as exc:
            await self._record(
                provider=exc.provider or "unknown", model=exc.model or "unknown", usage=TokenUsage(),
                cost=UsageCost(), latency_ms=None, status="failed",
                error_message=f"{exc.code}: {exc.public_message}",
            )
            raise
        except Exception as exc:
            await self._record(
                provider="unknown",
                model="unknown",
                usage=TokenUsage(),
                cost=UsageCost(),
                latency_ms=None,
                status="failed",
                error_message=str(exc),
            )
            raise

        cost = self._pricing_calculator.calculate(
            provider=response.provider,
            model=response.model,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
        )
        await self._record(
            provider=response.provider,
            model=response.model,
            usage=response.usage,
            cost=cost,
            latency_ms=response.latency_ms,
            status="success",
            error_message=None,
        )
        return response.content

    async def _record(
        self,
        *,
        provider: str,
        model: str,
        usage: TokenUsage,
        cost: UsageCost,
        latency_ms: int | None,
        status: str,
        error_message: str | None,
    ) -> str:
        async with self._usage_tracker as tracker:
            return await tracker.record(
                LLMUsageEventInput(
                    context=self._context,
                    provider=provider,
                    model=model,
                    model_policy=self._model_policy,
                    usage=usage,
                    cost=cost,
                    latency_ms=latency_ms,
                    status=status,
                    error_message=error_message,
                )
            )


def build_default_tracked_chat_client(
    *,
    db_path: str,
    session_id: str,
    job_id: str,
    model_policy: str,
    step_id: str | None = None,
    step_name: str | None = None,
    agent_name: str | None = None,
) -> TrackedLLMChatClient:
    return TrackedLLMChatClient(
        llm_service=build_default_llm_service(db_path),
        usage_tracker=LLMUsageTracker(db_path),
        pricing_calculator=PricingCalculator(),
        context=LLMCallContext(
            session_id=session_id,
            job_id=job_id,
            step_id=step_id,
            step_name=step_name,
            agent_name=agent_name,
        ),
        model_policy=model_policy,
    )
