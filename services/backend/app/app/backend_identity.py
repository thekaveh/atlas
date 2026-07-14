"""Authentication and ownership primitives for identity-bearing backend APIs."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt


_BEARER = HTTPBearer(auto_error=False)
_PLUGIN_API_KEY = APIKeyHeader(name="apikey", auto_error=False)


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
    if internal_token and secrets.compare_digest(token, internal_token):
        return BackendPrincipal(kind="service", subject="internal-service")

    scoped_tokens = (
        ("n8n", "BACKEND_N8N_API_TOKEN", "n8n"),
        ("notebook", "BACKEND_NOTEBOOK_API_TOKEN", "jupyterhub"),
        ("open-webui", "BACKEND_OPEN_WEBUI_API_TOKEN", "open-webui"),
    )
    for kind, env_name, subject in scoped_tokens:
        expected = (os.getenv(env_name) or "").strip()
        if expected and secrets.compare_digest(token, expected):
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
            options={"require_exp": True, "require_sub": True},
        )
        if claims.get("role") != "authenticated":
            raise JWTError("unexpected Supabase role")
        subject = str(UUID(str(claims["sub"])))
    except (JWTError, KeyError, TypeError, ValueError):
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
        if not secrets.compare_digest(claimed, principal.subject):
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
    if api_key is None or not secrets.compare_digest(api_key, expected):
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
    if consumer and not secrets.compare_digest(consumer, principal.subject):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authenticated user does not own the requested media consumer",
        )
    return principal.subject, project


def principal_scope_key(principal: BackendPrincipal) -> str:
    """Stable owner key for process-local operations."""
    return "service" if principal.can_delegate else f"user:{principal.subject}"
