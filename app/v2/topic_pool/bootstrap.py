"""Bootstrap helpers for selecting the V2 topic pool backend."""

from __future__ import annotations

from app.config import Settings
from app.v2.db.runner import run_p1_2_migrations
from app.v2.foundation.service import MasterDataService
from app.v2.ingestion.store import IngestionStore
from app.v2.runtime import resolve_v2_backend
from app.v2.topic_pool.postgres_store import PostgresTopicPoolStore
from app.v2.topic_pool.service import TopicPoolService
from app.v2.topic_pool.store import InMemoryTopicPoolStore


def build_topic_pool_runtime(
    config: Settings,
    *,
    master_data_service: MasterDataService,
    ingestion_store: IngestionStore,
):
    backend = resolve_v2_backend(config, component="topic_pool")
    if backend == "postgres":
        run_p1_2_migrations(config.POSTGRES_DSN)
        store = PostgresTopicPoolStore(config.POSTGRES_DSN)
        return store, TopicPoolService(
            master_data_service=master_data_service,
            ingestion_store=ingestion_store,
            topic_pool_store=store,
        )

    store = InMemoryTopicPoolStore()
    return store, TopicPoolService(
        master_data_service=master_data_service,
        ingestion_store=ingestion_store,
        topic_pool_store=store,
    )
