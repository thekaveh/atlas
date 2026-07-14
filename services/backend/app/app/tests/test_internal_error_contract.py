from __future__ import annotations

import ast
import logging
import os
from pathlib import Path


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


def test_generic_exception_handlers_never_interpolate_errors_into_http_detail() -> None:
    source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    leaks: list[int] = []

    for handler in (node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)):
        if not (
            isinstance(handler.type, ast.Name)
            and handler.type.id == "Exception"
            and handler.name
        ):
            continue
        for node in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "HTTPException"
            ):
                continue
            detail = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "detail"),
                None,
            )
            if detail is not None and any(
                isinstance(value, ast.Name) and value.id == handler.name
                for value in ast.walk(detail)
            ):
                leaks.append(node.lineno)

    assert leaks == [], f"generic exception details reach clients at lines {leaks}"
