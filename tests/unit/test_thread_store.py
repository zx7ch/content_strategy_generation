"""Unit tests for ALIGN-3 ThreadStore."""

from __future__ import annotations

import pytest

from app.memory.thread_store import ThreadStore


@pytest.fixture
async def store(tmp_path):
    db_path = str(tmp_path / "test_threads.db")
    async with ThreadStore(db_path) as s:
        yield s


async def test_create_thread_returns_id_and_title(store):
    row = await store.create_thread(title="我的第一个对话")
    assert row["id"]
    assert row["title"] == "我的第一个对话"
    assert row["status"] == "active"
    assert row["active_workflow_session_id"] is None
    assert row["active_job_id"] is None


async def test_create_thread_default_title(store):
    row = await store.create_thread()
    assert "对话" in row["title"]


async def test_list_threads_returns_created(store):
    await store.create_thread(title="Thread A")
    await store.create_thread(title="Thread B")
    rows = await store.list_threads()
    titles = [r["title"] for r in rows]
    assert "Thread A" in titles
    assert "Thread B" in titles


async def test_get_thread_returns_correct(store):
    created = await store.create_thread(title="Test Get")
    fetched = await store.get_thread(created["id"])
    assert fetched is not None
    assert fetched["id"] == created["id"]
    assert fetched["title"] == "Test Get"


async def test_get_thread_none_for_missing(store):
    result = await store.get_thread("does-not-exist")
    assert result is None


async def test_append_message_persists(store):
    thread = await store.create_thread(title="Msg Thread")
    msg = await store.append_message(
        thread_id=thread["id"],
        role="user",
        text="Hello",
        intent="free_chat",
    )
    assert msg["id"]
    assert msg["thread_id"] == thread["id"]
    assert msg["role"] == "user"
    assert msg["text"] == "Hello"
    assert msg["intent"] == "free_chat"

    messages = await store.get_thread_messages(thread["id"])
    assert len(messages) == 1
    assert messages[0]["id"] == msg["id"]


async def test_update_thread_active_job(store):
    thread = await store.create_thread(title="Job Thread")
    await store.update_thread_active_job(
        thread_id=thread["id"],
        session_id="sess-123",
        job_id="job-456",
    )
    updated = await store.get_thread(thread["id"])
    assert updated is not None
    assert updated["active_workflow_session_id"] == "sess-123"
    assert updated["active_job_id"] == "job-456"


async def test_update_thread_title_persists(store):
    thread = await store.create_thread(title="Old")
    await store.update_thread_title(thread["id"], "New")

    updated = await store.get_thread(thread["id"])
    assert updated is not None
    assert updated["title"] == "New"


async def test_delete_thread_removes_messages(store):
    thread = await store.create_thread(title="Delete Me")
    await store.append_message(thread_id=thread["id"], role="user", text="hello")

    deleted = await store.delete_thread(thread["id"])

    assert deleted is True
    assert await store.get_thread(thread["id"]) is None
    assert await store.get_thread_messages(thread["id"]) == []
