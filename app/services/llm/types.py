"""Shared types for the LLM abstraction layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class LLMServiceError(Exception):
    """Base exception for the LLM abstraction layer."""


class ModelRoutingError(LLMServiceError):
    """Raised when a request cannot be mapped to a provider/model."""


class CredentialResolutionError(LLMServiceError):
    """Raised when no usable credential is configured for a provider."""


class ProviderNotRegisteredError(LLMServiceError):
    """Raised when the resolved provider has no registered adapter."""


@dataclass(frozen=True)
class Message:
    role: str
    content: str


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class LLMCallContext:
    session_id: str | None = None
    job_id: str | None = None
    step_id: str | None = None
    step_name: str | None = None
    agent_name: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None


@dataclass(frozen=True)
class LLMRequest:
    messages: list[Message]
    task_type: str
    model_policy: str | None = None
    model_id: str | None = None
    provider: str | None = None
    temperature: float = 0.7
    max_tokens: int | None = None
    stream: bool = False
    response_format: dict[str, Any] | None = None
    context: LLMCallContext | None = None


@dataclass(frozen=True)
class LLMResponse:
    content: str
    provider: str
    model: str
    usage: TokenUsage
    latency_ms: int
    raw_response_id: str | None = None


@dataclass(frozen=True)
class ResolvedModel:
    provider: str
    model: str
    model_policy: str | None = None
