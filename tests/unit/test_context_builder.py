"""Unit tests for T5 ContextBuilder."""

from __future__ import annotations

import pytest

from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowArtifactType, WorkflowConstraintType, WorkflowPhase
from app.services.context_builder import ContextBuilder, STEP_DEFINITIONS
from app.services.workflow_run_manager import WorkflowRunManager


@pytest.fixture
async def seeded(tmp_path):
    db_path = str(tmp_path / "context_builder.db")
    async with ThreadStore(db_path) as thread_store:
        thread = await thread_store.create_thread(title="Context")
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
            steps = await manager.initialize_steps(
                run.run_id,
                [
                    {"step_name": "discovery.plan_queries", "phase": WorkflowPhase.DISCOVERY},
                    {"step_name": "strategy.llm_synthesize", "phase": WorkflowPhase.STRATEGY},
                    {
                        "step_name": "generation.generate_notes_parallel",
                        "phase": WorkflowPhase.GENERATION,
                    },
                ],
            )
    async with WorkflowStore(db_path) as store:
        await store.create_constraint(
            run_id=run.run_id,
            thread_id=run.thread_id,
            message_id="msg-style",
            raw_text="语气更生活化",
            constraint_type=WorkflowConstraintType.STYLE,
            scope="run",
            normalized={"tone": "lifestyle"},
        )
        await store.create_constraint(
            run_id=run.run_id,
            thread_id=run.thread_id,
            message_id="msg-topic",
            raw_text="主题改成露营",
            constraint_type=WorkflowConstraintType.TOPIC_CHANGE,
            scope="run",
            normalized={"topic": "camping"},
        )
        await store.create_artifact(
            run_id=run.run_id,
            thread_id=run.thread_id,
            artifact_type=WorkflowArtifactType.STRATEGY,
            payload={"positioning": "防晒衣"},
        )
        await store.create_artifact(
            run_id=run.run_id,
            thread_id=run.thread_id,
            artifact_type=WorkflowArtifactType.PROPOSAL,
            payload={"proposal_id": "p1"},
        )
        await store.create_artifact(
            run_id=run.run_id,
            thread_id=run.thread_id,
            artifact_type=WorkflowArtifactType.SOURCE_SNAPSHOT,
            payload={"source": "xhs"},
        )
    return db_path, run, steps


def test_step_definitions_include_all_t5_steps():
    expected = {
        "intake.capture_request",
        "context.build_context",
        "context.load_constraints",
        "context.load_previous_artifacts",
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
        "generation.plan_proposals",
        "generation.select_proposals",
        "generation.generate_notes_parallel",
        "generation.similarity_check",
        "generation.rewrite_or_reselect",
        "generation.aggregate_notes",
        "finalization.persist_artifacts",
        "finalization.emit_result_ready",
        "review.await_user_acceptance",
        "review.publish_candidates",
    }
    assert expected <= set(STEP_DEFINITIONS)


@pytest.mark.asyncio
async def test_different_steps_take_relevant_context(seeded):
    db_path, run, _steps = seeded
    builder = ContextBuilder(db_path)

    discovery_context = await builder.build_context(run.run_id, "discovery.plan_queries")
    generation_context = await builder.build_context(run.run_id, "generation.generate_notes_parallel")

    assert discovery_context.user_request == "帮我生成防晒衣内容策略"
    assert discovery_context.input_artifacts == []
    assert [artifact["artifact_type"] for artifact in generation_context.input_artifacts] == [
        "strategy",
        "proposal",
    ]
    assert generation_context.source_context == {"artifacts": []}
    assert generation_context.generation_targets[0]["artifact_type"] == "proposal"


@pytest.mark.asyncio
async def test_style_constraint_is_included_for_generation_step(seeded):
    db_path, run, _steps = seeded
    builder = ContextBuilder(db_path)

    context = await builder.build_context(run.run_id, "generation.generate_notes_parallel")

    constraint_types = {constraint["constraint_type"] for constraint in context.constraints}
    assert "style" in constraint_types
    assert "topic_change" not in constraint_types


@pytest.mark.asyncio
async def test_topic_change_is_marked_pending_for_generation_step(seeded):
    db_path, run, _steps = seeded
    builder = ContextBuilder(db_path)

    context = await builder.build_context(run.run_id, "generation.generate_notes_parallel")

    pending_types = {constraint["constraint_type"] for constraint in context.pending_constraints}
    assert "topic_change" in pending_types
    assert "style" not in pending_types


@pytest.mark.asyncio
async def test_input_hash_is_stable_and_persisted(seeded):
    db_path, run, steps = seeded
    builder = ContextBuilder(db_path)

    first = await builder.build_context(run.run_id, "generation.generate_notes_parallel")
    second = await builder.build_context(run.run_id, "generation.generate_notes_parallel")

    assert first.input_hash == second.input_hash
    async with WorkflowStore(db_path) as store:
        step = await store.get_step(steps[2].step_id)
    assert step is not None
    assert step.input_hash == first.input_hash


@pytest.mark.asyncio
async def test_input_hash_changes_when_relevant_input_changes(seeded):
    db_path, run, _steps = seeded
    builder = ContextBuilder(db_path)
    first = await builder.build_context(run.run_id, "generation.generate_notes_parallel")

    async with WorkflowStore(db_path) as store:
        await store.create_constraint(
            run_id=run.run_id,
            thread_id=run.thread_id,
            message_id="msg-forbidden",
            raw_text="不要太硬广",
            constraint_type=WorkflowConstraintType.FORBIDDEN_WORDS,
            scope="run",
            normalized={"avoid": "hard_sell"},
        )

    second = await builder.build_context(run.run_id, "generation.generate_notes_parallel")

    assert second.input_hash != first.input_hash
