"""Build deterministic subagent finding summaries."""

from __future__ import annotations

import hashlib
from typing import Any


class FindingSummarizer:
    def summarize(
        self,
        *,
        direction_id: str,
        direction_label: str,
        facts: list[dict[str, Any]],
        missing_evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        evidence_refs = [str(fact["evidence_id"]) for fact in facts if fact.get("evidence_id")]
        summary = _summary(direction_label, facts, missing_evidence)
        finding = {
            "schema_version": "content_research_subagent_finding_v1",
            "direction_id": direction_id,
            "finding_id": _finding_id(direction_id, evidence_refs, missing_evidence),
            "summary": summary,
            "supporting_fact_ids": [str(fact["fact_id"]) for fact in facts if fact.get("fact_id")],
            "evidence_refs": evidence_refs,
            "missing_evidence": missing_evidence,
            "evidence_boundary_hint": "partially_supported" if evidence_refs and not missing_evidence else "signal",
        }
        if not finding["evidence_refs"] and not finding["missing_evidence"]:
            finding["missing_evidence"] = [
                {
                    "schema_version": "content_research_missing_evidence_v1",
                    "reason": "no_evidence_available",
                    "message": "No evidence was available for this direction.",
                }
            ]
        return finding


def _summary(direction_label: str, facts: list[dict[str, Any]], missing_evidence: list[dict[str, Any]]) -> str:
    if facts:
        return f"已收集 {len(facts)} 条与「{direction_label}」相关的内容样本，待结合正文与评论进一步验证具体结论。"
    if missing_evidence:
        reason = str(missing_evidence[0].get("reason") or "missing evidence")
        return f"{direction_label}: insufficient evidence ({reason})."
    return f"{direction_label}: insufficient evidence."


def _finding_id(direction_id: str, evidence_refs: list[str], missing_evidence: list[dict[str, Any]]) -> str:
    encoded = repr([direction_id, evidence_refs, missing_evidence]).encode("utf-8")
    return f"fnd_{hashlib.sha256(encoded).hexdigest()[:20]}"
