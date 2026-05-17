import pytest

from app.services.creator_intent_router import (
    ACTIVE_JOB_STATUSES,
    IntentContext,
    classify_intent,
)


@pytest.mark.asyncio
async def test_pause_keyword_zh():
    ctx = IntentContext(has_active_job=True, active_job_status="running")
    assert await classify_intent("暂停一下", ctx) == "pause_job"


@pytest.mark.asyncio
async def test_pause_keyword_en():
    ctx = IntentContext(has_active_job=True, active_job_status="running")
    assert await classify_intent("please pause", ctx) == "pause_job"


@pytest.mark.asyncio
async def test_resume_keyword():
    ctx = IntentContext(has_active_job=True, active_job_status="paused")
    assert await classify_intent("恢复任务", ctx) == "resume_job"


@pytest.mark.asyncio
async def test_cancel_keyword():
    ctx = IntentContext(has_active_job=True, active_job_status="running")
    assert await classify_intent("取消吧", ctx) == "cancel_job"


@pytest.mark.asyncio
async def test_ask_status_keyword():
    ctx = IntentContext(has_active_job=True, active_job_status="running")
    assert await classify_intent("现在进度怎么样", ctx) == "ask_status"


@pytest.mark.asyncio
async def test_add_constraint_when_active_job():
    ctx = IntentContext(has_active_job=True, active_job_status="running")
    assert await classify_intent("目标用户改为25-35岁女性", ctx) == "add_constraint"


@pytest.mark.asyncio
async def test_free_chat_no_active_job():
    ctx = IntentContext(has_active_job=False, active_job_status=None)
    assert await classify_intent("你好，你能做什么？", ctx) == "free_chat"


@pytest.mark.asyncio
async def test_pause_takes_priority_over_add_constraint():
    ctx = IntentContext(has_active_job=True, active_job_status="running")
    # "暂停" appears first so pause_job wins even though has_active_job is True
    assert await classify_intent("先暂停，目标用户改一下", ctx) == "pause_job"


def test_active_job_statuses_set():
    assert ACTIVE_JOB_STATUSES == frozenset({"queued", "running", "retrying", "paused"})
