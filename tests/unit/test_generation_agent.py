"""Unit tests for P3-5 generation prompts."""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from app.agents.content_generation_agent import BudgetExceededError
from app.agents.content_generation_agent import ContentGenerationAgent
from app.agents.content_generation_agent import ContentGenerationError
from app.agents.content_generation_agent import ProposalPool
from app.agents.content_generation_agent import SessionTokenBudget
from app.config import settings
from app.memory.session_state import SessionManager
from app.models.schemas import ContentGeneratorRequest, GenerateRequest
from app.models.session import ContentStrategy, GeneratedNote, PlatformPreference, Proposal, SessionStage
from app.prompts.generation import (
    build_language_instruction,
    get_temperature_hint,
    render_note_generation_prompts,
    render_proposal_generation_prompts,
)


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat(self, system: str, user: str, max_tokens: int = 1024, temperature: float = 0.7):
        self.calls.append(
            {
                "system": system,
                "user": user,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        return self.responses.pop(0)


def _proposal(pid: str) -> Proposal:
    return Proposal(
        proposal_id=pid,
        angle=f"角度-{pid}",
        hook=f"标题-{pid}",
        outline="要点一\n要点二\n要点三",
        target_emotion="practical_value",
        content_pillars=["教程", "测评"],
        suggested_tags=["#教程", "#测评"],
        score=0.5,
    )


def test_render_proposal_generation_prompts_keeps_json_array_contract():
    system_prompt, user_prompt = render_proposal_generation_prompts(
        content_strategy='{"positioning":"城市户外穿搭"}',
        target_audience="22-30岁通勤女生",
        n=10,
    )

    assert "默认使用中文输出" in system_prompt
    assert "只返回合法 JSON 数组" in system_prompt
    assert '"proposal_id"' in system_prompt
    assert "必须生成恰好 10 个提案" in system_prompt
    assert "22-30岁通勤女生" in user_prompt
    assert "城市户外穿搭" in user_prompt
    assert "当前输出语言要求" in user_prompt


def test_render_note_generation_prompts_keeps_xhs_style_requirements():
    system_prompt, user_prompt = render_note_generation_prompts(
        content_strategy='{"positioning":"户外风"}',
        proposal='{"proposal_id":"p1"}',
        target_audience="轻户外新手",
        temperature=0.7,
        target_emotion="practical_value",
        angle="一衣多搭",
        title_concept="通勤到露营都能穿",
        content_outline=["场景一：上班", "场景二：周末徒步", "场景三：咖啡店"],
    )

    assert "默认使用中文输出" in system_prompt
    assert "小红书风格写作原则" in system_prompt
    assert "标题：中文场景下优先 15-20 个字" in system_prompt
    assert "正文必须包含换行符" in system_prompt
    assert "标签：1-2 个大词 + 2-3 个细分词" in system_prompt
    assert "轻户外新手" in user_prompt
    assert "目标情绪（practical_value）" in user_prompt
    assert "- 场景一：上班" in user_prompt
    assert "- 场景二：周末徒步" in user_prompt
    assert "语言要求" in user_prompt


def test_get_temperature_hint_returns_distinct_styles_for_low_mid_high():
    low = get_temperature_hint(0.31)
    mid = get_temperature_hint(0.7)
    high = get_temperature_hint(1.08)

    assert "保守稳妥" in low
    assert "平台原生" in mid
    assert "高创意边界" in high
    assert low != mid != high


def test_render_note_generation_prompts_allows_explicit_language_override():
    _system_prompt, user_prompt = render_note_generation_prompts(
        content_strategy='{"positioning":"旅行"}',
        proposal='{"proposal_id":"p2"}',
        target_audience="周末出游人群",
        temperature=0.5,
        target_emotion="inspiration",
        angle="城市周边短逃离",
        title_concept="两天一夜松弛感行程",
        content_outline=["路线", "拍照点", "预算"],
        language_instruction="用户明确要求英文输出，请使用英文。",
    )

    assert "用户明确要求英文输出，请使用英文。" in user_prompt


def test_content_generator_request_defaults_to_chinese_output():
    request = ContentGeneratorRequest(topic="护肤")

    assert request.output_language == "zh-CN"


def test_generate_request_defaults_to_chinese_output():
    request = GenerateRequest(text="生成一篇笔记")

    assert request.output_language == "zh-CN"


@pytest.mark.parametrize("request_model", [ContentGeneratorRequest, GenerateRequest])
def test_request_models_keep_explicit_output_language(request_model):
    payload = {"output_language": "en-US"}
    if request_model is GenerateRequest:
        payload["text"] = "生成一篇英文笔记"
    else:
        payload["topic"] = "护肤"

    request = request_model(**payload)

    assert request.output_language == "en-US"


@pytest.mark.parametrize("request_model", [ContentGeneratorRequest, GenerateRequest])
def test_request_models_treat_blank_language_as_default(request_model):
    payload = {"output_language": "   "}
    if request_model is GenerateRequest:
        payload["text"] = "生成一篇笔记"
    else:
        payload["topic"] = "护肤"

    request = request_model(**payload)

    assert request.output_language == "zh-CN"


def test_build_language_instruction_defaults_to_chinese_platform_output():
    assert build_language_instruction() == "默认使用中文输出；若用户明确指定其他语言，则按用户要求输出。"


def test_content_generation_agent_resolves_language_instruction_from_request():
    agent = ContentGenerationAgent()
    request = ContentGeneratorRequest(topic="旅行", output_language="ja-JP")

    language_instruction = agent.resolve_language_instruction(request)

    assert language_instruction == "用户明确要求使用 ja-JP 输出，请严格使用该语言。"


@pytest.mark.asyncio
async def test_generate_proposals_returns_ten_complete_proposals():
    response = json.dumps(
        [
            {
                "proposal_id": f"prop_{i}",
                "angle": f"角度{i}",
                "title_concept": f"标题概念{i}",
                "content_outline": [f"要点{i}-1", f"要点{i}-2", f"要点{i}-3"],
                "target_emotion": "curiosity",
                "expected_engagement": 0.5 + i * 0.01,
            }
            for i in range(1, 11)
        ],
        ensure_ascii=False,
    )
    strategy = ContentStrategy(
        positioning="城市轻运动",
        target_audience="20-30岁女生",
        content_pillars=["穿搭", "通勤", "户外"],
        key_messaging="真实可执行",
        content_types=["图文"],
        posting_strategy="晚间",
        data_source_quality=0.8,
    )
    agent = ContentGenerationAgent(llm_client=FakeLLM([response]))

    proposals = await agent.generate_proposals(
        content_strategy=strategy,
        target_audience="20-30岁女生",
        output_language="zh-CN",
    )

    assert len(proposals) == 10
    assert proposals[0].proposal_id == "prop_1"
    assert proposals[0].angle == "角度1"
    assert proposals[0].hook == "标题概念1"
    assert "要点1-1" in proposals[0].outline
    assert proposals[0].content_pillars == ["穿搭", "通勤", "户外"]
    assert proposals[0].suggested_tags == ["穿搭", "通勤", "户外"]


def test_score_proposals_ranks_high_fit_item_first():
    agent = ContentGenerationAgent()
    preferences = PlatformPreference(
        avg_title_length=12,
        popular_tags=["教程", "抹茶"],
        optimal_posting_times=["20:00"],
        content_patterns=["疑问式标题", "中等长度文案"],
    )
    proposals = [
        Proposal(
            proposal_id="p-low",
            angle="轻聊天",
            hook="随便聊聊",
            outline="短",
            target_emotion="relatability",
            content_pillars=["生活"],
            suggested_tags=["闲聊"],
        ),
        Proposal(
            proposal_id="p-high",
            angle="教程",
            hook="抹茶拿铁怎么做？",
            outline="步骤一\n步骤二\n步骤三" * 10,
            target_emotion="practical_value",
            content_pillars=["教程"],
            suggested_tags=["教程", "抹茶"],
        ),
    ]

    ranked = agent.score_proposals(proposals, preferences)

    assert ranked[0].proposal_id == "p-high"
    assert ranked[0].score > ranked[1].score


def test_select_top_k_returns_highest_scores_in_descending_order():
    agent = ContentGenerationAgent()
    proposals = [
        Proposal(
            proposal_id="p1",
            angle="a1",
            hook="h1",
            outline="o1",
            target_emotion="curiosity",
            content_pillars=[],
            suggested_tags=[],
            score=0.2,
        ),
        Proposal(
            proposal_id="p2",
            angle="a2",
            hook="h2",
            outline="o2",
            target_emotion="curiosity",
            content_pillars=[],
            suggested_tags=[],
            score=0.9,
        ),
        Proposal(
            proposal_id="p3",
            angle="a3",
            hook="h3",
            outline="o3",
            target_emotion="curiosity",
            content_pillars=[],
            suggested_tags=[],
            score=0.6,
        ),
    ]

    top_two = agent.select_top_k(proposals, k=2)

    assert [proposal.proposal_id for proposal in top_two] == ["p2", "p3"]


@pytest.mark.asyncio
async def test_parallel_generate_uses_five_slots_and_temperature_mapping():
    responses = [
        json.dumps(
            {
                "title": f"标题{i}",
                "content": f"正文{i}\n第二段",
                "tags": [f"#标签{i}"],
                "cover_design_prompt": f"封面{i}",
                "suggested_update_time": "2026-03-18 20:00",
            },
            ensure_ascii=False,
        )
        for i in range(5)
    ]
    llm = FakeLLM(responses)
    agent = ContentGenerationAgent(llm_client=llm)
    strategy = ContentStrategy(
        positioning="轻运动",
        target_audience="城市女生",
        content_pillars=["教程", "穿搭"],
        key_messaging="真实可执行",
        content_types=["图文"],
        posting_strategy="晚间",
        data_source_quality=0.8,
    )

    notes = await agent._parallel_generate(
        proposals=[_proposal(f"p{i}") for i in range(5)],
        content_strategy=strategy,
        target_audience="城市女生",
    )

    assert len(notes) == 5
    assert [note.generation_params["temperature"] for note in notes] == [0.3, 0.5, 0.7, 0.9, 1.1]
    assert [call["temperature"] for call in llm.calls] == [0.3, 0.5, 0.7, 0.9, 1.1]


@pytest.mark.asyncio
async def test_parallel_generate_injects_temperature_hint_into_prompt():
    responses = [
        json.dumps(
            {
                "title": "标题",
                "content": "正文\n第二段",
                "tags": ["#标签"],
                "cover_design_prompt": "封面",
                "suggested_update_time": "2026-03-18 20:00",
            },
            ensure_ascii=False,
        )
        for _ in range(2)
    ]
    llm = FakeLLM(responses)
    agent = ContentGenerationAgent(llm_client=llm)

    await agent._parallel_generate(
        proposals=[_proposal("p1"), _proposal("p2")],
        content_strategy='{"positioning":"轻运动"}',
        target_audience="城市女生",
    )

    assert "保守稳妥风格" in llm.calls[0]["user"]
    assert "平衡易读风格" in llm.calls[1]["user"]


@pytest.mark.asyncio
async def test_parallel_generate_keeps_other_slots_when_one_slot_fails(monkeypatch):
    agent = ContentGenerationAgent()

    async def fake_generate_single(**kwargs):
        slot_id = kwargs["slot_id"]
        proposal = kwargs["proposal"]
        temperature = kwargs["temperature"]
        if slot_id == 2:
            raise ContentGenerationError("slot failed")
        return GeneratedNote(
            note_id=f"note-{slot_id}",
            title=f"title-{slot_id}",
            content="正文\n第二段",
            tags=["#标签"],
            cover_design_prompt="封面",
            suggested_update_time="2026-03-18 20:00",
            similarity_check={"max_similarity": 0.0, "status": "safe"},
            generation_params={
                "temperature": temperature,
                "proposal_id": proposal.proposal_id,
                "slot_id": slot_id,
            },
        )

    monkeypatch.setattr(agent, "_generate_single", fake_generate_single)

    notes = await agent._parallel_generate(
        proposals=[_proposal(f"p{i}") for i in range(5)],
        content_strategy='{"positioning":"轻运动"}',
        target_audience="城市女生",
    )

    assert len(notes) == 4
    assert all(note.generation_params["slot_id"] != 2 for note in notes)


@pytest.mark.asyncio
async def test_check_similarity_retries_when_embedding_similarity_exceeds_threshold():
    agent = ContentGenerationAgent()
    note = GeneratedNote(
        note_id="n1",
        title="标题",
        content="正文\n第二段",
        tags=["#标签"],
        cover_design_prompt="封面",
        suggested_update_time="2026-03-18 20:00",
        similarity_check={"max_similarity": 0.0, "status": "safe"},
        generation_params={"proposal_id": "p1", "temperature": 0.7, "slot_id": 0},
    )

    result = await agent._check_similarity(
        note,
        session_id=None,
        similar_posts=[type("Similar", (), {"similarity": 0.75, "content": "近似正文"})()],
    )

    assert result.should_retry is True
    assert result.status == "rewritten"
    assert result.embedding_similarity == 0.75


@pytest.mark.asyncio
async def test_check_similarity_warns_on_mid_embedding_similarity_only():
    agent = ContentGenerationAgent()
    note = GeneratedNote(
        note_id="n1",
        title="标题",
        content="正文\n第二段",
        tags=["#标签"],
        cover_design_prompt="封面",
        suggested_update_time="2026-03-18 20:00",
        similarity_check={"max_similarity": 0.0, "status": "safe"},
        generation_params={"proposal_id": "p1", "temperature": 0.7, "slot_id": 0},
    )

    result = await agent._check_similarity(
        note,
        session_id=None,
        similar_posts=[type("Similar", (), {"similarity": 0.45, "content": "近似正文"})()],
    )

    assert result.should_retry is False
    assert result.status == "warning"


@pytest.mark.asyncio
async def test_check_similarity_warns_on_lexical_overlap_without_retry():
    agent = ContentGenerationAgent()
    note = GeneratedNote(
        note_id="n1",
        title="标题",
        content="几乎完全一样的正文内容",
        tags=["#标签"],
        cover_design_prompt="封面",
        suggested_update_time="2026-03-18 20:00",
        similarity_check={"max_similarity": 0.0, "status": "safe"},
        generation_params={"proposal_id": "p1", "temperature": 0.7, "slot_id": 0},
    )

    result = await agent._check_similarity(
        note,
        session_id=None,
        similar_posts=[type("Similar", (), {"similarity": 0.2, "content": "几乎完全一样的正文内容"})()],
    )

    assert result.should_retry is False
    assert result.status == "warning"
    assert result.embedding_similarity == 0.2
    assert result.lexical_overlap > 0.4


@pytest.mark.asyncio
async def test_proposal_pool_skips_high_risk_proposals():
    pool = ProposalPool([_proposal("p1"), _proposal("p2"), _proposal("p3")], slot_limit=2)
    first = await pool.select_proposal(0)
    assert first is not None
    await pool.mark_high_risk(first)

    second = await pool.select_proposal(0)

    assert second is not None
    assert second.proposal_id != first.proposal_id
    assert first.is_high_risk is True


@pytest.mark.asyncio
async def test_generate_with_retry_stops_after_two_reselections(monkeypatch):
    agent = ContentGenerationAgent()
    proposals = [_proposal("p1"), _proposal("p2"), _proposal("p3")]
    pool = ProposalPool(proposals, slot_limit=1)

    async def fake_generate_single(**kwargs):
        proposal = kwargs["proposal"]
        return GeneratedNote(
            note_id=f"note-{proposal.proposal_id}",
            title=f"title-{proposal.proposal_id}",
            content="正文\n第二段",
            tags=["#标签"],
            cover_design_prompt="封面",
            suggested_update_time="2026-03-18 20:00",
            similarity_check={"max_similarity": 0.0, "status": "safe"},
            generation_params={
                "temperature": kwargs["temperature"],
                "proposal_id": proposal.proposal_id,
                "slot_id": kwargs["slot_id"],
            },
        )

    async def always_retry(note, *, session_id, similar_posts=None):
        del note, session_id, similar_posts
        return type(
            "Similarity",
            (),
            {
                "embedding_similarity": 0.9,
                "lexical_overlap": 0.1,
                "should_retry": True,
                "status": "rewritten",
            },
        )()

    monkeypatch.setattr(agent, "_generate_single", fake_generate_single)
    monkeypatch.setattr(agent, "_check_similarity", always_retry)

    with pytest.raises(ContentGenerationError):
        await agent._generate_with_retry(
            slot_id=0,
            proposal_pool=pool,
            content_strategy='{"positioning":"轻运动"}',
            target_audience="城市女生",
            temperature=0.7,
            output_language="zh-CN",
            session_id=None,
            semaphore=asyncio.Semaphore(1),
            budget=None,
        )

    assert proposals[0].is_high_risk is True
    assert proposals[1].is_high_risk is True
    assert proposals[2].is_high_risk is True


async def _seed_generation_session(db_path: str, session_id: str) -> None:
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


@pytest.mark.asyncio
async def test_execute_returns_failed_when_all_generation_slots_fail(tmp_path, monkeypatch):
    db_path = str(tmp_path / "generation-failed.db")
    session_id = str(uuid.uuid4())
    await _seed_generation_session(db_path, session_id)
    agent = ContentGenerationAgent(session_manager=SessionManager(db_path))

    async def fake_generate_proposals(**kwargs):
        del kwargs
        return [_proposal(f"p{i}") for i in range(5)]

    async def fake_parallel_generate(**kwargs):
        del kwargs
        return []

    monkeypatch.setattr(agent, "generate_proposals", fake_generate_proposals)
    monkeypatch.setattr(agent, "_parallel_generate", fake_parallel_generate)

    result = await agent.execute(session_id)

    assert result.success is False
    assert result.status == "failed"
    assert result.error_code == "GENERATION_PARTIAL_FAILURE"


@pytest.mark.asyncio
async def test_execute_returns_failed_when_session_is_missing(tmp_path):
    db_path = str(tmp_path / "generation-missing.db")
    agent = ContentGenerationAgent(session_manager=SessionManager(db_path))

    result = await agent.execute("missing-session")

    assert result.success is False
    assert result.status == "failed"
    assert result.error_code == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_execute_returns_invalid_stage_when_strategy_data_is_missing(tmp_path):
    db_path = str(tmp_path / "generation-invalid-stage.db")
    session_id = str(uuid.uuid4())
    async with SessionManager(db_path) as manager:
        await manager.create_session(session_id, "u1", "轻运动")

    agent = ContentGenerationAgent(session_manager=SessionManager(db_path))

    result = await agent.execute(session_id)

    assert result.success is False
    assert result.status == "failed"
    assert result.error_code == "INVALID_STAGE"

    async with SessionManager(db_path) as manager:
        session = await manager.get_session(session_id)
        assert session is not None
        assert session.stage == SessionStage.FAILED
        assert session.error is not None
        assert session.error.code == "INVALID_STAGE"


@pytest.mark.asyncio
async def test_execute_returns_partial_when_budget_exceeded_with_some_notes(tmp_path, monkeypatch):
    db_path = str(tmp_path / "generation-budget.db")
    session_id = str(uuid.uuid4())
    await _seed_generation_session(db_path, session_id)
    monkeypatch.setattr(settings, "SESSION_TOKEN_BUDGET", 300)
    monkeypatch.setattr(settings, "GENERATION_PARALLEL_SLOTS", 3)
    monkeypatch.setattr(settings, "GENERATION_DEGRADED_SLOTS", 2)
    monkeypatch.setattr(settings, "PARALLEL_TEMPERATURES", [0.3, 0.5, 0.7])

    async def fake_generate_proposals(**kwargs):
        del kwargs
        return [_proposal(f"p{i}") for i in range(5)]

    agent = ContentGenerationAgent(session_manager=SessionManager(db_path))
    monkeypatch.setattr(agent, "generate_proposals", fake_generate_proposals)

    async def fake_parallel_generate(**kwargs):
        budget = kwargs["budget"]
        note = GeneratedNote(
            note_id="note-1",
            title="标题1",
            content="正文一\n第二段",
            tags=["#标签1"],
            cover_design_prompt="封面1",
            suggested_update_time="2026-03-18 20:00",
            similarity_check={"max_similarity": 0.0, "status": "safe"},
            generation_params={"proposal_id": "p0", "temperature": 0.3, "slot_id": 0},
        )
        try:
            await budget.consume("x" * 2000)
        except ContentGenerationError:
            pass
        return [note]

    monkeypatch.setattr(agent, "_parallel_generate", fake_parallel_generate)

    result = await agent.execute(session_id)

    assert result.success is True
    assert result.status == "partial"
    assert result.error_code == "BUDGET_EXCEEDED"
    assert len(result.notes) >= 1
    assert result.similarity_report["budget_exceeded"] is True


@pytest.mark.asyncio
async def test_session_token_budget_tracks_remaining_and_raises_when_exceeded():
    budget = SessionTokenBudget(session_budget=3)

    used = await budget.consume("1234")

    assert used == 1
    assert budget.remaining == 2
    assert budget.usage_estimated is True

    with pytest.raises(BudgetExceededError):
        await budget.consume("x" * 16)

    assert budget.remaining == 0


@pytest.mark.asyncio
async def test_generate_uses_request_fallbacks_and_returns_first_note(monkeypatch):
    agent = ContentGenerationAgent()
    request = ContentGeneratorRequest(
        topic="护肤",
        brand_preference="实验室风",
        content_type="图文",
        output_language="en-US",
    )
    expected_note = GeneratedNote(
        note_id="note-1",
        title="Title",
        content="Body\nMore",
        tags=["#tag"],
        cover_design_prompt="cover",
        suggested_update_time="2026-03-18 20:00",
        similarity_check={"max_similarity": 0.0, "status": "safe"},
        generation_params={"proposal_id": "p1", "temperature": 0.3, "slot_id": 0},
    )
    captured: dict[str, object] = {}

    async def fake_generate_proposals(**kwargs):
        captured["strategy"] = kwargs["content_strategy"]
        captured["target_audience"] = kwargs["target_audience"]
        captured["output_language"] = kwargs["output_language"]
        return [_proposal("p1")]

    def fake_score_proposals(proposals, preferences):
        captured["preferences"] = preferences
        return proposals

    async def fake_parallel_generate(**kwargs):
        captured["parallel_strategy"] = kwargs["content_strategy"]
        return [expected_note]

    monkeypatch.setattr(agent, "generate_proposals", fake_generate_proposals)
    monkeypatch.setattr(agent, "score_proposals", fake_score_proposals)
    monkeypatch.setattr(agent, "_parallel_generate", fake_parallel_generate)

    result = await agent.generate(request)

    assert result.title == "Title"
    assert result.content == "Body\nMore"
    assert result.tags == ["#tag"]
    assert result.cover_design_prompt == "cover"
    assert result.designed_update_time == "2026-03-18 20:00"
    assert captured["target_audience"] == "大众用户"
    assert captured["output_language"] == "en-US"
    assert isinstance(captured["strategy"], ContentStrategy)
    assert captured["strategy"].positioning == "实验室风"
    assert captured["strategy"].content_pillars == ["护肤"]
    assert isinstance(captured["preferences"], PlatformPreference)
    assert captured["preferences"].optimal_posting_times == ["20:00"]
    assert captured["parallel_strategy"] == captured["strategy"]


@pytest.mark.asyncio
async def test_generate_raises_when_no_notes_are_produced(monkeypatch):
    agent = ContentGenerationAgent()

    async def fake_generate_proposals(**kwargs):
        del kwargs
        return [_proposal("p1")]

    async def fake_parallel_generate(**kwargs):
        del kwargs
        return []

    monkeypatch.setattr(agent, "generate_proposals", fake_generate_proposals)
    monkeypatch.setattr(agent, "_parallel_generate", fake_parallel_generate)

    with pytest.raises(ContentGenerationError, match="Generation produced no notes."):
        await agent.generate(ContentGeneratorRequest(topic="护肤"))
