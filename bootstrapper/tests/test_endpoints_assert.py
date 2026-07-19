"""`./start.sh endpoints assert` — the consumer contract drift gate (#723).

Consumers pin Atlas as a submodule and read specific ATLAS_* export fields. The
assert command lets them fail loudly in CI when a field they depend on is
renamed/removed, instead of silently degrading.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from click.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))


class _CP:
    def parse_env_file(self):
        # A minimal configured stack; base services always emit a SOURCE field.
        return {
            "BASE_PORT": "63000",
            "LITELLM_SOURCE": "container",
            "MINIO_SOURCE": "container",
        }


class _Starter:
    def __init__(self):
        self.config_parser = _CP()


def _patch(monkeypatch):
    import start as start_module

    monkeypatch.setattr(start_module, "AtlasStarter", _Starter)
    return start_module


def test_endpoints_assert_lists_field_names_json(monkeypatch):
    start_module = _patch(monkeypatch)
    res = CliRunner().invoke(start_module.main, ["endpoints", "assert", "--format", "json"])
    assert res.exit_code == 0, res.output
    names = json.loads(res.output)
    assert isinstance(names, list) and names
    assert any(n.endswith("_SOURCE") for n in names)


def test_endpoints_assert_require_present_exits_zero(monkeypatch):
    start_module = _patch(monkeypatch)
    names = json.loads(
        CliRunner().invoke(start_module.main, ["endpoints", "assert", "--format", "json"]).output
    )
    # require the first two real contract fields -> all present
    res = CliRunner().invoke(
        start_module.main, ["endpoints", "assert", "--require", ",".join(names[:2])]
    )
    assert res.exit_code == 0, res.output
    assert "all 2 required field(s) present" in res.output


def test_endpoints_assert_require_missing_exits_nonzero(monkeypatch):
    start_module = _patch(monkeypatch)
    res = CliRunner().invoke(
        start_module.main,
        ["endpoints", "assert", "--require", "ATLAS_NONEXISTENT_FIELD"],
    )
    assert res.exit_code != 0
    assert "missing required export field" in res.output


def test_endpoints_assert_require_accepts_space_separated(monkeypatch):
    start_module = _patch(monkeypatch)
    names = json.loads(
        CliRunner().invoke(start_module.main, ["endpoints", "assert", "--format", "json"]).output
    )
    res = CliRunner().invoke(
        start_module.main, ["endpoints", "assert", "--require", f"{names[0]} {names[1]}"]
    )
    assert res.exit_code == 0, res.output
