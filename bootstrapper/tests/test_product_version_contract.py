from __future__ import annotations

import ast
from pathlib import Path
import tomllib

import yaml


ROOT = Path(__file__).resolve().parents[2]


def _bootstrapper_dunder_version() -> str:
    module = ast.parse((ROOT / "bootstrapper/__init__.py").read_text(encoding="utf-8"))
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
            value = ast.literal_eval(node.value)
            assert isinstance(value, str)
            return value
    raise AssertionError("bootstrapper.__version__ is not declared")


def test_product_version_sources_match() -> None:
    pyproject = tomllib.loads(
        (ROOT / "bootstrapper/pyproject.toml").read_text(encoding="utf-8")
    )
    manifest = yaml.safe_load(
        (ROOT / "services/globals/service.yml").read_text(encoding="utf-8")
    )
    globals_env = {
        entry["name"]: str(entry.get("default", "")) for entry in manifest["env"]
    }
    env_example = dict(
        line.split("=", 1)
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    )

    expected = pyproject["project"]["version"]
    assert _bootstrapper_dunder_version() == expected
    assert globals_env["BRAND_VERSION"] == expected
    assert env_example["BRAND_VERSION"] == expected


def test_release_documentation_records_current_tag() -> None:
    version = tomllib.loads(
        (ROOT / "bootstrapper/pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    releasing = (ROOT / "docs/deployment/releasing.md").read_text(encoding="utf-8")

    assert f"`v{version}`" in releasing
    assert "first tagged checkpoint" in releasing
