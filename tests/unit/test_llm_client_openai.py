from __future__ import annotations

from unittest.mock import patch

from app.llm.client import LLMClient, LLMProvider


def test_llm_client_accepts_uppercase_openai_provider() -> None:
    with patch("app.llm.client.AsyncOpenAI") as mock_openai:
        client = LLMClient(provider="OPENAI", model="gpt-4.1-mini")

    assert client.provider == LLMProvider.OPENAI
    assert client.model == "gpt-4.1-mini"
    mock_openai.assert_called_once()


def test_llm_client_uses_openai_base_url_override_from_settings() -> None:
    with patch("app.llm.client.settings.OPENAI_BASE_URL", "https://example.com/v1"):
        with patch("app.llm.client.settings.OPENAI_MODEL", "gpt-4.1-mini"):
            with patch("app.llm.client.AsyncOpenAI") as mock_openai:
                client = LLMClient(provider="openai")

    assert client.provider == LLMProvider.OPENAI
    assert client.model == "gpt-4.1-mini"
    assert mock_openai.call_args.kwargs["base_url"] == "https://example.com/v1"
