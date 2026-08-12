"""Durable Workspace/user scope for Content Research LLM calls."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.llm.failures import LLMProviderFailure
from app.services.llm.types import LLMCallContext


def content_research_llm_context(
    scope_owner: Mapping[str, Any],
    *,
    session_id: str,
    workflow_run_id: str,
    step_name: str,
    agent_name: str,
) -> LLMCallContext:
    """Build a scoped call context or fail before system routing can occur."""
    scope = scope_owner.get("llm_scope")
    workspace_id = scope.get("workspace_id") if isinstance(scope, Mapping) else None
    user_id = scope.get("user_id") if isinstance(scope, Mapping) else None
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise _missing_scope_failure()
    if not isinstance(user_id, str) or not user_id.strip():
        raise _missing_scope_failure()
    return LLMCallContext(
        session_id=session_id,
        job_id=workflow_run_id,
        step_name=step_name,
        agent_name=agent_name,
        tenant_id=workspace_id.strip(),
        user_id=user_id.strip(),
    )


def _missing_scope_failure() -> LLMProviderFailure:
    return LLMProviderFailure(
        "llm_configuration_scope_missing",
        "模型配置作用域不可用",
        False,
        None,
        provider="unresolved",
        model="unresolved",
        configuration_source="unresolved",
    )
