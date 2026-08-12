"""Safe, immutable shapes for Workspace-scoped Lite LLM configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


def _required(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


@dataclass(frozen=True)
class UserLLMConfiguration:
    workspace_id: str
    user_id: str
    base_url: str
    model: str
    api_key: str
    validation_status: str
    validated_at: datetime
    last_validation_error_code: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        for field_name in ("workspace_id", "user_id", "base_url", "model", "api_key"):
            _required(getattr(self, field_name), field_name)
        if self.validation_status != "validated":
            raise ValueError("stored configurations must be validated")


@dataclass(frozen=True)
class LLMConfigurationCandidate:
    base_url: str
    model: str
    api_key: str | None


@dataclass(frozen=True)
class LLMConfigurationSummary:
    source: str
    status: str
    base_url: str
    model: str
    api_key_configured: bool
    api_key_suffix: str | None
    validated_at: datetime | None
    error_code: str | None = None


class LLMConfigurationReader(Protocol):
    def get(self, workspace_id: str, user_id: str) -> UserLLMConfiguration | None:
        ...
