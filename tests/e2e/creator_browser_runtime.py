"""Production app wiring with deterministic external providers for browser E2E."""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import app.main as production
from app.content_research.presearch.service import PresearchService
from app.content_research.reporting.faithfulness import SemanticAuditResult
from app.content_research.research_embedding import (
    ResearchEmbeddingBatch,
    ResearchEmbeddingFingerprint,
    ResearchEmbeddingHealth,
)
from app.content_research.sources import SourceAdapterRegistry
from app.content_research.sources.base import ProviderCapability, SourceOperationResult
from app.services.llm.configuration_service import LiteLLMConfigurationService
from app.services.llm.configuration_store import SQLiteLLMConfigurationStore
from app.services.llm.failures import LLMProviderFailure
from app.services.llm.types import LLMResponse, TokenUsage


class DeterministicPresearchLLM:
    def __init__(self, configuration_store: SQLiteLLMConfigurationStore) -> None:
        self._configuration_store = configuration_store
        self._invalid_analysis_tracks = {
            item.strip()
            for item in os.getenv("CREATOR_E2E_INVALID_ANALYSIS_TRACKS", "").split(",")
            if item.strip()
        }
        self._empty_analysis_tracks = {
            item.strip()
            for item in os.getenv("CREATOR_E2E_EMPTY_ANALYSIS_TRACKS", "").split(",")
            if item.strip()
        }

    async def generate(self, request):
        if request.task_type == "content_research.marketing_evidence_extraction":
            payload = json.loads(request.messages[-1].content)
            evidence = []
            for note in payload["notes"]:
                body = note["content_text"]
                title = note["title"]
                polarity = "counter" if any(term in body for term in ("闷", "不凉")) else "support"
                scenes = [term for term in ("夏季", "通勤", "运动") if term in body]
                audiences = [term for term in ("儿童", "上班族") if term in body]
                if body:
                    for track in ("need", "value"):
                        evidence.append(
                            {
                                "note_id": note["note_id"],
                                "field_path": "content_text",
                                "quote": body,
                                "text_start": 0,
                                "text_end": len(body),
                                "track": track,
                                "aspect": "夏季凉感体验",
                                "evidence_type": "limitation" if polarity == "counter" else "experience",
                                "polarity": polarity,
                                "scenes": scenes,
                                "audiences": audiences,
                            }
                        )
                if title:
                    evidence.append(
                        {
                            "note_id": note["note_id"],
                            "field_path": "title",
                            "quote": title,
                            "text_start": 0,
                            "text_end": len(title),
                            "track": "message",
                            "aspect": "标题表达",
                            "evidence_type": "message_expression",
                            "polarity": "support",
                            "scenes": [term for term in ("夏季", "通勤", "运动") if term in title],
                            "audiences": [term for term in ("儿童", "上班族") if term in title],
                        }
                    )
            return LLMResponse(
                content=json.dumps({"evidence": evidence}, ensure_ascii=False),
                provider="deterministic-e2e",
                model="deterministic-e2e",
                usage=TokenUsage(total_tokens=1),
                latency_ms=1,
            )
        if request.task_type == "content_research.marketing_conclusion_analysis":
            payload = json.loads(request.messages[-1].content)
            track = payload["tracks"][0]
            if track in self._invalid_analysis_tracks:
                return LLMResponse(
                    content="not-json",
                    provider="deterministic-e2e",
                    model="deterministic-e2e",
                    usage=TokenUsage(total_tokens=1),
                    latency_ms=1,
                )
            if track in self._empty_analysis_tracks:
                return LLMResponse(
                    content='{"candidates":[]}',
                    provider="deterministic-e2e",
                    model="deterministic-e2e",
                    usage=TokenUsage(total_tokens=1),
                    latency_ms=1,
                )
            return LLMResponse(
                content=json.dumps(
                    {
                        "candidates": [
                            {
                                "track": track,
                                "statement": f"样本支持 {track} 方向的夏季凉感体验",
                                "supporting_claim_ids": [
                                    item["claim_id"]
                                    for item in payload["claims"]
                                    if item.get("polarity") != "counter"
                                ],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                provider="deterministic-e2e",
                model="deterministic-e2e",
                usage=TokenUsage(total_tokens=1),
                latency_ms=1,
            )
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


class DeterministicResearchEmbeddingRuntime:
    def __init__(self) -> None:
        self._fingerprint = ResearchEmbeddingFingerprint(
            provider="deterministic",
            model="creator-e2e",
            revision="v1",
            dimensions=3,
        )

    @property
    def health(self):
        return ResearchEmbeddingHealth("ready", self._fingerprint)

    def start(self):
        return self.health

    def stop(self):
        return ResearchEmbeddingHealth(
            "unavailable", self._fingerprint, error_code="RESEARCH_EMBEDDING_STOPPED"
        )

    def embed_documents(self, documents):
        return ResearchEmbeddingBatch(
            document_ids=tuple(item.note_id for item in documents),
            input_fingerprints=tuple(f"input-{item.note_id}" for item in documents),
            vectors=tuple((1.0, 0.0, 0.0) for _item in documents),
            embedding_fingerprint=self._fingerprint,
        )


class DeterministicTrackWithholdingEvaluator:
    """Fault-controlled faithfulness result for the publication/UI E2E."""

    def __init__(self, track: str) -> None:
        self._section_kind = f"marketing_{track}"

    async def evaluate(self, _snapshot, draft, _semantic_auditor):
        section = next(
            item for item in draft.sections if item.section_kind == self._section_kind
        )
        semantic = SemanticAuditResult(
            "failed",
            ("marketing_conclusion_prose_mismatch",),
            (section.section_id,),
        )
        return SimpleNamespace(
            passed=False,
            reason_codes=semantic.reason_codes,
            affected_section_ids=semantic.affected_section_ids,
            semantic_result=semantic,
        )


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

    def __init__(
        self,
        call_log: str,
        *,
        scenario: str = "complete",
        require_two_active_runs: bool = False,
    ) -> None:
        self._call_log = Path(call_log)
        self._scenario = scenario
        self._require_two_active_runs = require_two_active_runs
        self._active_discoveries: dict[str, int] = {}
        self._two_active_runs = asyncio.Event()

    def capabilities(self):
        return DeterministicAuthRequiredSource().capabilities()

    async def discover_candidates(self, request):
        run_id = request.workflow_run_id
        self._active_discoveries[run_id] = self._active_discoveries.get(run_id, 0) + 1
        active_run_ids = sorted(self._active_discoveries)
        if len(active_run_ids) >= 2:
            self._two_active_runs.set()
        with self._call_log.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "operation": "discover_candidates",
                        "workflow_run_id": run_id,
                        "query": request.query,
                        "active_run_ids": active_run_ids,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        try:
            if self._require_two_active_runs:
                try:
                    await asyncio.wait_for(self._two_active_runs.wait(), timeout=8)
                except TimeoutError:
                    # Let the first Run finish under a one-lane scheduler so the
                    # browser assertion reports the missing overlap instead of hanging.
                    pass
            query_key = re.sub(r"\s+", "-", request.query.strip())
            object_name = "长袖衬衫" if "长袖衬衫" in request.query else "T恤"
            items = []
            for index in range(1, 4):
                note_id = f"note-{query_key}-{index}"
                title = f"{object_name}真实体验 {index}"
                if self._scenario == "concurrent" and index == 1:
                    note_id = "note-shared-summer-cooling-1"
                    title = "T恤与长袖衬衫夏季凉感体验"
                items.append(
                    {
                        "canonical_id": note_id,
                        "canonical_source_id": note_id,
                        "source_url": f"https://www.xiaohongshu.com/explore/{note_id}",
                        "source_kind": "search_result_minimal",
                        "title": title,
                        "author_id": f"author-{note_id}",
                    }
                )
            return SourceOperationResult(
                provider="xiaohongshu",
                operation="discover_candidates",
                source_kind="search_result_minimal",
                status="completed",
                items=items,
                cookie_status="valid",
                completeness="complete",
            )
        finally:
            remaining = self._active_discoveries[run_id] - 1
            if remaining:
                self._active_discoveries[run_id] = remaining
            else:
                del self._active_discoveries[run_id]

    async def collect_note_detail(self, request):
        index = request.note_id.rsplit("-", 1)[-1]
        object_name = "长袖衬衫" if "长袖衬衫" in request.note_id else "T恤"
        if request.note_id == "note-shared-summer-cooling-1":
            object_name = "T恤与长袖衬衫"
        content_text = f"这件 {object_name}在夏季通勤中穿着凉爽，样本 {index}。"
        if self._scenario == "contested" and index in {"2", "3"}:
            content_text = f"这件 T恤在夏季通勤中一点也不凉爽，样本 {index}。"
        item = {
            "canonical_id": request.note_id,
            "canonical_source_id": request.note_id,
            "source_url": request.note_url,
            "source_kind": "note_detail",
            "author_id": f"detail-author-{request.note_id}",
            "author": f"体验作者 {index}",
            "title": f"夏季 {object_name}凉感体验 {index}",
            "content_text": content_text,
            "tags": [object_name, "凉感", "夏季"],
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
    production.build_research_embedding_runtime = (
        lambda _settings: DeterministicResearchEmbeddingRuntime()
    )
    original_job_worker_loop = production.JobWorker.run_loop
    async with _production_lifespan(application):
        production.JobWorker.run_loop = original_job_worker_loop
        service = application.state.content_research_service
        configuration_store = SQLiteLLMConfigurationStore(os.environ["SQLITE_DB_PATH"])
        source_scenario = os.getenv("CREATOR_E2E_SOURCE_SCENARIO")
        source = (
            DeterministicSuccessfulSource(
                os.environ["CREATOR_E2E_SOURCE_CALL_LOG"],
                scenario=source_scenario,
                require_two_active_runs=(
                    os.getenv("CREATOR_E2E_REQUIRE_TWO_ACTIVE_RUNS") == "1"
                ),
            )
            if source_scenario in {"complete", "contested", "concurrent"}
            else DeterministicAuthRequiredSource()
        )
        registry = SourceAdapterRegistry({"xiaohongshu": source})
        deterministic_llm = DeterministicPresearchLLM(configuration_store)
        service._presearch = PresearchService(
            deterministic_llm,
            first_feedback_timeout_seconds=0.05,
            hard_cutoff_seconds=0.1,
        )
        service._source_registry = registry
        service._task_router._source_registry = registry
        service._analysis_llm = deterministic_llm
        withheld_track = os.getenv("CREATOR_E2E_WITHHOLD_MARKETING_TRACK", "").strip()
        if withheld_track:
            service._report_execution._evaluator = (
                DeterministicTrackWithholdingEvaluator(withheld_track)
            )
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
