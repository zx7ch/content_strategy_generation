from __future__ import annotations

from app.content_research.workflow import (
    ResearchDirectionRegistry,
    SubagentTaskRouter,
)


def test_task_router_builds_queued_task_specs_with_expected_output_schema():
    directions = ResearchDirectionRegistry().require_many(["brand_activity"])

    specs = SubagentTaskRouter().build_task_specs(
        workflow_run_id="run_1",
        brief_id="rb_1",
        plan_id="rp_1",
        confirmed_subject="Satisfy Running",
        selected_competitors=["Salomon"],
        custom_competitors=[],
        custom_research_question="关注品牌活动",
        directions=directions,
        workspace_id="workspace-1",
        user_id="user-1",
    )

    assert len(specs) == 1
    spec = specs[0]
    assert spec["status"] == "queued"
    assert spec["agent_name"] == "DirectionalExecutionPipeline"
    assert spec["llm_scope"] == {
        "workspace_id": "workspace-1",
        "user_id": "user-1",
    }
    assert spec["expected_output_schema"]["required"] == ["finding", "evidence_refs", "missing_evidence"]
