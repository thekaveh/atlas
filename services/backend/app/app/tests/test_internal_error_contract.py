from __future__ import annotations

import ast
import logging
import os
from pathlib import Path


def _stub_celery_env(monkeypatch) -> None:
    for var, default in (
        ("CELERY_SOURCE", "container"),
        ("CELERY_BROKER_URL", "redis://:password@redis:6379/4"),
        ("CELERY_RESULT_BACKEND", "redis://:password@redis:6379/4"),
    ):
        monkeypatch.setenv(var, default)


def test_unexpected_error_logs_exception_without_exposing_detail(
    caplog, monkeypatch
) -> None:
    for var, default in (
        ("KONG_URL", "http://kong-api-gateway:8000"),
        ("SUPABASE_SERVICE_KEY", "dummy-key"),
        ("DATABASE_URL", "postgresql://x:x@localhost/x"),
    ):
        if not os.environ.get(var):
            monkeypatch.setenv(var, default)
    import main

    secret = "postgresql://admin:secret@database/internal"
    try:
        raise RuntimeError(secret)
    except RuntimeError as exc:
        with caplog.at_level(logging.ERROR, logger=main.__name__):
            error = main._unexpected_error("List workflows", exc)

    assert error.status_code == 500
    assert error.detail == "List workflows failed"
    assert secret not in error.detail
    assert secret not in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "test_internal_error_contract.py" in caplog.text


_GENERIC_EXCEPTIONS = {"Exception", "BaseException"}
_NOT_APP_CODE = ("tests", ".venv", ".ci-venv", "site-packages", "node_modules")


def _is_generic_handler(handler: ast.ExceptHandler) -> bool:
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return bool(handler.name) and any(
        isinstance(t, ast.Name) and t.id in _GENERIC_EXCEPTIONS for t in types
    )


def _http_detail(call: ast.Call) -> ast.AST | None:
    """`detail=` or the positional second argument of HTTPException(...)."""
    keyword = next((k.value for k in call.keywords if k.arg == "detail"), None)
    if keyword is not None:
        return keyword
    return call.args[1] if len(call.args) > 1 else None


def _leaks_in(tree: ast.AST) -> list[int]:
    leaks: list[int] = []
    for handler in (node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)):
        if not _is_generic_handler(handler):
            continue
        for node in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
            if not (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", None)) == "HTTPException"
            ):
                continue
            detail = _http_detail(node)
            if detail is not None and any(
                isinstance(value, ast.Name) and value.id == handler.name
                for value in ast.walk(detail)
            ):
                leaks.append(node.lineno)
    return leaks


def test_generic_exception_handlers_never_interpolate_errors_into_http_detail() -> None:
    # Every app module, not only main.py: ray_routes.py and the other routers
    # raise HTTPException from their own handlers.
    app_dir = Path(__file__).parents[1]
    leaks: dict[str, list[int]] = {}
    for path in sorted(app_dir.rglob("*.py")):
        rel = path.relative_to(app_dir)
        if any(part in _NOT_APP_CODE for part in rel.parts):
            continue
        found = _leaks_in(ast.parse(path.read_text(encoding="utf-8")))
        if found:
            leaks[str(rel)] = found

    assert leaks == {}, f"generic exception details reach clients: {leaks}"


def test_leak_scan_sees_positional_tuple_and_base_exception_forms() -> None:
    tree = ast.parse(
        "try:\n    pass\nexcept (ValueError, Exception) as e:\n"
        "    raise HTTPException(500, str(e))\n"
        "try:\n    pass\nexcept BaseException as err:\n"
        "    raise fastapi.HTTPException(status_code=500, detail=f'{err}')\n"
    )
    assert _leaks_in(tree) == [4, 8]


def test_memory_execution_recovery_atomically_fences_prior_owner(monkeypatch):
    _stub_celery_env(monkeypatch)
    import celery_app
    import redis

    calls = []

    class Client:
        def set(self, *_args, **_kwargs):
            return False

        def get(self, _key):
            return "running:owner-1"

        def eval(self, *args):
            calls.append(args)
            return 1

        def close(self):
            return None

    monkeypatch.setattr(redis.Redis, "from_url", lambda *_a, **_k: Client())

    claim = celery_app.claim_memory_execution("job-1", "owner-2", "owner-1")

    assert claim == ("claimed", None)
    assert calls[0][1:] == (
        1,
        "atlas:celery:memory-execution:job-1",
        "running:owner-1",
        "running:owner-2",
        celery_app.memory_execution_lease_seconds(),
    )
    assert "SET" in calls[0][0] and "GET" in calls[0][0]


def test_memory_execution_recovery_loses_an_atomic_refresh_race(monkeypatch):
    _stub_celery_env(monkeypatch)
    import celery_app
    import redis

    class Client:
        def set(self, *_args, **_kwargs):
            return False

        def get(self, _key):
            return "running:owner-1"

        def eval(self, *_args):
            return 0

        def close(self):
            return None

    monkeypatch.setattr(redis.Redis, "from_url", lambda *_a, **_k: Client())

    claim = celery_app.claim_memory_execution("job-1", "owner-2", "owner-1")

    assert claim == ("busy", None)


def test_memory_execution_allows_only_one_duplicate_recovery(monkeypatch):
    _stub_celery_env(monkeypatch)
    import celery_app
    import redis

    class Client:
        value = "running:owner-1"

        def set(self, *_args, **_kwargs):
            return False

        def get(self, _key):
            return type(self).value

        def eval(self, *args):
            _script, _count, _key, old_owner, new_owner, _seconds = args
            if type(self).value != old_owner:
                return 0
            type(self).value = new_owner
            return 1

        def close(self):
            return None

    monkeypatch.setattr(redis.Redis, "from_url", lambda *_a, **_k: Client())

    first = celery_app.claim_memory_execution("job-1", "owner-2", "owner-1")
    second = celery_app.claim_memory_execution("job-1", "owner-3", "owner-1")

    assert first == ("claimed", None)
    assert second == ("busy", None)
