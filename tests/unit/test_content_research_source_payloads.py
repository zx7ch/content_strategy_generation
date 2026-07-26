from __future__ import annotations

import pytest

from app.content_research.sources.xiaohongshu.normalizer import XiaohongshuSourceNormalizer
from app.services.xhs_spider import XHSPost


def _post(note_id: str = "note_1") -> XHSPost:
    return XHSPost(
        note_id=note_id,
        title="夏季徒步短裤怎么选",
        content="轻量速干, 适合通勤和短途徒步。",
        author="户外作者",
        tags=["徒步", "短裤"],
        liked_count=12,
        collected_count=3,
        comment_count=2,
        share_count=1,
        note_url=f"https://www.xiaohongshu.com/explore/{note_id}",
        images=["https://example.com/1.jpg"],
    )


@pytest.mark.parametrize(
    "source_kind",
    ["search_result", "note_detail", "comment", "topic_or_keyword_page"],
)
def test_xhs_source_payload_contains_evidence_ready_fields_for_p1_kinds(source_kind):
    payload = XiaohongshuSourceNormalizer().normalize_search_result(
        _post(),
        query="徒步短裤",
        source_kind=source_kind,
    )

    assert payload["schema_version"] == "content_research_source_payload_v1"
    assert payload["provider"] == "xiaohongshu"
    assert payload["source_url"] == "https://www.xiaohongshu.com/explore/note_1"
    assert payload["canonical_id"] == "note_1"
    assert payload["source_kind"] == source_kind
    assert payload["captured_at"]
    assert payload["raw_payload_hash"]
    assert payload["cookie_status"] == "valid"
    assert payload["failure_reason"] is None
    assert payload["query_used"] == "徒步短裤"
    assert "raw_payload" not in payload


def test_xhs_failure_payload_uses_same_required_shape():
    payload = XiaohongshuSourceNormalizer().build_failure_payload(
        workflow_run_id="run_1",
        query="徒步短裤",
        source_kind="comment",
        failure_reason="auth_required",
        cookie_status="invalid",
    )

    assert payload["schema_version"] == "content_research_source_payload_v1"
    assert payload["provider"] == "xiaohongshu"
    assert payload["source_url"] == ""
    assert payload["canonical_id"].startswith("xhs_failure:")
    assert payload["source_kind"] == "comment"
    assert payload["captured_at"]
    assert payload["raw_payload_hash"]
    assert payload["cookie_status"] == "invalid"
    assert payload["failure_reason"] == "auth_required"
    assert payload["query_used"] == "徒步短裤"
    assert "raw_payload" not in payload
