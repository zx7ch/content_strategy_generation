"""Prompts for lightweight Content Research presearch."""

from __future__ import annotations

import json

from app.services.llm.types import Message

PRESEARCH_SYSTEM_PROMPT = (
    "你是小红书内容调研的预检索助手。只做意图澄清和 checklist，"
    "不要输出正式结论，不要声称已经完成证据调研。\n"
    "你必须只输出一个合法 JSON 对象，不要输出 Markdown，不要输出代码块，不要输出解释文字。\n"
    "JSON 字段必须包含：\n"
    '- subject_confirmation: string，一句可让用户确认或修改的主体识别。\n'
    '- competitor_tags: string[]，候选竞品、相邻品牌或相关品类标签；如果无法确定，返回空数组。\n'
    '- research_directions: string[]，本轮可选调研方向；如果无法确定，返回空数组。\n'
    '- custom_competitor_input: string，可选用户补充竞品；没有则返回空字符串。\n'
    '- subject_structure: object，必须包含 schema_version、canonical_subject、subject_type、'
    'core_entities（每项包含 canonical_name、raw_mentions）、research_intents、'
    'context_modifiers、synonym_groups、ambiguities、resolution_state。raw_mentions 必须来自用户原文。\n'
    "结构拆分规则：核心对象只保留可被调研的实体（品牌、品类、产品或型号）；"
    "research_intents 只放用户会直接和核心对象一起检索的具体产品属性或体验短词；"
    "不要放产品卖点、购买考虑、研究目标等分析概念。季节、人群、地点、场合等放入 context_modifiers。\n"
    "不要把包含意图或场景修饰的完整用户句子直接复制为核心对象。"
    "raw_mentions 必须是用户原文中连续出现的实体片段；canonical_name 可对该片段做品类归一化。\n"
    "示例输出：\n"
    '{"subject_confirmation":"徒步短裤更可能是户外服饰品类，请确认。",'
    '"competitor_tags":["迪卡侬","凯乐石"],'
    '"research_directions":["产品卖点表达","用户评论痛点"],'
    '"custom_competitor_input":"",'
    '"subject_structure":{"schema_version":"content_research_subject_structure_v1",'
    '"canonical_subject":"徒步短裤","subject_type":"category",'
    '"core_entities":[{"canonical_name":"徒步短裤","raw_mentions":["徒步短裤"]}],'
    '"research_intents":["轻量"],"context_modifiers":["夏季户外"],'
    '"synonym_groups":{"徒步短裤":["户外短裤"]},"ambiguities":[],'
    '"resolution_state":"resolved"}}'
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


def build_presearch_repair_messages(
    seed_text: str,
    user_note: str | None,
    *,
    invalid_response: str,
    reason_codes: tuple[str, ...],
) -> list[Message]:
    """Request one bounded, reason-directed repair of a parseable structure."""

    note = user_note or "无"
    repair_prompt = f"""
上一次输出可以解析，但 subject_structure 未通过结构质量检查。
请只修正 subject_structure 的拆分，并返回完整 JSON；不要增加解释文字。

用户输入: {seed_text}
用户补充: {note}
质量问题: {json.dumps(list(reason_codes), ensure_ascii=False)}
上一次输出: {invalid_response}

特别注意：核心对象只保留品牌、品类、产品或型号；产品属性或体验短词放入
research_intents；季节、人群、地点和场合放入 context_modifiers。
"""
    return [
        Message(role="system", content=PRESEARCH_SYSTEM_PROMPT),
        Message(role="user", content=repair_prompt),
    ]
