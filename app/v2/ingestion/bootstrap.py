"""Bootstrap helpers for selecting the V2 ingestion runtime."""

from __future__ import annotations

from app.config import Settings
from app.v2.db.runner import run_p1_2_migrations
from app.v2.ingestion.postgres_store import PostgresIngestionStore
from app.v2.runtime import resolve_v2_backend
from app.v2.ingestion.service import IngestionService
from app.v2.ingestion.store import InMemoryIngestionStore


def build_ingestion_runtime(config: Settings):
    backend = resolve_v2_backend(config, component="ingestion")
    if backend == "postgres":
        run_p1_2_migrations(config.POSTGRES_DSN)
        store = PostgresIngestionStore(config.POSTGRES_DSN)
        return store, IngestionService(store)

    store = InMemoryIngestionStore()
    return store, IngestionService(store)
