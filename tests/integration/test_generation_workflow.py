from __future__ import annotations

import json
import uuid

import pytest

from app.agents.content_generation_agent import ContentGenerationAgent
from app.config import settings
from app.memory.session_state import SessionManager
from app.models.session import ContentStrategy, PlatformPreference, SessionStage


class FakeLLM:
    def __init__(self, outputs):
        self.outputs = list(outputs)

    async def chat(self, system: str, user: str, max_tokens: int = 1024, temperature: float = 0.7):
        del system, user, max_tokens, temperature
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


class FakeRAG:
    def __init__(self, similarities):
        self.similarities = list(similarities)

    async def query_similar(self, session_id: str, content: str, top_k: int = 3):
        del session_id, content, top_k
        payload = self.similarities.pop(0) if self.similarities else []
        return [type("Similar", (), item)() for item in payload]


async def _create_generation_ready_session(db_path: str, session_id: str) -> None:
    async with SessionManager(db_path) as manager:
        await manager.create_session(session_id, "u1", "轻运动")
        await manager.update_session(
            session_id,
            stage=SessionStage.STRATEGY,
            content_strategy=ContentStrategy(
                positioning="轻运动",
                target_audience="城市女生",
                content_pillars=["教程", "穿搭"],
                key_messaging="真实可执行",
                content_types=["图文"],
                posting_strategy="晚间",
                data_source_quality=0.8,
            ),
            platform_preference=PlatformPreference(
                avg_title_length=12,
                popular_tags=["教程", "穿搭"],
                optimal_posting_times=["20:00"],
                content_patterns=["中等长度文案"],
            ),
        )


def _proposal_payload() -> str:
    return json.dumps(
        [
            {
                "proposal_id": f"prop_{i}",
                "angle": f"角度{i}",
                "title_concept": f"标题概念{i}",
                "content_outline": ["要点1", "要点2", "要点3"],
                "target_emotion": "curiosity",
                "expected_engagement": 0.5 + i * 0.01,
            }
            for i in range(1, 11)
        ],
        ensure_ascii=False,
    )


def _note_payload(index: int) -> str:
    return json.dumps(
        {
            "title": f"标题{index}",
            "content": f"正文{index}\n第二段",
            "tags": [f"#标签{index}"],
            "cover_design_prompt": f"封面{index}",
            "suggested_update_time": "2026-03-18 20:00",
        },
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_generation_workflow_success_persists_notes_and_similarity_report(tmp_path, monkeypatch):
    db_path = str(tmp_path / "generation-success.db")
    session_id = str(uuid.uuid4())
    await _create_generation_ready_session(db_path, session_id)
    monkeypatch.setattr(settings, "GENERATION_PARALLEL_SLOTS", 3)
    monkeypatch.setattr(settings, "PARALLEL_TEMPERATURES", [0.3, 0.5, 0.7])

    agent = ContentGenerationAgent(
        llm_client=FakeLLM([_proposal_payload(), _note_payload(1), _note_payload(2), _note_payload(3)]),
        rag_service=FakeRAG([[], [], []]),
        session_manager=SessionManager(db_path),
    )

    result = await agent.execute(session_id)

    assert result.success is True
    assert result.status == "success"
    assert len(result.notes) == 3

    async with SessionManager(db_path) as manager:
        session = await manager.get_session(session_id)
        assert session is not None
        assert session.stage.value == "completed"
        assert session.generated_notes is not None
        assert len(session.generated_notes) == 3
        assert session.similarity_report is not None
        assert session.similarity_report["notes_generated"] == 3


@pytest.mark.asyncio
async def test_generation_workflow_high_similarity_reselects_and_succeeds(tmp_path, monkeypatch):
    db_path = str(tmp_path / "generation-reselect.db")
    session_id = str(uuid.uuid4())
    await _create_generation_ready_session(db_path, session_id)
    monkeypatch.setattr(settings, "GENERATION_PARALLEL_SLOTS", 1)
    monkeypatch.setattr(settings, "PARALLEL_TEMPERATURES", [0.3])

    agent = ContentGenerationAgent(
        llm_client=FakeLLM([_proposal_payload(), _note_payload(1), _note_payload(2)]),
        rag_service=FakeRAG(
            [
                [{"similarity": 0.8, "content": "近似内容"}],
                [{"similarity": 0.1, "content": "安全内容"}],
            ]
        ),
        session_manager=SessionManager(db_path),
    )

    result = await agent.execute(session_id)

    assert result.success is True
    assert len(result.notes) == 1
    assert result.notes[0].similarity_check["status"] == "safe"

    async with SessionManager(db_path) as manager:
        session = await manager.get_session(session_id)
        assert session is not None
        assert session.proposals is not None
        assert any(proposal.is_high_risk for proposal in session.proposals)


@pytest.mark.asyncio
async def test_generation_workflow_slot_failure_keeps_other_slots(tmp_path, monkeypatch):
    db_path = str(tmp_path / "generation-partial.db")
    session_id = str(uuid.uuid4())
    await _create_generation_ready_session(db_path, session_id)
    monkeypatch.setattr(settings, "GENERATION_PARALLEL_SLOTS", 3)
    monkeypatch.setattr(settings, "PARALLEL_TEMPERATURES", [0.3, 0.5, 0.7])

    agent = ContentGenerationAgent(
        llm_client=FakeLLM([_proposal_payload(), _note_payload(1), Exception("slot fail"), _note_payload(3)]),
        rag_service=FakeRAG([[], [], []]),
        session_manager=SessionManager(db_path),
    )

    result = await agent.execute(session_id)

    assert result.success is True
    assert result.status == "partial"
    assert result.error_code == "GENERATION_PARTIAL_FAILURE"
    assert len(result.notes) == 2


@pytest.mark.asyncio
async def test_generation_workflow_writes_similarity_report_fields(tmp_path, monkeypatch):
    db_path = str(tmp_path / "generation-report.db")
    session_id = str(uuid.uuid4())
    await _create_generation_ready_session(db_path, session_id)
    monkeypatch.setattr(settings, "GENERATION_PARALLEL_SLOTS", 2)
    monkeypatch.setattr(settings, "PARALLEL_TEMPERATURES", [0.3, 0.5])

    agent = ContentGenerationAgent(
        llm_client=FakeLLM([_proposal_payload(), _note_payload(1), _note_payload(2)]),
        rag_service=FakeRAG(
            [
                [{"similarity": 0.45, "content": "有点像"}],
                [],
            ]
        ),
        session_manager=SessionManager(db_path),
    )

    result = await agent.execute(session_id)

    assert result.similarity_report["notes_generated"] == 2
    assert result.similarity_report["failed_count"] == 0
    assert isinstance(result.similarity_report["similarity_warnings"], list)
