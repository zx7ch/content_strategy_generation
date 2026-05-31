from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from app.agents.orchestrator import Orchestrator
from app.api.routes.router import app, schedule_embedding_prewarm
from app.config import settings
from app.memory.job_store import JobStore
from app.memory.thread_store import ThreadStore
from app.services.step_executors import build_agent_step_executor_registry
from app.v2.discovery.bootstrap import build_discovery_runtime
from app.v2.decision.bootstrap import build_decision_runtime
from app.v2.feedback.bootstrap import build_feedback_runtime
from app.v2.foundation.bootstrap import build_master_data_runtime
from app.v2.ingestion.bootstrap import build_ingestion_runtime
from app.v2.topic_pool.scorer import ScorerService
from app.v2.topic_pool.bootstrap import build_topic_pool_runtime
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

    # Start embedding model preload immediately in background.
    # Model downloads (~780 MB) or loads from cache without blocking startup.
    # By the time the user runs their first task, the model is likely ready.
    schedule_embedding_prewarm()

    application.state.job_store = job_store
    application.state.orchestrator = orchestrator
    application.state.job_worker = worker
    application.state.worker_stop_event = stop_event
    application.state.worker_task = worker_task
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
        try:
            await asyncio.wait_for(worker_task, timeout=5)
        except asyncio.TimeoutError:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

        await job_store.close()
        await thread_store.close()
        application.state.worker_started = False


app.router.lifespan_context = _worker_lifespan


def create_app():
    return app
