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
