from app.content_research.reporting.lite_read_model import (
    LiteReportReader,
    _citation_ref,
    _lite_citations,
    _weak_signal,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore


def test_lite_projection_with_empty_requested_scope_exposes_no_directional_data(tmp_path):
    reader = LiteReportReader(SQLiteContentResearchStore(str(tmp_path / "empty-scope.db")), str(tmp_path / "empty-scope.db"))
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

    payload = reader._published_projection(report, citation_group_ids=None)

    assert payload["sections"]["main_findings"] == []
    assert payload["sections"]["weak_signals"] == []
    assert payload["citations"] == []
    assert payload["status_strip"]["admitted_finding_count"] == 0


def test_lite_citations_retain_only_note_title_and_body_fields_with_navigation_reason():
    citations = _lite_citations(
        [
            {
                "citation_group_id": "cg_1",
                "display_index": 1,
                "claim_candidate_id": "claim_1",
                "evidence_refs": [
                    {"field_path": "comment_text", "quote": "comment", "source_url": "https://x"},
                    {"field_path": "content_text", "quote": "body", "source_url": None},
                    {
                        "field_path": "title",
                        "quote": "title",
                        "source_url": "https://x/note",
                        "navigation_state": "navigation_unavailable",
                        "navigation_reason": "provider_auth_required",
                    },
                ],
            }
        ]
    )

    assert [ref["quote"] for ref in citations[0]["evidence_refs"]] == ["body", "title"]
    assert citations[0]["evidence_refs"][0]["navigation_state"] == "missing_source_url"
    assert citations[0]["evidence_refs"][1]["navigation_state"] == "navigation_unavailable"
    assert citations[0]["evidence_refs"][1]["navigation_reason"] == "provider_auth_required"


def test_weak_signal_requires_a_lite_citation_and_never_becomes_a_finding():
    citations = [{"citation_group_id": "cg_1"}]
    signal = _weak_signal(
        {
            "claim_candidate_id": "weak_1",
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
