from app.content_research.reporting.lite_read_model import (
    _citation_ref,
    _lite_citations,
    _weak_signal,
)


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
