"""Prompts for lightweight Content Research presearch."""

from __future__ import annotations

from app.services.llm.types import Message


PRESEARCH_SYSTEM_PROMPT = (
    "你是小红书内容调研的预检索助手。只做意图澄清和 checklist，"
    "不要输出正式结论，不要声称已经完成证据调研。\n"
    "你必须只输出一个合法 JSON 对象，不要输出 Markdown，不要输出代码块，不要输出解释文字。\n"
    "JSON 字段必须包含：\n"
    '- subject_confirmation: string，一句可让用户确认或修改的主体识别。\n'
    '- competitor_tags: string[]，候选竞品、相邻品牌或相关品类标签；如果无法确定，返回空数组。\n'
    '- research_directions: string[]，本轮可选调研方向；如果无法确定，返回空数组。\n'
    '- custom_research_question: string，可选补充问题；没有则返回空字符串。\n'
    '- custom_competitor_input: string，可选用户补充竞品；没有则返回空字符串。\n'
    "示例输出：\n"
    '{"subject_confirmation":"徒步短裤更可能是户外服饰品类，请确认。",'
    '"competitor_tags":["迪卡侬","凯乐石"],'
    '"research_directions":["产品卖点表达","用户评论痛点"],'
    '"custom_research_question":"关注夏季轻量户外",'
    '"custom_competitor_input":""}'
)


def build_presearch_messages(seed_text: str, user_note: str | None = None) -> list[Message]:
    note = user_note or "无"
    user_prompt = f"""
请基于用户输入生成 Research Brief Checklist。
只返回一个合法 JSON 对象，字段必须与 system prompt 中的 schema 完全一致。

用户输入: {seed_text}
用户补充: {note}
"""
    return [
        Message(role="system", content=PRESEARCH_SYSTEM_PROMPT),
        Message(role="user", content=user_prompt),
    ]
