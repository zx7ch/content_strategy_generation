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
    'source_terms、term_roles、core_entities（每项包含 canonical_name、raw_mentions）、research_intents、'
    'context_modifiers、synonym_groups、ambiguities、resolution_state。raw_mentions 必须来自用户原文。\n'
    "结构拆分必须分两步完成。第一步生成 source_terms：如果用户输入已经用空格分隔，"
    "source_terms 必须逐项等于这些分段，不得再次拆分；如果没有空格，拆成可独立搜索的完整短词，"
    "所有短词按原顺序连接后必须能还原用户输入。第二步把每个 source_term 恰好一次映射到 term_roles："
    "core_object 是品牌、品类、产品或型号，至少一个；product_experience 是具体产品属性或体验短词，可为空；"
    "context_audience 是季节、人群、地点或场合，可为空。不得创造 source_terms 之外的词。\n"
    "core_entities、research_intents、context_modifiers 必须分别与上述三类映射保持一致；"
    "不要放产品卖点、购买考虑、研究目标等分析概念。\n"
    "不要把包含意图或场景修饰的完整用户句子直接复制为核心对象。"
    "raw_mentions 必须是用户原文中连续出现的实体片段；canonical_name 可对该片段做品类归一化。\n"
    "示例输出：\n"
    '{"subject_confirmation":"徒步短裤更可能是户外服饰品类，请确认。",'
    '"competitor_tags":["迪卡侬","凯乐石"],'
    '"research_directions":["产品卖点表达","用户评论痛点"],'
    '"custom_competitor_input":"",'
    '"subject_structure":{"schema_version":"content_research_subject_structure_v1",'
    '"canonical_subject":"徒步短裤","subject_type":"category",'
    '"source_terms":["夏季","轻量","徒步短裤"],'
    '"term_roles":{"core_object":["徒步短裤"],"product_experience":["轻量"],'
    '"context_audience":["夏季"]},'
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

特别注意：先按规则生成 source_terms，再把每个 source_term 恰好一次映射到 term_roles；
不得创造分词结果之外的属性、体验、场景或人群词。
"""
    return [
        Message(role="system", content=PRESEARCH_SYSTEM_PROMPT),
        Message(role="user", content=repair_prompt),
    ]
