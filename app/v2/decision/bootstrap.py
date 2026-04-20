"""Bootstrap helpers for selecting the V2 decision backend."""

from __future__ import annotations

from app.config import Settings
from app.v2.db.runner import run_p1_2_migrations
from app.v2.decision.postgres_store import PostgresDecisionStore
from app.v2.decision.service import DecisionService
from app.v2.decision.store import InMemoryDecisionStore
from app.v2.foundation.service import MasterDataService
from app.v2.runtime import resolve_v2_backend
from app.v2.topic_pool.store import TopicPoolStore


def build_decision_runtime(
    config: Settings,
    *,
    master_data_service: MasterDataService,
    topic_pool_store: TopicPoolStore,
):
    backend = resolve_v2_backend(config, component="decision")
    if backend == "postgres":
        run_p1_2_migrations(config.POSTGRES_DSN)
        store = PostgresDecisionStore(config.POSTGRES_DSN)
        return store, DecisionService(
            master_data_service=master_data_service,
            topic_pool_store=topic_pool_store,
            decision_store=store,
        )

    store = InMemoryDecisionStore()
    return store, DecisionService(
        master_data_service=master_data_service,
        topic_pool_store=topic_pool_store,
        decision_store=store,
    )
