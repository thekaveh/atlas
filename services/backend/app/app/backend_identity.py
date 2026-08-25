"""Authentication and ownership primitives for identity-bearing backend APIs."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import (
    APIKeyHeader,
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from starlette.requests import HTTPConnection


_BEARER = HTTPBearer(auto_error=False)


class _PluginAPIKeyHeader(APIKeyHeader):
    """Extract a plugin key from either HTTP or WebSocket connections."""

    async def __call__(self, connection: HTTPConnection) -> str | None:
        api_key = connection.headers.get(self.model.name)
        if api_key is None:
            api_key = connection.query_params.get(self.model.name)
        return self.check_api_key(api_key)


_PLUGIN_API_KEY = _PluginAPIKeyHeader(
    name="apikey", scheme_name="APIKeyHeader", auto_error=False
)


@dataclass(frozen=True)
class BackendPrincipal:
    kind: Literal["n8n", "notebook", "open-webui", "service", "user"]
    subject: str

    @property
    def can_delegate(self) -> bool:
        return self.kind in {"n8n", "open-webui", "service"}


def _unauthorized(detail: str = "Valid backend bearer authentication is required"):
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _ct_equals(a: str, b: str) -> bool:
    """Constant-time string comparison that tolerates non-ASCII input.

    ``secrets.compare_digest`` raises ``TypeError`` when a ``str`` argument
    contains a non-ASCII character, so a caller-supplied bearer token, plugin
    api-key, or media-consumer label with any non-ASCII byte (e.g. a benign
    Unicode ``"café"`` consumer) would otherwise escape as an unhandled 500
    instead of a clean 401/403. Comparing the utf-8 bytes keeps it constant-time
    and simply returns False for a non-matching non-ASCII value.
    """
    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _authenticate_backend_principal(
    credentials: HTTPAuthorizationCredentials | None,
    *,
    allowed_scoped_callers: frozenset[str] = frozenset(),
    allow_users: bool = True,
) -> BackendPrincipal:
    """Authenticate one backend bearer under the requested route scope."""
    mode = (os.getenv("BACKEND_IDENTITY_AUTH") or "required").strip().lower()
    if mode == "disabled":
        return BackendPrincipal(kind="service", subject="auth-disabled")
    if mode != "required":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="BACKEND_IDENTITY_AUTH must be required or disabled",
        )
    if credentials is None or credentials.scheme.lower() != "bearer":
        _unauthorized()

    token = credentials.credentials
    internal_token = (os.getenv("BACKEND_INTERNAL_API_TOKEN") or "").strip()
    if internal_token and _ct_equals(token, internal_token):
        return BackendPrincipal(kind="service", subject="internal-service")

    scoped_tokens = (
        ("n8n", "BACKEND_N8N_API_TOKEN", "n8n"),
        ("notebook", "BACKEND_NOTEBOOK_API_TOKEN", "jupyterhub"),
        ("open-webui", "BACKEND_OPEN_WEBUI_API_TOKEN", "open-webui"),
    )
    for kind, env_name, subject in scoped_tokens:
        expected = (os.getenv(env_name) or "").strip()
        if expected and _ct_equals(token, expected):
            if kind in allowed_scoped_callers:
                return BackendPrincipal(kind=kind, subject=subject)  # type: ignore[arg-type]
            label = kind.replace("-", " ").title()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"{label} credentials are not authorized for this backend route",
            )

    if not allow_users:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This backend route is restricted to an authorized service scope",
        )

    jwt_secret = (os.getenv("SUPABASE_JWT_SECRET") or "").strip()
    if not jwt_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SUPABASE_JWT_SECRET is required for backend user authentication",
        )
    try:
        claims = jwt.decode(
            token,
            jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
            options={"require": ["exp", "sub"]},
        )
        if claims.get("role") != "authenticated":
            raise jwt.InvalidTokenError("unexpected Supabase role")
        subject = str(UUID(str(claims["sub"])))
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
        _unauthorized("Invalid or expired backend bearer token")
    return BackendPrincipal(kind="user", subject=subject)


async def require_backend_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_BEARER),
) -> BackendPrincipal:
    """Authenticate a trusted service token or a Supabase user JWT."""
    return _authenticate_backend_principal(credentials)


async def require_stateless_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_BEARER),
) -> BackendPrincipal:
    """Also accept the notebook token on routes that hold no user state."""
    return _authenticate_backend_principal(
        credentials, allowed_scoped_callers=frozenset({"notebook"})
    )


async def require_research_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_BEARER),
) -> BackendPrincipal:
    return _authenticate_backend_principal(
        credentials, allowed_scoped_callers=frozenset({"n8n"})
    )


async def require_memory_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_BEARER),
) -> BackendPrincipal:
    return _authenticate_backend_principal(
        credentials, allowed_scoped_callers=frozenset({"open-webui"})
    )


async def require_memory_automation_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_BEARER),
) -> BackendPrincipal:
    return _authenticate_backend_principal(
        credentials, allowed_scoped_callers=frozenset({"n8n", "open-webui"})
    )


async def require_comfy_read_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_BEARER),
) -> BackendPrincipal:
    return _authenticate_backend_principal(
        credentials, allowed_scoped_callers=frozenset({"n8n", "open-webui"})
    )


async def require_comfy_automation_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_BEARER),
) -> BackendPrincipal:
    return _authenticate_backend_principal(
        credentials,
        allowed_scoped_callers=frozenset({"n8n", "open-webui"}),
        allow_users=False,
    )


async def require_n8n_operator_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_BEARER),
) -> BackendPrincipal:
    return _authenticate_backend_principal(
        credentials,
        allowed_scoped_callers=frozenset({"n8n"}),
        allow_users=False,
    )


def authorize_user_id(
    principal: BackendPrincipal,
    claimed_user_id: str | None,
) -> str | None:
    """Resolve a user id while preventing end users from impersonating peers."""
    if principal.can_delegate:
        if claimed_user_id is None:
            return None
        try:
            return str(UUID(claimed_user_id))
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid user_id: must be a valid UUID",
            )
    if claimed_user_id is not None:
        try:
            claimed = str(UUID(claimed_user_id))
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid user_id: must be a valid UUID",
            )
        if not _ct_equals(claimed, principal.subject):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Authenticated user does not own the requested user_id",
            )
    return principal.subject


def research_owner_id(principal: BackendPrincipal) -> str | None:
    """Return the SQL owner filter; trusted services may access delegated work."""
    return None if principal.can_delegate else principal.subject


async def require_service_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_BEARER),
) -> BackendPrincipal:
    """Restrict an operator surface to the trusted internal-service token."""
    return _authenticate_backend_principal(credentials, allow_users=False)


async def require_plugin_gateway_key(
    api_key: str | None = Depends(_PLUGIN_API_KEY),
) -> str:
    """Enforce a plugin's key-auth policy at the application boundary."""
    expected = (os.getenv("BACKEND_KONG_API_KEY") or "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="BACKEND_KONG_API_KEY is required by this plugin route",
        )
    if api_key is None or not _ct_equals(api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid plugin API key authentication is required",
        )
    return "gateway-key"


def authorize_media_scope(
    principal: BackendPrincipal,
    claimed_consumer: str | None,
    claimed_project: str | None,
) -> tuple[str, str]:
    """Bind media spend attribution to users while preserving service labels."""
    consumer = (claimed_consumer or "").strip()
    project = (claimed_project or "default").strip() or "default"
    if principal.can_delegate:
        return consumer or "default", project
    if consumer and not _ct_equals(consumer, principal.subject):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authenticated user does not own the requested media consumer",
        )
    return principal.subject, project


def principal_scope_key(principal: BackendPrincipal) -> str:
    """Stable owner key for process-local operations."""
    return "service" if principal.can_delegate else f"user:{principal.subject}"
