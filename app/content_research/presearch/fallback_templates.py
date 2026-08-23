"""Fallback checklist templates for Content Research presearch."""

from __future__ import annotations

from app.content_research.presearch.service import PresearchChecklist


DEFAULT_RESEARCH_DIRECTIONS = [
    "高增长关键词",
    "产品卖点表达",
    "竞品品牌",
    "小红书内容表现",
    "用户评论痛点",
]


def build_fallback_checklist(seed_text: str, user_note: str | None = None) -> PresearchChecklist:
    note_hint = f" 用户补充目标: {user_note}" if user_note else ""
    return PresearchChecklist(
        subject_confirmation=(
            f"{seed_text} 需要进一步确认是品牌、品类/SKU、产品还是场景。"
            f"请确认这是否是本轮内容调研主体。{note_hint}"
        ),
        competitor_tags=[],
        research_directions=list(DEFAULT_RESEARCH_DIRECTIONS),
        custom_competitor_input="",
    )
