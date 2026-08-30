from __future__ import annotations

import inspect
import json
import sqlite3
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest

from app.api.routes.router import app
from app.content_research.presearch.service import PresearchService
from app.content_research.query import ContentResearchQueryService
from app.content_research.service import ContentResearchService, WorkflowRunManagerRuntime
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.thread_store import ThreadStore
from app.services.llm.types import LLMResponse, TokenUsage


class _FakeLLM:
    async def generate(self, _request: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                {
                    "subject_confirmation": "已识别本轮需要调研的对象与方向。",
                    "competitor_tags": ["迪卡侬", "凯乐石"],
                    "research_directions": ["产品营销"],
                    "custom_competitor_input": "",
                    "subject_structure": {
                        "schema_version": "content_research_subject_structure_v1",
                        "canonical_subject": "夏季凉感T恤",
                        "subject_type": "category",
                        "source_terms": ["夏季", "凉感", "T恤"],
                        "term_roles": {
                            "core_object": ["T恤"],
                            "product_experience": ["凉感"],
                            "context_audience": ["夏季"],
                        },
                        "core_entities": [
                            {"canonical_name": "T恤", "raw_mentions": ["T恤"]}
                        ],
                        "research_intents": ["凉感"],
                        "context_modifiers": ["夏季"],
                        "synonym_groups": {"T恤": ["冰感T恤"]},
                        "ambiguities": [],
                        "resolution_state": "resolved",
                    },
                },
                ensure_ascii=False,
            ),
            provider="fake",
            model="fake-model",
            usage=TokenUsage(total_tokens=10),
            latency_ms=1,
        )


def _database_snapshot(db_path: str) -> tuple[str, ...]:
    with sqlite3.connect(db_path) as connection:
        return tuple(connection.iterdump())


def _public_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


async def _capture(call: Callable[[], Any | Awaitable[Any]]) -> tuple[str, Any]:
    try:
        value = call()
        if inspect.isawaitable(value):
            value = await value
    except Exception as exc:  # noqa: BLE001
        return "error", (type(exc).__name__, str(exc))
    return "value", _public_value(value)


@pytest.mark.asyncio
async def test_query_interface_matches_public_read_contract_without_writes(tmp_path):
    db_path = str(tmp_path / "content-research.db")
    async with ThreadStore(db_path) as thread_store:
        thread = await thread_store.create_thread(
            title="夏季凉感T恤",
            workspace_id="ws-query",
            brand_id="brand-query",
        )
    service = ContentResearchService(
        store=SQLiteContentResearchStore(db_path),
        presearch=PresearchService(
            _FakeLLM(),
            first_feedback_timeout_seconds=0.05,
            hard_cutoff_seconds=0.1,
        ),
        workflow_runtime=WorkflowRunManagerRuntime(db_path),
    )
    presearch = await service.submit_presearch(
        command_id="query-interface-fixture",
        seed_text="夏季凉感T恤",
        user_note="关注通勤场景",
        thread_id=thread["id"],
        user_id="user-query",
        workspace_id="ws-query",
    )
    query = ContentResearchQueryService(
        service,
        store=service._store,
        lifecycle=service._lifecycle,
        workflow_runtime=service._workflow_runtime,
    )
    run_id = presearch.workflow_run_id
    cases: tuple[
        tuple[str, Callable[[], Any | Awaitable[Any]], Callable[[], Any | Awaitable[Any]]],
        ...,
    ] = (
        ("presearch", lambda: service.get_presearch(presearch.attempt_id), lambda: query.get_presearch(presearch.attempt_id)),
        ("workflow", lambda: service.get_workflow_summary(run_id), lambda: query.get_workflow_summary(run_id)),
        ("policy", lambda: service.get_policy_snapshot(run_id), lambda: query.get_policy_snapshot(run_id)),
        ("events", lambda: service.list_workflow_events(run_id), lambda: query.list_workflow_events(run_id)),
        ("scope", lambda: service.get_scope_projection(run_id), lambda: query.get_scope_projection(run_id)),
        ("trace", lambda: service.get_workflow_trace(run_id), lambda: query.get_workflow_trace(run_id)),
        ("decisions", lambda: service.list_human_decisions(run_id), lambda: query.list_human_decisions(run_id)),
        ("report", lambda: service.get_lite_report(workflow_run_id=run_id), lambda: query.get_lite_report(workflow_run_id=run_id)),
        (
            "evidence",
            lambda: service.get_direction_evidence(workflow_run_id=run_id, direction_id="product_marketing"),
            lambda: query.get_direction_evidence(workflow_run_id=run_id, direction_id="product_marketing"),
        ),
        (
            "governance",
            lambda: service.get_governance_read_model(workflow_run_id=run_id),
            lambda: query.get_governance_read_model(workflow_run_id=run_id),
        ),
    )
    before = _database_snapshot(db_path)

    for name, legacy_call, query_call in cases:
        assert await _capture(query_call) == await _capture(legacy_call), name

    assert _database_snapshot(db_path) == before


class _QueryReached(RuntimeError):
    pass


class _QueryProbe:
    def __getattr__(self, name: str) -> Callable[..., Any]:
        async_methods = {
            "get_workflow_summary",
            "list_workflow_events",
            "get_scope_projection",
            "get_workflow_trace",
            "get_lite_report",
        }

        def sync_probe(*_args: Any, **_kwargs: Any) -> None:
            raise _QueryReached(f"query:{name}")

        async def async_probe(*_args: Any, **_kwargs: Any) -> None:
            raise _QueryReached(f"query:{name}")

        return async_probe if name in async_methods else sync_probe


class _LegacyReadTrap:
    def __getattr__(self, name: str) -> Callable[..., Any]:
        def trap(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError(f"legacy:{name}")

        return trap


@pytest.mark.asyncio
async def test_router_content_research_reads_use_query_interface():
    previous_query = getattr(app.state, "content_research_query", None)
    previous_service = getattr(app.state, "content_research_service", None)
    app.state.content_research_query = _QueryProbe()
    app.state.content_research_service = _LegacyReadTrap()
    paths = {
        "/content-research/presearch/attempt": "get_presearch",
        "/content-research/workflows/run": "get_workflow_summary",
        "/content-research/workflows/run/policy-snapshot": "get_policy_snapshot",
        "/content-research/workflows/run/events": "list_workflow_events",
        "/content-research/workflows/run/scope": "get_scope_projection",
        "/content-research/workflows/run/trace": "get_workflow_trace",
        "/content-research/workflows/run/decisions": "list_human_decisions",
        "/content-research/workflows/run/lite-report": "get_lite_report",
        "/content-research/workflows/run/directions/product_marketing/evidence": "get_direction_evidence",
        "/content-research/workflows/run/governance": "get_governance_read_model",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            for path, method in paths.items():
                response = await client.get(path)
                assert response.status_code == 500
                assert f"query:{method}" in response.text
                assert "legacy:" not in response.text
    finally:
        if previous_query is None:
            delattr(app.state, "content_research_query")
        else:
            app.state.content_research_query = previous_query
        if previous_service is None:
            delattr(app.state, "content_research_service")
        else:
            app.state.content_research_service = previous_service
