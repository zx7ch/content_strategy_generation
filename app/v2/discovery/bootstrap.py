"""Bootstrap helpers for the integrated discovery runtime."""

from __future__ import annotations

from app.config import Settings
from app.core.runtime_write_coordinator import RuntimeWriteCoordinator
from app.llm.client import LLMClient
from app.v2.discovery.query_expander import DiscoveryQueryExpander
from app.v2.discovery.service import DiscoveryService


def build_discovery_runtime(
    config: Settings,
    *,
    database_path: str | None = None,
    writer: RuntimeWriteCoordinator | None = None,
) -> DiscoveryService:
    return DiscoveryService(
        database_path=database_path or config.V2_DISCOVERY_SQLITE_PATH,
        secret=config.V2_DISCOVERY_TOKEN_SECRET,
        query_expander=DiscoveryQueryExpander(
            llm_client=LLMClient(provider=config.LLM_PROVIDER),
        ),
        writer=writer,
    )
