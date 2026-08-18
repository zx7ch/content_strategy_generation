from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CREATOR_PAGE = ROOT / "frontend/src/app/creator/page.tsx"
CONTENT_RESEARCH_API = ROOT / "frontend/src/lib/content-research-api.ts"


def test_subject_clarification_uses_structured_creator_card() -> None:
    source = CREATOR_PAGE.read_text(encoding="utf-8")

    assert 'aria-label="调研主体核心对象"' in source
    assert 'aria-label="调研主体研究意图"' in source
    assert 'aria-label="调研主体使用场景"' in source
    assert "confirmContentResearchSubjectStructure(" in source
    assert "补充你要调研的具体对象……" not in source


def test_subject_confirmation_posts_the_same_run_workflow_action() -> None:
    source = CONTENT_RESEARCH_API.read_text(encoding="utf-8")

    assert 'action: "confirm_subject_structure"' in source
    assert "subject_structure_hash: string" in source


def test_creator_scope_actions_keep_server_owned_fields_out_of_confirmation() -> None:
    source = CONTENT_RESEARCH_API.read_text(encoding="utf-8")

    confirm_request = source.split(
        "export interface ContentResearchConfirmScopeRequest", 1
    )[1].split("}", 1)[0]
    assert "scope_draft_id: string" in confirm_request
    assert "structure_hash: string" in confirm_request
    assert "final_query: string" in confirm_request
    assert "constraints" not in confirm_request
    assert "suggested_query" not in confirm_request
    assert "execution_role" not in confirm_request
    assert 'action: "prepare_scope"' in source
    assert 'action: "confirm_scope"' in source
    assert 'action: "resolve_coverage"' in source
    assert "/scope${suffix}" in source


def test_creator_renders_scope_confirmation_and_only_explicit_pending_decisions() -> None:
    source = CREATOR_PAGE.read_text(encoding="utf-8")

    assert 'aria-label={`检索组 ${index + 1}`}' in source
    assert "scopeExecutionRoleLabel(group.execution_role)" in source
    assert 'event.event_name === "coverage_evaluated"' in source
    assert 'stringField(event.payload, "state") === "awaiting_scope_decision"' in source
    assert "继续补充{unmetLabel}样本" in source
    assert "基于现有证据生成受限报告" in source
    assert "放宽{unmetLabel}约束" in source
    assert "hideReportForPendingScope" in source
    assert "await getContentResearchScope(runIdForThread)" in source
