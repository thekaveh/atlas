from __future__ import annotations

import json
import sys
from pathlib import Path

from click.testing import CliRunner


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))
REUSING_ATLAS = REPO_ROOT / "docs" / "deployment" / "reusing-atlas.md"
SITE_OPERATIONS = REPO_ROOT / "docs" / "site" / "operations.md"
WIKI_OPERATIONS = REPO_ROOT / "docs" / "wiki" / "Operations.md"


def _write_base_env(tmp_path: Path, extra: str = "") -> None:
    (tmp_path / ".env.example").write_text(
        "PROJECT_NAME=atlas\n"
        "COMFYUI_ENDPOINT=http://comfyui:18188\n"
        "LITELLM_URL=http://litellm:4000\n"
        "MINIO_ENDPOINT=http://minio:9000\n"
        f"{extra}",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "PROJECT_NAME=atlas\n"
        "COMFYUI_ENDPOINT=http://host.docker.internal:8188\n"
        "LITELLM_URL=http://litellm:4000\n"
        "MINIO_ENDPOINT=http://minio:9000\n"
        f"{extra}",
        encoding="utf-8",
    )


def _patch_starter_paths(monkeypatch, tmp_path: Path) -> None:
    import start as start_module

    original_init = start_module.AtlasStarter.__init__

    def init_with_tmp_root(self):
        original_init(self)
        self.config_parser.root_dir = tmp_path
        self.config_parser.env_file_path = tmp_path / ".env"
        self.config_parser.env_example_path = tmp_path / ".env.example"
        self.docker_manager.root_dir = tmp_path
        self.docker_manager.config_parser.root_dir = tmp_path
        self.docker_manager.config_parser.env_file_path = tmp_path / ".env"
        self.docker_manager.config_parser.env_example_path = tmp_path / ".env.example"

    monkeypatch.setattr(start_module.AtlasStarter, "__init__", init_with_tmp_root)


def test_doctor_json_skips_compose_when_docker_unavailable(tmp_path, monkeypatch) -> None:
    import start as start_module

    _write_base_env(tmp_path)
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (_ for _ in ()).throw(RuntimeError("Docker is not installed")),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    checks = {entry["id"]: entry for entry in payload["checks"]}
    assert checks["compose"]["status"] == "skipped"
    assert checks["overlay-env"]["status"] == "pass"
    assert checks["endpoints"]["status"] == "pass"
    assert payload["ok"] is True


def test_doctor_fails_and_names_unresolved_overlay_variable(tmp_path, monkeypatch) -> None:
    import start as start_module

    _write_base_env(tmp_path, extra="KNOWN_IMAGE=alpine:3.20\n")
    overlay = tmp_path / "services" / "_user" / "demo"
    overlay.mkdir(parents=True)
    (overlay / "compose.yml").write_text(
        "services:\n"
        "  demo:\n"
        "    image: ${MISSING_IMAGE}\n"
        "  ok:\n"
        "    image: ${KNOWN_IMAGE:-alpine:3.20}\n",
        encoding="utf-8",
    )
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor"])

    assert result.exit_code == 1
    assert "overlay-env" in result.output
    assert "MISSING_IMAGE" in result.output
    assert "services/_user/demo/compose.yml" in result.output


def test_doctor_text_reports_endpoint_resolution(tmp_path, monkeypatch) -> None:
    import start as start_module

    _write_base_env(tmp_path)
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "Consumer Doctor" in result.output
    assert "COMFYUI_ENDPOINT=http://host.docker.internal:8188" in result.output
    assert "compose" in result.output
    assert "overlay-env" in result.output


def test_doctor_accepts_common_plugin_requirement_markers(tmp_path, monkeypatch) -> None:
    import start as start_module

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "requirements.txt").write_text(
        'fastapi>=0.115; python_version >= "3.10"\n',
        encoding="utf-8",
    )
    _write_base_env(tmp_path, extra=f"BACKEND_PLUGINS_DIR={plugins_dir}\n")
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])

    assert result.exit_code == 0, result.output
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    assert checks["plugins"]["status"] == "pass"
    assert checks["plugins"]["details"]["requirement_entries"] == 1


def _write_plugin(plugins_dir: Path, dirname: str, manifest_body: str) -> None:
    pkg = plugins_dir / dirname
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("router = None\n", encoding="utf-8")
    (pkg / "plugin.yml").write_text(manifest_body, encoding="utf-8")


def test_doctor_plugin_manifests_pass_when_valid(tmp_path, monkeypatch) -> None:
    import start as start_module

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write_plugin(
        plugins_dir, "tableau",
        "plugin_manifest_version: 1\nname: tableau\nroute_prefix: /tableau\nauth: key-auth\n",
    )
    _write_base_env(tmp_path, extra=f"BACKEND_PLUGINS_DIR={plugins_dir}\n")
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    assert checks["plugin-manifests"]["status"] == "pass"
    assert "tableau" in checks["plugin-manifests"]["details"]["plugins"]


def test_doctor_plugin_manifests_warns_on_missing_required_env(tmp_path, monkeypatch) -> None:
    import start as start_module

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write_plugin(
        plugins_dir, "tableau",
        "plugin_manifest_version: 1\nname: tableau\nroute_prefix: /tableau\n"
        "env:\n  - name: LITELLM_MASTER_KEY\n    required: true\n    secret: true\n",
    )
    _write_base_env(tmp_path, extra=f"BACKEND_PLUGINS_DIR={plugins_dir}\n")
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    # warn does not fail the doctor (a missing plugin env is advisory)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    checks = {entry["id"]: entry for entry in payload["checks"]}
    assert checks["plugin-manifests"]["status"] == "warn"
    warnings = " ".join(checks["plugin-manifests"]["details"]["warnings"])
    assert "LITELLM_MASTER_KEY" in warnings and "required" in warnings
    assert payload["ok"] is True


def test_doctor_plugin_manifests_reports_malformed(tmp_path, monkeypatch) -> None:
    import start as start_module

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write_plugin(plugins_dir, "broken", "plugin_manifest_version: 2\nname: broken\nroute_prefix: /broken\n")
    _write_base_env(tmp_path, extra=f"BACKEND_PLUGINS_DIR={plugins_dir}\n")
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    assert checks["plugin-manifests"]["status"] == "warn"
    assert "invalid plugin.yml" in " ".join(checks["plugin-manifests"]["details"]["warnings"])


def test_consumer_doctor_docs_are_published_on_all_surfaces() -> None:
    reusing = REUSING_ATLAS.read_text(encoding="utf-8")
    assert "./start.sh doctor" in reusing
    assert "./start.sh doctor --format json" in reusing
    assert "consumer CI" in reusing

    for path in (SITE_OPERATIONS, WIKI_OPERATIONS):
        text = path.read_text(encoding="utf-8")
        assert "./start.sh doctor" in text
        assert "--format json" in text
        assert "overlay" in text.lower()
