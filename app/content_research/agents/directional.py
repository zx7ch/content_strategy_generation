"""Directional subagents with persisted evidence derivation and verification."""

from __future__ import annotations

import hashlib
from typing import Any

from app.content_research.agents.base import (
    SubagentExecutionContext,
    SubagentExecutionResult,
    SubagentFinding,
)
from app.content_research.compression import EvidenceFactExtractor, FindingSummarizer
from app.content_research.analysis import DirectionalAnalysisService
from app.content_research.evidence import EvidenceBundleItemRecord, EvidenceBundleRecord, EvidenceBundleService, EvidenceService
from app.content_research.sources import SourceCollectionRequest


class DirectionalResearchAgent:
    agent_name = "DirectionalResearchAgent"
    agent_version = "p1_directional_verified_v1"
    direction_id = ""
    direction_label = "Content Research"

    def __init__(
        self,
        *,
        evidence_service: EvidenceService,
        bundle_service: EvidenceBundleService,
        fact_extractor: EvidenceFactExtractor | None = None,
        finding_summarizer: FindingSummarizer | None = None,
        analysis_service: DirectionalAnalysisService | None = None,
    ) -> None:
        self._evidence_service = evidence_service
        self._bundle_service = bundle_service
        self._fact_extractor = fact_extractor or EvidenceFactExtractor()
        self._finding_summarizer = finding_summarizer or FindingSummarizer()
        self._analysis_service = analysis_service

    async def execute(self, context: SubagentExecutionContext) -> SubagentExecutionResult:
        task_payload = context.task.payload
        input_payload = dict(task_payload.get("input_payload") or {})
        direction = dict(input_payload.get("direction") or {})
        direction_id = str(direction.get("id") or context.task.direction_id or self.direction_id)
        direction_label = str(direction.get("label") or self.direction_label)
        query = context.query or _query_from_input(input_payload, direction_label)

        # Directional specialists intentionally do not reuse the parent
        # workflow's generic search response. Their evidence must be tied to
        # their own question and query for a meaningful comparison later.
        source_result = await context.source_registry.get(context.provider).collect(
            SourceCollectionRequest(
                workflow_run_id=context.task.workflow_run_id,
                query=query,
                source_kind=context.source_kind,
                limit=context.limit,
                context={
                    "subagent_task_id": context.task.id,
                    "direction_id": direction_id,
                    "plan_id": context.task.plan_id,
                },
            )
        )

        missing_evidence = _missing_evidence_from_source_result(source_result.status, source_result.failure_reason)
        evidence_records = []
        for item in source_result.items:
            if not isinstance(item, dict):
                continue
            evidence_records.append(
                self._evidence_service.ingest_source_payload(
                    workflow_run_id=context.task.workflow_run_id,
                    research_plan_id=context.task.plan_id,
                    research_direction_id=context.task.direction_id or direction_id,
                    subagent_task_id=context.task.id,
                    source_payload=item,
                )
            )

        facts = self._fact_extractor.extract(evidence_records)
        missing_evidence.extend(self._fact_extractor.missing_evidence_for_records(evidence_records))
        missing_evidence.extend(_direction_quality_gaps(direction, facts))
        evidence_record_by_id = {record.id: record for record in evidence_records}
        fact_records_by_source_id = {
            str(fact.get("evidence_id")): self._evidence_service.derive_fact_evidence(
                parent=evidence_record_by_id[str(fact.get("evidence_id"))],
                fact=fact,
            )
            for fact in facts
            if str(fact.get("evidence_id")) in evidence_record_by_id
        }
        finding_payload = self._finding_summarizer.summarize(
            direction_id=direction_id,
            direction_label=direction_label,
            facts=facts,
            missing_evidence=missing_evidence,
        )
        analysis = await self._analysis_service.analyze(
            task=context.task, direction=direction, query=query, facts=facts,
        ) if self._analysis_service is not None and facts else None
        if analysis is not None:
            finding_payload["summary"] = analysis["summary"] or finding_payload["summary"]
            finding_payload["evidence_refs"] = analysis["evidence_refs"]
            finding_payload["missing_evidence"] = [*finding_payload["missing_evidence"], *analysis["missing_evidence"]]
            finding_payload["observations"] = analysis["observations"]
            finding_payload["analysis_mode"] = "llm"
        elif self._analysis_service is not None:
            finding_payload["missing_evidence"].append({
                "schema_version": "content_research_missing_evidence_v1",
                "reason": "llm_analysis_unavailable",
                "message": "方向分析暂不可用；当前只能查看原始样本，不能形成可采用结论。",
            })
        finding = SubagentFinding(
            finding_id=finding_payload["finding_id"],
            summary=finding_payload["summary"],
            evidence_refs=list(finding_payload["evidence_refs"]),
            supporting_fact_ids=list(finding_payload["supporting_fact_ids"]),
            missing_evidence=list(finding_payload["missing_evidence"]),
            evidence_boundary_hint=str(finding_payload["evidence_boundary_hint"]),
            payload=finding_payload,
        )

        cited_fact_records = [
            fact_records_by_source_id[evidence_id]
            for evidence_id in finding.evidence_refs
            if evidence_id in fact_records_by_source_id
        ]
        finding_record = self._evidence_service.derive_finding_evidence(
            task_id=context.task.id,
            workflow_run_id=context.task.workflow_run_id,
            research_plan_id=context.task.plan_id,
            research_direction_id=context.task.direction_id or direction_id,
            finding_id=finding.finding_id,
            summary=finding.summary,
            supporting_facts=cited_fact_records,
        )
        verification_missing = _verification_missing_evidence(
            cited_fact_records=cited_fact_records,
            raw_evidence_records=evidence_records,
            evidence_service=self._evidence_service,
        )
        if verification_missing:
            finding.missing_evidence.extend(verification_missing)
            finding.payload["missing_evidence"] = list(finding.missing_evidence)
        bundle = self._create_bundle(
            context,
            direction_id,
            direction_label,
            finding,
            evidence_records,
            list(fact_records_by_source_id.values()),
            cited_fact_records,
            finding_record,
        )
        status = _status_for_result(source_result.status, evidence_records, finding.missing_evidence)
        return SubagentExecutionResult(
            status=status,
            findings=[finding],
            evidence_records=evidence_records,
            missing_evidence=finding.missing_evidence,
            evidence_bundle=bundle,
            failure_reason=source_result.failure_reason,
            metadata={
                "source_status": source_result.status,
                "source_kind": source_result.source_kind,
                "source_failure_reason": source_result.failure_reason,
                "source_query": query,
            },
        )

    def _create_bundle(
        self,
        context: SubagentExecutionContext,
        direction_id: str,
        direction_label: str,
        finding: SubagentFinding,
        evidence_records: list,
        fact_records: list,
        cited_fact_records: list,
        finding_record,
    ) -> EvidenceBundleRecord:
        bundle_id = _stable_id("eb", context.task.id, finding.finding_id)
        cited_fact_ids = {record.id for record in cited_fact_records}
        independence_keys = {
            self._evidence_service.source_independence_key(record)
            for record in cited_fact_records
        }
        fact_by_evidence_id = {str(fact["evidence_id"]): fact for fact in facts if fact.get("evidence_id")}
        fact_record_by_source_id = {
            str(record.normalized_payload.get("source_evidence_id")): record
            for record in fact_records
        }
        supported_claim_count = 1 if cited_fact_ids else 0
        citation_coverage_score = float(supported_claim_count)
        contradiction_summary = _contradiction_summary(cited_fact_records)
        accepted_evidence_count = len(facts)
        bundle = EvidenceBundleRecord(
            id=bundle_id,
            workflow_run_id=context.task.workflow_run_id,
            research_plan_id=context.task.plan_id,
            research_direction_id=context.task.direction_id or direction_id,
            schema_version="content_research_evidence_bundle_v1",
            status="ready" if cited_fact_ids else "insufficient",
            bundle_type="research_direction",
            bundle_version="p1_subagent_v1",
            summary=finding.summary,
            coverage={
                "schema_version": "content_research_bundle_coverage_v1",
                "source_count": len(evidence_records),
                "accepted_evidence_count": accepted_evidence_count,
                "independent_source_count": len(independence_keys),
                "candidate_lead_count": max(0, len(evidence_records) - accepted_evidence_count),
                "direction_coverage": {direction_id: "covered" if accepted_evidence_count else "insufficient"},
            },
            cross_source_metrics={
                "schema_version": "content_research_cross_source_metrics_v1",
                "independent_source_count": len(independence_keys),
                "source_independence_keys": sorted(independence_keys),
                "cited_fact_count": len(cited_fact_ids),
                "candidate_lead_count": max(0, len(evidence_records) - accepted_evidence_count),
            },
            contradiction_summary=contradiction_summary,
            citation_coverage={
                "schema_version": "content_research_citation_coverage_v1",
                "citation_coverage_score": citation_coverage_score,
                "claim_count": 1,
                "supported_claim_count": supported_claim_count,
                "cited_fact_count": len(cited_fact_ids),
                "claim_without_citation_ids": [] if cited_fact_ids else [finding.finding_id],
            },
            unsupported_claim_count=0 if cited_fact_ids else 1,
            missing_evidence=finding.missing_evidence,
            metadata={
                "schema_version": "content_research_evidence_bundle_metadata_v1",
                "finding": {
                    "finding_id": finding.finding_id,
                    "summary": finding.summary,
                    "supporting_fact_ids": finding.supporting_fact_ids,
                    "evidence_refs": finding.evidence_refs,
                    "evidence_boundary_hint": finding.evidence_boundary_hint,
                    "observations": finding.payload.get("observations") or [],
                    "analysis_mode": finding.payload.get("analysis_mode") or "deterministic",
                },
                "fact_evidence_map": [
                    {
                        "fact_id": fact.get("fact_id"),
                        "source_evidence_id": fact.get("evidence_id"),
                        "fact_evidence_id": fact_record_by_source_id.get(str(fact.get("evidence_id"))).id
                        if fact_record_by_source_id.get(str(fact.get("evidence_id"))) else None,
                        "claim": fact.get("claim"),
                        "evidence_quote": fact.get("evidence_quote"),
                        "evidence_quality": fact.get("evidence_quality"),
                    }
                    for fact in fact_by_evidence_id.values()
                ],
            },
        )
        items = [
            EvidenceBundleItemRecord(
                id=_stable_id("ebi", bundle_id, finding_record.id),
                bundle_id=bundle_id,
                evidence_record_id=finding_record.id,
                role="finding_claim",
                sort_order=1,
                schema_version="content_research_evidence_bundle_item_v1",
                payload={
                    "schema_version": "content_research_evidence_bundle_item_payload_v1",
                    "finding_id": finding.finding_id,
                },
            ),
        ]
        items.extend(
            EvidenceBundleItemRecord(
                id=_stable_id("ebi", bundle_id, record.id),
                bundle_id=bundle_id,
                evidence_record_id=record.id,
                role="supporting_fact",
                sort_order=index + 1,
                schema_version="content_research_evidence_bundle_item_v1",
                payload={
                    "schema_version": "content_research_evidence_bundle_item_payload_v1",
                    "finding_id": finding.finding_id,
                    "direction_id": direction_id,
                    "fact_evidence_id": record.id,
                },
            )
            for index, record in enumerate(cited_fact_records, start=1)
        )
        items.extend(
            EvidenceBundleItemRecord(
                id=_stable_id("ebi", bundle_id, "context", record.id),
                bundle_id=bundle_id,
                evidence_record_id=record.id,
                role="context_evidence",
                sort_order=index + len(cited_fact_records) + 1,
                schema_version="content_research_evidence_bundle_item_v1",
                payload={"schema_version": "content_research_evidence_bundle_item_payload_v1"},
            )
            for index, record in enumerate(evidence_records, start=1)
        )
        for offset, missing in enumerate(finding.missing_evidence, start=len(items) + 1):
            items.append(
                EvidenceBundleItemRecord(
                    id=_stable_id("ebi", bundle_id, f"missing_{offset}", missing),
                    bundle_id=bundle_id,
                    evidence_record_id=None,
                    role="missing_evidence",
                    sort_order=offset,
                    schema_version="content_research_evidence_bundle_item_v1",
                    payload=missing,
                )
            )
        return self._bundle_service.create_bundle(bundle, items)


class ProductMarketingResearchAgent(DirectionalResearchAgent):
    agent_name = "ProductMarketingResearchAgent"
    direction_id = "product_marketing"
    direction_label = "产品营销"


class CompetitorDiscoveryAgent(DirectionalResearchAgent):
    agent_name = "CompetitorDiscoveryAgent"
    direction_id = "competitor_discovery"
    direction_label = "竞品品牌"


class UGCCommunityResearchAgent(DirectionalResearchAgent):
    agent_name = "UGCCommunityResearchAgent"
    direction_id = "ugc_community"
    direction_label = "UGC 社群互动"


class CommentInsightAgent(DirectionalResearchAgent):
    agent_name = "CommentInsightAgent"
    direction_id = "comment_insight"
    direction_label = "用户评论痛点"


class BrandActivityResearchAgent(DirectionalResearchAgent):
    agent_name = "BrandActivityResearchAgent"
    direction_id = "brand_activity"
    direction_label = "品牌活动"


class DecisionDrivenDeepResearchAgent(DirectionalResearchAgent):
    """Executes a user-selected follow-up using the normal evidence pipeline."""

    agent_name = "DecisionDrivenDeepResearchAgent"
    direction_id = "decision_deep_research"
    direction_label = "用户选择的深度调研"


class KeywordGrowthResearchAgent(DirectionalResearchAgent):
    agent_name = "KeywordGrowthResearchAgent"
    direction_id = "keyword_growth"
    direction_label = "高增长关键词"


class ContentPerformanceResearchAgent(DirectionalResearchAgent):
    agent_name = "ContentPerformanceResearchAgent"
    direction_id = "content_performance"
    direction_label = "小红书内容表现"


def build_default_subagent_registry(
    *,
    evidence_service: EvidenceService,
    bundle_service: EvidenceBundleService,
    analysis_service: DirectionalAnalysisService | None = None,
) -> dict[str, DirectionalResearchAgent]:
    agents = [
        ProductMarketingResearchAgent,
        CompetitorDiscoveryAgent,
        UGCCommunityResearchAgent,
        CommentInsightAgent,
        BrandActivityResearchAgent,
        DecisionDrivenDeepResearchAgent,
        KeywordGrowthResearchAgent,
        ContentPerformanceResearchAgent,
    ]
    return {
        cls.agent_name: cls(evidence_service=evidence_service, bundle_service=bundle_service, analysis_service=analysis_service)
        for cls in agents
    }


def _query_from_input(input_payload: dict[str, Any], fallback: str) -> str:
    subject = str(input_payload.get("confirmed_subject") or "").strip()
    question = str(input_payload.get("custom_research_question") or "").strip()
    return " ".join(item for item in [subject, question or fallback] if item).strip() or fallback


def _missing_evidence_from_source_result(status: str, failure_reason: str | None) -> list[dict[str, Any]]:
    if status == "completed":
        return []
    return [
        {
            "schema_version": "content_research_missing_evidence_v1",
            "reason": failure_reason or status,
            "message": "Source collection did not produce complete usable evidence.",
        }
    ]


def _status_for_result(status: str, evidence_records: list, missing_evidence: list[dict[str, Any]]) -> str:
    # Verification gaps constrain the bundle's evidence state, not task
    # execution. A collected direction must not remain "in progress" merely
    # because it needs corroboration.
    if evidence_records and status == "completed":
        return "completed"
    if evidence_records or missing_evidence:
        return "partial_completed"
    return "failed"


def _verification_missing_evidence(
    *,
    cited_fact_records: list,
    raw_evidence_records: list,
    evidence_service: EvidenceService,
) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    if not cited_fact_records:
        missing.append({
            "schema_version": "content_research_missing_evidence_v1",
            "reason": "claim_without_citation",
            "message": "该方向尚无可追溯到原始内容的引用事实；请补充可引用的正文或样本。",
        })
        return missing
    independence_count = len({
        evidence_service.source_independence_key(record) for record in cited_fact_records
    })
    if independence_count < 2:
        missing.append({
            "schema_version": "content_research_missing_evidence_v1",
            "reason": "insufficient_independent_sources",
            "message": "当前引用主要来自同一作者或同一来源；请补充至少一个独立作者或独立来源的样本进行交叉验证。",
            "independent_source_count": independence_count,
        })
    if not raw_evidence_records:
        missing.append({
            "schema_version": "content_research_missing_evidence_v1",
            "reason": "no_evidence_available",
            "message": "未采集到原始内容样本，无法验证该方向。",
        })
    return missing


def _direction_quality_gaps(direction: dict[str, Any], facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    direction_id = str(direction.get("id") or "").strip()
    expected = {str(item) for item in direction.get("expected_evidence_types") or []}
    qualities = [dict(fact.get("evidence_quality") or {}) for fact in facts]
    questions = [str(item) for item in direction.get("questions") or [] if str(item).strip()]

    needs_comment = "comment" in expected or direction_id == "comment_insight"
    has_comment = any(quality.get("has_comment_excerpt") for quality in qualities)
    if needs_comment and facts and not has_comment:
        gaps.append({
            "schema_version": "content_research_missing_evidence_v1",
            "reason": "expected_comment_evidence_missing",
            "message": "该方向需要评论原文或评论摘要支撑，但当前可用事实未包含评论片段。",
            "questions": questions,
        })

    needs_metric = "metric_snapshot" in expected or direction_id in {"keyword_growth", "content_performance"}
    has_metric = any(quality.get("has_metric_signal") for quality in qualities)
    if needs_metric and facts and not has_metric:
        gaps.append({
            "schema_version": "content_research_missing_evidence_v1",
            "reason": "expected_metric_evidence_missing",
            "message": "该方向需要互动指标或趋势指标支撑，但当前可用事实未包含明确指标信号。",
            "questions": questions,
        })

    return gaps


def _contradiction_summary(fact_records: list) -> dict[str, Any]:
    groups: dict[str, set[str]] = {}
    for record in fact_records:
        group_id = str(record.metadata.get("contradiction_group_id") or "").strip()
        polarity = str(record.metadata.get("claim_polarity") or "").strip().lower()
        if group_id and polarity in {"positive", "negative"}:
            groups.setdefault(group_id, set()).add(polarity)
    unresolved = sorted(group_id for group_id, polarities in groups.items() if len(polarities) > 1)
    return {
        "schema_version": "content_research_contradiction_summary_v1",
        "has_unresolved_contradiction": bool(unresolved),
        "unresolved_group_ids": unresolved,
        "checked_group_count": len(groups),
    }


def _stable_id(prefix: str, *parts: Any) -> str:
    encoded = repr(parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"
