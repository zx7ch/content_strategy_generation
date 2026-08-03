"""Deterministic report-grounding checks and bounded semantic-audit contract."""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, Protocol

from app.content_research.analysis import DirectionalAnalysisLLM
from app.content_research.llm_scope import content_research_llm_context
from app.content_research.models import ResearchResultSnapshotRecord
from app.content_research.reporting.composer import (
    _limitation_ids as _composed_limitation_ids,
)
from app.content_research.reporting.composer import (
    _recovery_ids as _composed_recovery_ids,
)
from app.content_research.reporting.composer import _scope_card_id
from app.content_research.reporting.contracts import ReportDraft
from app.services.llm.pricing import DEFAULT_PRICING, PricingCalculator
from app.services.llm.failures import LLMProviderFailure
from app.services.llm.types import LLMRequest, Message


@dataclass(frozen=True)
class SemanticAuditResult:
    state: str
    reason_codes: tuple[str, ...] = ()
    affected_section_ids: tuple[str, ...] = ()
    model_version: str | None = None
    prompt_version: str | None = None
    usage: dict[str, int | float | bool | str] | None = None


class ReportSemanticAuditor(Protocol):
    def audit(
        self, snapshot: ResearchResultSnapshotRecord, draft: ReportDraft
    ) -> SemanticAuditResult | Any: ...


class UnavailableReportSemanticAuditor:
    """Safe default when no bounded semantic-review provider is configured."""

    def audit(
        self, _snapshot: ResearchResultSnapshotRecord, _draft: ReportDraft
    ) -> SemanticAuditResult:
        return SemanticAuditResult("unavailable", ("semantic_audit_unavailable",))


class LLMReportSemanticAuditor:
    """Bounded JSON-only semantic review; it cannot alter report evidence or prose."""

    _PROMPT_VERSION = "report_semantic_audit_v1"

    def __init__(self, llm: DirectionalAnalysisLLM) -> None:
        self._llm = llm

    async def audit(
        self, snapshot: ResearchResultSnapshotRecord, draft: ReportDraft
    ) -> SemanticAuditResult:
        try:
            response = await self._llm.generate(
                LLMRequest(
                    messages=[
                        Message(
                            role="system",
                            content=(
                                "You are a bounded report-faithfulness reviewer. Return JSON only with exactly "
                                "state (passed|failed), reason_codes (array of strings), affected_section_ids "
                                "(array of supplied IDs). Check paraphrase, scope expansion, unsupported entities "
                                "or comparisons, causal language, and aggregate wording. Never infer or admit facts."
                            ),
                        ),
                        Message(
                            role="user",
                            content=json.dumps(
                                _semantic_input(snapshot, draft), ensure_ascii=False, sort_keys=True
                            ),
                        ),
                    ],
                    task_type="content_research.report_semantic_audit",
                    model_policy="quality",
                    temperature=0,
                    max_tokens=500,
                    response_format={"type": "json_object"},
                    context=content_research_llm_context(
                        snapshot.metadata,
                        session_id=snapshot.workflow_run_id,
                        workflow_run_id=snapshot.workflow_run_id,
                        step_name="report_faithfulness",
                        agent_name="report_semantic_auditor",
                    ),
                )
            )
            payload = json.loads(response.content)
            result = _parse_semantic_payload(payload, draft)
            if result is None:
                return SemanticAuditResult("unavailable", ("semantic_audit_invalid_response",))
            pricing_key = f"{response.provider.lower()}:{response.model}"
            if pricing_key not in DEFAULT_PRICING:
                return SemanticAuditResult(
                    "unavailable",
                    ("semantic_audit_cost_unknown",),
                    model_version=response.model,
                    prompt_version=self._PROMPT_VERSION,
                    usage={"total_tokens": response.usage.total_tokens, "cost_unknown": True},
                )
            cost = PricingCalculator().calculate(
                provider=response.provider,
                model=response.model,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
            )
            return SemanticAuditResult(
                result.state,
                result.reason_codes,
                result.affected_section_ids,
                model_version=response.model,
                prompt_version=self._PROMPT_VERSION,
                usage={
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                    "cost_usd": cost.total_cost,
                    "cost_unknown": False,
                },
            )
        except LLMProviderFailure as exc:
            return SemanticAuditResult(
                "unavailable",
                (exc.code,),
                model_version=exc.model,
                prompt_version=self._PROMPT_VERSION,
            )
        except Exception:
            return SemanticAuditResult("unavailable", ("semantic_audit_provider_unavailable",))


@dataclass(frozen=True)
class FaithfulnessEvaluation:
    passed: bool
    reason_codes: tuple[str, ...]
    affected_section_ids: tuple[str, ...]
    semantic_result: SemanticAuditResult


class ReportFaithfulnessEvaluator:
    """Reject report prose that cannot be resolved to the immutable snapshot."""

    async def evaluate(
        self,
        snapshot: ResearchResultSnapshotRecord,
        draft: ReportDraft,
        semantic_auditor: ReportSemanticAuditor,
    ) -> FaithfulnessEvaluation:
        deterministic_reasons, affected = self._deterministic(snapshot, draft)
        policy_scope = (snapshot.metadata.get("governed_snapshot") or {}).get("policy_scope") or {}
        if policy_scope.get("report_compose_mode") == "template_only":
            semantic = SemanticAuditResult("not_applicable", ())
            return FaithfulnessEvaluation(
                passed=not deterministic_reasons,
                reason_codes=tuple(sorted(set(deterministic_reasons))),
                affected_section_ids=tuple(sorted(set(affected))),
                semantic_result=semantic,
            )
        if (
            not deterministic_reasons
            and isinstance(semantic_auditor, LLMReportSemanticAuditor)
            and _is_direct_observation_draft(snapshot, draft)
        ):
            # A direct observation is not model-authored prose: every line is
            # an admitted statement and the deterministic audit already proves
            # its claim/citation identity.  Asking an LLM to reinterpret the
            # same verbatim source text caused non-reproducible false positives
            # in live Gate 2 runs.  Transformed prose still takes the bounded
            # semantic-review path below.
            semantic = SemanticAuditResult("not_applicable", ())
            return FaithfulnessEvaluation(
                passed=True,
                reason_codes=(),
                affected_section_ids=(),
                semantic_result=semantic,
            )
        try:
            semantic = semantic_auditor.audit(snapshot, draft)
            if inspect.isawaitable(semantic):
                semantic = await semantic
        except Exception:  # bounded semantic providers must never make a complete report
            semantic = SemanticAuditResult("unavailable", ("semantic_audit_exception",))
        if not isinstance(semantic, SemanticAuditResult) or semantic.state not in {
            "passed",
            "failed",
            "unavailable",
        }:
            semantic = SemanticAuditResult("unavailable", ("semantic_audit_invalid_response",))
        if semantic.state == "passed" and (
            not semantic.model_version or not semantic.prompt_version or semantic.usage is None
        ):
            semantic = SemanticAuditResult("unavailable", ("semantic_audit_metadata_missing",))
        if semantic.usage and (
            semantic.usage.get("cost_unknown") or semantic.usage.get("budget_state") == "exhausted"
        ):
            semantic = SemanticAuditResult(
                "unavailable",
                semantic.reason_codes or ("semantic_audit_cost_or_budget_unavailable",),
                semantic.affected_section_ids,
                semantic.model_version,
                semantic.prompt_version,
                semantic.usage,
            )
        semantic_reasons = (
            ()
            if semantic.state == "passed"
            else semantic.reason_codes
            or (
                "semantic_audit_unavailable"
                if semantic.state == "unavailable"
                else "semantic_audit_failed",
            )
        )
        return FaithfulnessEvaluation(
            passed=not deterministic_reasons and semantic.state == "passed",
            reason_codes=tuple(sorted(set(deterministic_reasons + semantic_reasons))),
            affected_section_ids=tuple(sorted(set(affected + semantic.affected_section_ids))),
            semantic_result=semantic,
        )

    def _deterministic(
        self, snapshot: ResearchResultSnapshotRecord, draft: ReportDraft
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        governed = snapshot.metadata.get("governed_snapshot")
        if snapshot.schema_version != "content_research_governed_snapshot_v2" or not isinstance(
            governed, dict
        ):
            return ("governed_snapshot_invalid",), ()
        cards = {
            item.get("claim_candidate_id"): item
            for item in governed.get("claim_cards", [])
            if isinstance(item, dict)
        }
        citations = {
            item.get("citation_group_id"): item
            for item in governed.get("citation_groups", [])
            if isinstance(item, dict)
        }
        aggregates = {
            item.get("aggregate_claim_id"): item
            for item in governed.get("aggregate_claims", [])
            if isinstance(item, dict)
        }
        cross = {
            item.get("cross_direction_record_id"): item
            for item in governed.get("cross_direction_records", [])
            if isinstance(item, dict)
        }
        weak = {
            item.get("weak_signal_id"): item
            for item in governed.get("weak_signals", [])
            if isinstance(item, dict)
        }
        limitations = _limitation_ids(snapshot, governed)
        reasons: list[str] = []
        affected: list[str] = []
        for section in draft.sections:
            section_reasons: list[str] = []
            if any(item not in cards for item in section.claim_candidate_ids):
                section_reasons.append("claim_reference_unknown")
            if any(item not in aggregates for item in section.aggregate_claim_ids):
                section_reasons.append("aggregate_reference_unknown")
            if any(item not in cross for item in section.cross_direction_record_ids):
                section_reasons.append("cross_direction_reference_unknown")
            if any(item not in weak for item in section.weak_signal_ids):
                section_reasons.append("weak_signal_reference_unknown")
            if any(item not in limitations for item in section.limitation_ids):
                section_reasons.append("limitation_reference_unknown")
            if any(item not in citations for item in section.citation_group_ids):
                section_reasons.append("citation_reference_unknown")
            if any(
                cards[item].get("admission_state") != "admitted"
                for item in section.claim_candidate_ids
                if item in cards
            ):
                section_reasons.append("claim_state_not_admitted")
            if any(
                not _valid_card_scope(cards[item], governed)
                for item in section.claim_candidate_ids
                if item in cards
            ):
                section_reasons.append("claim_scope_invalid")
            if any(
                not _valid_card_metrics(cards[item])
                for item in section.claim_candidate_ids
                if item in cards
            ):
                section_reasons.append("computed_metrics_invalid")
            for aggregate_id in section.aggregate_claim_ids:
                aggregate = aggregates.get(aggregate_id)
                source_ids = (
                    aggregate.get("source_claim_ids") if isinstance(aggregate, dict) else None
                )
                if (
                    not isinstance(source_ids, list)
                    or not source_ids
                    or any(item not in cards for item in source_ids)
                ):
                    section_reasons.append("aggregate_derivation_invalid")
            if section.prose:
                allowed = {
                    str(cards[item].get("statement"))
                    for item in section.claim_candidate_ids
                    if item in cards
                }
                material = [line for line in section.prose.split("\n") if line]
                if not material or any(line not in allowed for line in material):
                    section_reasons.append("prose_not_direct_admitted_statement")
                if any(
                    term in section.prose.lower()
                    for term in ("导致", "造成", "因果", "therefore", "causes")
                ):
                    section_reasons.append("causal_language_forbidden")
                for anchor in section.citation_anchors:
                    group = citations.get(anchor.citation_group_id)
                    refs = group.get("evidence_refs") if isinstance(group, dict) else None
                    if (
                        not isinstance(refs, list)
                        or not refs
                        or any(not _valid_citation_ref(ref) for ref in refs)
                    ):
                        section_reasons.append("citation_quote_hash_or_url_invalid")
                        break
                    if group.get("claim_candidate_id") not in section.claim_candidate_ids:
                        section_reasons.append("citation_claim_ownership_invalid")
                        break
            if section_reasons:
                reasons.extend(section_reasons)
                affected.append(section.section_id)
        return tuple(sorted(set(reasons))), tuple(sorted(set(affected)))


def _is_direct_observation_draft(
    snapshot: ResearchResultSnapshotRecord, draft: ReportDraft
) -> bool:
    """Whether deterministic evidence identity fully covers every prose line."""
    governed = snapshot.metadata.get("governed_snapshot")
    if not isinstance(governed, dict):
        return False
    cards = {
        str(item.get("claim_candidate_id")): str(item.get("statement"))
        for item in governed.get("claim_cards", [])
        if isinstance(item, dict)
        and isinstance(item.get("claim_candidate_id"), str)
        and isinstance(item.get("statement"), str)
    }
    for section in draft.sections:
        if not section.prose:
            continue
        if (
            not section.claim_candidate_ids
            or section.aggregate_claim_ids
            or section.cross_direction_record_ids
            or section.weak_signal_ids
        ):
            return False
        allowed = {cards.get(claim_id) for claim_id in section.claim_candidate_ids}
        lines = [line for line in section.prose.split("\n") if line]
        if not lines or any(line not in allowed for line in lines):
            return False
    return True


def _semantic_input(
    snapshot: ResearchResultSnapshotRecord, draft: ReportDraft
) -> dict[str, object]:
    governed = snapshot.metadata["governed_snapshot"]
    cards = [
        {
            key: item.get(key)
            for key in (
                "claim_candidate_id",
                "statement",
                "scope",
                "computed_metrics",
                "admission_state",
            )
        }
        for item in governed.get("claim_cards", [])
        if isinstance(item, dict)
    ]
    aggregates = [
        {
            key: item.get(key)
            for key in (
                "aggregate_claim_id",
                "aggregate_type",
                "source_claim_ids",
                "request_origin",
            )
        }
        for item in governed.get("aggregate_claims", [])
        if isinstance(item, dict)
    ]
    return {
        "prompt_version": LLMReportSemanticAuditor._PROMPT_VERSION,
        "draft_id": draft.id,
        "sections": [
            {
                "section_id": s.section_id,
                "section_kind": s.section_kind,
                "prose": s.prose,
                "claim_candidate_ids": s.claim_candidate_ids,
                "aggregate_claim_ids": s.aggregate_claim_ids,
            }
            for s in draft.sections
        ],
        "admitted_claim_cards": cards,
        "aggregate_claims": aggregates,
        "policy_scope": governed.get("policy_scope", {}),
    }


def _parse_semantic_payload(payload: object, draft: ReportDraft) -> SemanticAuditResult | None:
    if not isinstance(payload, dict) or set(payload) != {
        "state",
        "reason_codes",
        "affected_section_ids",
    }:
        return None
    state, reasons, affected = (
        payload.get("state"),
        payload.get("reason_codes"),
        payload.get("affected_section_ids"),
    )
    if (
        state not in {"passed", "failed"}
        or not isinstance(reasons, list)
        or not isinstance(affected, list)
    ):
        return None
    if any(not isinstance(item, str) or not item for item in reasons + affected):
        return None
    valid_sections = {section.section_id for section in draft.sections}
    if not set(affected).issubset(valid_sections):
        return None
    if state == "passed" and (reasons or affected):
        return None
    return SemanticAuditResult(state, tuple(sorted(set(reasons))), tuple(sorted(set(affected))))


def _valid_citation_ref(ref: object) -> bool:
    if not isinstance(ref, dict):
        return False
    quote, source_hash = ref.get("quote"), ref.get("source_text_hash")
    return (
        bool(quote and ref.get("field_path") and ref.get("source_url"))
        and isinstance(ref.get("text_start"), int)
        and isinstance(ref.get("text_end"), int)
        and ref["text_start"] >= 0
        and ref["text_end"] - ref["text_start"] == len(quote)
        and isinstance(source_hash, str)
        and len(source_hash) == 64
        and all(char in "0123456789abcdef" for char in source_hash.lower())
    )


def _valid_card_metrics(card: dict[str, Any]) -> bool:
    metrics = card.get("computed_metrics")
    return metrics is None or isinstance(metrics, dict)


def _valid_card_scope(card: dict[str, Any], governed: dict[str, Any]) -> bool:
    scope = card.get("scope")
    policy_scope = governed.get("policy_scope")
    return scope is None or (isinstance(scope, dict) and isinstance(policy_scope, dict))


def _limitation_ids(snapshot: ResearchResultSnapshotRecord, governed: dict[str, Any]) -> set[str]:
    limitations = governed.get("limitations_recovery")
    if not isinstance(limitations, list):
        return set()
    values = [item for item in limitations if isinstance(item, dict)]
    result = set(
        _composed_limitation_ids(snapshot, values) + _composed_recovery_ids(snapshot, values)
    )
    if not values:
        policy_scope = governed.get("policy_scope")
        if isinstance(policy_scope, dict):
            # Composer's no-limitation fallback is itself a governed scope
            # reference.  Treating it as unknown made every otherwise-valid
            # report fail its deterministic audit.
            result.add(_scope_card_id(snapshot, policy_scope))
    return result
