from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.content_research.api_schemas import (
    P0_WORKFLOW_ACTIONS,
    ContentResearchWorkflowActionRequest,
)
from app.content_research.commands.dispatcher import (
    WorkflowActionContext,
    WorkflowActionDispatcher,
    build_workflow_action_dispatcher,
)
from app.content_research.errors import ContentResearchValidationError


def _request(action: str) -> ContentResearchWorkflowActionRequest:
    return ContentResearchWorkflowActionRequest(
        command_id="command-dispatcher-test",
        expected_state="recovery_required",
        expected_revision=2,
        action=action,
        payload={},
    )


def test_default_workflow_action_registry_matches_public_contract() -> None:
    dispatcher = build_workflow_action_dispatcher()

    assert dispatcher.registered_actions == P0_WORKFLOW_ACTIONS


@pytest.mark.asyncio
async def test_dispatcher_preserves_unsupported_action_validation() -> None:
    dispatcher = build_workflow_action_dispatcher()

    with pytest.raises(
        ContentResearchValidationError,
        match="Unsupported Content Research workflow action: future_action",
    ):
        await dispatcher.dispatch(
            WorkflowActionContext(
                application=Any,
                command_service=Any,
                workflow_run_id="run-dispatcher-test",
                request=_request("future_action"),
            )
        )


@dataclass(frozen=True)
class _DuplicateHandler:
    action: str = "cancel"

    async def execute(self, _context: WorkflowActionContext) -> Any:
        raise AssertionError("duplicate handlers must be rejected before execution")


def test_dispatcher_rejects_duplicate_action_handlers() -> None:
    with pytest.raises(ValueError, match="duplicate workflow action handler: cancel"):
        WorkflowActionDispatcher((_DuplicateHandler(), _DuplicateHandler()))
