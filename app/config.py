"""Application settings via pydantic-settings.

Production settings must stay aligned with `.env.example`.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_alias(name: str) -> AliasChoices:
    """Allow both canonical name and XHS_* alias for backward compatibility."""
    return AliasChoices(name, f"XHS_{name}")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Runtime
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    RUNTIME_SERVICE_NAME: str = Field(
        default="xhs-agent-runtime",
        validation_alias=_env_alias("RUNTIME_SERVICE_NAME"),
    )
    RUNTIME_VERSION: str = Field(default="0.1.0", validation_alias=_env_alias("RUNTIME_VERSION"))
    RUNTIME_API_CONTRACT: str = Field(
        default="local-runtime-v1",
        validation_alias=_env_alias("RUNTIME_API_CONTRACT"),
    )
    CORS_ALLOWED_ORIGINS: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000,https://xhs-creator.vercel.app",
        validation_alias=_env_alias("CORS_ALLOWED_ORIGINS"),
    )

    # LLM providers
    LLM_PROVIDER: str = "anthropic"
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    ANTHROPIC_API_KEY: str = ""
    DEEPSEEK_API_KEY: str = ""
    MINIMAX_API_KEY: str = ""
    KIMI_API_KEY: str = ""

    ANTHROPIC_MODEL: str = "claude-opus-4-6"
    OPENAI_MODEL: str = "gpt-4o-mini"
    DEEPSEEK_MODEL: str = "deepseek-chat"
    MINIMAX_MODEL: str = "abab6.5s-chat"
    KIMI_MODEL: str = "moonshot-v1-8k"

    # Spider
    XHS_SPIDER_COOKIES: str = ""
    XHS_SPIDER_MAX_RETRIES: int = Field(default=5, ge=0)
    XHS_SPIDER_MAX_AUTO_RETRIES: int = Field(default=3, ge=0)
    XHS_SPIDER_MAX_USER_RETRIES: int = Field(default=2, ge=0)
    XHS_SPIDER_BACKOFF_BASE: int = Field(default=2, ge=1)
    XHS_SPIDER_SORT_TYPE: int = 2

    # Strategy quality
    QUALITY_SCORE_THRESHOLD: float = Field(
        default=0.35,
        validation_alias=_env_alias("QUALITY_SCORE_THRESHOLD"),
        ge=0.0,
        le=1.0,
    )
    EXPANSION_DOC_COUNT_MAX: int = Field(
        default=10,
        validation_alias=_env_alias("EXPANSION_DOC_COUNT_MAX"),
        ge=1,
    )
    EXPANSION_MIN_NEW_UNIQUE_DOCS: int = Field(
        default=3,
        validation_alias=_env_alias("EXPANSION_MIN_NEW_UNIQUE_DOCS"),
        ge=0,
    )
    EXPANSION_MIN_QUALITY_GAIN: float = Field(
        default=0.05,
        validation_alias=_env_alias("EXPANSION_MIN_QUALITY_GAIN"),
        ge=0.0,
        le=1.0,
    )

    # Generation
    PARALLEL_TEMPERATURES: List[float] = Field(
        default_factory=lambda: [0.3, 0.5, 0.7, 0.9, 1.1],
        validation_alias=_env_alias("PARALLEL_TEMPERATURES"),
    )
    GENERATION_MAX_RETRIES: int = Field(default=2, validation_alias=_env_alias("GENERATION_MAX_RETRIES"), ge=0)
    GENERATION_PARALLEL_SLOTS: int = Field(default=5, validation_alias=_env_alias("GENERATION_PARALLEL_SLOTS"), ge=1)
    GENERATION_DEGRADED_SLOTS: int = Field(default=3, validation_alias=_env_alias("GENERATION_DEGRADED_SLOTS"), ge=1)
    NUM_PROPOSALS: int = Field(default=10, validation_alias=_env_alias("NUM_PROPOSALS"), ge=1)
    NUM_FINAL_NOTES: int = Field(default=5, validation_alias=_env_alias("NUM_FINAL_NOTES"), ge=1)

    # Budget & routing
    SESSION_TOKEN_BUDGET: int = Field(default=120000, validation_alias=_env_alias("SESSION_TOKEN_BUDGET"), ge=1)
    LLM_MODEL_STRATEGY: str = Field(default="high_quality_model", validation_alias=_env_alias("LLM_MODEL_STRATEGY"))
    LLM_MODEL_QUERY_EXPANSION: str = Field(default="high_quality_model", validation_alias=_env_alias("LLM_MODEL_QUERY_EXPANSION"))
    LLM_MODEL_PROPOSAL: str = Field(default="balanced_model", validation_alias=_env_alias("LLM_MODEL_PROPOSAL"))
    LLM_MODEL_NOTE_GENERATION: str = Field(default="low_cost_model", validation_alias=_env_alias("LLM_MODEL_NOTE_GENERATION"))
    LLM_MODEL_FALLBACK_GENERIC: str = Field(default="balanced_model", validation_alias=_env_alias("LLM_MODEL_FALLBACK_GENERIC"))

    # Similarity check
    EMBEDDING_REWRITE_THRESHOLD: float = Field(
        default=0.6,
        validation_alias=_env_alias("EMBEDDING_REWRITE_THRESHOLD"),
        ge=0.0,
        le=1.0,
    )
    EMBEDDING_WARNING_THRESHOLD: float = Field(
        default=0.3,
        validation_alias=_env_alias("EMBEDDING_WARNING_THRESHOLD"),
        ge=0.0,
        le=1.0,
    )
    LEXICAL_WARNING_THRESHOLD: float = Field(
        default=0.4,
        validation_alias=_env_alias("LEXICAL_WARNING_THRESHOLD"),
        ge=0.0,
        le=1.0,
    )

    # Session lifecycle
    SESSION_TIMEOUT_MINUTES: int = 30
    SESSION_ALIVE_HOURS: int = Field(default=24, validation_alias=_env_alias("SESSION_ALIVE_HOURS"), ge=1)
    SESSION_FROZEN_AFTER_HOURS: int = Field(default=24, validation_alias=_env_alias("SESSION_FROZEN_AFTER_HOURS"), ge=1)
    SESSION_PURGE_AFTER_DAYS: int = Field(default=10, validation_alias=_env_alias("SESSION_PURGE_AFTER_DAYS"), ge=1)

    # Job queue / SSE
    JOB_POLL_INTERVAL_MS: int = Field(default=500, validation_alias=_env_alias("JOB_POLL_INTERVAL_MS"), ge=1)
    JOB_LEASE_SECONDS: int = Field(default=60, validation_alias=_env_alias("JOB_LEASE_SECONDS"), ge=1)
    JOB_MAX_RETRIES: int = Field(default=5, validation_alias=_env_alias("JOB_MAX_RETRIES"), ge=0)
    JOB_RECOVERY_ON_STARTUP: bool = Field(default=True, validation_alias=_env_alias("JOB_RECOVERY_ON_STARTUP"))
    SSE_HEARTBEAT_SECONDS: int = Field(default=15, validation_alias=_env_alias("SSE_HEARTBEAT_SECONDS"), ge=1)
    SSE_REPLAY_LIMIT: int = Field(default=500, validation_alias=_env_alias("SSE_REPLAY_LIMIT"), ge=1)

    # Observability
    METRICS_ROLLING_WINDOW_MINUTES: int = Field(default=5, validation_alias=_env_alias("METRICS_ROLLING_WINDOW_MINUTES"), ge=1)
    ALERT_EVALUATOR_INTERVAL_SECONDS: int = Field(default=60, validation_alias=_env_alias("ALERT_EVALUATOR_INTERVAL_SECONDS"), ge=1)
    ALERT_JOB_SUCCESS_RATE_MIN: float = Field(default=0.99, validation_alias=_env_alias("ALERT_JOB_SUCCESS_RATE_MIN"), ge=0.0, le=1.0)
    ALERT_JOB_RECOVERY_SUCCESS_RATE_MIN: float = Field(default=0.99, validation_alias=_env_alias("ALERT_JOB_RECOVERY_SUCCESS_RATE_MIN"), ge=0.0, le=1.0)
    ALERT_LLM_P95_LATENCY_MS_MAX: int = Field(default=8000, validation_alias=_env_alias("ALERT_LLM_P95_LATENCY_MS_MAX"), ge=1)
    ALERT_BUDGET_EXCEEDED_COUNT_MAX: int = Field(default=5, validation_alias=_env_alias("ALERT_BUDGET_EXCEEDED_COUNT_MAX"), ge=0)
    ALERT_REINDEX_BACKLOG_COUNT_MAX: int = Field(default=20, validation_alias=_env_alias("ALERT_REINDEX_BACKLOG_COUNT_MAX"), ge=0)
    ALERT_REINDEX_SUCCESS_RATE_MIN: float = Field(default=0.95, validation_alias=_env_alias("ALERT_REINDEX_SUCCESS_RATE_MIN"), ge=0.0, le=1.0)

    # RAG
    RAG_EMBEDDING_MODEL: str = Field(default="BAAI/bge-base-zh-v1.5", validation_alias=_env_alias("RAG_EMBEDDING_MODEL"))
    RAG_CHUNK_MAX_LENGTH: int = Field(default=1000, validation_alias=_env_alias("RAG_CHUNK_MAX_LENGTH"), ge=1)
    REINDEX_MAX_ATTEMPTS: int = Field(default=3, validation_alias=_env_alias("REINDEX_MAX_ATTEMPTS"), ge=1)

    # Storage
    SQLITE_DB_PATH: str = Field(default="./data/xhs_agent.db", validation_alias=_env_alias("SQLITE_DB_PATH"))
    CHROMA_PERSIST_DIR: str = Field(default="./data/chroma", validation_alias=_env_alias("CHROMA_PERSIST_DIR"))
    POSTGRES_DSN: str = Field(default="", validation_alias=_env_alias("POSTGRES_DSN"))
    V2_DISCOVERY_SQLITE_PATH: str = Field(
        default="./data/xhs_discovery.db",
        validation_alias=_env_alias("V2_DISCOVERY_SQLITE_PATH"),
    )
    V2_DISCOVERY_TOKEN_SECRET: str = Field(
        default="xhs-discovery-dev-secret",
        validation_alias=_env_alias("V2_DISCOVERY_TOKEN_SECRET"),
    )

    # V2 foundation
    V2_AUTH_ENABLED: bool = Field(default=False, validation_alias=_env_alias("V2_AUTH_ENABLED"))
    V2_AUTH_TOKEN: str = Field(default="", validation_alias=_env_alias("V2_AUTH_TOKEN"))
    V2_AUTH_HEADER: str = Field(default="Authorization", validation_alias=_env_alias("V2_AUTH_HEADER"))
    V2_WORKSPACE_HEADER: str = Field(default="X-Workspace-Id", validation_alias=_env_alias("V2_WORKSPACE_HEADER"))
    V2_USER_HEADER: str = Field(default="X-User-Id", validation_alias=_env_alias("V2_USER_HEADER"))

    @property
    def cors_allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()]

    @model_validator(mode="after")
    def _validate_paths(self) -> "Settings":
        sqlite = Path(self.SQLITE_DB_PATH).expanduser()
        if str(sqlite) != ":memory:":
            # Auto-create parent directory if not exists
            if not sqlite.parent.exists():
                sqlite.parent.mkdir(parents=True, exist_ok=True)

        discovery_sqlite = Path(self.V2_DISCOVERY_SQLITE_PATH).expanduser()
        if str(discovery_sqlite) != ":memory:" and not discovery_sqlite.parent.exists():
            discovery_sqlite.parent.mkdir(parents=True, exist_ok=True)

        chroma_dir = Path(self.CHROMA_PERSIST_DIR).expanduser()
        if not chroma_dir.exists():
            chroma_dir.mkdir(parents=True, exist_ok=True)

        if self.XHS_SPIDER_MAX_RETRIES <= 0:
            self.XHS_SPIDER_MAX_RETRIES = self.XHS_SPIDER_MAX_AUTO_RETRIES + self.XHS_SPIDER_MAX_USER_RETRIES
        return self


settings = Settings()
