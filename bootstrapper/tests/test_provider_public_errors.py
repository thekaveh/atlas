from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROVIDERS = (
    "services/docling/provider/shared/api_server.py",
    "services/docling/provider/localhost/server.py",
    "services/parakeet/provider/shared/api_server.py",
    "services/parakeet/provider/mlx/api_server.py",
)
DIAGNOSTIC_MODULES = PROVIDERS + (
    "services/parakeet/provider/gpu/transcribe.py",
    "services/parakeet/provider/shared/utils.py",
    "services/backend/app/app/memory_service.py",
    "services/backend/app/app/memory_store.py",
)


def test_provider_http_errors_do_not_serialize_exception_text():
    for relative in PROVIDERS:
        source = (ROOT / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        unsafe = []
        for handler in (node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)):
            if not (
                isinstance(handler.type, ast.Name)
                and handler.type.id == "Exception"
            ):
                continue
            for node in ast.walk(handler):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if keyword.arg != "detail":
                        continue
                    rendered = ast.unparse(keyword.value)
                    if "str(" in rendered or isinstance(keyword.value, ast.JoinedStr):
                        unsafe.append((node.lineno, rendered))
        assert not unsafe, f"{relative} exposes exception text: {unsafe}"


def test_provider_and_memory_logs_do_not_emit_sensitive_runtime_values():
    forbidden_names = {"audio_path", "file_path", "tmp_path", "uid"}
    for relative in DIAGNOSTIC_MODULES:
        source = (ROOT / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        unsafe = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "logger"
            ):
                continue
            names = {
                child.id for child in ast.walk(node) if isinstance(child, ast.Name)
            }
            attributes = {
                child.attr
                for child in ast.walk(node)
                if isinstance(child, ast.Attribute)
            }
            has_traceback = any(
                keyword.arg == "exc_info"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in node.keywords
            )
            if names & forbidden_names or "filename" in attributes or has_traceback:
                unsafe.append((node.lineno, ast.unparse(node)))
        assert not unsafe, f"{relative} logs sensitive runtime values: {unsafe}"


def test_provider_and_memory_exception_logs_only_emit_error_type():
    for relative in DIAGNOSTIC_MODULES:
        source = (ROOT / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        unsafe = []
        for handler in (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler) and node.name
        ):
            for node in ast.walk(handler):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "logger"
                ):
                    continue
                rendered = ast.unparse(node)
                safe_type = f"type({handler.name}).__name__"
                if handler.name in rendered.replace(safe_type, ""):
                    unsafe.append((node.lineno, rendered))
        assert not unsafe, f"{relative} logs exception details: {unsafe}"
