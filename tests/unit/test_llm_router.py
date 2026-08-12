from __future__ import annotations

from app.services.llm.providers.openai_compatible import DEFAULT_BASE_URLS
from app.services.llm.router import ModelRouter
from app.services.llm.tracked_client import build_default_llm_service
from app.services.llm.types import LLMRequest


def test_default_router_supports_content_research_presearch_cheap_fast_policy():
    resolved = ModelRouter().resolve(
        LLMRequest(
            messages=[],
            task_type="content_research.presearch",
            model_policy="cheap_fast",
        )
    )

    assert resolved.provider == "kimi"
    assert resolved.model_policy == "cheap_fast"


def test_kimi_default_base_url_uses_kimi_code_endpoint():
    assert DEFAULT_BASE_URLS["kimi"] == "https://api.kimi.com/coding/v1"


def test_default_llm_service_registers_kimi_code_endpoint():
    service = build_default_llm_service()

    assert service._providers["kimi"].base_url == "https://api.kimi.com/coding/v1"
