from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from app.agents.orchestrator import Orchestrator
from app.api.routes.router import app, schedule_embedding_prewarm
from app.config import settings
from app.content_research.presearch.service import PresearchService
from app.content_research.research_embedding import build_research_embedding_runtime
from app.content_research.service import ContentResearchService, WorkflowRunManagerRuntime
from app.content_research.sources import SourceAdapterRegistry
from app.content_research.sources.xiaohongshu.adapter import XiaohongshuSourceAdapter
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.content_research.worker import (
    ContentResearchAnalysisWorker,
    ContentResearchDispatchWorker,
)
from app.memory.job_store import JobStore
from app.memory.thread_store import ThreadStore
from app.services.llm.configuration_service import LiteLLMConfigurationService
from app.services.llm.configuration_store import SQLiteLLMConfigurationStore
from app.services.llm.providers.openai_compatible import OpenAICompatibleAdapter
from app.services.llm.tracked_client import build_default_llm_service
from app.services.step_executors import build_agent_step_executor_registry
from app.services.xhs_credentials import XHSCredentialStore
from app.services.xhs_qr_auth import XHSQRLoginSession
from app.services.xhs_spider import XHSSpiderClient
from app.v2.decision.bootstrap import build_decision_runtime
from app.v2.discovery.bootstrap import build_discovery_runtime
from app.v2.feedback.bootstrap import build_feedback_runtime
from app.v2.foundation.bootstrap import build_master_data_runtime
from app.v2.ingestion.bootstrap import build_ingestion_runtime
from app.v2.topic_pool.bootstrap import build_topic_pool_runtime
from app.v2.topic_pool.scorer import ScorerService
from app.workers.job_worker import JobWorker


@asynccontextmanager
async def _worker_lifespan(application):
    job_store = JobStore(settings.SQLITE_DB_PATH)
    await job_store.connect()
    thread_store = ThreadStore()
    await thread_store.connect()
    orchestrator = Orchestrator(
        db_path=settings.SQLITE_DB_PATH,
        step_executor_registry=build_agent_step_executor_registry(db_path=settings.SQLITE_DB_PATH),
    )
    worker = JobWorker(job_store=job_store, orchestrator=orchestrator)
    content_research_llm_service = build_default_llm_service(settings.SQLITE_DB_PATH)
    llm_configuration_store = SQLiteLLMConfigurationStore(settings.SQLITE_DB_PATH)
    llm_configuration_service = LiteLLMConfigurationService(
        store=llm_configuration_store,
        probe_adapter=OpenAICompatibleAdapter(provider="openai_compatible"),
    )
    content_research_dispatch_event = asyncio.Event()
    content_research_analysis_event = asyncio.Event()
    research_embedding_runtime = build_research_embedding_runtime(settings)
    xhs_credential_store = XHSCredentialStore(settings.SQLITE_DB_PATH)
    xhs_qr_login_session = XHSQRLoginSession(credential_store=xhs_credential_store)
    content_research_service = ContentResearchService(
        store=SQLiteContentResearchStore(settings.SQLITE_DB_PATH),
        presearch=PresearchService(content_research_llm_service),
        workflow_runtime=WorkflowRunManagerRuntime(settings.SQLITE_DB_PATH),
        analysis_llm=content_research_llm_service,
        source_registry=SourceAdapterRegistry({"xiaohongshu": XiaohongshuSourceAdapter(
            spider_client=XHSSpiderClient(
                auth_provider=xhs_qr_login_session.get_auth,
                on_auth_failure=xhs_qr_login_session.mark_auth_stale,
            ),
        )}),
        dispatch_wake_event=content_research_dispatch_event,
        analysis_wake_event=content_research_analysis_event,
        research_embedding_runtime=research_embedding_runtime,
    )
    await content_research_service.reconcile_startup()
    content_research_worker = ContentResearchDispatchWorker(
        store=content_research_service._store,
        execution_factory=lambda: content_research_service.execution_interface,
        wake_event=content_research_dispatch_event,
    )
    content_research_analysis_worker = ContentResearchAnalysisWorker(
        store=content_research_service._store,
        execution_factory=lambda: content_research_service.execution_interface,
        wake_event=content_research_analysis_event,
    )
    v2_master_data_store, v2_master_data_service = build_master_data_runtime(settings)
    v2_ingestion_store, v2_ingestion_service = build_ingestion_runtime(settings)
    v2_discovery_service = build_discovery_runtime(settings)
    v2_topic_pool_store, v2_topic_pool_service = build_topic_pool_runtime(
        settings,
        master_data_service=v2_master_data_service,
        ingestion_store=v2_ingestion_store,
    )
    v2_decision_store, v2_decision_service = build_decision_runtime(
        settings,
        master_data_service=v2_master_data_service,
        topic_pool_store=v2_topic_pool_store,
    )
    v2_feedback_store, v2_feedback_service = build_feedback_runtime(
        settings,
        master_data_service=v2_master_data_service,
        topic_pool_store=v2_topic_pool_store,
        decision_store=v2_decision_store,
    )
    v2_scorer_service = ScorerService(
        master_data_service=v2_master_data_service,
        topic_pool_store=v2_topic_pool_store,
        feedback_store=v2_feedback_store,
    )
    v2_topic_pool_service.attach_scorer_service(v2_scorer_service)
    v2_decision_service.attach_scorer_service(v2_scorer_service)
    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(worker.run_loop(stop_event=stop_event))
    content_research_worker_task = asyncio.create_task(
        content_research_worker.run_loop(stop_event=stop_event)
    )
    content_research_analysis_worker_task = asyncio.create_task(
        content_research_analysis_worker.run_loop(stop_event=stop_event)
    )
    research_embedding_task: asyncio.Task | None = None
    if settings.F003_LITE_PREVIEW_ENABLED:
        research_embedding_task = asyncio.create_task(
            asyncio.to_thread(research_embedding_runtime.start)
        )

    # Start embedding model preload immediately in background.
    # Model downloads (~780 MB) or loads from cache without blocking startup.
    # By the time the user runs their first task, the model is likely ready.
    schedule_embedding_prewarm()

    application.state.job_store = job_store
    application.state.orchestrator = orchestrator
    application.state.job_worker = worker
    application.state.worker_stop_event = stop_event
    application.state.worker_task = worker_task
    application.state.content_research_llm_service = content_research_llm_service
    application.state.llm_configuration_service = llm_configuration_service
    application.state.content_research_service = content_research_service
    application.state.content_research_query = content_research_service.query_interface
    application.state.content_research_command = content_research_service.command_interface
    application.state.xhs_qr_login_session = xhs_qr_login_session
    application.state.xhs_credential_store = xhs_credential_store
    application.state.content_research_dispatch_worker = content_research_worker
    application.state.content_research_dispatch_worker_task = content_research_worker_task
    application.state.content_research_analysis_worker = content_research_analysis_worker
    application.state.content_research_analysis_worker_task = (
        content_research_analysis_worker_task
    )
    application.state.content_research_embedding_runtime = research_embedding_runtime
    application.state.content_research_embedding_task = research_embedding_task
    application.state.worker_started = True
    application.state.v2_master_data_store = v2_master_data_store
    application.state.v2_master_data_service = v2_master_data_service
    application.state.v2_ingestion_store = v2_ingestion_store
    application.state.v2_ingestion_service = v2_ingestion_service
    application.state.v2_discovery_service = v2_discovery_service
    application.state.v2_topic_pool_store = v2_topic_pool_store
    application.state.v2_topic_pool_service = v2_topic_pool_service
    application.state.v2_decision_store = v2_decision_store
    application.state.v2_decision_service = v2_decision_service
    application.state.v2_feedback_store = v2_feedback_store
    application.state.v2_feedback_service = v2_feedback_service
    application.state.thread_store = thread_store

    try:
        yield
    finally:
        stop_event.set()
        if research_embedding_task is not None and not research_embedding_task.done():
            research_embedding_task.cancel()
            try:
                await research_embedding_task
            except asyncio.CancelledError:
                pass
        try:
            await asyncio.wait_for(worker_task, timeout=5)
        except asyncio.TimeoutError:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
        try:
            await asyncio.wait_for(content_research_worker_task, timeout=5)
        except asyncio.TimeoutError:
            content_research_worker_task.cancel()
            try:
                await content_research_worker_task
            except asyncio.CancelledError:
                pass
        try:
            await asyncio.wait_for(content_research_analysis_worker_task, timeout=5)
        except asyncio.TimeoutError:
            content_research_analysis_worker_task.cancel()
            try:
                await content_research_analysis_worker_task
            except asyncio.CancelledError:
                pass
        research_embedding_runtime.stop()

        await job_store.close()
        await thread_store.close()
        application.state.worker_started = False


app.router.lifespan_context = _worker_lifespan


def create_app():
    return app
