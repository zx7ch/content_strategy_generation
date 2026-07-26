import pytest

from app.content_research.admission.ugc_community import build_ugc_candidates
from app.content_research.persistence_models import DirectionalEvidencePacketRecord


def _packet(*, comments: int = 30, authors: int = 5, reply_depth: int | None = 0):
    return DirectionalEvidencePacketRecord(
        "dep_1", "v1",
        {
            "field_projection": {"comment_text": "用户讨论的具体场景", "reply_depth": reply_depth},
            "retrieval_context": {
                "source_kind": "comment",
                "parent_note_canonical_source_id": "cs_note_1",
                "collection": {
                    "sort": "provider_return_order", "target_comment_count": 30,
                    "actual_comment_count": comments, "completeness": "complete",
                    "deduplicated_comment_count": comments,
                    "deduplicated_author_count": authors,
                },
            },
        },
        workflow_run_id="run_1", research_direction_id="ugc_community",
        canonical_source_id="cs_comment_1", field_projection_hash="hash",
    )


def test_ugc_factory_builds_comment_scoped_candidates_for_complete_sample():
    candidates = build_ugc_candidates(_packet())

    assert candidates
    assert all(item.payload["scope"]["parent_note_canonical_source_id"] == "cs_note_1" for item in candidates)
    assert all(item.payload["scope"]["reply_relation"] == 0 for item in candidates)


@pytest.mark.parametrize("comments,authors,reply_depth", [(29, 5, 0), (30, 4, 0), (30, 5, None)])
def test_ugc_factory_never_builds_formal_candidate_without_qualified_comment_provenance(comments, authors, reply_depth):
    assert build_ugc_candidates(_packet(comments=comments, authors=authors, reply_depth=reply_depth)) == []
