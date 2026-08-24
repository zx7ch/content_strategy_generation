from __future__ import annotations

from app.api.routes.router import _content_research_error, app
from app.content_research.api_schemas import (
    P0_WORKFLOW_ACTIONS,
    ContentResearchWorkflowActionRequest,
    ContentResearchWorkflowActionResponse,
)
from app.content_research.service import ContentResearchStateConflictError


def test_p0_workflow_action_contract_lists_supported_actions():
    assert P0_WORKFLOW_ACTIONS == (
        "cancel",
        "retry_presearch",
        "revise_subject",
        "confirm_brief",
        "replace_scope_draft",
        "confirm_scope",
    )

    request = ContentResearchWorkflowActionRequest(
        command_id="revise-subject-contract",
        expected_state="brief_confirmation_required",
        expected_revision=2,
        action="revise_subject",
    )

    assert request.schema_version == "content_research_workflow_action_request_v1"
    assert request.payload == {}


def test_slice_one_removes_legacy_parallel_mutation_routes():
    paths = {route.path for route in app.routes}

    assert "/content-research/workflows/{workflow_run_id}/source-collections" not in paths
    assert "/content-research/briefs/{brief_id}/confirm" not in paths
    assert "/content-research/workflows" not in paths


def test_workflow_action_response_envelope_has_remote_ready_fields():
    response = ContentResearchWorkflowActionResponse(
        workflow_run_id="run_1",
        action="confirm_brief",
        status="completed",
        result={"workflow_run_id": "run_1"},
        local_cache_id="rb_1",
    )
    payload = response.model_dump()

    assert payload["schema_version"] == "content_research_workflow_action_response_v1"
    assert payload["execution_mode"] == "local"
    assert payload["remote_run_id"] is None
    assert payload["local_cache_id"] == "rb_1"
    assert payload["sync_status"] == "local_only"


def test_presearch_pending_state_has_stable_retryable_conflict_contract():
    error = _content_research_error(
        ContentResearchStateConflictError(
            "Presearch final outcome is not ready",
            error_code="CONTENT_RESEARCH_PRESEARCH_NOT_READY",
            suggested_action="等待预检索最终完成或完成模型配置后重试",
        )
    )

    assert error.status_code == 409
    assert error.payload.error_code == "CONTENT_RESEARCH_PRESEARCH_NOT_READY"
    assert error.payload.retryable is True
    assert error.payload.suggested_action == "等待预检索最终完成或完成模型配置后重试"


def test_formal_dispatch_pending_state_has_stable_retryable_conflict_contract():
    error = _content_research_error(
        ContentResearchStateConflictError(
            "Formal research is not ready to dispatch",
            error_code="CONTENT_RESEARCH_FORMAL_RESEARCH_NOT_READY",
            suggested_action="先确认最终版调研 brief，再开始正式调研",
        )
    )

    assert error.status_code == 409
    assert error.payload.error_code == "CONTENT_RESEARCH_FORMAL_RESEARCH_NOT_READY"
    assert error.payload.retryable is True
    assert error.payload.suggested_action == "先确认最终版调研 brief，再开始正式调研"
