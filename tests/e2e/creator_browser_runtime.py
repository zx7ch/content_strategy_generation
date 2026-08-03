"""Production app wiring with deterministic external providers for browser E2E."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager

import app.main as production
from app.content_research.presearch.service import PresearchService
from app.content_research.sources import SourceAdapterRegistry
from app.content_research.sources.base import ProviderCapability, SourceOperationResult
from app.services.llm.types import LLMResponse, TokenUsage
from app.services.llm.configuration_service import LiteLLMConfigurationService
from app.services.llm.configuration_store import SQLiteLLMConfigurationStore
from app.services.llm.failures import LLMProviderFailure


class DeterministicPresearchLLM:
    def __init__(self, configuration_store: SQLiteLLMConfigurationStore) -> None:
        self._configuration_store = configuration_store

    async def generate(self, request):
        is_recovery_probe = any("模型失败恢复" in message.content for message in request.messages)
        context = request.context
        if (
            is_recovery_probe
            and context is not None
            and self._configuration_store.get(context.tenant_id or "", context.user_id or "") is None
        ):
            raise LLMProviderFailure(
                "llm_auth_invalid",
                "API Key 无效",
                True,
                401,
                provider="openai_compatible",
                model="deterministic-e2e",
                configuration_source="user",
            )
        return LLMResponse(
            content=json.dumps(
                {
                    "subject_confirmation": "夏季通勤短裤",
                    "competitor_tags": ["迪卡侬"],
                    "research_directions": [
                        "product_marketing",
                        "competitor_discovery",
                        "content_performance",
                    ],
                    "custom_research_question": "",
                    "custom_competitor_input": "",
                },
                ensure_ascii=False,
            ),
            provider="deterministic-e2e",
            model="deterministic-e2e",
            usage=TokenUsage(total_tokens=1),
            latency_ms=1,
        )


class DeterministicConfigurationProbe:
    async def generate(self, _request, _api_key, model, _base_url=None):
        return LLMResponse(content='{"ok":true}', provider="openai_compatible", model=model, usage=TokenUsage(), latency_ms=1)


class DeterministicAuthRequiredSource:
    """A local fake that cannot make network calls and always pauses safely."""

    def capabilities(self):
        return (
            ProviderCapability(
                "discover_candidates",
                "supported",
                ("title", "author", "metrics"),
            ),
            ProviderCapability(
                "collect_note_detail",
                "supported",
                ("title", "content_text", "author", "metrics"),
            ),
            ProviderCapability(
                "collect_comments",
                "supported",
                ("comment_text", "author", "parent_note_id"),
            ),
        )

    async def discover_candidates(self, _request):
        return SourceOperationResult(
            provider="xiaohongshu",
            operation="discover_candidates",
            source_kind="search_result_minimal",
            status="failed",
            items=[],
            failure_reason="auth_required",
            retryable=False,
            completeness="unavailable",
        )

    async def collect_note_detail(self, _request):  # pragma: no cover - guarded by discover
        raise AssertionError("detail collection must not follow auth-required discovery")

    async def collect_comments(self, _request):  # pragma: no cover - guarded by discover
        raise AssertionError("comment collection must not follow auth-required discovery")


class DeterministicWorkflowRestoreFailure:
    def __init__(self, delegate) -> None:
        self._delegate = delegate

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    async def get_runtime_snapshot(self, _workflow_run_id: str) -> dict:
        raise RuntimeError("deterministic workflow restore failure")


class DeterministicQRLoginSession:
    """Expose a pending QR once, then a redacted authenticated projection."""

    def __init__(self) -> None:
        self._started = False
        self._poll_count = 0

    def start(self):
        self._started = True
        self._poll_count = 0
        return self._projection("pending")

    def current_status(self):
        if not self._started:
            return None
        self._poll_count += 1
        return self._projection("pending" if self._poll_count == 1 else "authenticated")

    def status(self, attempt_id: str):
        if not self._started or attempt_id != "xhsqr_browser_e2e":
            return None
        return self.current_status()

    @staticmethod
    def _projection(status: str):
        return {
            "attempt_id": "xhsqr_browser_e2e",
            "status": status,
            "qr_image_data_url": "data:image/png;base64,AA==",
            "failure_code": None,
        }


_production_lifespan = production.app.router.lifespan_context


@asynccontextmanager
async def deterministic_lifespan(application):
    production.schedule_embedding_prewarm = lambda: None
    async with _production_lifespan(application):
        service = application.state.content_research_service
        configuration_store = SQLiteLLMConfigurationStore(os.environ["SQLITE_DB_PATH"])
        registry = SourceAdapterRegistry(
            {"xiaohongshu": DeterministicAuthRequiredSource()}
        )
        service._presearch = PresearchService(
            DeterministicPresearchLLM(configuration_store),
            first_feedback_timeout_seconds=0.05,
            hard_cutoff_seconds=0.1,
        )
        service._source_registry = registry
        service._task_router._source_registry = registry
        application.state.llm_configuration_service = LiteLLMConfigurationService(
            store=configuration_store,
            probe_adapter=DeterministicConfigurationProbe(),
        )
        application.state.xhs_qr_login_session = DeterministicQRLoginSession()
        if os.getenv("CREATOR_E2E_FAIL_WORKFLOW_RESTORE") == "1":
            service._workflow_runtime = DeterministicWorkflowRestoreFailure(
                service._workflow_runtime
            )
        yield


app = production.app
app.router.lifespan_context = deterministic_lifespan
