"""Validate and safely expose Lite OpenAI-compatible configurations."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

from app.services.llm.configuration import (
    LLMConfigurationCandidate,
    LLMConfigurationSummary,
    UserLLMConfiguration,
)
from app.services.llm.configuration_store import SQLiteLLMConfigurationStore
from app.services.llm.failures import LLMProviderFailure, classify_provider_exception
from app.services.llm.types import LLMRequest, Message


class LiteLLMConfigurationService:
    def __init__(self, *, store: SQLiteLLMConfigurationStore, probe_adapter) -> None:
        self._store = store
        self._probe_adapter = probe_adapter

    def get_summary(self, workspace_id: str, user_id: str) -> LLMConfigurationSummary:
        configuration = self._store.get(workspace_id, user_id)
        if configuration is None:
            return LLMConfigurationSummary(
                source="system_default", status="not_configured", base_url="", model="",
                api_key_configured=False, api_key_suffix=None, validated_at=None,
            )
        return self._summary(configuration)

    async def validate(
        self, *, workspace_id: str, user_id: str, candidate: LLMConfigurationCandidate
    ) -> LLMConfigurationSummary:
        try:
            normalized = self._normalize_candidate(workspace_id, user_id, candidate)
        except LLMProviderFailure as exc:
            return self._invalid_summary(candidate, exc.code)
        try:
            response = await self._probe_adapter.generate(
                LLMRequest(
                    messages=[
                        Message(role="system", content='Return only {"ok":true}.'),
                        Message(role="user", content="configuration probe"),
                    ],
                    task_type="content_research.configuration_probe", temperature=0.0, max_tokens=32,
                ),
                normalized.api_key or "", normalized.model, normalized.base_url,
            )
            payload = json.loads(response.content)
            if not isinstance(payload, dict) or payload.get("ok") is not True:
                raise ValueError("non-conforming probe payload")
        except LLMProviderFailure as exc:
            return self._invalid_summary(normalized, exc.code)
        except Exception as exc:
            del exc
            return self._invalid_summary(normalized, "llm_protocol_incompatible")
        return LLMConfigurationSummary(
            source="candidate", status="validated", base_url=normalized.base_url,
            model=normalized.model, api_key_configured=True,
            api_key_suffix=self._suffix(normalized.api_key), validated_at=datetime.now(timezone.utc),
        )

    async def save(
        self, *, workspace_id: str, user_id: str, candidate: LLMConfigurationCandidate
    ) -> LLMConfigurationSummary:
        normalized = self._normalize_candidate(workspace_id, user_id, candidate)
        validation = await self.validate(
            workspace_id=workspace_id, user_id=user_id, candidate=normalized
        )
        if validation.status != "validated":
            return validation
        saved = self._store.upsert(UserLLMConfiguration(
            workspace_id=workspace_id, user_id=user_id, base_url=normalized.base_url,
            model=normalized.model, api_key=normalized.api_key or "", validation_status="validated",
            validated_at=validation.validated_at or datetime.now(timezone.utc),
        ))
        return self._summary(saved)

    def delete(self, workspace_id: str, user_id: str) -> LLMConfigurationSummary:
        self._store.delete(workspace_id, user_id)
        return self.get_summary(workspace_id, user_id)

    def _normalize_candidate(
        self, workspace_id: str, user_id: str, candidate: LLMConfigurationCandidate
    ) -> LLMConfigurationCandidate:
        parsed = urlparse(candidate.base_url.strip())
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise LLMProviderFailure("llm_protocol_incompatible", "模型服务地址无效", True, None)
        model = candidate.model.strip()
        if not model:
            raise LLMProviderFailure("llm_protocol_incompatible", "模型名称无效", True, None)
        api_key = candidate.api_key.strip() if isinstance(candidate.api_key, str) else None
        if not api_key:
            current = self._store.get(workspace_id, user_id)
            api_key = current.api_key if current is not None else None
        if not api_key:
            raise LLMProviderFailure("llm_auth_invalid", "API Key 未配置", True, None)
        path = parsed.path.rstrip("/")
        normalized_url = urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))
        return LLMConfigurationCandidate(normalized_url, model, api_key)

    @staticmethod
    def _suffix(api_key: str | None) -> str | None:
        return api_key[-4:] if api_key else None

    def _summary(self, configuration: UserLLMConfiguration) -> LLMConfigurationSummary:
        return LLMConfigurationSummary(
            source="user", status=configuration.validation_status, base_url=configuration.base_url,
            model=configuration.model, api_key_configured=True,
            api_key_suffix=self._suffix(configuration.api_key), validated_at=configuration.validated_at,
            error_code=configuration.last_validation_error_code,
        )

    def _invalid_summary(
        self, candidate: LLMConfigurationCandidate, error_code: str
    ) -> LLMConfigurationSummary:
        return LLMConfigurationSummary(
            source="candidate", status="invalid", base_url=candidate.base_url.rstrip("/"),
            model=candidate.model, api_key_configured=bool(candidate.api_key),
            api_key_suffix=self._suffix(candidate.api_key), validated_at=None, error_code=error_code,
        )
