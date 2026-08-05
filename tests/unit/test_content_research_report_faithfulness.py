import asyncio
from dataclasses import replace

from app.content_research.reporting.composer import ResearchReportComposer
from app.content_research.reporting.faithfulness import (
    LLMReportSemanticAuditor,
    ReportFaithfulnessEvaluator,
    SemanticAuditResult,
)
from app.services.llm.types import LLMResponse, TokenUsage
from tests.unit.test_content_research_report_composer import _snapshot


class AuditPass:
    def audit(self, _snapshot, _draft):
        return SemanticAuditResult("passed", model_version="test-model", prompt_version="v1", usage={"total_tokens": 1})


class AuditUnavailable:
    def audit(self, _snapshot, _draft):
        return SemanticAuditResult("unavailable", ("semantic_provider_unavailable",))


class AuditRaises:
    def audit(self, _snapshot, _draft):
        raise TimeoutError("provider timeout")


class SemanticLLM:
    async def generate(self, _request):
        return LLMResponse(
            content='{"state":"passed","reason_codes":[],"affected_section_ids":[]}',
            provider="openai", model="gpt-4o-mini", usage=TokenUsage(1, 1, 2), latency_ms=1,
        )


class UnknownCostSemanticLLM:
    async def generate(self, _request):
        return LLMResponse(
            content='{"state":"passed","reason_codes":[],"affected_section_ids":[]}',
            provider="unpriced", model="model", usage=TokenUsage(1, 1, 2), latency_ms=1,
        )


def _marketing_snapshot():
    snapshot = _snapshot()
    governed = snapshot.metadata["governed_snapshot"]
    base_card = governed["claim_cards"][0]
    base_group = governed["citation_groups"][0]
    cards = [
        {
            **base_card,
            "claim_candidate_id": f"cc_need_{index}",
            "admission_decision_id": f"cad_need_{index}",
            "admission_state": "admitted",
            "direction_id": "product_marketing",
            "statement": f"原始准入事实 {index}",
            "scope": {"sample": "selected_packets"},
        }
        for index in range(1, 4)
    ]
    groups = [
        {
            **base_group,
            "citation_group_id": f"citation_need_{index}",
            "display_index": index,
            "claim_candidate_id": f"cc_need_{index}",
            "admission_decision_id": f"cad_need_{index}",
        }
        for index in range(1, 4)
    ]
    return replace(
        snapshot,
        metadata={
            **snapshot.metadata,
            "governed_snapshot": {
                **governed,
                "policy_scope": {
                    **governed["policy_scope"],
                    "direction_ids": ["product_marketing"],
                    "report_compose_mode": "template_only",
                    "marketing_conclusion_policy": {
                        "primary_marketing_goal": "content_seeding",
                        "tracks": ["need", "value", "message"],
                        "minimum_notes_per_conclusion": 3,
                        "minimum_independent_authors_per_conclusion": 2,
                        "require_core_and_first_intent_support": True,
                        "maximum_primary_conclusions_per_track": 1,
                    },
                },
                "claim_cards": cards,
                "citation_groups": groups,
                "marketing_conclusions": [
                    {
                        "track": "need",
                        "state": "selected",
                        "candidate_id": "mc_need",
                        "statement": "高温通勤场景中的凉感需求明确",
                        "supporting_claim_ids": [
                            "cc_need_1",
                            "cc_need_2",
                            "cc_need_3",
                        ],
                        "supporting_note_count": 3,
                        "independent_author_count": 2,
                        "reason_codes": [],
                    },
                    {
                        "track": "value",
                        "state": "insufficient_evidence",
                        "reason_codes": ["conclusion_no_qualified_candidate"],
                    },
                    {
                        "track": "message",
                        "state": "analysis_unavailable",
                        "reason_codes": ["marketing_analysis_unavailable"],
                    },
                ],
            },
        },
    )


def test_template_audit_accepts_exact_selected_governed_marketing_conclusion():
    snapshot = _marketing_snapshot()
    draft = ResearchReportComposer().compose(snapshot)

    result = asyncio.run(
        ReportFaithfulnessEvaluator().evaluate(snapshot, draft, AuditUnavailable())
    )

    assert result.passed is True
    assert result.reason_codes == ()
    assert result.semantic_result.state == "not_applicable"


def test_template_audit_rejects_raw_claim_substitution_for_marketing_conclusion():
    snapshot = _marketing_snapshot()
    draft = ResearchReportComposer().compose(snapshot)
    need = next(section for section in draft.sections if section.section_kind == "marketing_need")
    raw_statement = snapshot.metadata["governed_snapshot"]["claim_cards"][0]["statement"]
    substituted = replace(
        need,
        prose=raw_statement,
        citation_anchors=tuple(
            replace(anchor, text_end=len(raw_statement))
            for anchor in need.citation_anchors
        ),
    )

    result = asyncio.run(
        ReportFaithfulnessEvaluator().evaluate(
            snapshot,
            replace(
                draft,
                sections=tuple(
                    substituted if section.section_id == need.section_id else section
                    for section in draft.sections
                ),
            ),
            AuditUnavailable(),
        )
    )

    assert result.passed is False
    assert "marketing_conclusion_prose_mismatch" in result.reason_codes


def test_template_audit_rejects_tampered_marketing_decision_support_and_citation_owner():
    snapshot = _marketing_snapshot()
    draft = ResearchReportComposer().compose(snapshot)
    governed = snapshot.metadata["governed_snapshot"]
    selected = governed["marketing_conclusions"][0]
    tampered = replace(
        snapshot,
        metadata={
            **snapshot.metadata,
            "governed_snapshot": {
                **governed,
                "marketing_conclusions": [
                    {**selected, "supporting_claim_ids": ["cc_need_1", "unknown_claim"]},
                    *governed["marketing_conclusions"][1:],
                ],
                "citation_groups": [
                    {**governed["citation_groups"][0], "claim_candidate_id": "other_claim"},
                    *governed["citation_groups"][1:],
                ],
            },
        },
    )

    result = asyncio.run(
        ReportFaithfulnessEvaluator().evaluate(tampered, draft, AuditUnavailable())
    )

    assert result.passed is False
    assert "marketing_conclusion_support_invalid" in result.reason_codes
    assert "citation_claim_ownership_invalid" in result.reason_codes


def test_direct_observation_uses_deterministic_identity_proof_without_llm_reinterpretation():
    snapshot = _snapshot()
    draft = ResearchReportComposer().compose(snapshot)

    passed = asyncio.run(ReportFaithfulnessEvaluator().evaluate(snapshot, draft, AuditPass()))
    unavailable = asyncio.run(ReportFaithfulnessEvaluator().evaluate(snapshot, draft, AuditUnavailable()))
    llm = asyncio.run(
        ReportFaithfulnessEvaluator().evaluate(
            snapshot, draft, LLMReportSemanticAuditor(SemanticLLM())
        )
    )

    assert passed.passed is True
    assert unavailable.passed is False
    assert unavailable.reason_codes == ("semantic_provider_unavailable",)
    assert llm.passed is True
    assert llm.semantic_result.state == "not_applicable"


def test_transformed_prose_still_requires_a_semantic_audit():
    snapshot = _snapshot()
    draft = ResearchReportComposer().compose(snapshot)
    finding = next(section for section in draft.sections if section.section_kind == "main_findings")
    transformed = replace(finding, aggregate_claim_ids=("ac_requested",))
    changed = replace(
        draft,
        sections=tuple(
            transformed if section.section_id == finding.section_id else section
            for section in draft.sections
        ),
    )

    result = asyncio.run(ReportFaithfulnessEvaluator().evaluate(snapshot, changed, AuditUnavailable()))

    assert result.passed is False
    assert result.reason_codes == ("semantic_provider_unavailable",)


def test_audit_rejects_tampered_statement_unknown_reference_and_causal_upgrade():
    snapshot = _snapshot()
    draft = ResearchReportComposer().compose(snapshot)
    finding = next(section for section in draft.sections if section.section_kind == "main_findings")
    prose = "这会导致销量提升。"
    tampered = replace(
        finding,
        prose=prose,
        citation_anchors=(replace(finding.citation_anchors[0], text_end=len(prose)),),
    )
    result = asyncio.run(ReportFaithfulnessEvaluator().evaluate(
        snapshot,
        replace(draft, sections=tuple(tampered if section.section_id == finding.section_id else section for section in draft.sections)),
        AuditPass(),
    ))

    assert result.passed is False
    assert "prose_not_direct_admitted_statement" in result.reason_codes
    assert "causal_language_forbidden" in result.reason_codes
    assert tampered.section_id in result.affected_section_ids


def test_audit_rejects_unadmitted_cards_invalid_aggregate_and_incomplete_citation_source():
    snapshot = _snapshot()
    governed = snapshot.metadata["governed_snapshot"]
    malformed_group = {**governed["citation_groups"][0], "evidence_refs": [{"quote": "通勤"}]}
    malformed_aggregate = {
        "aggregate_claim_id": "ac_requested",
        "aggregate_type": "action_hypothesis",
        "request_origin": "user_requested_next_steps",
        "source_claim_ids": [],
    }
    bad_snapshot = replace(
        snapshot,
        metadata={
            **snapshot.metadata,
            "governed_snapshot": {
                **governed,
                "claim_cards": [{**governed["claim_cards"][0], "admission_state": "rejected"}],
                "citation_groups": [malformed_group],
                "aggregate_claims": [malformed_aggregate],
            },
        },
    )
    result = asyncio.run(ReportFaithfulnessEvaluator().evaluate(
        bad_snapshot, ResearchReportComposer().compose(snapshot), AuditPass()
    ))

    assert result.passed is False
    assert {"claim_state_not_admitted", "aggregate_derivation_invalid", "citation_quote_hash_or_url_invalid"} <= set(result.reason_codes)


def test_audit_rejects_invalid_metric_scope_and_limitation_references():
    snapshot = _snapshot()
    governed = snapshot.metadata["governed_snapshot"]
    malformed = replace(snapshot, metadata={
        **snapshot.metadata,
        "governed_snapshot": {
            **governed,
            "claim_cards": [{**governed["claim_cards"][0], "computed_metrics": ["not-a-mapping"], "scope": "expanded"}],
        },
    })
    draft = ResearchReportComposer().compose(snapshot)
    limitations = next(section for section in draft.sections if section.section_kind == "limitations_scope")
    bad_limitations = replace(limitations, limitation_ids=("unknown_limit",))
    result = asyncio.run(ReportFaithfulnessEvaluator().evaluate(
        malformed,
        replace(draft, sections=tuple(bad_limitations if section.section_id == limitations.section_id else section for section in draft.sections)),
        AuditPass(),
    ))

    assert {"computed_metrics_invalid", "claim_scope_invalid", "limitation_reference_unknown"} <= set(result.reason_codes)


def test_audit_accepts_the_composer_scope_card_when_no_limitations_exist():
    snapshot = _snapshot()
    draft = ResearchReportComposer().compose(snapshot)

    result = asyncio.run(ReportFaithfulnessEvaluator().evaluate(snapshot, draft, AuditPass()))

    assert result.passed is True


def test_llm_semantic_auditor_uses_strict_safe_protocol_and_never_passes_unknown_cost():
    snapshot = _snapshot()
    snapshot = replace(
        snapshot,
        metadata={
            **snapshot.metadata,
            "llm_scope": {"workspace_id": "workspace-1", "user_id": "user-1"},
        },
    )
    draft = ResearchReportComposer().compose(snapshot)

    passed = asyncio.run(LLMReportSemanticAuditor(SemanticLLM()).audit(snapshot, draft))
    unknown_cost = asyncio.run(LLMReportSemanticAuditor(UnknownCostSemanticLLM()).audit(snapshot, draft))

    assert passed.state == "passed"
    assert passed.prompt_version == "report_semantic_audit_v1"
    assert passed.usage == {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost_usd": 0.00000075, "cost_unknown": False}
    assert unknown_cost.state == "unavailable"
    assert unknown_cost.reason_codes == ("semantic_audit_cost_unknown",)
