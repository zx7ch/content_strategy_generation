"""Production app wiring with deterministic external providers for browser E2E."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from contextlib import asynccontextmanager

import app.main as production
from app.content_research.presearch.service import PresearchService
from app.content_research.sources import SourceAdapterRegistry
from app.content_research.sources.base import ProviderCapability, SourceOperationResult
from app.services.llm.configuration_service import LiteLLMConfigurationService
from app.services.llm.configuration_store import SQLiteLLMConfigurationStore
from app.services.llm.failures import LLMProviderFailure
from app.services.llm.types import LLMResponse, TokenUsage


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
        user_prompt = next(
            (message.content for message in reversed(request.messages) if message.role == "user"),
            "",
        )
        seed_match = re.search(r"用户输入:\s*(.+)", user_prompt)
        subject = seed_match.group(1).strip() if seed_match else "夏季通勤短裤"
        if subject == "长袖衬衫 凉感 夏季通勤":
            source_terms = ["长袖衬衫", "凉感", "夏季通勤"]
            core_object = "长袖衬衫"
            research_intents = ["凉感"]
            context_modifiers = ["夏季通勤"]
        elif subject == "长袖衬衫":
            source_terms = ["长袖衬衫"]
            core_object = "长袖衬衫"
            research_intents = []
            context_modifiers = []
        elif subject == "夏季凉感T恤":
            source_terms = ["夏季", "凉感", "T恤"]
            core_object = "T恤"
            research_intents = ["凉感"]
            context_modifiers = ["夏季"]
        else:
            source_terms = [subject]
            core_object = subject
            research_intents = []
            context_modifiers = []
        return LLMResponse(
            content=json.dumps(
                {
                    "subject_confirmation": subject,
                    "competitor_tags": ["迪卡侬"],
                    "research_directions": [
                        "product_marketing",
                        "competitor_discovery",
                        "content_performance",
                    ],
                    "custom_research_question": "",
                    "custom_competitor_input": "",
                    "subject_structure": {
                        "schema_version": "content_research_subject_structure_v1",
                        "canonical_subject": subject,
                        "subject_type": "category",
                        "source_terms": source_terms,
                        "term_roles": {
                            "core_object": [core_object],
                            "product_experience": research_intents,
                            "context_audience": context_modifiers,
                        },
                        "core_entities": [
                            {"canonical_name": core_object, "raw_mentions": [core_object]}
                        ],
                        "research_intents": research_intents,
                        "context_modifiers": context_modifiers,
                        "synonym_groups": {},
                        "ambiguities": [],
                        "resolution_state": "resolved",
                    },
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


class DeterministicSuccessfulSource:
    """Return complete, relevant note facts and record the exact submitted queries."""

    def __init__(self, call_log: str) -> None:
        self._call_log = Path(call_log)

    def capabilities(self):
        return DeterministicAuthRequiredSource().capabilities()

    async def discover_candidates(self, request):
        with self._call_log.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "operation": "discover_candidates",
                        "workflow_run_id": request.workflow_run_id,
                        "query": request.query,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        query_key = re.sub(r"\s+", "-", request.query.strip())
        return SourceOperationResult(
            provider="xiaohongshu",
            operation="discover_candidates",
            source_kind="search_result_minimal",
            status="completed",
            items=[
                {
                    "canonical_id": f"note-{query_key}-{index}",
                    "canonical_source_id": f"note-{query_key}-{index}",
                    "source_url": f"https://www.xiaohongshu.com/explore/note-{query_key}-{index}",
                    "source_kind": "search_result_minimal",
                    "title": f"T恤真实体验 {index}",
                    "author_id": f"author-{query_key}-{index}",
                }
                for index in range(1, 4)
            ],
            cookie_status="valid",
            completeness="complete",
        )

    async def collect_note_detail(self, request):
        index = request.note_id.rsplit("-", 1)[-1]
        item = {
            "canonical_id": request.note_id,
            "canonical_source_id": request.note_id,
            "source_url": request.note_url,
            "source_kind": "note_detail",
            "author_id": f"detail-author-{request.note_id}",
            "author": f"体验作者 {index}",
            "title": f"夏季 T恤凉感体验 {index}",
            "content_text": f"这件 T恤在夏季通勤中穿着凉爽，样本 {index}。",
            "tags": ["T恤", "凉感", "夏季"],
            "note_type": "image_text",
            "source_published_at": "2026-08-20T00:00:00+00:00",
            "metrics": {"like_count": 100 + int(index)},
            "metrics_observed_at": "2026-08-24T00:00:00+00:00",
            "ip_location": "上海",
            "media": {"cover_count": 1},
            "field_availability": {
                field: "present" for field in request.required_fields
            },
        }
        return SourceOperationResult(
            provider="xiaohongshu",
            operation="collect_note_detail",
            source_kind="note_detail",
            status="completed",
            items=[item],
            cookie_status="valid",
            completeness="complete",
            field_availability=item["field_availability"],
        )

    async def collect_comments(self, _request):
        return SourceOperationResult(
            provider="xiaohongshu",
            operation="collect_comments",
            source_kind="comment",
            status="empty",
            items=[],
            cookie_status="valid",
            completeness="complete",
        )


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


async def _idle_unrelated_job_worker(_worker, *, stop_event) -> None:
    """Keep the generic job queue out of the Content Research worker gate."""
    await stop_event.wait()


@asynccontextmanager
async def deterministic_lifespan(application):
    production.schedule_embedding_prewarm = lambda: None
    original_job_worker_loop = production.JobWorker.run_loop
    async with _production_lifespan(application):
        production.JobWorker.run_loop = original_job_worker_loop
        service = application.state.content_research_service
        configuration_store = SQLiteLLMConfigurationStore(os.environ["SQLITE_DB_PATH"])
        source = (
            DeterministicSuccessfulSource(os.environ["CREATOR_E2E_SOURCE_CALL_LOG"])
            if os.getenv("CREATOR_E2E_SOURCE_SCENARIO") == "complete"
            else DeterministicAuthRequiredSource()
        )
        registry = SourceAdapterRegistry({"xiaohongshu": source})
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
