"""Real-dependency gates for T10.1 workflow external-call boundaries."""

from __future__ import annotations

import importlib.util
import os
from dataclasses import asdict

import pytest

from app.config import settings
from app.llm.client import LLMClient
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowArtifactType, WorkflowPhase
from app.services.conversation_orchestrator import LLMStructuredConstraintClassifier
from app.services.rag_service import RAGService
from app.services.step_executors import RetrievalStepExecutor
from app.services.workflow_run_manager import WorkflowRunManager
from app.services.xhs_spider import XHSPost, XHSSpiderClient


def _real_enabled() -> None:
    if os.getenv("ACCEPTANCE_RUN_REAL") != "1":
        pytest.skip("set ACCEPTANCE_RUN_REAL=1 to run T10.1 real dependency tests")


def _has_llm_credentials() -> bool:
    provider = settings.LLM_PROVIDER.lower()
    key_map = {
        "anthropic": settings.ANTHROPIC_API_KEY,
        "deepseek": settings.DEEPSEEK_API_KEY,
        "minimax": settings.MINIMAX_API_KEY,
        "kimi": settings.KIMI_API_KEY,
        "openai": settings.OPENAI_API_KEY,
    }
    return bool((key_map.get(provider) or "").strip())


def _sample_post(note_id: str = "real-rag-note") -> XHSPost:
    return XHSPost(
        note_id=note_id,
        title="敏感肌屏障修护",
        content="换季泛红时先减少刺激，再使用修护类精华建立屏障。",
        author="integration",
        tags=["敏感肌", "修护"],
        liked_count=120,
        collected_count=70,
        comment_count=10,
        share_count=5,
        note_url=f"https://example.com/{note_id}",
        images=[],
    )


@pytest.fixture
def real_db(tmp_path, monkeypatch):
    db_path = tmp_path / "workflow_real_step_boundaries.db"
    chroma_dir = tmp_path / "chroma"
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(db_path))
    monkeypatch.setattr(settings, "CHROMA_PERSIST_DIR", str(chroma_dir))
    return str(db_path)


async def _seed_run_with_step(db_path: str, step_name: str, phase: WorkflowPhase):
    async with ThreadStore(db_path) as thread_store:
        thread = await thread_store.create_thread(title="T10.1 real boundary")
        message = await thread_store.append_message(
            thread_id=thread["id"],
            role="user",
            text="敏感肌修护",
        )

    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(
            thread_id=thread["id"],
            user_id="real-user",
            user_message_id=message["id"],
            initial_request="敏感肌修护",
        )
        step = (
            await manager.initialize_steps(
                run.run_id,
                [{"step_name": step_name, "phase": phase}],
            )
        )[0]
    return run, step


@pytest.mark.real_dependency
@pytest.mark.asyncio
async def test_real_llm_structured_constraint_classifier_returns_schema():
    _real_enabled()
    if not _has_llm_credentials():
        pytest.skip(f"credentials for LLM_PROVIDER={settings.LLM_PROVIDER!r} are required")

    classifier = LLMStructuredConstraintClassifier(llm_client=LLMClient(), fallback=None)

    result = await classifier.classify("目标用户改成25到35岁的敏感肌女性，语气生活化一点")

    assert result.constraint_type.value in {item.value for item in result.constraint_type.__class__}
    assert result.scope
    assert 0.0 <= result.confidence <= 1.0
    assert isinstance(result.normalized, dict)


@pytest.mark.real_dependency
@pytest.mark.asyncio
async def test_real_spider_dependency_smoke_records_posts():
    _real_enabled()
    if not settings.XHS_SPIDER_COOKIES.strip():
        pytest.skip("XHS_SPIDER_COOKIES is required for real spider boundary tests")

    posts = await XHSSpiderClient().search_with_retry("敏感肌修护", num=3)

    assert posts
    assert posts[0].note_id
    assert posts[0].title


@pytest.mark.real_dependency
@pytest.mark.asyncio
async def test_real_rag_index_step_persists_artifact_through_workflow_executor(real_db):
    _real_enabled()
    if importlib.util.find_spec("sentence_transformers") is None:
        pytest.skip("sentence-transformers is required for real RAG boundary tests")

    run, step = await _seed_run_with_step(real_db, "retrieval.rag_index", WorkflowPhase.RETRIEVAL)
    post = _sample_post()
    async with WorkflowRunManager(real_db) as manager:
        await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.SOURCE_SNAPSHOT,
            payload={"items": [post.model_dump(mode="json")]},
            summary_text="real rag source",
        )

    rag = RAGService(persist_dir=settings.CHROMA_PERSIST_DIR)

    async def rag_index_runner(context):
        posts = [post]
        quality = await rag.index_documents(context.run["run_id"], posts, context.user_request or "敏感肌修护")
        return asdict(quality)

    result = await RetrievalStepExecutor(db_path=real_db, rag_runner=rag_index_runner).execute(
        run.run_id,
        step.step_name,
    )

    async with WorkflowStore(real_db) as store:
        artifacts = await store.list_artifacts(run.run_id)

    assert result.artifact_refs
    assert any(artifact.artifact_type == WorkflowArtifactType.RAG_INDEX for artifact in artifacts)
