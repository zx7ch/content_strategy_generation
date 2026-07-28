from __future__ import annotations

from app.content_research.models import ResearchBriefRecord
from app.content_research.workflow import (
    BriefConfirmation,
    ResearchDirectionRegistry,
    ResearchPlanBuilder,
    SubagentTaskRouter,
)


def test_plan_builder_outputs_required_research_plan_contract():
    brief = ResearchBriefRecord(
        id="rb_1",
        workflow_run_id="run_1",
        thread_id="thread_1",
        schema_version="content_research_brief_v1",
        status="draft",
        payload={"schema_version": "content_research_brief_v1", "seed_text": "徒步短裤"},
    )
    confirmation = BriefConfirmation(
        confirmed_subject="徒步短裤",
        subject_type="category",
        selected_competitors=["迪卡侬"],
        custom_competitors=["凯乐石"],
        selected_directions=["product_marketing", "comment_insight"],
        custom_research_question="关注夏季轻量户外",
    )
    registry = ResearchDirectionRegistry()
    directions = registry.require_many(confirmation.selected_directions)
    task_specs = SubagentTaskRouter().build_task_specs(
        workflow_run_id=brief.workflow_run_id,
        brief_id=brief.id,
        plan_id="rp_1",
        confirmed_subject=confirmation.confirmed_subject,
        selected_competitors=confirmation.selected_competitors,
        custom_competitors=confirmation.custom_competitors,
        custom_research_question=confirmation.custom_research_question,
        directions=directions,
    )

    payload = ResearchPlanBuilder().build(
        brief=brief,
        confirmation=confirmation,
        directions=directions,
        task_specs=task_specs,
    )

    assert payload["schema_version"] == "content_research_plan_v1"
    assert payload["confirmed_subject"] == "徒步短裤"
    assert payload["selected_competitors"] == ["迪卡侬"]
    assert payload["custom_competitors"] == ["凯乐石"]
    assert payload["evidence_requirements"]["minimum_source_kinds"] == ["search_result"]
    assert [item["research_direction_id"] for item in payload["subagent_tasks"]] == [
        "product_marketing",
        "comment_insight",
    ]


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
    )

    assert len(specs) == 1
    spec = specs[0]
    assert spec["status"] == "queued"
    assert spec["agent_name"] == "DirectionalExecutionPipeline"
    assert spec["expected_output_schema"]["required"] == ["finding", "evidence_refs", "missing_evidence"]
