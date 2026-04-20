"""Basic workspace-scoped request identity helpers for V2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from app.config import Settings, settings


class WorkspaceAuthError(ValueError):
    """Raised when workspace-scoped request identity cannot be resolved."""


@dataclass(frozen=True)
class WorkspacePrincipal:
    workspace_id: str
    user_id: Optional[str]
    auth_type: str


def _lookup_header(headers: Mapping[str, str], name: str) -> Optional[str]:
    expected = name.lower()
    for key, value in headers.items():
        if key.lower() == expected:
            normalized = str(value).strip()
            return normalized or None
    return None


def resolve_workspace_principal(
    headers: Mapping[str, str],
    *,
    config: Settings | None = None,
    require_user: bool = True,
) -> WorkspacePrincipal:
    config = config or settings
    workspace_id = _lookup_header(headers, config.V2_WORKSPACE_HEADER)
    if workspace_id is None:
        raise WorkspaceAuthError(f"Missing workspace scope header: {config.V2_WORKSPACE_HEADER}")

    user_id = _lookup_header(headers, config.V2_USER_HEADER)
    if require_user and user_id is None:
        raise WorkspaceAuthError(f"Missing user scope header: {config.V2_USER_HEADER}")

    auth_type = "workspace_headers"
    if config.V2_AUTH_ENABLED:
        auth_header = _lookup_header(headers, config.V2_AUTH_HEADER)
        expected = f"Bearer {config.V2_AUTH_TOKEN}".strip()
        if auth_header is None:
            raise WorkspaceAuthError(f"Missing auth header: {config.V2_AUTH_HEADER}")
        if not config.V2_AUTH_TOKEN:
            raise WorkspaceAuthError("V2 auth is enabled but no V2_AUTH_TOKEN is configured")
        if auth_header != expected:
            raise WorkspaceAuthError("Invalid workspace auth token")
        auth_type = "bearer"

    return WorkspacePrincipal(workspace_id=workspace_id, user_id=user_id, auth_type=auth_type)
