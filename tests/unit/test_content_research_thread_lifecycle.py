"""Creator thread lifecycle invariants for Content Research."""

from __future__ import annotations

import pytest

from app.content_research.api_schemas import ContentResearchSourceCollectionRequest
from app.content_research.models import ResearchBriefRecord
from app.content_research.service import (
    ContentResearchService,
    ContentResearchValidationError,
    WorkflowRunManagerRuntime,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.thread_store import ThreadStore
from app.services.workflow_run_manager import WorkflowRunManager


@pytest.mark.asyncio
async def test_ending_content_research_keeps_creator_thread_for_the_next_run(tmp_path):
    db_path = str(tmp_path / "content-research.db")
    async with ThreadStore(db_path) as thread_store:
        thread = await thread_store.create_thread(title="北面内容调研")
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id=thread["id"], user_id="test-user")

    runtime = WorkflowRunManagerRuntime(db_path)
    result = await runtime.end_content_research_run(
        workflow_run_id=run.run_id,
        thread_id=thread["id"],
    )

    async with ThreadStore(db_path) as thread_store:
        persisted_thread = await thread_store.get_thread(thread["id"])

    assert result["resources_destroyed"] is False
    assert persisted_thread is not None
    assert persisted_thread["active_run_id"] is None


@pytest.mark.asyncio
async def test_formal_research_rejects_an_orphaned_creator_thread_before_execution(tmp_path):
    db_path = str(tmp_path / "orphaned-run.db")
    store = SQLiteContentResearchStore(db_path)
    store.save_brief(ResearchBriefRecord(
        id="brief_orphaned",
        workflow_run_id="run_orphaned",
        thread_id="thread_deleted",
        schema_version="content_research_brief_v1",
        status="ready",
        payload={"schema_version": "content_research_brief_v1", "seed_text": "北面"},
    ))
    service = ContentResearchService(
        store=store,
        presearch=None,
        workflow_runtime=WorkflowRunManagerRuntime(db_path),
    )

    with pytest.raises(ContentResearchValidationError, match="Creator thread no longer exists"):
        await service.start_formal_research(
            workflow_run_id="run_orphaned",
            request=ContentResearchSourceCollectionRequest(),
        )
