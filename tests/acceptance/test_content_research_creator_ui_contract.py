from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CREATOR_PAGE = ROOT / "frontend/src/app/creator/page.tsx"
CONTENT_RESEARCH_API = ROOT / "frontend/src/lib/content-research-api.ts"


def test_subject_clarification_uses_the_normal_creator_composer() -> None:
    source = CREATOR_PAGE.read_text(encoding="utf-8")

    assert 'aria-label="调研主体"' not in source
    assert "补充你要调研的具体对象……" in source
    assert "clarifyContentResearchSubject(" in source
    assert "核心对象：{coreObject}｜意图：{intents}｜场景：{contexts}" in source


def test_subject_clarification_posts_the_same_run_workflow_action() -> None:
    source = CONTENT_RESEARCH_API.read_text(encoding="utf-8")

    assert 'action: "clarify_subject"' in source
    assert "clarification_text: clarificationText" in source
