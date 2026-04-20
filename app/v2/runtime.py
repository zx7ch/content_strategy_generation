"""Shared runtime backend selection rules for V2 shipped behavior."""

from __future__ import annotations

from app.config import Settings

_LOCAL_RUNTIME_ENVS = {"", "development", "dev", "test", "local"}


class V2RuntimeConfigurationError(RuntimeError):
    """Raised when shipped runtime configuration violates the Postgres contract."""


def resolve_v2_backend(config: Settings, *, component: str) -> str:
    """Resolve whether a V2 component may use Postgres or in-memory persistence."""
    if config.POSTGRES_DSN.strip():
        return "postgres"

    env = config.APP_ENV.strip().lower()
    if env in _LOCAL_RUNTIME_ENVS:
        return "in_memory"

    raise V2RuntimeConfigurationError(
        f"POSTGRES_DSN is required for {component} runtime when APP_ENV={config.APP_ENV!r}"
    )
