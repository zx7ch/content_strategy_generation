from __future__ import annotations

from app.content_research.api_schemas import (
    P0_WORKFLOW_ACTIONS,
    ContentResearchWorkflowActionRequest,
    ContentResearchWorkflowActionResponse,
)


def test_p0_workflow_action_contract_lists_supported_actions():
    assert P0_WORKFLOW_ACTIONS == (
        "confirm_brief",
        "start_formal_research",
        "retry_formal_research",
        "pause_formal_research",
        "resume_formal_research",
        "end_content_research",
    )

    request = ContentResearchWorkflowActionRequest(action="confirm_brief")

    assert request.schema_version == "content_research_workflow_action_request_v1"
    assert request.payload == {}


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
