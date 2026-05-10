from __future__ import annotations

import os
from dataclasses import dataclass

from openai import OpenAI


class MVPLLMConfigError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MVPLLMConfig:
    api_key: str
    base_url: str
    model: str


def load_llm_config() -> MVPLLMConfig:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise MVPLLMConfigError("OPENAI_API_KEY is not configured")
    return MVPLLMConfig(
        api_key=api_key,
        base_url=os.environ.get("OPENAI_BASE_URL", "").strip(),
        model=os.environ.get("OPENAI_MODEL", "").strip() or "gpt-4o-mini",
    )


class OpenAICompatibleLLMClient:
    def __init__(self, *, config: MVPLLMConfig | None = None) -> None:
        self.config = config or load_llm_config()
        self._client = OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url or None,
        )

    def chat(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 400,
        temperature: float = 0.4,
    ) -> str:
        response = self._client.chat.completions.create(
            model=self.config.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return (response.choices[0].message.content or "").strip()
