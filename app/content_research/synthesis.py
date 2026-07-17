"""Evidence-bounded, user-readable synthesis for research result snapshots."""

from __future__ import annotations

from typing import Any


def synthesize_snapshot(items: list[Any]) -> dict[str, Any]:
    """Turn verified directional results into bounded report copy.

    This deliberately consumes result items only (which already reference
    bundles and their verification state), never raw source text.
    """
    limitations = _limitations(items)
    return {
        "executive_summary": _summary(items, limitations),
        "limitations": limitations,
        "recommendations": _recommendations(items, limitations),
    }


def _summary(items: list[Any], limitations: list[dict[str, Any]]) -> str:
    if not items:
        return "本轮尚未采集到可用内容样本，因此还不能形成调研结论。请检查采集权限或调整调研范围后重试。"
    supported = [item for item in items if item.claim_status == "supported"]
    if supported:
        headline = supported[0].summary.strip()
        if limitations:
            return f"本轮形成了 {len(supported)} 条有证据边界的观察：{headline} 其余方向仍需补充证据后再扩大结论范围。"
        return f"本轮形成了 {len(supported)} 条有证据支持的观察：{headline}"
    return (
        f"本轮已完成 {len(items)} 个调研方向的初步分析，但现有样本尚不足以支持可采用的结论。"
        "请根据下方“为什么还不足”和“下一步怎么做”补充验证。"
    )


def _limitations(items: list[Any]) -> list[dict[str, Any]]:
    limitations: list[dict[str, Any]] = []
    for item in items:
        missing = list(item.missing_evidence or [])
        if missing:
            limitations.extend(_limitation_from_missing(item, entry) for entry in missing)
        elif item.claim_status != "supported":
            limitations.append(_limitation_from_state(item))
    return _dedupe_limitations(limitations)


def _limitation_from_missing(item: Any, entry: dict[str, Any]) -> dict[str, Any]:
    reason = str(entry.get("reason") or "evidence_gap")
    message, impact, next_step = _explanation(reason)
    return {
        "schema_version": "content_research_result_limitation_v2",
        "result_item_id": item.result_item_id,
        "evidence_bundle_id": item.evidence_bundle_id,
        "reason": reason,
        "message": message,
        "impact": impact,
        "next_step": next_step,
    }


def _limitation_from_state(item: Any) -> dict[str, Any]:
    reason = "no_usable_evidence" if item.evidence_state == "invalid" else "evidence_not_yet_sufficient"
    message, impact, next_step = _explanation(reason)
    return {
        "schema_version": "content_research_result_limitation_v2",
        "result_item_id": item.result_item_id,
        "evidence_bundle_id": item.evidence_bundle_id,
        "reason": reason,
        "message": message,
        "impact": impact,
        "next_step": next_step,
    }


def _recommendations(items: list[Any], limitations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    for item in items:
        related = next((entry for entry in limitations if entry["result_item_id"] == item.result_item_id), None)
        if related:
            action = related["next_step"]
            action_type = "collect_or_verify_evidence"
        elif item.evidence_state == "verified":
            action = "将这条证据充分的观察作为下一轮内容策略讨论的输入，并保留证据链接供复核。"
            action_type = "review_verified_finding"
        else:
            action = "先复核当前证据范围，再决定是否把这条观察用于后续内容选题。"
            action_type = "review_finding"
        recommendations.append({
            "schema_version": "content_research_recommendation_v2",
            "recommendation_id": f"rec_{item.result_item_id}",
            "action": action,
            "action_type": action_type,
            "based_on_findings": [item.result_item_id],
            "evidence_bundle_ids": item.evidence_bundle_ids,
        })
    if not recommendations and not items:
        recommendations.append({
            "schema_version": "content_research_recommendation_v2",
            "recommendation_id": "rec_recover_collection",
            "action": "确认数据源登录状态与调研关键词后重新采集内容样本。",
            "action_type": "recover_collection",
            "based_on_findings": [],
            "evidence_bundle_ids": [],
        })
    return recommendations


def _explanation(reason: str) -> tuple[str, str, str]:
    mapping = {
        "claim_without_citation": (
            "这条观察没有关联到可复核的内容证据。",
            "因此不能把它当作已验证结论。",
            "补充包含正文或明确卖点表达的内容样本，并确保分析引用这些样本。",
        ),
        "insufficient_independent_sources": (
            "现有引用主要来自同一作者或同一来源，缺少独立佐证。",
            "同一来源的重复内容不足以证明这是普遍现象。",
            "补充至少一位独立作者或独立来源的同类内容，再比较结论是否一致。",
        ),
        "auth_required": (
            "数据源当前需要登录或授权，采集没有得到完整样本。",
            "样本不完整会让结论偏向已能访问的少量内容。",
            "恢复数据源登录状态后重试采集，再重新生成调研结果。",
        ),
        "llm_analysis_unavailable": (
            "专家分析本轮未能完成，因此只有原始样本，尚未形成可采用的分析结论。",
            "无法确认哪些信号真正支持当前调研问题。",
            "检查模型服务可用性后重试该专家任务，或先查看原始证据。",
        ),
        "no_evidence_available": (
            "本方向没有采集到可用的内容样本。",
            "没有样本时不能判断内容趋势或给出策略建议。",
            "调整关键词、调研主体或数据源权限后重新采集。",
        ),
        "empty_result": (
            "本次检索没有返回与当前方向匹配的内容。",
            "当前关键词或范围不足以验证这个方向。",
            "补充更具体的产品、场景或竞品关键词后重新检索。",
        ),
        "evidence_not_yet_sufficient": (
            "现有样本只能说明存在一个待验证信号。",
            "它不应被扩大解读为普遍趋势或策略结论。",
            "补充正文、评论或独立作者样本，并复核引用后再采用。",
        ),
        "no_usable_evidence": (
            "当前没有足以支撑该方向的可用证据。",
            "系统不会在证据不足时生成看似确定的结论。",
            "先恢复采集或补充有效样本，再重新运行该方向。",
        ),
    }
    return mapping.get(reason, (
        "现有证据仍有关键缺口，暂不适合直接采用这条观察。",
        "缺口会限制结论的适用范围和可靠性。",
        "查看对应证据包，补充缺失样本后重新验证该方向。",
    ))


def _dedupe_limitations(limitations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for item in limitations:
        key = (str(item.get("result_item_id") or ""), str(item.get("reason") or ""))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
