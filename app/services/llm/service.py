"""Unified LLM service orchestration."""

from __future__ import annotations

from collections.abc import Mapping

from app.services.llm.credentials import CredentialResolver
from app.services.llm.providers.base import BaseLLMProvider
from app.services.llm.router import ModelRouter
from app.services.llm.types import LLMRequest, LLMResponse, ProviderNotRegisteredError


class LLMService:
    def __init__(
        self,
        *,
        router: ModelRouter,
        credential_resolver: CredentialResolver,
        providers: Mapping[str, BaseLLMProvider],
    ) -> None:
        self._router = router
        self._credential_resolver = credential_resolver
        self._providers = {name.lower(): provider for name, provider in providers.items()}

    async def generate(self, request: LLMRequest) -> LLMResponse:
        resolved_model = self._router.resolve(request)
        context = request.context
        api_key = self._credential_resolver.resolve(
            provider=resolved_model.provider,
            tenant_id=context.tenant_id if context else None,
            user_id=context.user_id if context else None,
        )

        provider = self._providers.get(resolved_model.provider.lower())
        if provider is None:
            raise ProviderNotRegisteredError(
                f"No LLM provider adapter registered for provider: {resolved_model.provider}"
            )

        return await provider.generate(
            request=request,
            api_key=api_key,
            model=resolved_model.model,
        )
