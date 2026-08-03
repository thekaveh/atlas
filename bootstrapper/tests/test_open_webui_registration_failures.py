from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "services/open-webui/init/scripts"


def _load_script(monkeypatch, filename: str):
    monkeypatch.setenv("WEBUI_URL", "http://open-webui")
    monkeypatch.setenv("WEBUI_SECRET_KEY", "secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://atlas")
    monkeypatch.setitem(
        sys.modules,
        "jwt",
        types.SimpleNamespace(encode=lambda *_args, **_kwargs: "token"),
    )

    class DatabaseError(Exception):
        pass

    monkeypatch.setitem(
        sys.modules,
        "psycopg2",
        types.SimpleNamespace(Error=DatabaseError, connect=lambda *_args, **_kwargs: None),
    )
    module_name = f"test_{filename.replace('-', '_').replace('.', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("filename", "register_name", "directory_name", "exists_name", "create_name"),
    [
        ("register-tools.py", "register_tools", "TOOLS_DIR", "tool_exists", "create_tool"),
        (
            "register-functions.py",
            "register_functions",
            "FUNCTIONS_DIR",
            "function_exists",
            "create_function",
        ),
    ],
)
def test_registration_attempts_every_artifact_then_fails_nonzero(
    tmp_path,
    monkeypatch,
    filename,
    register_name,
    directory_name,
    exists_name,
    create_name,
):
    module = _load_script(monkeypatch, filename)
    (tmp_path / "bad.py").write_text('"""title: Bad"""\n', encoding="utf-8")
    (tmp_path / "good.py").write_text('"""title: Good"""\n', encoding="utf-8")
    monkeypatch.setattr(module, directory_name, str(tmp_path))
    monkeypatch.setattr(module, exists_name, lambda *_args: False)

    attempted: list[str] = []

    def create(artifact_id, *_args, **_kwargs):
        attempted.append(artifact_id)
        if artifact_id == "bad":
            raise RuntimeError("provider rejected artifact")

    monkeypatch.setattr(module, create_name, create)

    with pytest.raises(RuntimeError, match="bad"):
        getattr(module, register_name)("token")

    assert attempted == ["bad", "good"]
