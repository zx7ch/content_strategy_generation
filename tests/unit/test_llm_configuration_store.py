from __future__ import annotations

from datetime import datetime, timezone

from app.services.llm.configuration import UserLLMConfiguration
from app.services.llm.configuration_store import SQLiteLLMConfigurationStore


def test_configuration_round_trip_and_scope(tmp_path):
    store = SQLiteLLMConfigurationStore(str(tmp_path / "config.db"))
    saved = store.upsert(
        UserLLMConfiguration(
            workspace_id="ws_1",
            user_id="user_1",
            base_url="https://proxy.example/v1",
            model="custom-model-2026",
            api_key="sk-secret-1234",
            validation_status="validated",
            validated_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
        )
    )

    assert store.get("ws_1", "user_1") == saved
    assert store.get("ws_1", "user_2") is None
    assert store.delete("ws_1", "user_1") is True
    assert store.get("ws_1", "user_1") is None
