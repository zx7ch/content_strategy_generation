import pytest

from app.content_research.admission.comment_insight import build_comment_insight_candidates
from app.content_research.persistence_models import DirectionalEvidencePacketRecord


def _packet(text: str, *, field: str = "comment_text"):
    return DirectionalEvidencePacketRecord("dep", "v1", {"field_projection": {field: text, "reply_depth": 0}, "retrieval_context": {"source_kind": "comment", "parent_note_canonical_source_id": "cs_note", "collection": {"sort": "provider_return_order", "target_comment_count": 30, "actual_comment_count": 30, "completeness": "complete", "deduplicated_comment_count": 30, "deduplicated_author_count": 5}}}, workflow_run_id="run", research_direction_id="comment_insight", canonical_source_id="cs_comment", field_projection_hash="hash")


def test_comment_insight_cites_direct_question_and_objection_only_from_comment_text():
    assert {item.claim_type for item in build_comment_insight_candidates(_packet("这个尺码怎么选？"))} == {"explicit_question"}
    assert {item.claim_type for item in build_comment_insight_candidates(_packet("这个设计太贵，不好用"))} == {"objection_or_failure"}
    assert build_comment_insight_candidates(_packet("这个尺码怎么选？", field="title")) == []


def test_comment_insight_requires_frozen_cross_comment_counts_for_repeated_need_language():
    packet = _packet("希望增加口袋")
    packet.payload["retrieval_context"]["collection"]["repeated_need_phrases"] = {
        "希望增加口袋": {"comment_count": 3, "independent_author_count": 2},
    }

    assert {item.claim_type for item in build_comment_insight_candidates(packet)} == {"repeated_need_language"}


@pytest.mark.parametrize(
    "collection_update",
    [
        {"deduplicated_comment_count": 29},
        {"deduplicated_author_count": 4},
        {"completeness": "partial"},
    ],
)
def test_comment_insight_requires_complete_30_comment_five_author_collection(collection_update):
    packet = _packet("这个尺码怎么选？")
    packet.payload["retrieval_context"]["collection"].update(collection_update)

    assert build_comment_insight_candidates(packet) == []


def test_comment_insight_requires_reply_relation():
    packet = _packet("这个尺码怎么选？")
    del packet.payload["field_projection"]["reply_depth"]

    assert build_comment_insight_candidates(packet) == []
