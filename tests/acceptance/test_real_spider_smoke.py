from __future__ import annotations

import time

import pytest

from app.services.xhs_spider import XHSSpiderClient
from tests.acceptance.conftest import write_acceptance_artifact


@pytest.mark.acceptance
@pytest.mark.real_dependency
@pytest.mark.asyncio
async def test_real_spider_smoke(
    spider_ready: None,
    acceptance_queries: dict[str, str],
    acceptance_artifact_dir,
):
    client = XHSSpiderClient()
    started = time.perf_counter()
    posts = await client.search_with_retry(acceptance_queries["primary"], num=5)
    latency_ms = int((time.perf_counter() - started) * 1000)

    assert posts
    first = posts[0]
    assert first.note_id
    assert first.title
    assert isinstance(first.tags, list)
    assert first.note_url.startswith("http")
    assert latency_ms < 120_000

    write_acceptance_artifact(
        acceptance_artifact_dir,
        "real_spider_smoke",
        {
            "query": acceptance_queries["primary"],
            "result_count": len(posts),
            "latency_ms": latency_ms,
            "sample_note_id": first.note_id,
            "sample_title": first.title,
        },
    )
