import pytest

from app.content_research.admission.candidates import (
    build_claim_candidate,
    extract_facts,
    validate_candidate_packet,
)
from app.content_research.persistence_models import DirectionalEvidencePacketRecord


def _packet(*, text: str = "轻量透气", comment: bool = False) -> DirectionalEvidencePacketRecord:
    projection = {"source_url": "https://example/n1", "comment_text" if comment else "content_text": text}
    context = {"parent_note_canonical_source_id": "cs_parent"} if comment else {}
    return DirectionalEvidencePacketRecord("dep_1", "v1", {"field_projection": projection, "retrieval_context": context}, workflow_run_id="run_1", research_direction_id="product_marketing", canonical_source_id="cs_1", field_projection_hash="hash")


def test_fact_and_candidate_keep_recomputable_quote_reference():
    fact = extract_facts(_packet())[0]
    candidate = build_claim_candidate(workflow_run_id="run_1", direction_id="product_marketing", intent_id="value", claim_type="observation", statement="样本提到轻量", scope={"sample": "selected_notes"}, fact=fact, quote="轻量", text_start=0, text_end=2)
    validate_candidate_packet(candidate, _packet())
    assert candidate.payload["quote_refs"][0]["field_path"] == "content_text"


@pytest.mark.parametrize("quote,start,end", [("错误", 0, 2), ("轻量", 1, 3)])
def test_candidate_rejects_invalid_quote_span(quote, start, end):
    with pytest.raises(ValueError, match="quote span"):
        build_claim_candidate(workflow_run_id="run_1", direction_id="product_marketing", intent_id="value", claim_type="observation", statement="x", scope={}, fact=extract_facts(_packet())[0], quote=quote, text_start=start, text_end=end)


def test_comment_candidate_requires_parent_lineage():
    packet = _packet(comment=True)
    fact = extract_facts(packet)[0]
    with pytest.raises(ValueError, match="parent-note"):
        build_claim_candidate(workflow_run_id="run_1", direction_id="product_marketing", intent_id="pain", claim_type="observation", statement="x", scope={}, fact=fact, quote="轻量", text_start=0, text_end=2)
