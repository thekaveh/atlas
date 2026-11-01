"""Every backend route must carry an authentication dependency.

This is a STATIC scan, so it runs in the bootstrapper suite without the
backend's own dependencies installed — the backend's `tests/` directory is not
collected here, and installing FastAPI plus Ray to assert a structural property
would be the wrong trade.

Why it exists: the backend's per-route dependencies remain the route-level
access control. `BACKEND_KONG_AUTH` defaults to `disabled`; the direct backend
port is loopback-bound by default, but an operator can deliberately publish it
remotely with `HOST_BIND_IP`. A route added without a dependency would then be
reachable without a route-level gate. Coverage was complete when this was
written; this keeps it that way.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND_APP = Path(__file__).resolve().parents[2] / "services" / "backend" / "app" / "app"

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
#: Decorators that register a route without naming an HTTP verb. A websocket
#: endpoint, an `api_route(methods=[...])` or a Starlette `route` /
#: `websocket_route` is just as reachable.
_ROUTE_DECORATORS = _HTTP_METHODS | {"websocket", "api_route", "route", "websocket_route"}

#: Vendored third-party code that happens to live under the app directory.
_VENDORED = ("site-packages", ".ci-venv", ".venv", "node_modules", "/tests/")

#: Calls that register a route without a decorator. Only `add_api_route` can
#: carry a literal auth `dependencies=[...]`; the Starlette forms cannot.
_IMPERATIVE_ROUTE_CALLS = {
    "add_api_route", "add_api_websocket_route", "add_route", "add_websocket_route", "mount",
}

#: Deliberately public. The first three return a fixed liveness/readiness token
#: and reveal nothing about configuration — `/ready` answers only
#: `ready`/`unavailable` per dependency. `/metrics` is the Prometheus scrape
#: endpoint that services/backend/README.md lists as public diagnostics.
_PUBLIC_ROUTES = {
    ("main.py", "GET", "/"),
    ("main.py", "GET", "/health"),
    ("main.py", "GET", "/ready"),
    ("main.py", "GET", "/metrics"),
}


def _is_vendored(path: Path) -> bool:
    return any(marker in str(path) for marker in _VENDORED)


def _is_depends_on_auth(node: ast.AST) -> bool:
    """`Depends(<auth dependency>)` — nothing else counts."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Depends"
        and bool(node.args)
        and _is_auth_dependency(node.args[0])
    )


def _declares_dependencies(call: ast.Call) -> bool:
    """True when a literal `dependencies=[...]` holds an auth dependency.

    A non-empty list is not enough: `dependencies=[Depends(get_db)]` is not
    access control. A computed list cannot be inspected statically, so it fails
    closed rather than being assumed to carry auth.
    """
    for keyword in call.keywords:
        if keyword.arg != "dependencies":
            continue
        if isinstance(keyword.value, (ast.List, ast.Tuple)):
            return any(_is_depends_on_auth(elt) for elt in keyword.value.elts)
        return False
    return False


def _is_auth_dependency(target: ast.AST) -> bool:
    """`require_*` / `_require_*` are the backend's enforcing dependencies.

    Anything else does not count: `Depends(_BEARER)` is an
    `HTTPBearer(auto_error=False)` that enforces nothing, and `get_db`-style
    dependencies are not access control.
    """
    name = getattr(target, "id", None) or getattr(target, "attr", None) or ""
    return name.startswith(("require_", "_require_"))


def _signature_uses_depends(func: ast.AST) -> bool:
    """True when a parameter default is `Depends(<auth dependency>)`."""
    return any(_is_depends_on_auth(node) for node in ast.walk(func.args))


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
        rows.extend(_imperative_routes(tree, rel))
    return rows


def _imperative_route(call: ast.Call) -> tuple[str, str] | None:
    """(method label, path) when ``call`` registers a route imperatively.

    A keyword-only registration (``path=...``) is scanned too; its path reads
    as ``?`` when not literal, which never matches ``_PUBLIC_ROUTES``.
    """
    if not isinstance(call.func, ast.Attribute):
        return None
    if call.func.attr in _IMPERATIVE_ROUTE_CALLS:
        return call.func.attr.upper(), _decorated_path(call)
    if call.func.attr == "expose":
        # prometheus_fastapi_instrumentator: expose(app, endpoint="/metrics") adds GET <endpoint>.
        endpoint = next((kw.value for kw in call.keywords if kw.arg == "endpoint"), ast.Constant("/metrics"))
        return "GET", endpoint.value if isinstance(endpoint, ast.Constant) else "?"
    return None


def _imperative_routes(tree: ast.AST, rel: str) -> list:
    """Routes registered by a call rather than a decorator.

    Only a literal auth ``dependencies=[...]`` counts, so a Starlette route,
    a mount or an exposed endpoint fails closed until it is listed as public.
    """
    rows = []
    for node in ast.walk(tree):
        route = _imperative_route(node) if isinstance(node, ast.Call) else None
        if route:
            method, path = route
            rows.append((rel, method, path, _declares_dependencies(node), f"<{node.func.attr} call>"))
    return rows


def _route_decorators(func: ast.AST):
    """Decorators of `func` that register an HTTP or websocket route."""
    for decorator in func.decorator_list:
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr in _ROUTE_DECORATORS
        ):
            yield decorator


def _decorated_path(decorator: ast.Call) -> str:
    """The route path, positional or ``path=``; ``?`` when not a literal."""
    first = decorator.args[0] if decorator.args else next(
        (kw.value for kw in decorator.keywords if kw.arg == "path"), None
    )
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


def _add_middleware_calls(tree: ast.AST) -> dict[str, list[ast.Call]]:
    """All ``app.add_middleware(Name, ...)`` calls, keyed by middleware name."""
    calls: dict[str, list[ast.Call]] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_middleware"
            and node.args
            and isinstance(node.args[0], ast.Name)
        ):
            calls.setdefault(node.args[0].id, []).append(node)
    return calls


def _ruled_paths(rules: ast.expr | None) -> set[str]:
    """Route paths declared by a ``rules=[...]`` middleware keyword."""
    paths: set[str] = set()
    if not isinstance(rules, ast.List):
        return paths
    for element in rules.elts:
        if not isinstance(element, ast.Call):
            continue
        if isinstance(element.func, ast.Name) and element.func.id == "media_rule":
            paths.add("/media/generate")
            continue
        for keyword in element.keywords:
            if keyword.arg == "path" and isinstance(keyword.value, ast.Constant):
                paths.add(keyword.value.value)
    return paths


def _rule_authenticators(rules: ast.expr | None) -> dict[str, str]:
    """Path -> authenticator name for each ``BodyLimitRule(..., authenticate=True)``."""
    found: dict[str, str] = {}
    for element in getattr(rules, "elts", []):
        keywords = {kw.arg: kw.value for kw in getattr(element, "keywords", [])}
        path, enabled = keywords.get("path"), keywords.get("authenticate")
        if not (isinstance(path, ast.Constant) and isinstance(enabled, ast.Constant) and enabled.value is True):
            continue
        authenticator = keywords.get("authenticator")
        found[path.value] = getattr(authenticator, "id", "<middleware default>")
    return found


def _limit_policy_call(tree, registration):
    """The LimitPolicy(...) call a registration installs. main.py builds it in
    _request_limit_policy so it can take the plugin inventory (2026-10-08
    run, cycle 44); follow that helper to the LimitPolicy it returns."""
    policy = {kw.arg: kw.value for kw in registration.keywords}.get("policy")
    assert isinstance(policy, ast.Call), "policy=LimitPolicy(<rules>) must be present"
    builders = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    builder = builders.get(getattr(policy.func, "id", None))
    if builder is None:
        return policy
    return next(node.value for node in ast.walk(builder) if isinstance(node, ast.Return))


def test_request_limit_middleware_is_wired_to_header_authentication():
    """The body-limit gate must sit outside request-parsing instrumentation.

    #1167 generalized the media-only middleware into route-class byte
    envelopes; this pins the registration shape: exactly one
    RequestLimitMiddleware, carrying the media rule plus the two multipart
    routes, authenticated by the backend-scope header check, ordered
    CORS -> RequestLimit -> Prometheus.
    """
    tree = ast.parse((BACKEND_APP / "main.py").read_text(encoding="utf-8"))
    calls = _add_middleware_calls(tree)
    registrations = calls.get("RequestLimitMiddleware", [])
    cors_registrations = calls.get("CORSMiddleware", [])

    assert len(registrations) == 1, (
        "expected exactly one RequestLimitMiddleware registration"
    )

    policy = _limit_policy_call(tree, registrations[0])
    rules = next(
        (kw.value for kw in policy.keywords if kw.arg == "rules"),
        None,
    )
    ruled_paths = _ruled_paths(rules)
    assert {"/media/generate", "/storage/upload", "/documents/extract"} <= ruled_paths, (
        f"route-class rules must cover the media and multipart routes; saw {ruled_paths}"
    )
    authenticate = next(
        (
            keyword.value
            for keyword in registrations[0].keywords
            if keyword.arg == "authenticate"
        ),
        None,
    )
    assert isinstance(authenticate, ast.Name)
    assert authenticate.id == "authenticate_backend_scope"
    # FastAPI spools a multipart body before route dependencies run, so each
    # upload rule authenticates first, with the check its route dependency uses.
    assert _rule_authenticators(rules) == {
        "/storage/upload": "authenticate_n8n_operator_scope",
        "/documents/extract": "authenticate_stateless_scope",
    }

    prometheus_instrument = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "instrument"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "app"
    ]
    assert len(prometheus_instrument) == 1
    assert len(cors_registrations) == 1
    assert (
        prometheus_instrument[0].lineno
        < registrations[0].lineno
        < cors_registrations[0].lineno
    ), "registration order must build CORS -> Media -> Prometheus"


@pytest.mark.parametrize("public", sorted(_PUBLIC_ROUTES))
def test_each_declared_public_route_still_exists(public):
    """Otherwise the allowlist rots into a licence for a future route."""
    rows = {(r[0], r[1], r[2]) for r in _collect_routes()}
    assert public in rows, f"{public} is allowlisted as public but no longer exists"


def test_a_non_enforcing_dependency_does_not_count_as_auth() -> None:
    tree = ast.parse(
        "async def a(c = Depends(_BEARER), db = Depends(get_db)): ...\n"
        "async def b(p = Depends(require_service_principal)): ...\n"
    )
    a, b = tree.body
    assert _signature_uses_depends(a) is False
    assert _signature_uses_depends(b) is True


def test_a_non_auth_dependencies_list_does_not_count_as_auth() -> None:
    """`dependencies=[Depends(get_db)]` is non-empty but enforces nothing."""
    tree = ast.parse(
        "@app.get('/a', dependencies=[Depends(get_db)])\n"
        "async def a(): ...\n"
        "@app.get('/b', dependencies=[Depends(get_db), Depends(require_x)])\n"
        "async def b(): ...\n"
        "@app.get('/c', dependencies=DEPS)\n"
        "async def c(): ...\n"
    )
    authed = {r[2]: r[3] for f in tree.body for r in _routes_on(f, "x.py", {})}
    assert authed == {"/a": False, "/b": True, "/c": False}


def test_a_router_with_only_non_auth_dependencies_is_not_authed() -> None:
    tree = ast.parse(
        "open_router = APIRouter(dependencies=[Depends(get_db)])\n"
        "gated = APIRouter(dependencies=[Depends(_require_token)])\n"
    )
    assert _router_level_auth(tree) == {"open_router": False, "gated": True}


def test_websocket_and_api_route_decorators_are_scanned() -> None:
    tree = ast.parse(
        "@router.websocket('/ws')\n"
        "async def ws(websocket): ...\n"
        "@app.api_route('/any', methods=['GET', 'POST'])\n"
        "async def any_(): ...\n"
        "@app.websocket_route('/w')\n"
        "async def w(websocket): ...\n"
        "@router.route('/rr')\n"
        "async def rr(request): ...\n"
        "@app.get(path='/kw')\n"
        "async def kw(): ...\n"
    )
    rows = [r for f in tree.body for r in _routes_on(f, "x.py", {})]
    assert {(r[1], r[2], r[3]) for r in rows} == {
        ("WEBSOCKET", "/ws", False),
        ("API_ROUTE", "/any", False),
        ("WEBSOCKET_ROUTE", "/w", False),
        ("ROUTE", "/rr", False),
        ("GET", "/kw", False),
    }


def test_imperative_route_registrations_are_scanned() -> None:
    tree = ast.parse(
        "app.add_api_route('/a', a)\n"
        "app.add_api_route('/b', b, dependencies=[Depends(require_x)])\n"
        "router.add_route('/c', c)\n"
        "app.add_websocket_route('/d', d)\n"
        "app.mount('/static', files)\n"
        "Instrumentator().instrument(app).expose(app, endpoint='/m')\n"
        "Instrumentator().expose(app)\n"
        "app.add_middleware(Middleware)\n"
        "app.add_api_websocket_route('/aw', h)\n"
        "app.add_api_route(path='/k', endpoint=f)\n"
        "app.add_route(path='/r', route=f)\n"
        "app.mount(path='/s', app=files)\n"
        "app.add_api_route(path=PATH, endpoint=f)\n"
    )
    assert {(r[1], r[2], r[3]) for r in _imperative_routes(tree, "x.py")} == {
        ("ADD_API_ROUTE", "/a", False), ("ADD_API_ROUTE", "/b", True),
        ("ADD_ROUTE", "/c", False), ("ADD_WEBSOCKET_ROUTE", "/d", False),
        ("MOUNT", "/static", False), ("GET", "/m", False), ("GET", "/metrics", False),
        ("ADD_API_WEBSOCKET_ROUTE", "/aw", False), ("ADD_API_ROUTE", "/k", False),
        ("ADD_ROUTE", "/r", False), ("MOUNT", "/s", False), ("ADD_API_ROUTE", "?", False),
    }
