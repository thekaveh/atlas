"""Every backend route must carry an authentication dependency.

This is a STATIC scan, so it runs in the bootstrapper suite without the
backend's own dependencies installed — the backend's `tests/` directory is not
collected here, and installing FastAPI plus Ray to assert a structural property
would be the wrong trade.

Why it exists: the backend's per-route dependencies are the only access control
in the DEFAULT profile. `BACKEND_KONG_AUTH` defaults to `disabled`, and the
backend port publishes on `0.0.0.0` unless `--profile prod` sets `HOST_BIND_IP`.
A route added without a dependency is therefore LAN-reachable with no gate at
all, and nothing in the suite noticed. Coverage was complete when this was
written; this keeps it that way.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND_APP = Path(__file__).resolve().parents[2] / "services" / "backend" / "app" / "app"

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}

#: Vendored third-party code that happens to live under the app directory.
_VENDORED = ("site-packages", ".ci-venv", ".venv", "node_modules", "/tests/")

#: Deliberately public. Each returns a fixed liveness/readiness token and
#: reveals nothing about configuration — `/ready` answers only
#: `ready`/`unavailable` per dependency.
_PUBLIC_ROUTES = {
    ("main.py", "GET", "/"),
    ("main.py", "GET", "/health"),
    ("main.py", "GET", "/ready"),
}


def _is_vendored(path: Path) -> bool:
    return any(marker in str(path) for marker in _VENDORED)


def _declares_dependencies(call: ast.Call) -> bool:
    """True when `dependencies=[...]` is present AND non-empty."""
    for keyword in call.keywords:
        if keyword.arg != "dependencies":
            continue
        if isinstance(keyword.value, (ast.List, ast.Tuple)):
            return bool(keyword.value.elts)
        return True  # a computed list — assume it carries something
    return False


def _signature_uses_depends(func: ast.AST) -> bool:
    """True when any parameter default is a `Depends(...)` call."""
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Depends"
        for node in ast.walk(func.args)
    )


def _router_level_auth(tree: ast.Module) -> dict:
    """Router/app objects constructed with their own `dependencies=`.

    `ray_routes.py` gates its whole surface this way — the routes themselves
    carry no dependency, so ignoring this would report false offenders.
    """
    authed = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        func = node.value.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name not in ("APIRouter", "FastAPI"):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                authed[target.id] = _declares_dependencies(node.value)
    return authed


def _collect_routes() -> list[tuple[str, str, str, bool, str]]:
    rows = []
    for path in sorted(BACKEND_APP.rglob("*.py")):
        if _is_vendored(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a parse failure is its own test
            continue
        router_authed = _router_level_auth(tree)
        rel = str(path.relative_to(BACKEND_APP))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                rows.extend(_routes_on(node, rel, router_authed))
    return rows


def _route_decorators(func: ast.AST):
    """Decorators of `func` that register an HTTP route."""
    for decorator in func.decorator_list:
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr in _HTTP_METHODS
        ):
            yield decorator


def _decorated_path(decorator: ast.Call) -> str:
    first = decorator.args[0] if decorator.args else None
    return first.value if isinstance(first, ast.Constant) else "?"


def _routes_on(func: ast.AST, rel: str, router_authed: dict) -> list:
    rows = []
    for decorator in _route_decorators(func):
        owner = getattr(decorator.func.value, "id", "?")
        authed = (
            _declares_dependencies(decorator)
            or _signature_uses_depends(func)
            or router_authed.get(owner, False)
        )
        rows.append((
            rel,
            decorator.func.attr.upper(),
            _decorated_path(decorator),
            authed,
            func.name,
        ))
    return rows


def test_the_scan_actually_finds_the_backend_routes():
    """A guard that silently matches nothing is worse than no guard."""
    assert BACKEND_APP.is_dir(), f"backend app tree not found at {BACKEND_APP}"
    rows = _collect_routes()
    assert len(rows) >= 40, f"only found {len(rows)} routes — the scan is not matching"
    assert any(r[2] == "/api/ray/jobs/submit" or r[0] == "ray_routes.py" for r in rows)


def test_every_backend_route_requires_authentication():
    unauthenticated = [
        row for row in _collect_routes()
        if not row[3] and (row[0], row[1], row[2]) not in _PUBLIC_ROUTES
    ]
    assert not unauthenticated, (
        "backend routes with no auth dependency (add one, or add it to "
        "_PUBLIC_ROUTES with a reason):\n"
        + "\n".join(f"  {r[0]}:{r[4]} {r[1]} {r[2]}" for r in unauthenticated)
    )


def test_the_ray_job_surface_is_gated_at_the_router():
    """Ray job submission runs an arbitrary shell entrypoint on the cluster.

    It is gated by a router-level dependency rather than per-route ones, so a
    per-route-only check would have reported it as open. Pin the actual shape.
    """
    tree = ast.parse((BACKEND_APP / "ray_routes.py").read_text(encoding="utf-8"))
    routers = _router_level_auth(tree)
    assert routers, "no APIRouter found in ray_routes.py"
    assert all(routers.values()), f"ray router has no dependencies: {routers}"


@pytest.mark.parametrize("public", sorted(_PUBLIC_ROUTES))
def test_each_declared_public_route_still_exists(public):
    """Otherwise the allowlist rots into a licence for a future route."""
    rows = {(r[0], r[1], r[2]) for r in _collect_routes()}
    assert public in rows, f"{public} is allowlisted as public but no longer exists"
