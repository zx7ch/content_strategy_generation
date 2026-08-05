import pytest

from app.content_research.reporting.lite_read_model import (
    LiteReportReader,
    _citation_ref,
    _lite_citations,
    _weak_signal,
)
from app.content_research.reporting.read_model import PublishedReportNotFoundError


class _StoreWithoutBrief:
    """Smallest store seam needed by the published projection under test."""

    def get_brief_by_workflow(self, workflow_run_id: str):
        return None


def test_lite_projection_rejects_directional_data_outside_an_empty_requested_scope():
    reader = LiteReportReader(_StoreWithoutBrief(), ":memory:")
    report = {
        "workflow_run_id": "run_empty_scope",
        "workflow_terminal_state": "succeeded",
        "publication_state": "complete_verified_report",
        "publication": {"compose_mode": "template_only"},
        "release": {
            "direction_set_version": "direction_set_v1",
            "direction_ids": [],
        },
        "claim_cards": [
            {
                "claim_candidate_id": "claim_product",
                "direction_id": "product_marketing",
                "admission_state": "admitted",
                "statement": "must remain hidden",
                "claim_type": "finding",
                "scope": "one sample",
            }
        ],
        "weak_signals": [
            {
                "claim_candidate_id": "weak_product",
                "direction_id": "product_marketing",
                "statement": "must also remain hidden",
            }
        ],
        "citation_groups": [
            {
                "citation_group_id": "cg_product",
                "display_index": 1,
                "claim_candidate_id": "claim_product",
                "evidence_refs": [
                    {
                        "field_path": "content_text",
                        "quote": "private directional evidence",
                        "source_url": "https://example.test/private",
                    }
                ],
            }
        ],
        "run_direction_states": [
            {"direction": "product_marketing", "state": "completed"}
        ],
        "limitations_recovery": [],
    }

    with pytest.raises(
        PublishedReportNotFoundError,
        match="governed card identity is invalid",
    ):
        reader._published_projection(report, citation_group_ids=None)


def test_lite_citations_retain_only_note_title_and_body_fields_with_navigation_reason():
    citations = _lite_citations(
        [
            {
                "citation_group_id": "cg_1",
                "display_index": 1,
                "claim_candidate_id": "claim_1",
                "evidence_refs": [
                    {
                        "canonical_note_id": "note-1",
                        "field_path": "comment_text",
                        "quote": "comment",
                        "text_start": 0,
                        "text_end": 7,
                        "source_text_hash": "hash-comment",
                        "source_url": "https://www.xiaohongshu.com/explore/note-1",
                    },
                    {
                        "canonical_note_id": "note-1",
                        "field_path": "content_text",
                        "quote": "body",
                        "text_start": 0,
                        "text_end": 4,
                        "source_text_hash": "hash-body",
                        "source_url": "https://www.xiaohongshu.com/explore/note-1",
                    },
                    {
                        "canonical_note_id": "note-1",
                        "field_path": "title",
                        "quote": "title",
                        "text_start": 0,
                        "text_end": 5,
                        "source_text_hash": "hash-title",
                        "source_url": "https://www.xiaohongshu.com/explore/note-1",
                        "navigation_state": "navigation_unavailable",
                        "navigation_reason": "provider_auth_required",
                    },
                ],
            }
        ]
    )

    assert [ref["quote"] for ref in citations[0]["evidence_refs"]] == ["body", "title"]
    assert citations[0]["evidence_refs"][0]["navigation_state"] == "navigation_unavailable"
    assert citations[0]["evidence_refs"][1]["navigation_state"] == "navigation_unavailable"
    assert citations[0]["evidence_refs"][1]["navigation_reason"] == "provider_auth_required"


def test_weak_signal_requires_a_lite_citation_and_never_becomes_a_finding():
    citations = [{"citation_group_id": "cg_1", "admission_decision_id": "cad_1"}]
    signal = _weak_signal(
        {
            "claim_candidate_id": "weak_1",
            "admission_decision_id": "cad_1",
            "reason": "minimum independent sources not met",
            "direction_id": "product_marketing",
        },
        {"weak_1": citations},
    )

    assert signal == {
        "statement": "minimum independent sources not met",
        "direction": "product_marketing",
        "sample_summary": None,
        "qualification_reason": "minimum independent sources not met",
        "citation_group_ids": ["cg_1"],
    }
    assert _weak_signal({"claim_candidate_id": "weak_2"}, {}) is None


def test_citation_with_url_is_available_unless_runtime_marks_it_unavailable():
    assert (
        _citation_ref({"field_path": "title", "source_url": "https://x"})["navigation_state"]
        == "available"
    )
    assert (
        _citation_ref({"field_path": "title", "source_url": None})["navigation_state"]
        == "missing_source_url"
    )


def test_lite_projection_uses_only_selected_governed_marketing_conclusion():
    reader = LiteReportReader(_StoreWithoutBrief(), ":memory:")
    claim_cards = [
        {
            "claim_candidate_id": f"claim_{index}",
            "admission_decision_id": f"admission_{index}",
            "admission_state": "admitted",
            "direction_id": "product_marketing",
            "claim_type": "use_context",
            "statement": f"raw claim {index} must not become the conclusion",
            "scope": {"sample": "selected_packets"},
        }
        for index in range(1, 4)
    ]
    citation_groups = [
        {
            "citation_group_id": f"citation_{index}",
            "display_index": index,
            "claim_candidate_id": f"claim_{index}",
            "admission_decision_id": f"admission_{index}",
            "evidence_refs": [
                {
                    "canonical_note_id": f"note_{index}",
                    "field_path": "content_text",
                    "quote": f"frozen quote {index}",
                    "text_start": 0,
                    "text_end": len(f"frozen quote {index}"),
                    "source_text_hash": str(index) * 64,
                    "source_url": f"https://example.test/note/{index}",
                }
            ],
        }
        for index in range(1, 4)
    ]
    report = {
        "workflow_run_id": "run_1",
        "workflow_terminal_state": "succeeded",
        "publication_state": "complete_verified_report",
        "publication": {"compose_mode": "template_only"},
        "release": {
            "direction_set_version": "direction_set_v1",
            "direction_ids": ["product_marketing"],
        },
        "claim_cards": claim_cards,
        "weak_signals": [],
        "citation_groups": citation_groups,
        "run_direction_states": [
            {"direction": "product_marketing", "state": "completed"}
        ],
        "limitations_recovery": [],
        "primary_marketing_goal": "content_seeding",
        "marketing_conclusions": [
            {
                "track": "need",
                "state": "selected",
                "candidate_id": "mc_need_primary",
                "statement": "高温通勤场景中的凉感需求…",
                "supporting_claim_ids": ["claim_1", "claim_2", "claim_3"],
                "supporting_note_count": 3,
                "independent_author_count": 2,
                "reason_codes": [],
            },
            {
                "track": "need",
                "state": "qualified",
                "candidate_id": "mc_need_secondary",
                "statement": "另一条合格结论不得进入 Lite 报告",
                "supporting_claim_ids": ["claim_1", "claim_2", "claim_3"],
                "supporting_note_count": 3,
                "independent_author_count": 2,
                "reason_codes": [],
            },
            {
                "track": "value",
                "state": "insufficient_evidence",
                "candidate_id": None,
                "statement": None,
                "supporting_claim_ids": [],
                "supporting_note_count": 0,
                "independent_author_count": 0,
                "reason_codes": ["conclusion_no_qualified_candidate"],
            },
            {
                "track": "message",
                "state": "analysis_unavailable",
                "candidate_id": None,
                "statement": None,
                "supporting_claim_ids": [],
                "supporting_note_count": 0,
                "independent_author_count": 0,
                "reason_codes": ["marketing_analysis_unavailable"],
            },
        ],
        "sections": [
            {
                "section_kind": "marketing_need",
                "prose": "高温通勤场景中的凉感需求…",
                "claim_candidate_ids": ["claim_1", "claim_2", "claim_3"],
                "citation_group_ids": ["citation_1", "citation_2", "citation_3"],
                "marketing_conclusion_ids": ["mc_need_primary"],
                "conclusion_state": "selected",
            }
        ],
    }

    projected = reader._published_projection(report, citation_group_ids=None)
    need = projected["sections"]["marketing_conclusions"]["need"]

    assert need == {
        "state": "selected",
        "conclusion_id": "mc_need_primary",
        "statement": "高温通勤场景中的凉感需求…",
        "citation_group_ids": ["citation_1", "citation_2", "citation_3"],
        "supporting_note_count": 3,
        "independent_author_count": 2,
        "additional_qualified_count": 1,
    }
    assert "other_qualified_statements" not in need
    assert projected["sections"]["marketing_conclusions"]["value"] == {
        "state": "insufficient_evidence",
        "reason_codes": ["conclusion_no_qualified_candidate"],
        "verification_direction": "补充至少 3 篇合格笔记，并覆盖至少 2 位独立作者后重新验证。",
    }
    assert "statement" not in projected["sections"]["marketing_conclusions"]["message"]
    assert projected["sections"]["priority_action"]["label"] == "建议"
    assert projected["sections"]["priority_action"]["supporting_conclusion_ids"] == [
        "mc_need_primary"
    ]


def test_lite_projection_never_returns_withdrawn_marketing_conclusion_prose():
    report = {
        "workflow_run_id": "run_withdrawn",
        "workflow_terminal_state": "succeeded",
        "publication_state": "partial_verified_report",
        "publication": {
            "compose_mode": "template_only",
            "omitted_section_ids": ["section_need"],
        },
        "release": {
            "direction_set_version": "direction_set_v1",
            "direction_ids": ["product_marketing"],
        },
        "claim_cards": [
            {
                "claim_candidate_id": f"claim_{index}",
                "admission_decision_id": f"admission_{index}",
                "admission_state": "admitted",
                "direction_id": "product_marketing",
                "claim_type": "use_context",
                "statement": f"raw claim {index}",
                "scope": {"sample": "selected_packets"},
            }
            for index in range(1, 4)
        ],
        "weak_signals": [],
        "citation_groups": [
            {
                "citation_group_id": f"citation_{index}",
                "display_index": index,
                "claim_candidate_id": f"claim_{index}",
                "admission_decision_id": f"admission_{index}",
                "evidence_refs": [
                    {
                        "canonical_note_id": f"note_{index}",
                        "field_path": "content_text",
                        "quote": f"quote {index}",
                        "text_start": 0,
                        "text_end": len(f"quote {index}"),
                        "source_text_hash": str(index) * 64,
                        "source_url": f"https://www.xiaohongshu.com/explore/{index}",
                    }
                ],
            }
            for index in range(1, 4)
        ],
        "run_direction_states": [
            {"direction": "product_marketing", "state": "completed"}
        ],
        "limitations_recovery": [],
        "primary_marketing_goal": "content_seeding",
        "marketing_conclusions": [
            {
                "track": "need",
                "state": "selected",
                "candidate_id": "mc_need",
                "statement": "该结论已被审计撤回",
                "supporting_claim_ids": ["claim_1", "claim_2", "claim_3"],
                "supporting_note_count": 3,
                "independent_author_count": 2,
                "reason_codes": [],
            }
        ],
        "sections": [
            {
                "section_id": "section_need",
                "section_kind": "marketing_need",
                "prose": None,
                "claim_candidate_ids": ["claim_1", "claim_2", "claim_3"],
                "citation_group_ids": ["citation_1", "citation_2", "citation_3"],
                "marketing_conclusion_ids": ["mc_need"],
                "conclusion_state": "selected",
            }
        ],
    }

    projected = LiteReportReader(_StoreWithoutBrief(), ":memory:")._published_projection(
        report, citation_group_ids=None
    )

    need = projected["sections"]["marketing_conclusions"]["need"]
    assert need["state"] == "analysis_unavailable"
    assert "statement" not in need
    assert "mc_need" not in projected["sections"]["priority_action"][
        "supporting_conclusion_ids"
    ]
