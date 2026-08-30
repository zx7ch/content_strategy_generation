from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.api.routes.router import app
from app.config import settings
from app.content_research.command import ContentResearchCommand
from app.content_research.presearch.service import PresearchService
from app.content_research.service import ContentResearchService, WorkflowRunManagerRuntime
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.memory.thread_store import ThreadStore
from app.services.llm.types import LLMResponse, TokenUsage


class _FakeLLM:
    async def generate(self, _request: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                {
                    "subject_confirmation": "夏季凉感T恤",
                    "competitor_tags": ["优衣库", "蕉下"],
                    "research_directions": ["product_marketing"],
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
                        "synonym_groups": {},
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


def _restore_state(name: str, previous: Any) -> None:
    if previous is None:
        if hasattr(app.state, name):
            delattr(app.state, name)
    else:
        setattr(app.state, name, previous)


@pytest.mark.asyncio
async def test_command_interface_preserves_revision_idempotency_and_actions(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "F003_LITE_PREVIEW_ENABLED", True)
    db_path = str(tmp_path / "content-research-command.db")
    async with ThreadStore(db_path) as thread_store:
        thread = await thread_store.create_thread(
            title="夏季凉感T恤",
            workspace_id="ws-command",
            brand_id="brand-command",
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
    command: ContentResearchCommand = service.command_interface
    previous_service = getattr(app.state, "content_research_service", None)
    previous_query = getattr(app.state, "content_research_query", None)
    previous_command = getattr(app.state, "content_research_command", None)
    app.state.content_research_service = service
    app.state.content_research_query = service.query_interface
    app.state.content_research_command = command
    transport = httpx.ASGITransport(app=app)
    headers = {"X-Workspace-Id": "ws-command", "X-User-Id": "user-command"}
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            presearch_payload = {
                "command_id": "command-interface-submit",
                "seed_text": "夏季凉感T恤",
                "user_note": "关注通勤场景",
                "thread_id": thread["id"],
            }
            created = await client.post(
                "/content-research/presearch",
                headers=headers,
                json=presearch_payload,
            )
            replayed_create = await client.post(
                "/content-research/presearch",
                headers=headers,
                json=presearch_payload,
            )

            assert created.status_code == replayed_create.status_code == 201
            assert replayed_create.json() == created.json()
            presearch = created.json()
            run = presearch["run"]
            assert run["state"] == "brief_confirmation_required"
            assert run["state_revision"] == 2
            assert run["allowed_actions"] == [
                "confirm_brief",
                "revise_subject",
                "cancel",
            ]

            confirmation = {
                "command_id": "command-interface-confirm",
                "expected_state": run["state"],
                "expected_revision": run["state_revision"],
                "action": "confirm_brief",
                "payload": {
                    "brief_id": presearch["brief_id"],
                    "selected_competitors": ["优衣库"],
                    "custom_competitor_input": "",
                    "selected_directions": ["product_marketing"],
                },
            }
            confirmed = await client.post(
                f"/content-research/workflows/{run['run_id']}/actions",
                json=confirmation,
            )
            replayed_confirmation = await client.post(
                f"/content-research/workflows/{run['run_id']}/actions",
                json=confirmation,
            )

            assert confirmed.status_code == replayed_confirmation.status_code == 200
            assert replayed_confirmation.json() == confirmed.json()
            confirmed_run = confirmed.json()["result"]["run"]
            assert confirmed_run["state"] == "scope_confirmation_required"
            assert confirmed_run["state_revision"] == 3
            assert confirmed_run["allowed_actions"] == [
                "confirm_scope",
                "replace_scope_draft",
                "cancel",
            ]

            stale = await client.post(
                f"/content-research/workflows/{run['run_id']}/actions",
                json={
                    **confirmation,
                    "command_id": "command-interface-stale",
                },
            )
            conflicting_reuse = await client.post(
                f"/content-research/workflows/{run['run_id']}/actions",
                json={
                    **confirmation,
                    "payload": {
                        **confirmation["payload"],
                        "selected_competitors": ["蕉下"],
                    },
                },
            )

            assert stale.status_code == 409
            assert conflicting_reuse.status_code == 409
            unsupported = await client.post(
                f"/content-research/workflows/{run['run_id']}/actions",
                json={
                    **confirmation,
                    "command_id": "command-interface-unsupported",
                    "action": "future_action",
                },
            )
            assert unsupported.status_code == 422
            assert unsupported.json()["error_code"] == "INVALID_CONTENT_RESEARCH_ACTION"
            assert unsupported.json()["error_message"] == (
                "Unsupported Content Research workflow action: future_action"
            )
            refreshed = await client.get(
                f"/content-research/workflows/{run['run_id']}"
            )
            assert refreshed.status_code == 200
            assert refreshed.json()["run"] == confirmed_run
    finally:
        _restore_state("content_research_command", previous_command)
        _restore_state("content_research_query", previous_query)
        _restore_state("content_research_service", previous_service)


class _CommandReached(RuntimeError):
    pass


class _CommandProbe:
    def __getattr__(self, name: str) -> Callable[..., Any]:
        async def probe(*_args: Any, **_kwargs: Any) -> None:
            raise _CommandReached(f"command:{name}")

        return probe


class _LegacyCommandTrap:
    def __getattr__(self, name: str) -> Callable[..., Any]:
        async def trap(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError(f"legacy:{name}")

        return trap


@pytest.mark.asyncio
async def test_router_content_research_mutations_use_command_interface(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "F003_LITE_PREVIEW_ENABLED", True)
    previous_command = getattr(app.state, "content_research_command", None)
    previous_service = getattr(app.state, "content_research_service", None)
    app.state.content_research_command = _CommandProbe()
    app.state.content_research_service = _LegacyCommandTrap()
    cases = (
        (
            "/content-research/presearch",
            {
                "command_id": "probe-submit",
                "seed_text": "夏季凉感T恤",
                "thread_id": "thread-probe",
            },
            "submit_presearch",
            {"X-Workspace-Id": "ws-probe", "X-User-Id": "user-probe"},
        ),
        (
            "/content-research/workflows/run-probe/brand-decisions",
            {
                "target_id": "brand-probe",
                "decision_request_id": "decision-probe",
                "decision_status": "approved",
            },
            "submit_brand_decision",
            {},
        ),
        (
            "/content-research/workflows/run-probe/content-decisions",
            {
                "target_id": "content-probe",
                "decision_request_id": "decision-probe",
                "decision_status": "approved",
            },
            "submit_content_decision",
            {},
        ),
        (
            "/content-research/workflows/run-probe/actions",
            {
                "command_id": "action-probe",
                "expected_state": "presearch_running",
                "expected_revision": 1,
                "action": "cancel",
                "payload": {},
            },
            "run_workflow_action",
            {},
        ),
    )
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            for path, payload, method, headers in cases:
                response = await client.post(path, json=payload, headers=headers)
                assert response.status_code == 500
                assert f"command:{method}" in response.text
                assert "legacy:" not in response.text
    finally:
        _restore_state("content_research_command", previous_command)
        _restore_state("content_research_service", previous_service)
