from __future__ import annotations

import pytest

from app.services.xhs_spider import SpiderSearchSortOption, XHSPost
from experiments.xhs_extension_mvp.server.hotspot_service import build_hotspot_snapshot
from experiments.xhs_extension_mvp.server.storage import MVPStorage


class FakeSpiderClient:
    def __init__(self, responses: dict[tuple[str, int], list[XHSPost]] | None = None, *, error: Exception | None = None):
        self.responses = responses or {}
        self.error = error
        self.calls: list[tuple[str, int, int]] = []

    @staticmethod
    def get_hotspot_sort_options() -> tuple[SpiderSearchSortOption, ...]:
        return (
            SpiderSearchSortOption(key="likes", label="最多点赞", value=2),
            SpiderSearchSortOption(key="comments", label="最多评论", value=3),
            SpiderSearchSortOption(key="collections", label="最多收藏", value=4),
        )

    async def search_with_retry(self, query: str, num: int = 50, sort: int = 2) -> list[XHSPost]:
        self.calls.append((query, num, sort))
        if self.error is not None:
            raise self.error
        return self.responses.get((query, sort), [])


def make_post(note_id: str, title: str, *, likes: int, comments: int, collections: int, author: str = "author") -> XHSPost:
    return XHSPost(
        note_id=note_id,
        title=title,
        title_is_explicit=True,
        content=f"{title} 的正文摘要",
        author=author,
        tags=[],
        liked_count=likes,
        collected_count=collections,
        comment_count=comments,
        share_count=0,
        note_url=f"https://www.xiaohongshu.com/explore/{note_id}",
        images=[],
    )


@pytest.mark.asyncio
async def test_build_hotspot_snapshot_merges_query_sources_and_sorts() -> None:
    responses = {
        ("通勤穿搭", 2): [
            make_post("shared", "高赞通勤穿搭", likes=200, comments=15, collections=60),
            make_post("other", "普通通勤穿搭", likes=80, comments=12, collections=20),
        ],
        ("小个子通勤穿搭", 2): [
            make_post("shared", "高赞通勤穿搭", likes=200, comments=15, collections=60),
            make_post("petite", "小个子通勤穿搭", likes=120, comments=10, collections=18),
        ],
        ("通勤穿搭", 3): [
            make_post("comment-1", "评论高的通勤穿搭", likes=90, comments=88, collections=35),
        ],
        ("小个子通勤穿搭", 3): [
            make_post("comment-2", "小个子评论高", likes=70, comments=66, collections=22),
        ],
        ("通勤穿搭", 4): [
            make_post("collect-1", "收藏高的通勤穿搭", likes=110, comments=25, collections=99),
        ],
        ("小个子通勤穿搭", 4): [
            make_post("collect-2", "小个子收藏高", likes=105, comments=20, collections=77),
        ],
    }
    spider = FakeSpiderClient(responses)

    snapshot = await build_hotspot_snapshot(
        task_id="task-1",
        topic="通勤穿搭",
        queries=["小个子通勤穿搭"],
        spider_client=spider,
    )

    assert snapshot.status == "ready"
    assert [hotspot_list.metric for hotspot_list in snapshot.lists] == ["likes", "comments", "collections"]
    likes_top = snapshot.lists[0].items[0]
    assert likes_top.note_id == "shared"
    assert likes_top.query_sources == ["小个子通勤穿搭", "通勤穿搭"]
    assert spider.calls[0] == ("通勤穿搭", 10, 2)


@pytest.mark.asyncio
async def test_storage_refresh_hotspots_returns_error_with_last_successful_lists(tmp_path) -> None:
    storage = MVPStorage(tmp_path / "mvp.db", secret="secret")
    storage.init_db()
    task_id, _, _, _ = storage.create_task("敏感肌修护")

    good_spider = FakeSpiderClient(
        {
            ("敏感肌修护", 2): [make_post("l1", "点赞高", likes=180, comments=16, collections=33)],
            ("敏感肌修护", 3): [make_post("c1", "评论高", likes=90, comments=88, collections=18)],
            ("敏感肌修护", 4): [make_post("f1", "收藏高", likes=75, comments=10, collections=95)],
            ("油皮敏感肌修护", 2): [],
            ("油皮敏感肌修护", 3): [],
            ("油皮敏感肌修护", 4): [],
            ("换季敏感肌修护", 2): [],
            ("换季敏感肌修护", 3): [],
            ("换季敏感肌修护", 4): [],
            ("敏感肌修护怎么选", 2): [],
            ("敏感肌修护怎么选", 3): [],
            ("敏感肌修护怎么选", 4): [],
            ("敏感肌修护对比", 2): [],
            ("敏感肌修护对比", 3): [],
            ("敏感肌修护对比", 4): [],
            ("敏感肌修护避坑", 2): [],
            ("敏感肌修护避坑", 3): [],
            ("敏感肌修护避坑", 4): [],
        }
    )

    first_snapshot = await storage.refresh_hotspots(task_id, spider_client=good_spider)
    assert first_snapshot.status == "ready"
    assert len(first_snapshot.lists) == 3

    failed_snapshot = await storage.refresh_hotspots(task_id, spider_client=FakeSpiderClient(error=RuntimeError("cookie expired")))
    assert failed_snapshot.status == "error"
    assert failed_snapshot.error_message == "cookie expired"
    assert len(failed_snapshot.lists) == 3
    assert failed_snapshot.lists[0].items[0].title == "点赞高"

    latest = storage.get_hotspots(task_id)
    assert latest is not None
    assert latest.status == "error"
    assert latest.error_message == "cookie expired"
    assert latest.lists[0].items[0].title == "点赞高"

    task_snapshot = storage.get_task_snapshot(task_id)
    assert task_snapshot is not None
    assert task_snapshot.collection_summary.capture_batch_count == 0


@pytest.mark.asyncio
async def test_storage_refresh_hotspots_uses_topic_only(tmp_path) -> None:
    storage = MVPStorage(tmp_path / "mvp.db", secret="secret")
    storage.init_db()
    task_id, _, _, _ = storage.create_task("通勤穿搭")
    storage.add_custom_queries(task_id=task_id, text="小个子通勤穿搭\n通勤穿搭春夏")

    spider = FakeSpiderClient(
        {
            ("通勤穿搭", 2): [make_post("l1", "通勤穿搭", likes=100, comments=12, collections=20)],
            ("通勤穿搭", 3): [make_post("c1", "通勤穿搭评论高", likes=80, comments=88, collections=12)],
            ("通勤穿搭", 4): [make_post("f1", "通勤穿搭收藏高", likes=90, comments=10, collections=99)],
            ("小个子通勤穿搭", 2): [],
            ("小个子通勤穿搭", 3): [],
            ("小个子通勤穿搭", 4): [],
            ("通勤穿搭春夏", 2): [],
            ("通勤穿搭春夏", 3): [],
            ("通勤穿搭春夏", 4): [],
        }
    )

    await storage.refresh_hotspots(task_id, spider_client=spider)

    assert ("通勤穿搭", 10, 2) in spider.calls
    assert all(call[0] != "小个子通勤穿搭" for call in spider.calls)
    assert all(call[0] != "通勤穿搭春夏" for call in spider.calls)
    assert all(call[0] != "上班通勤穿搭" for call in spider.calls)
    assert all(call[0] != "通勤穿搭怎么搭" for call in spider.calls)
