"""Unified LLM service orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from app.services.llm.configuration import LLMConfigurationReader
from app.services.llm.credentials import CredentialResolver
from app.services.llm.failures import LLMProviderFailure
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
        configuration_reader: LLMConfigurationReader | None = None,
    ) -> None:
        self._router = router
        self._credential_resolver = credential_resolver
        self._providers = {name.lower(): provider for name, provider in providers.items()}
        self._configuration_reader = configuration_reader

    async def generate(self, request: LLMRequest) -> LLMResponse:
        context = request.context
        user_configuration = (
            self._configuration_reader.get(context.tenant_id, context.user_id)
            if self._configuration_reader is not None
            and context is not None
            and context.tenant_id
            and context.user_id
            else None
        )
        if user_configuration is not None:
            provider_name = "openai_compatible"
            model = user_configuration.model
            api_key = user_configuration.api_key
            base_url = user_configuration.base_url
            configuration_source = "user"
        else:
            resolved_model = self._router.resolve(request)
            provider_name = resolved_model.provider
            model = resolved_model.model
            api_key = self._credential_resolver.resolve(
                provider=provider_name,
                tenant_id=context.tenant_id if context else None,
                user_id=context.user_id if context else None,
            )
            base_url = None
            configuration_source = "system_default"

        provider = self._providers.get(provider_name.lower())
        if provider is None:
            raise ProviderNotRegisteredError(
                f"No LLM provider adapter registered for provider: {provider_name}"
            )

        try:
            response = await provider.generate(
                request=request, api_key=api_key, model=model, base_url=base_url
            )
        except LLMProviderFailure as exc:
            raise exc.with_target(
                provider=provider_name, model=model, configuration_source=configuration_source
            ) from exc.__cause__
        return replace(response, configuration_source=configuration_source)
