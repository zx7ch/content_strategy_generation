"""Unit tests for extract_visible_items."""

from __future__ import annotations

import pytest

from experiments.xhs_extension_mvp.server.models import CaptureItemIn
from experiments.xhs_extension_mvp.server.scraper import extract_visible_items


def _build_raw_item(**overrides) -> dict:
    base: dict = {
        "source_url": "https://www.xiaohongshu.com/explore/abc123",
        "raw_href": "/explore/abc123",
        "xsec_token": "tk_abc",
        "xsec_source": "pc_search",
        "debug_url_source": "link.href",
        "page_type": "search_result",
        "query_text": "敏感肌护肤",
        "note_id": "abc123",
        "title": "敏感肌护肤大测评",
        "author": "tester",
        "visible_text_excerpt": "这是一段笔记摘要",
        "tags": ["敏感肌"],
        "likes": 1200,
        "comments": 88,
        "collections": 300,
        "cover_image_url": "https://example.com/img.jpg",
    }
    base.update(overrides)
    return base


class FakePage:
    def __init__(self, eval_result):
        self._eval_result = eval_result
        self.eval_calls: list[str] = []

    async def evaluate(self, expression: str):
        self.eval_calls.append(expression)
        return self._eval_result


class TestExtractVisibleItems:
    @pytest.mark.asyncio
    async def test_returns_capture_item_in_list(self) -> None:
        raw = [_build_raw_item(note_id="n1"), _build_raw_item(note_id="n2")]
        page = FakePage(raw)

        items = await extract_visible_items(page, keyword="敏感肌护肤")

        assert len(items) == 2
        assert all(isinstance(it, CaptureItemIn) for it in items)
        assert {it.note_id for it in items} == {"n1", "n2"}

    @pytest.mark.asyncio
    async def test_filters_out_search_page_context(self) -> None:
        raw = [
            _build_raw_item(note_id="n1"),
            _build_raw_item(
                note_id="",
                title="search_page_marker",
                debug_url_source="search_page_context",
            ),
        ]
        page = FakePage(raw)

        items = await extract_visible_items(page, keyword="敏感肌护肤")

        assert len(items) == 1
        assert items[0].note_id == "n1"

    @pytest.mark.asyncio
    async def test_skips_invalid_item_without_raising(self) -> None:
        raw = [
            _build_raw_item(note_id="n1"),
            # Missing required fields ('title' / 'page_type') triggers validation error
            {"note_id": "broken"},
        ]
        page = FakePage(raw)

        items = await extract_visible_items(page, keyword="敏感肌护肤")

        assert len(items) == 1
        assert items[0].note_id == "n1"

    @pytest.mark.asyncio
    async def test_handles_empty_extractor_response(self) -> None:
        page = FakePage([])
        items = await extract_visible_items(page, keyword="kw")
        assert items == []

    @pytest.mark.asyncio
    async def test_handles_none_extractor_response(self) -> None:
        page = FakePage(None)
        items = await extract_visible_items(page, keyword="kw")
        assert items == []
