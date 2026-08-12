from __future__ import annotations

import pytest

from app.services.xhs_spider import XHSSpiderClient


class FakeDetailApi:
    def get_note_info(self, url: str):
        assert url.endswith("xsec_token=token")
        return True, "", {
            "data": {
                "items": [{
                    "id": "note_1",
                    "note_card": {
                        "type": "video",
                        "title": "真实详情标题",
                        "desc": "真实详情正文",
                        "time": 1_720_000_000_000,
                        "user": {"nickname": "作者"},
                        "tag_list": [{"name": "通勤"}],
                        "interact_info": {
                            "liked_count": "10", "collected_count": "20",
                            "comment_count": "30", "share_count": "40",
                        },
                    },
                }]
            }
        }


class FakeCommentApi:
    def get_note_out_comment(self, note_id: str, cursor: str, xsec_token: str):
        assert (note_id, cursor, xsec_token) == ("note_1", "cursor_1", "token")
        return True, "", {
            "data": {
                "comments": [{"id": "comment_1"}],
                "cursor": "cursor_2",
                "has_more": True,
            }
        }


@pytest.mark.asyncio
async def test_collect_note_detail_uses_detail_endpoint_and_normalizes_detail_fields(monkeypatch):
    client = XHSSpiderClient(cookies="cookie")
    monkeypatch.setattr(client, "_get_api", lambda: FakeDetailApi())

    post = await client.collect_note_detail(
        note_id="note_1",
        note_url="https://www.xiaohongshu.com/explore/note_1?xsec_token=token",
    )

    assert post.content == "真实详情正文"
    assert post.note_type == "video"
    assert post.source_published_at == "2024-07-03T09:46:40+00:00"


@pytest.mark.asyncio
async def test_collect_comment_page_uses_current_upstream_signature(monkeypatch):
    client = XHSSpiderClient(cookies="cookie")
    monkeypatch.setattr(client, "_get_api", lambda: FakeCommentApi())

    comments, next_cursor, has_more = await client.collect_comment_page(
        note_id="note_1",
        note_url="https://www.xiaohongshu.com/explore/note_1?xsec_token=token",
        cursor="cursor_1",
    )

    assert comments == [{"id": "comment_1"}]
    assert next_cursor == "cursor_2"
    assert has_more is True
