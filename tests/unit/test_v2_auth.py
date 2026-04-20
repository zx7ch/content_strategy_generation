from __future__ import annotations

import pytest

from app.config import Settings
from app.v2.auth import WorkspaceAuthError, resolve_workspace_principal


def test_resolve_workspace_principal_from_headers() -> None:
    config = Settings(_env_file=None)

    principal = resolve_workspace_principal(
        {
            "X-Workspace-Id": "ws-1",
            "X-User-Id": "user-1",
        },
        config=config,
    )

    assert principal.workspace_id == "ws-1"
    assert principal.user_id == "user-1"
    assert principal.auth_type == "workspace_headers"


def test_resolve_workspace_principal_requires_workspace_header() -> None:
    config = Settings(_env_file=None)

    with pytest.raises(WorkspaceAuthError, match="Missing workspace scope header"):
        resolve_workspace_principal({"X-User-Id": "user-1"}, config=config)


def test_resolve_workspace_principal_validates_bearer_token_when_enabled() -> None:
    config = Settings(
        _env_file=None,
        V2_AUTH_ENABLED=True,
        V2_AUTH_TOKEN="top-secret",
    )

    principal = resolve_workspace_principal(
        {
            "X-Workspace-Id": "ws-1",
            "X-User-Id": "user-1",
            "Authorization": "Bearer top-secret",
        },
        config=config,
    )

    assert principal.auth_type == "bearer"


def test_resolve_workspace_principal_rejects_invalid_bearer_token() -> None:
    config = Settings(
        _env_file=None,
        V2_AUTH_ENABLED=True,
        V2_AUTH_TOKEN="top-secret",
    )

    with pytest.raises(WorkspaceAuthError, match="Invalid workspace auth token"):
        resolve_workspace_principal(
            {
                "X-Workspace-Id": "ws-1",
                "X-User-Id": "user-1",
                "Authorization": "Bearer wrong",
            },
            config=config,
        )
