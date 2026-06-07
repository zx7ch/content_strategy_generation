"""Model routing for the LLM abstraction layer."""

from __future__ import annotations

from collections.abc import Mapping

from app.config import settings
from app.services.llm.types import LLMRequest, ModelRoutingError, ResolvedModel


def _setting_value(settings_obj: object, name: str, default: str) -> str:
    value = getattr(settings_obj, name, default)
    return value if isinstance(value, str) and value else default


def build_default_policy_map(settings_obj: object = settings) -> dict[str, ResolvedModel]:
    return {
        "cheap": ResolvedModel(
            provider="deepseek",
            model=_setting_value(settings_obj, "DEEPSEEK_MODEL", "deepseek-chat"),
            model_policy="cheap",
        ),
        "balanced": ResolvedModel(
            provider="openai",
            model=_setting_value(settings_obj, "OPENAI_MODEL", "gpt-4o-mini"),
            model_policy="balanced",
        ),
        "quality": ResolvedModel(
            provider="openai",
            model=_setting_value(settings_obj, "OPENAI_MODEL", "gpt-4o-mini"),
            model_policy="quality",
        ),
        "json_strict": ResolvedModel(
            provider="openai",
            model=_setting_value(settings_obj, "OPENAI_MODEL", "gpt-4o-mini"),
            model_policy="json_strict",
        ),
        "long_context": ResolvedModel(
            provider="kimi",
            model=_setting_value(settings_obj, "KIMI_MODEL", "moonshot-v1-8k"),
            model_policy="long_context",
        ),
    }


class ModelRouter:
    def __init__(
        self,
        policy_map: Mapping[str, ResolvedModel] | None = None,
        *,
        settings_obj: object = settings,
    ) -> None:
        self._policy_map = dict(policy_map or build_default_policy_map(settings_obj))

    def resolve(self, request: LLMRequest) -> ResolvedModel:
        if request.provider and request.model_id:
            return ResolvedModel(
                provider=request.provider.lower(),
                model=request.model_id,
                model_policy=request.model_policy,
            )

        policy = request.model_policy or request.task_type
        if policy and policy in self._policy_map:
            resolved = self._policy_map[policy]
            return ResolvedModel(
                provider=resolved.provider.lower(),
                model=resolved.model,
                model_policy=resolved.model_policy or policy,
            )

        raise ModelRoutingError(f"No LLM model route configured for policy/task: {policy!r}")
