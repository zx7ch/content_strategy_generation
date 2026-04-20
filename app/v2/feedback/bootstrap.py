"""Bootstrap helpers for selecting the V2 feedback backend."""

from __future__ import annotations

from app.config import Settings
from app.v2.db.runner import run_p1_5_migrations
from app.v2.decision.store import DecisionStore
from app.v2.feedback.postgres_store import PostgresFeedbackStore
from app.v2.feedback.service import FeedbackService
from app.v2.feedback.store import InMemoryFeedbackStore
from app.v2.foundation.service import MasterDataService
from app.v2.runtime import resolve_v2_backend
from app.v2.topic_pool.store import TopicPoolStore


def build_feedback_runtime(
    config: Settings,
    *,
    master_data_service: MasterDataService,
    topic_pool_store: TopicPoolStore,
    decision_store: DecisionStore,
):
    backend = resolve_v2_backend(config, component="feedback")
    if backend == "postgres":
        run_p1_5_migrations(config.POSTGRES_DSN)
        store = PostgresFeedbackStore(config.POSTGRES_DSN)
        return store, FeedbackService(
            master_data_service=master_data_service,
            topic_pool_store=topic_pool_store,
            decision_store=decision_store,
            feedback_store=store,
        )

    store = InMemoryFeedbackStore()
    return store, FeedbackService(
        master_data_service=master_data_service,
        topic_pool_store=topic_pool_store,
        decision_store=decision_store,
        feedback_store=store,
    )
