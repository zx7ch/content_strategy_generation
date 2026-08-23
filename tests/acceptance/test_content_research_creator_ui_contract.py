from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CREATOR_PAGE = ROOT / "frontend/src/app/creator/page.tsx"
CONTENT_RESEARCH_API = ROOT / "frontend/src/lib/content-research-api.ts"


def test_deleted_subject_confirmation_card_cannot_be_rendered() -> None:
    source = CREATOR_PAGE.read_text(encoding="utf-8")

    assert 'aria-label="调研主体核心对象"' not in source
    assert 'aria-label="调研主体研究意图"' not in source
    assert 'aria-label="调研主体使用场景"' not in source
    assert "confirmContentResearchSubjectStructure(" not in source
    assert "还需要你确认调研主体" not in source


def test_deleted_subject_confirmation_action_is_not_public() -> None:
    source = CONTENT_RESEARCH_API.read_text(encoding="utf-8")

    assert 'action: "confirm_subject_structure"' not in source
    assert "confirmContentResearchSubjectStructure" not in source
    assert "run: ContentResearchRunProjection" in source
