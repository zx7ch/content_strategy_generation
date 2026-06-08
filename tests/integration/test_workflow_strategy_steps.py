"""Integration tests for T7 strategy-side workflow step executors."""

from __future__ import annotations

import pytest

from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowPhase
from app.services.step_executors import (
    DiscoveryStepExecutor,
    RetrievalStepExecutor,
    StrategyStepExecutor,
    build_agent_step_executor_registry,
)
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


async def _seed_canonical_strategy_run(db_path: str):
    async with ThreadStore(db_path) as thread_store:
        thread = await thread_store.create_thread(title="Canonical Strategy Steps")
        message = await thread_store.append_message(
            thread_id=thread["id"],
            role="user",
            text="帮我生成防晒衣内容策略",
            intent="start_workflow",
        )
    step_names = [
        ("discovery.plan_queries", WorkflowPhase.DISCOVERY),
        ("discovery.spider_search", WorkflowPhase.DISCOVERY),
        ("discovery.assess_source_quality", WorkflowPhase.DISCOVERY),
        ("discovery.expand_queries", WorkflowPhase.DISCOVERY),
        ("discovery.persist_sources", WorkflowPhase.DISCOVERY),
        ("retrieval.rag_index", WorkflowPhase.RETRIEVAL),
        ("retrieval.rag_retrieve", WorkflowPhase.RETRIEVAL),
        ("strategy.prepare_prompt", WorkflowPhase.STRATEGY),
        ("strategy.llm_synthesize", WorkflowPhase.STRATEGY),
        ("strategy.validate_strategy", WorkflowPhase.STRATEGY),
        ("strategy.persist_strategy", WorkflowPhase.STRATEGY),
    ]
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(
            thread_id=thread["id"],
            user_id="user-1",
            user_message_id=message["id"],
            initial_request=message["text"],
        )
        await manager.initialize_steps(
            run.run_id,
            [{"step_name": step_name, "phase": phase} for step_name, phase in step_names],
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


@pytest.mark.asyncio
async def test_strategy_canonical_steps_are_registered_and_produce_artifact_chain(tmp_path):
    db_path = str(tmp_path / "workflow_strategy_canonical_steps.db")
    run = await _seed_canonical_strategy_run(db_path)

    class FakeStrategyAgent:
        async def plan_queries_step(self, context):
            return {"queries": [context.user_request]}

        async def spider_search_step(self, _context):
            return [{"note_id": "n1", "title": "防晒衣怎么选", "content": "轻薄通勤", "tags": ["防晒"]}]

        async def assess_source_quality_step(self, _context):
            return {"total_notes": 1, "quality_hint": "sufficient"}

        async def expand_queries_step(self, _context):
            return {"queries": ["防晒衣 通勤"]}

        async def persist_sources_step(self, _context):
            return [{"note_id": "n1", "title": "防晒衣怎么选", "content": "轻薄通勤", "tags": ["防晒"]}]

        async def rag_index_step(self, _context):
            return {"score": 0.86, "total_notes": 1}

        async def rag_retrieve_step(self, _context):
            return {"summary": "轻薄通勤是核心卖点"}

        async def prepare_prompt_step(self, context):
            return {"user_query": context.user_request}

        async def llm_synthesize_step(self, _context):
            return {
                "positioning": "城市轻户外",
                "target_audience": "通勤女生",
                "content_pillars": ["通勤", "防晒"],
                "key_messaging": "轻薄好穿",
                "content_types": ["图文笔记"],
                "posting_strategy": "晚间发布",
                "data_source_quality": 0.86,
            }

        async def validate_strategy_step(self, context):
            return {"valid": True, "strategy": context.input_artifacts[-1]["payload_json"]}

        async def persist_strategy_step(self, context):
            return context.input_artifacts[-1]["payload_json"]

    registry = build_agent_step_executor_registry(
        db_path=db_path,
        strategy_agent=FakeStrategyAgent(),
        generation_agent=None,
    )
    expected_steps = [
        "discovery.plan_queries",
        "discovery.spider_search",
        "discovery.assess_source_quality",
        "discovery.expand_queries",
        "discovery.persist_sources",
        "retrieval.rag_index",
        "retrieval.rag_retrieve",
        "strategy.prepare_prompt",
        "strategy.llm_synthesize",
        "strategy.validate_strategy",
        "strategy.persist_strategy",
    ]

    results = [
        await registry.execute(run_id=run.run_id, step_name=step_name)
        for step_name in expected_steps
    ]

    async with WorkflowStore(db_path) as store:
        artifacts = await store.list_artifacts(run.run_id)

    assert [result.step_name for result in results] == expected_steps
    assert {step_name for step_name in expected_steps if registry.get(step_name)} == set(expected_steps)
    assert [artifact.artifact_type.value for artifact in artifacts].count("source_snapshot") >= 4
    assert [artifact.artifact_type.value for artifact in artifacts].count("rag_index") == 1
    assert [artifact.artifact_type.value for artifact in artifacts].count("rag_result") == 1
    assert [artifact.artifact_type.value for artifact in artifacts].count("strategy") >= 4
