"""Build ResearchPlan payloads from confirmed briefs."""

from __future__ import annotations

from dataclasses import dataclass

from app.content_research.models import ResearchBriefRecord
from app.content_research.workflow.direction_registry import ResearchDirectionDefinition


@dataclass(frozen=True)
class BriefConfirmation:
    confirmed_subject: str
    subject_type: str
    selected_competitors: list[str]
    custom_competitors: list[str]
    selected_directions: list[str]
    custom_research_question: str
    primary_marketing_goal: str


class ResearchPlanBuilder:
    def build(
        self,
        *,
        brief: ResearchBriefRecord,
        confirmation: BriefConfirmation,
        directions: list[ResearchDirectionDefinition],
        task_specs: list[dict],
    ) -> dict:
        direction_payloads = [
            {
                "direction_id": item.id,
                "label": item.label,
                "direction_type": item.direction_type,
                "priority": item.priority,
                "questions": item.default_questions,
                "expected_evidence_types": item.expected_evidence_types,
                "source_scope": item.source_scope,
            }
            for item in directions
        ]
        return {
            "schema_version": "content_research_plan_v1",
            "research_brief_id": brief.id,
            "workflow_run_id": brief.workflow_run_id,
            "confirmed_subject": confirmation.confirmed_subject,
            "subject_structure": dict(brief.payload.get("subject_structure") or {}),
            "subject_structure_hash": brief.payload.get("subject_structure_hash"),
            "subject_type": confirmation.subject_type,
            "selected_competitors": confirmation.selected_competitors,
            "custom_competitors": confirmation.custom_competitors,
            "selected_directions": confirmation.selected_directions,
            "custom_research_question": confirmation.custom_research_question,
            "primary_marketing_goal": confirmation.primary_marketing_goal,
            "objective": _objective(confirmation),
            "strategy_summary": "P0 plan compiles selected directions into queued subagent task specs.",
            "directions_payload": direction_payloads,
            "subagent_tasks": task_specs,
            "task_generation_policy": {
                "version": "p0_task_generation_v1",
                "execution_mode": "spec_only",
                "subagent_execution": "deferred",
            },
            "evidence_requirements": {
                "minimum_source_kinds": ["search_result"],
                "required_fields": ["source_url", "canonical_id", "source_kind", "captured_at"],
                "missing_evidence_behavior": "mark_missing_evidence",
            },
        }


def _objective(confirmation: BriefConfirmation) -> str:
    question = confirmation.custom_research_question.strip()
    if question:
        return question
    return f"围绕 {confirmation.confirmed_subject} 生成小红书内容调研计划"
