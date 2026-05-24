"""Integration tests for T7 strategy-side workflow step executors."""

from __future__ import annotations

import pytest

from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowPhase
from app.services.step_executors import DiscoveryStepExecutor, RetrievalStepExecutor, StrategyStepExecutor
from app.services.workflow_run_manager import WorkflowRunManager


async def _seed_run(db_path: str):
    async with ThreadStore(db_path) as thread_store:
        thread = await thread_store.create_thread(title="Strategy Steps")
        message = await thread_store.append_message(
            thread_id=thread["id"],
            role="user",
            text="帮我生成防晒衣内容策略",
            intent="start_workflow",
        )
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(
            thread_id=thread["id"],
            user_id="user-1",
            user_message_id=message["id"],
            initial_request=message["text"],
        )
        await manager.initialize_steps(
            run.run_id,
            [
                {"step_name": "discovery.spider_search", "phase": WorkflowPhase.DISCOVERY},
                {"step_name": "retrieval.rag_retrieve", "phase": WorkflowPhase.RETRIEVAL},
                {"step_name": "strategy.llm_synthesize", "phase": WorkflowPhase.STRATEGY},
            ],
        )
    return run


@pytest.mark.asyncio
async def test_strategy_step_chain_creates_source_rag_and_strategy_artifacts(tmp_path):
    db_path = str(tmp_path / "workflow_strategy_steps.db")
    run = await _seed_run(db_path)

    async def fake_spider(context):
        assert context.user_request == "帮我生成防晒衣内容策略"
        return [{"note_id": "n1", "title": "防晒衣怎么选"}]

    async def fake_rag(context):
        assert any(artifact["artifact_type"] == "source_snapshot" for artifact in context.prior_artifacts)
        return {"summary": "防晒衣选题集中在轻薄和通勤"}

    async def fake_strategy(context):
        assert context.user_request == "帮我生成防晒衣内容策略"
        assert any(artifact["artifact_type"] == "rag_result" for artifact in context.prior_artifacts)
        return {
            "positioning": "城市轻户外",
            "target_audience": "通勤女生",
            "content_pillars": ["通勤", "防晒", "轻户外"],
        }

    discovery = await DiscoveryStepExecutor(db_path=db_path, source_runner=fake_spider).execute(run.run_id)
    retrieval = await RetrievalStepExecutor(db_path=db_path, rag_runner=fake_rag).execute(run.run_id)
    strategy = await StrategyStepExecutor(db_path=db_path, strategy_runner=fake_strategy).execute(run.run_id)

    assert discovery.artifact_refs[0]["artifact_type"] == "source_snapshot"
    assert retrieval.artifact_refs[0]["artifact_type"] == "rag_result"
    assert strategy.artifact_refs[0]["artifact_type"] == "strategy"

    async with WorkflowStore(db_path) as store:
        artifacts = await store.list_artifacts(run.run_id)

    assert [artifact.artifact_type.value for artifact in artifacts] == [
        "source_snapshot",
        "rag_result",
        "strategy",
    ]
    assert artifacts[-1].payload_json["positioning"] == "城市轻户外"
