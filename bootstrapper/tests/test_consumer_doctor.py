from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from tests.three_surface_test_utils import surface_text


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))
REUSING_ATLAS = REPO_ROOT / "docs" / "operations" / "reusing-atlas.md"


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


def test_doctor_fails_on_typod_consumer_manifest_top_level_key(tmp_path, monkeypatch) -> None:
    """#649 AC#2: the consumer `doctor` must FAIL (not pass) when a manifest
    has a typo'd top-level key, naming the offending key."""
    import start as start_module

    _write_base_env(tmp_path)
    manifest = tmp_path / "atlas.consumer.yml"
    manifest.write_text(
        # `compose_overlay` — the ticket's typo (missing trailing 's').
        "name: showcase\ncompose_overlay:\n  - ./compose/overlay.yml\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])

    payload = json.loads(result.output)
    checks = {entry["id"]: entry for entry in payload["checks"]}
    assert checks["consumer-manifests"]["status"] == "fail", result.output
    assert "compose_overlay" in checks["consumer-manifests"]["message"]
    assert payload["ok"] is False


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


# ─── Managed Apple-Silicon/Metal ComfyUI preflight (#335) ───────────────

def _stub_compose_ok(monkeypatch) -> None:
    import start as start_module

    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )


def test_doctor_comfyui_mps_skipped_when_source_not_selected(tmp_path, monkeypatch) -> None:
    import start as start_module

    _write_base_env(tmp_path)  # COMFYUI_SOURCE unset → default container path
    _patch_starter_paths(monkeypatch, tmp_path)
    _stub_compose_ok(monkeypatch)

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])

    assert result.exit_code == 0, result.output
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    assert checks["comfyui-mps"]["status"] == "skipped"


def test_doctor_comfyui_mps_fails_on_unsupported_host(tmp_path, monkeypatch) -> None:
    import start as start_module
    from services import comfyui_mps_manager as mps

    _write_base_env(
        tmp_path,
        extra=(
            "COMFYUI_SOURCE=managed-localhost-mps\n"
            f"COMFYUI_MPS_STATE_DIR={tmp_path}/mps-state\n"
        ),
    )
    _patch_starter_paths(monkeypatch, tmp_path)
    _stub_compose_ok(monkeypatch)
    # Force a non-Apple host regardless of where the suite runs.
    monkeypatch.setattr(mps.platform, "system", lambda: "Linux")
    monkeypatch.setattr(mps.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(mps.shutil, "which", lambda name: f"/usr/bin/{name}")

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])

    assert result.exit_code == 1  # a fail check flips the overall exit code
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    assert checks["comfyui-mps"]["status"] == "fail"
    assert "macOS" in checks["comfyui-mps"]["message"] or "Apple" in checks["comfyui-mps"]["message"]


def test_doctor_comfyui_mps_passes_on_apple_silicon(tmp_path, monkeypatch) -> None:
    import start as start_module
    from services import comfyui_mps_manager as mps

    _write_base_env(
        tmp_path,
        extra=(
            "COMFYUI_SOURCE=managed-localhost-mps\n"
            f"COMFYUI_MPS_STATE_DIR={tmp_path}/mps-state\n"
        ),
    )
    _patch_starter_paths(monkeypatch, tmp_path)
    _stub_compose_ok(monkeypatch)
    monkeypatch.setattr(mps.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(mps.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(mps.shutil, "which", lambda name: f"/usr/bin/{name}")
    # Patch the probe method (not subprocess.run) so the sibling
    # submodule-cleanliness git-status check is unaffected.
    monkeypatch.setattr(mps.ComfyUiMpsManager, "_unified_memory_gb", lambda self: 64)

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])

    assert result.exit_code == 0, result.output
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    assert checks["comfyui-mps"]["status"] == "pass"
    assert checks["comfyui-mps"]["details"]["running"] is False


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


def _write_consumer_manifest(tmp_path: Path, name: str, body: str) -> Path:
    import textwrap

    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    manifest = d / "atlas.consumer.yml"
    manifest.write_text(f"name: {name}\n" + textwrap.dedent(body), encoding="utf-8")
    return manifest


def test_doctor_litellm_models_pass_when_route_matches_plugin(tmp_path, monkeypatch) -> None:
    import start as start_module

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write_plugin(
        plugins_dir, "graphrag",
        "plugin_manifest_version: 1\nname: graphrag\nroute_prefix: /graph-rag\n",
    )
    manifest = _write_consumer_manifest(
        tmp_path, "rag-showcase",
        """
        litellm_models:
          version: 1
          models:
            - name: graph-rag
              api_base: "${ATLAS_BACKEND_INTERNAL}/graph-rag/v1"
              api_key_var: RAG_KEY
        """,
    )
    _write_base_env(tmp_path, extra=f"BACKEND_PLUGINS_DIR={plugins_dir}\n")
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    assert checks["litellm-models"]["status"] == "pass"
    assert "graph-rag" in checks["litellm-models"]["details"]["models"]
    assert "rag-showcase" in checks["litellm-models"]["details"]["owners"]


def test_doctor_litellm_models_warns_on_unmatched_route(tmp_path, monkeypatch) -> None:
    import start as start_module

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    # Plugin serves /tableau, but the model points at /graph-rag → warn.
    _write_plugin(
        plugins_dir, "tableau",
        "plugin_manifest_version: 1\nname: tableau\nroute_prefix: /tableau\n",
    )
    manifest = _write_consumer_manifest(
        tmp_path, "rag-showcase",
        """
        litellm_models:
          version: 1
          models:
            - name: graph-rag
              api_base: "${ATLAS_BACKEND_INTERNAL}/graph-rag/v1"
        """,
    )
    _write_base_env(tmp_path, extra=f"BACKEND_PLUGINS_DIR={plugins_dir}\n")
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    # warn is advisory — doctor stays green.
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    checks = {entry["id"]: entry for entry in payload["checks"]}
    assert checks["litellm-models"]["status"] == "warn"
    warnings = " ".join(checks["litellm-models"]["details"]["warnings"])
    assert "/graph-rag" in warnings and "no declared plugin route_prefix" in warnings
    assert payload["ok"] is True


def test_doctor_litellm_models_no_warn_on_builtin_route(tmp_path, monkeypatch) -> None:
    import start as start_module

    # A plugin exists (so the cross-check is active), but the model points at a
    # BUILT-IN backend route (/research is a reserved route prefix), which is a
    # legitimate target — the doctor must not cry wolf.
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write_plugin(
        plugins_dir, "tableau",
        "plugin_manifest_version: 1\nname: tableau\nroute_prefix: /tableau\n",
    )
    manifest = _write_consumer_manifest(
        tmp_path, "research-consumer",
        """
        litellm_models:
          version: 1
          models:
            - name: deep-research
              api_base: "${ATLAS_BACKEND_INTERNAL}/research/v1"
        """,
    )
    _write_base_env(tmp_path, extra=f"BACKEND_PLUGINS_DIR={plugins_dir}\n")
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    assert checks["litellm-models"]["status"] == "pass"


def test_doctor_litellm_models_fails_on_invalid_api_base(tmp_path, monkeypatch) -> None:
    import start as start_module

    manifest = _write_consumer_manifest(
        tmp_path, "ssrf",
        """
        litellm_models:
          version: 1
          models:
            - name: exfil
              api_base: "http://evil.example.com/v1"
        """,
    )
    _write_base_env(tmp_path)
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    assert checks["litellm-models"]["status"] == "fail"
    assert "not an approved Atlas endpoint" in checks["litellm-models"]["message"]


def test_doctor_litellm_models_pass_when_none(tmp_path, monkeypatch) -> None:
    import start as start_module

    _write_base_env(tmp_path)
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    assert checks["litellm-models"]["status"] == "pass"
    assert "No consumer LiteLLM models" in checks["litellm-models"]["message"]


def _write_n8n_consumer(tmp_path: Path, name: str, body: str, workflow: str) -> Path:
    import json
    import textwrap

    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "wf.json").write_text(
        json.dumps({"name": "WF", "active": True, "nodes": [], "connections": {}}),
        encoding="utf-8",
    )
    manifest = d / "atlas.consumer.yml"
    manifest.write_text(f"name: {name}\n" + textwrap.dedent(body), encoding="utf-8")
    return manifest


def test_doctor_n8n_workflows_pass_when_valid(tmp_path, monkeypatch) -> None:
    import start as start_module

    manifest = _write_n8n_consumer(
        tmp_path, "rag-showcase",
        """
        n8n_workflows:
          version: 1
          workflows:
            - id: adaptive-rag
              path: ./wf.json
              active: "false"
        """,
        "wf.json",
    )
    _write_base_env(tmp_path, extra="N8N_API_KEY=k\n")
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    assert checks["n8n-workflows"]["status"] == "pass"
    assert "adaptive-rag" in checks["n8n-workflows"]["details"]["workflows"]


def test_doctor_n8n_workflows_warns_without_api_key(tmp_path, monkeypatch) -> None:
    import start as start_module

    manifest = _write_n8n_consumer(
        tmp_path, "rag-showcase",
        """
        n8n_workflows:
          version: 1
          workflows:
            - id: adaptive-rag
              path: ./wf.json
              active: "true"
              required_webhooks:
                - path: /webhook/adaptive-rag
                  method: GET
        """,
        "wf.json",
    )
    # No N8N_API_KEY → active workflow + webhook → advisory warn.
    _write_base_env(tmp_path)
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    checks = {entry["id"]: entry for entry in payload["checks"]}
    assert checks["n8n-workflows"]["status"] == "warn"
    assert "N8N_API_KEY" in " ".join(checks["n8n-workflows"]["details"]["warnings"])
    assert payload["ok"] is True


def test_doctor_n8n_workflows_no_warn_for_fromjson_inactive_file(tmp_path, monkeypatch) -> None:
    # Regression (#412 F9): a fromJson workflow whose FILE is inactive is not a
    # live-activation case, so an unset N8N_API_KEY must NOT trigger the warning.
    import start as start_module

    d = tmp_path / "rag-showcase"
    d.mkdir(parents=True)
    (d / "wf.json").write_text(
        json.dumps({"name": "WF", "active": False, "nodes": [], "connections": {}}),
        encoding="utf-8",
    )
    manifest = d / "atlas.consumer.yml"
    manifest.write_text(
        "name: rag-showcase\n"
        "n8n_workflows:\n  version: 1\n  workflows:\n"
        "    - id: adaptive-rag\n      path: ./wf.json\n      active: fromJson\n"
        "      required_webhooks:\n        - path: /webhook/adaptive-rag\n          method: GET\n",
        encoding="utf-8",
    )
    _write_base_env(tmp_path)  # no N8N_API_KEY
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    assert checks["n8n-workflows"]["status"] == "pass"


def test_doctor_n8n_workflows_fails_on_malformed(tmp_path, monkeypatch) -> None:
    import start as start_module

    d = tmp_path / "broken"
    d.mkdir(parents=True)
    (d / "wf.json").write_text("{not json", encoding="utf-8")
    manifest = d / "atlas.consumer.yml"
    manifest.write_text(
        "name: broken\nn8n_workflows:\n  version: 1\n  workflows:\n"
        "    - id: wf\n      path: ./wf.json\n",
        encoding="utf-8",
    )
    _write_base_env(tmp_path)
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    assert checks["n8n-workflows"]["status"] == "fail"
    assert "not valid JSON" in checks["n8n-workflows"]["message"]


def test_doctor_n8n_workflows_pass_when_none(tmp_path, monkeypatch) -> None:
    import start as start_module

    _write_base_env(tmp_path)
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    assert checks["n8n-workflows"]["status"] == "pass"
    assert "No consumer n8n workflows" in checks["n8n-workflows"]["message"]


def _write_rag_consumer(tmp_path: Path, name: str, on_unavailable: str) -> Path:
    import textwrap

    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    manifest = d / "atlas.consumer.yml"
    manifest.write_text(
        f"name: {name}\n"
        + textwrap.dedent(
            f"""
            rag_ingestion_profiles:
              version: 1
              profiles:
                - name: showcase-default
                  corpus: {{source: mount, path: raw}}
                  vector_targets:
                    - {{backend: weaviate, collection_prefix: RagShowcase, on_unavailable: {on_unavailable}}}
            """
        ),
        encoding="utf-8",
    )
    return manifest


def test_doctor_rag_ingestion_pass_when_target_skips(tmp_path, monkeypatch) -> None:
    import start as start_module

    manifest = _write_rag_consumer(tmp_path, "rag-showcase", "skip")
    _write_base_env(tmp_path)  # no WEAVIATE_URL, but on_unavailable=skip → no warn
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    assert checks["rag-ingestion-profiles"]["status"] == "pass"
    assert "showcase-default" in checks["rag-ingestion-profiles"]["details"]["profiles"]


def test_doctor_rag_ingestion_warns_when_fail_target_disabled(tmp_path, monkeypatch) -> None:
    import start as start_module

    manifest = _write_rag_consumer(tmp_path, "rag-showcase", "fail")
    _write_base_env(tmp_path)  # WEAVIATE_SOURCE unset (disabled) + on_unavailable=fail → warn
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    checks = {entry["id"]: entry for entry in payload["checks"]}
    assert checks["rag-ingestion-profiles"]["status"] == "warn"
    assert "WEAVIATE_SOURCE" in " ".join(checks["rag-ingestion-profiles"]["details"]["warnings"])
    assert payload["ok"] is True


def test_doctor_rag_ingestion_follows_the_source_not_the_start_written_endpoint(
    tmp_path, monkeypatch
) -> None:
    """A fresh .env has WEAVIATE_SOURCE=container and an empty WEAVIATE_URL,
    which only a start fills in; that warned before (#1391)."""
    import start as start_module

    manifest = _write_rag_consumer(tmp_path, "rag-showcase", "fail")
    _write_base_env(tmp_path, extra="WEAVIATE_SOURCE=container\nWEAVIATE_URL=\n")
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    assert checks["rag-ingestion-profiles"]["status"] == "pass"


def test_doctor_rag_ingestion_pass_when_none(tmp_path, monkeypatch) -> None:
    import start as start_module

    _write_base_env(tmp_path)
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    assert checks["rag-ingestion-profiles"]["status"] == "pass"
    assert "No consumer RAG ingestion profiles" in checks["rag-ingestion-profiles"]["message"]


def _write_lightrag_profile_consumer(tmp_path: Path, name: str, alias: str = "") -> Path:
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    manifest = d / "atlas.consumer.yml"
    lines = [
        f"name: {name}",
        "lightrag_query_profiles:",
        "  version: 1",
        "  profiles:",
        "    - name: graph-hybrid-default",
        "      mode: hybrid",
        "      top_k: 10",
    ]
    if alias:
        lines.append(f"      litellm_alias: {alias}")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


def test_doctor_lightrag_profiles_pass_when_endpoint_set(tmp_path, monkeypatch) -> None:
    import start as start_module

    manifest = _write_lightrag_profile_consumer(tmp_path, "rag-showcase")
    _write_base_env(tmp_path, extra="LIGHTRAG_SOURCE=container\nLIGHTRAG_ENDPOINT=http://lightrag:9621\n")
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    assert checks["lightrag-query-profiles"]["status"] == "pass"
    assert "graph-hybrid-default" in checks["lightrag-query-profiles"]["details"]["profiles"]


def test_doctor_lightrag_profiles_warn_when_endpoint_unset(tmp_path, monkeypatch) -> None:
    import start as start_module

    manifest = _write_lightrag_profile_consumer(tmp_path, "rag-showcase")
    _write_base_env(tmp_path)  # no LIGHTRAG_SOURCE (disabled) → warn
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    checks = {entry["id"]: entry for entry in payload["checks"]}
    assert checks["lightrag-query-profiles"]["status"] == "warn"
    assert "LIGHTRAG_SOURCE" in checks["lightrag-query-profiles"]["message"]
    assert payload["ok"] is True  # warn does not fail the run


def test_doctor_lightrag_profiles_reports_alias(tmp_path, monkeypatch) -> None:
    import start as start_module

    manifest = _write_lightrag_profile_consumer(
        tmp_path, "rag-showcase", alias="graph-rag-hybrid"
    )
    _write_base_env(tmp_path, extra="LIGHTRAG_SOURCE=container\nLIGHTRAG_ENDPOINT=http://lightrag:9621\n")
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    assert checks["lightrag-query-profiles"]["details"]["aliases"] == ["graph-rag-hybrid"]


def test_doctor_lightrag_profiles_pass_when_none(tmp_path, monkeypatch) -> None:
    import start as start_module

    _write_base_env(tmp_path)
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    assert checks["lightrag-query-profiles"]["status"] == "pass"
    assert "No consumer LightRAG query profiles" in checks["lightrag-query-profiles"]["message"]


def test_consumer_doctor_docs_are_published_on_all_surfaces() -> None:
    reusing = REUSING_ATLAS.read_text(encoding="utf-8")
    assert "./start.sh doctor" in reusing
    assert "./start.sh doctor --format json" in reusing
    assert "consumer CI" in reusing

    for text in (
        surface_text("docs/operations/index.md", "site"),
        surface_text("docs/operations/index.md", "wiki"),
    ):
        assert "./start.sh doctor" in text
        assert "--format json" in text
        assert "overlay" in text.lower()


# ─── LightRAG rerank adapter doctor check (#415) ────────────────────


def test_doctor_rerank_adapter_pass_when_disabled(tmp_path, monkeypatch) -> None:
    import start as start_module

    _write_base_env(tmp_path)  # no LIGHTRAG_RERANK_ADAPTER_ENABLED → default off
    _patch_starter_paths(monkeypatch, tmp_path)
    _stub_compose_ok(monkeypatch)

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    assert checks["lightrag-rerank-adapter"]["status"] == "pass"
    assert "disabled" in checks["lightrag-rerank-adapter"]["message"]


def test_doctor_rerank_adapter_warns_when_enabled_but_tei_disabled(tmp_path, monkeypatch) -> None:
    import start as start_module

    _write_base_env(
        tmp_path,
        extra=(
            "LIGHTRAG_RERANK_ADAPTER_ENABLED=true\n"
            "LIGHTRAG_SOURCE=container\n"
            "TEI_RERANKER_SOURCE=disabled\n"
            "LIGHTRAG_RERANK_ADAPTER_TOKEN=sk-lightrag-rerank-x\n"
        ),
    )
    _patch_starter_paths(monkeypatch, tmp_path)
    _stub_compose_ok(monkeypatch)

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    assert checks["lightrag-rerank-adapter"]["status"] == "warn"
    assert "TEI_RERANKER_SOURCE" in checks["lightrag-rerank-adapter"]["message"]


def test_doctor_rerank_adapter_pass_when_fully_wired(tmp_path, monkeypatch) -> None:
    import start as start_module

    _write_base_env(
        tmp_path,
        extra=(
            "LIGHTRAG_RERANK_ADAPTER_ENABLED=true\n"
            "LIGHTRAG_SOURCE=container\n"
            "TEI_RERANKER_SOURCE=container-cpu\n"
            "LIGHTRAG_RERANK_ADAPTER_TOKEN=sk-lightrag-rerank-x\n"
        ),
    )
    _patch_starter_paths(monkeypatch, tmp_path)
    _stub_compose_ok(monkeypatch)

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    assert checks["lightrag-rerank-adapter"]["status"] == "pass"
    assert "wired to TEI" in checks["lightrag-rerank-adapter"]["message"]


def test_doctor_accepts_consumer_env_file_enabling_rerank_adapter(tmp_path, monkeypatch) -> None:
    """#654: a consumer that enables the rerank adapter in its own env.file and
    declares enable_rerank in a LightRAG query profile validates on a fresh
    checkout — the base .env has no LIGHTRAG_RERANK_ADAPTER_ENABLED, and the
    consumer overlay flips the gate before profile validation."""
    import start as start_module

    _write_base_env(tmp_path)  # base .env: no adapter flag (fresh checkout)
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    (consumer / "adapter.env").write_text(
        "LIGHTRAG_RERANK_ADAPTER_ENABLED=true\n", encoding="utf-8"
    )
    manifest = consumer / "atlas.consumer.yml"
    manifest.write_text(
        "name: showcase\n"
        "env:\n  file: ./adapter.env\n"
        "lightrag_query_profiles:\n  version: 1\n  profiles:\n"
        "    - {name: graph-rag-rerank, mode: hybrid, enable_rerank: true}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])

    payload = json.loads(result.output)
    checks = {entry["id"]: entry for entry in payload["checks"]}
    assert checks["consumer-manifests"]["status"] == "pass", result.output
    assert "showcase" in checks["consumer-manifests"]["details"]["consumers"]


# ─── LightRAG role transport doctor check (#658) ────────────────────

# Not secret-shaped on purpose: the values only have to be recognisable.
_LIGHTRAG_KEY_CANARIES = ("litellm-master-canary", "role-key-canary")


def _lightrag_role_transport(tmp_path, monkeypatch, extra: str) -> tuple[dict, str]:
    import start as start_module

    _write_base_env(tmp_path, extra=f"LITELLM_MASTER_KEY={_LIGHTRAG_KEY_CANARIES[0]}\n{extra}")
    _patch_starter_paths(monkeypatch, tmp_path)
    _stub_compose_ok(monkeypatch)

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    checks = {entry["id"]: entry for entry in json.loads(result.output)["checks"]}
    return checks["lightrag-role-transport"], result.output


def test_doctor_lightrag_role_transport_names_transport_and_request_defaults(
    tmp_path, monkeypatch,
) -> None:
    """#658 AC6: with a native-Ollama base, the doctor names each role's
    effective transport and request defaults (KEYWORD and QUERY stay on
    LiteLLM for qwen3.8's {think: false}; EXTRACT stays native) and the
    output carries no key value."""
    check, output = _lightrag_role_transport(tmp_path, monkeypatch, (
        "LIGHTRAG_SOURCE=container\n"
        "LIGHTRAG_LLM_BINDING=ollama\n"
        "LIGHTRAG_LLM_BINDING_HOST=http://host.docker.internal:11434\n"
        "LIGHTRAG_LLM_MODEL=qwen3.8:latest\n"
        "LIGHTRAG_EXTRACT_LLM_MODEL=mistral-small3.2:24b\n"
    ))
    roles = check["details"]["roles"]

    assert (
        check["status"],
        {role: (roles[role]["transport"], roles[role]["request_defaults"]) for role in roles},
        'KEYWORD role: openai at http://litellm:4000 (litellm)' in check["message"],
        any(canary in output for canary in _LIGHTRAG_KEY_CANARIES),
    ) == (
        "pass",
        {"EXTRACT": ("native", {}), "KEYWORD": ("litellm", {"think": False}),
         "QUERY": ("litellm", {"think": False})},
        True,
        False,
    )


def test_doctor_lightrag_role_transport_warns_when_a_native_role_loses_defaults(
    tmp_path, monkeypatch,
) -> None:
    """#658 AC5/AC6: a role pinned to native Ollama by its own settings keeps
    them, so it cannot receive qwen3.8's {think: false}; the doctor warns and
    names the lost defaults, without echoing the role's key."""
    check, output = _lightrag_role_transport(tmp_path, monkeypatch, (
        "LIGHTRAG_SOURCE=container\n"
        "LIGHTRAG_LLM_MODEL=qwen3.8:latest\n"
        "LIGHTRAG_KEYWORD_LLM_BINDING=ollama\n"
        "LIGHTRAG_KEYWORD_LLM_BINDING_HOST=http://host.docker.internal:11434\n"
        f"LIGHTRAG_KEYWORD_LLM_BINDING_API_KEY={_LIGHTRAG_KEY_CANARIES[1]}\n"
    ))
    keyword = check["details"]["roles"]["KEYWORD"]

    assert (
        check["status"], keyword["binding"], keyword["unsent_request_defaults"],
        check["details"]["roles"]["QUERY"]["transport"],
        any(canary in output for canary in _LIGHTRAG_KEY_CANARIES),
    ) == ("warn", "ollama", {"think": False}, "litellm", False)


def test_doctor_lightrag_role_transport_skipped_without_in_stack_lightrag(
    tmp_path, monkeypatch,
) -> None:
    check, _output = _lightrag_role_transport(tmp_path, monkeypatch, "LIGHTRAG_SOURCE=disabled\n")

    assert check["status"] == "skipped"


def test_doctor_base_port_warns_on_default_squat():
    """#717: the base-port doctor check warns when a consumer squats the
    default BASE_PORT (project_name isolates Docker resources, not host ports)."""
    import start as start_module

    class _CP:
        def __init__(self, env, project):
            self._env = env
            self._project = project

        def parse_env_file(self):
            return dict(self._env)

        def get_project_name(self):
            return self._project

    from core.port_manager import PortManager

    class _Starter:
        def __init__(self, env, project):
            self.config_parser = _CP(env, project)
            self.port_manager = PortManager()

    # consumer squatting the default port -> warn
    r = start_module._doctor_check_base_port(_Starter({"BASE_PORT": "63000"}, "tableau"))
    assert r["status"] == "warn"
    assert "63000" in r["message"]
    assert r["details"]["project_name"] == "tableau"

    # bare atlas on the default port -> pass (expected)
    r = start_module._doctor_check_base_port(_Starter({"BASE_PORT": "63000"}, "atlas"))
    assert r["status"] == "pass"

    # consumer moved to a distinct block -> pass
    r = start_module._doctor_check_base_port(_Starter({"BASE_PORT": "64000"}, "daydreams"))
    assert r["status"] == "pass"

    # missing BASE_PORT defaults to the Atlas default; consumer project -> warn
    r = start_module._doctor_check_base_port(_Starter({}, "rag-showcase"))
    assert r["status"] == "warn"


def test_doctor_unpullable_models_warns_on_localhost_sources(monkeypatch):
    """#718 (+#757): the ollama branch now presence-checks the host daemon —
    mocked here so the test is hermetic (CI has no daemon; a dev machine's
    real daemon would make the result environment-dependent)."""
    import start as start_module
    import services.ollama_localhost as ollama_localhost

    class _CP:
        def __init__(self, env):
            self._env = env

        def parse_env_file(self):
            return dict(self._env)

    class _Starter:
        def __init__(self, env):
            self.config_parser = _CP(env)

    # declared tag missing from a reachable host daemon -> warn + pull guidance
    monkeypatch.setattr(ollama_localhost, "list_host_tags", lambda base_url, **kw: set())
    r = start_module._doctor_check_unpullable_models(_Starter({
        "OLLAMA_CUSTOM_MODELS": "mistral-small3.2:24b",
        "LLM_PROVIDER_SOURCE": "ollama-localhost",
    }))
    assert r["status"] == "warn"
    assert "ollama pull mistral-small3.2:24b" in r["message"]

    # declared tag present on the host daemon -> pass (#757: warn → pass)
    monkeypatch.setattr(
        ollama_localhost,
        "list_host_tags",
        lambda base_url, **kw: {"mistral-small3.2:24b"},
    )
    r = start_module._doctor_check_unpullable_models(_Starter({
        "OLLAMA_CUSTOM_MODELS": "mistral-small3.2:24b",
        "LLM_PROVIDER_SOURCE": "ollama-localhost",
    }))
    assert r["status"] == "pass"

    # unreachable daemon -> warn pointing at `ollama serve` + next start
    monkeypatch.setattr(ollama_localhost, "list_host_tags", lambda base_url, **kw: None)
    r = start_module._doctor_check_unpullable_models(_Starter({
        "OLLAMA_CUSTOM_MODELS": "mistral-small3.2:24b",
        "LLM_PROVIDER_SOURCE": "ollama-localhost",
    }))
    assert r["status"] == "warn"
    assert "not reachable" in r["message"]
    monkeypatch.setattr(ollama_localhost, "list_host_tags", lambda base_url, **kw: set())

    # ollama models under a container source -> pass
    r = start_module._doctor_check_unpullable_models(_Starter({
        "OLLAMA_CUSTOM_MODELS": "mistral-small3.2:24b",
        "LLM_PROVIDER_SOURCE": "ollama-container-gpu",
    }))
    assert r["status"] == "pass"

    # comfyui models under managed-localhost-mps -> warn + MPS-path guidance
    r = start_module._doctor_check_unpullable_models(_Starter({
        "COMFYUI_USER_MODELS": "krea2-turbo-bf16",
        "COMFYUI_SOURCE": "managed-localhost-mps",
    }))
    assert r["status"] == "warn"
    assert "COMFYUI_MPS_MODELS_PATH" in r["message"]

    # comfyui models under a container source -> pass
    r = start_module._doctor_check_unpullable_models(_Starter({
        "COMFYUI_USER_MODELS": "krea2-turbo-bf16",
        "COMFYUI_SOURCE": "container-gpu",
    }))
    assert r["status"] == "pass"

    # both no-op conditions together -> one warn covering both
    r = start_module._doctor_check_unpullable_models(_Starter({
        "OLLAMA_CUSTOM_MODELS": "m1",
        "LLM_PROVIDER_SOURCE": "ollama-localhost",
        "COMFYUI_USER_MODELS": "c1",
        "COMFYUI_SOURCE": "managed-localhost-mps",
    }))
    assert r["status"] == "warn"
    assert len(r["details"]["warnings"]) == 2

    # sources host-run but nothing declared -> pass
    r = start_module._doctor_check_unpullable_models(_Starter({
        "LLM_PROVIDER_SOURCE": "ollama-localhost",
        "COMFYUI_SOURCE": "managed-localhost-mps",
    }))
    assert r["status"] == "pass"


# ─── ComfyUI custom-node doctor lint (#905) ────────────────────────────


def test_doctor_unpullable_models_warns_on_missing_custom_nodes(monkeypatch):
    """#905: under ``managed-localhost-mps``, a consumer-declared custom node
    whose ``dest/.git`` is absent surfaces a doctor warning naming the node +
    ``comfyui-mps provision-nodes``. The doctor resolves consumer-declared nodes
    via ``load_custom_nodes`` (from_consumer), independent of
    COMFYUI_USER_MODELS — a workflow-only node (no catalog models) is still
    linted. ``nodes_satisfied`` is stubbed (the real probe runs ``git
    rev-parse``, a subprocess) to keep the test hermetic."""
    import start as start_module
    from services import comfyui_mps_manager as mps
    from utils.comfyui_custom_nodes import ComfyUICustomNode

    node = ComfyUICustomNode(
        name="comfyui-krea2edit",
        repo="https://github.com/krea-ai/krea2-comfyui.git",
        ref="a" * 40,
        from_consumer=True,
    )
    monkeypatch.setattr(
        "utils.comfyui_custom_nodes.load_custom_nodes", lambda env: [node]
    )
    monkeypatch.setattr(
        mps.ComfyUiMpsManager,
        "nodes_satisfied",
        lambda self, nodes: (False, ["comfyui-krea2edit"]),
    )

    class _CP:
        def __init__(self, env):
            self._env = env

        def parse_env_file(self):
            return dict(self._env)

    class _Starter:
        def __init__(self, env):
            self.config_parser = _CP(env)

    r = start_module._doctor_check_unpullable_models(
        _Starter({"COMFYUI_SOURCE": "managed-localhost-mps"})
    )
    assert r["status"] == "warn"
    joined = " ".join(r["details"]["warnings"])
    assert "comfyui-krea2edit" in joined
    assert "comfyui-mps provision-nodes" in joined


def test_doctor_unpullable_models_skips_mps_unsafe_custom_nodes(monkeypatch, tmp_path):
    """#905: ``mps_unsafe`` custom nodes are excluded from ``nodes_satisfied``
    (the provisioner deliberately skips them — CUDA/x86-only wheels that
    cannot build on Apple Silicon), so the doctor lint must not flag them even
    when ``dest/.git`` is absent. Exercises the REAL ``nodes_satisfied``: the
    mps_unsafe skip happens before any git rev-parse probe, so no subprocess
    runs and tmp_path staying empty is sufficient."""
    import start as start_module
    from services import comfyui_mps_manager as mps

    mps_unsafe_node = {
        "name": "cuda-only-node",
        "repo": "example/cuda-only",
        "ref": "main",
        "install_requirements": ["onnxruntime-gpu"],
        "mps_unsafe": True,
    }
    monkeypatch.setattr(
        start_module,
        "_resolved_comfyui_custom_nodes",
        lambda env: [mps_unsafe_node],
    )

    # Direct contract on the real method: an mps_unsafe node is excluded →
    # (True, []) even though dest/.git does not exist under tmp_path.
    manager = mps.manager_from_env(
        {"COMFYUI_MPS_STATE_DIR": str(tmp_path / "mps")}
    )
    assert manager.nodes_satisfied([mps_unsafe_node]) == (True, [])

    class _CP:
        def __init__(self, env):
            self._env = env

        def parse_env_file(self):
            return dict(self._env)

    class _Starter:
        def __init__(self, env):
            self.config_parser = _CP(env)

    r = start_module._doctor_check_unpullable_models(_Starter({
        "COMFYUI_USER_MODELS": "krea2-turbo-bf16",
        "COMFYUI_SOURCE": "managed-localhost-mps",
        "COMFYUI_MPS_STATE_DIR": str(tmp_path / "mps"),
    }))
    # The node branch runs (COMFYUI_USER_MODELS set + managed-localhost-mps)
    # but produces NO node warning — the sole declared node is mps_unsafe and
    # is skipped by nodes_satisfied. (Any warning present comes from the
    # orthogonal model branch, e.g. COMFYUI_MPS_MODELS_PATH guidance, and
    # must never name the node or the provision-nodes command.)
    joined = " ".join(r["details"].get("warnings", []))
    assert "cuda-only-node" not in joined
    assert "provision-nodes" not in joined


# ─── Redacted support bundle (#1057) ────────────────────────────────

_CANARIES = {
    "env": "canary-env-value-6f1c9a2b",
    "url": "canary-url-cred-83b2d4e1",
    "bearer": "canary-bearer-tok-5d7e0c93",
    "pem": "canary-pem-body-1a2b3c4d",
    "json": "canary-json-pass-9e8f7a6b",
    "assign": "canary-assign-val-4c3d2e1f",
}


def _bundle_module():
    from core import support_bundle

    return support_bundle


def _canary_log() -> str:
    c = _CANARIES
    return (
        "12:00:01 starting litellm\n"
        f"12:00:02 connecting to postgres://atlas:{c['url']}@supabase-db:5432/atlas\n"
        f"12:00:03 > POST /v1/chat/completions\n> Authorization: Bearer {c['bearer']}\n"
        f"12:00:04 curl -H 'Authorization: Bearer {c['bearer']}' http://litellm:4000\n"
        "-----BEGIN RSA PRIVATE KEY-----\n"
        f"MIIEow{c['pem']}AAAA\n{c['pem']}BBBB\n"
        "-----END RSA PRIVATE KEY-----\n"
        f'{{"user": "atlas",\n "password": "{c["json"]}"}}\n'
        f"OPENAI_API_KEY={c['assign']}\n"
        f"echoed secret value {c['env']} from the environment\n"
        "12:00:05 ERROR compose up failed: service litellm exited 1\n"
    )


def _canary_request(tmp_path: Path, *, include_unlisted: bool):
    sb = _bundle_module()
    log_file = tmp_path / "session.log"
    log_file.write_text(_canary_log(), encoding="utf-8")
    c = _CANARIES
    env = {
        "SUPABASE_DB_PASSWORD": c["env"],
        "LITELLM_URL": f"http://proxy:{c['url']}@litellm:4000",
        "LITELLM_SOURCE": "container",
        "BASE_PORT": "63000",
    }
    checks = [
        {"id": "compose", "status": "fail",
         "message": f"compose failed reaching postgres://atlas:{c['url']}@db",
         "details": {"output": _canary_log(), "api_key": c["assign"]}},
        {"id": "endpoints", "status": "pass", "message": "ok", "details": {}},
    ]
    return sb.BundleRequest(
        env=env, env_file=None, checks=checks, include_unlisted=include_unlisted,
        logs=[sb.LogSource("session.log", path=log_file),
              sb.LogSource("transcript.log", text=_canary_log())],
        context={"command": "start", "note": f"Bearer {c['bearer']}"},
    )


def _archive_payloads(bundle) -> list[bytes]:
    """The .tar.gz as written, its decompressed tar stream (member headers and
    contents), and each member's bytes."""
    import gzip
    import io
    import tarfile

    sb = _bundle_module()
    archive = sb.archive_bytes(bundle)
    raw_tar = gzip.decompress(archive)
    members = []
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for info in tar.getmembers():
            members.append(f"{info.name} {info.uname} {info.gname}".encode())
            members.append(tar.extractfile(info).read())
    return [archive, raw_tar, *members]



@pytest.mark.parametrize("include_unlisted", [False, True])
def test_support_bundle_leaks_no_planted_canary(tmp_path, include_unlisted) -> None:
    """#1057 AC1: canaries in an env value, a URL credential, a bearer header
    and a multiline log are absent from every file, the archive metadata and
    the preview — in the default bundle and with every opt-in field."""
    sb = _bundle_module()
    bundle = sb.build_bundle(_canary_request(tmp_path, include_unlisted=include_unlisted))
    preview = "\n".join(sb.preview_lines(bundle)).encode()

    for blob in [*_archive_payloads(bundle), preview]:
        for name, canary in _CANARIES.items():
            assert canary.encode() not in blob, f"{name} canary leaked"
    # Redaction keeps the diagnosis readable.
    assert b"ERROR compose up failed: service litellm exited 1" in preview
    assert b"[REDACTED]" in preview


def _synthetic_jwt() -> str:
    """A well-formed but meaningless JWT, assembled at runtime so secret
    scanners do not flag this test's canary as a leaked token."""
    import base64
    import json as _json

    def part(payload) -> str:
        raw = payload if isinstance(payload, bytes) else _json.dumps(payload).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return ".".join([part({"alg": "HS256"}), part({"sub": "1234"}), part(b"signature-value")])


# Synthetic canaries in real secret shapes, assembled at runtime for the
# same reason: none is a credential, and none should look like one to a scanner.
_JWT = _synthetic_jwt()
_GOOGLE_SHAPED_KEY = "AI" + "za" + "SyA1234567890" + "abcdefghijklmnopqrstuv"
_SHELL_KEY = "-".join(["Sh3ll", "Exported", "K3y"])
_COLOURED_PASSWORD_LINE = "".join(
    ["POSTGRES_PASS", "WORD", "=", "abc-", "\x1b[1;36m", "123", "\x1b[0m", "-def-456"]
)

_LEAKS = [
    # (text, secret that must not survive)
    ("redis://:p4ssw0rd-x@redis:6379/0", "p4ssw0rd-x"),
    ("https://h/x?access_token=abc123def&page=2", "abc123def"),
    ("x-api-key: zzzzzzzzzzz", "zzzzzzzzzzz"),
    ("ghp_" + "a" * 36, "a" * 36),
    ("sk-ant-" + "b" * 30, "b" * 30),
    (_JWT, _JWT.rsplit(".", 1)[1]),
    ("value n8n-key-value-123 echoed", "n8n-key-value-123"),
    ("value n8n-key-value-123 urlencoded%3A", "n8n-key-value-123"),
    # Found by review: each of these leaked before the fix.
    ('{"password": "hunter2-leaked-pw"}', "hunter2-leaked-pw"),
    ("password=hunter2-leaked-pw", "hunter2-leaked-pw"),
    ("{'password': 'hunter2-prod-pw'}", "hunter2-prod-pw"),
    ("litellm-1  | Cookie: session=c00kie-val", "c00kie-val"),
    ("> Cookie: session=c00kie-val", "c00kie-val"),
    ("12:00 Authorization: Token t0ken-VALUE-99", "t0ken-VALUE-99"),
    ("N8N_ENCRYPTION_KEY=enc-Key-4411", "enc-Key-4411"),
    ("SUPABASE_SERVICE_ROLE_KEY=svc-Role-9988", "svc-Role-9988"),
    ("postgres://atlas:Ab3/xY+9Qz==@db:5432/atlas", "Ab3/xY+9Qz=="),
    ("password: 'correct horse battery staple'", "horse battery staple"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0rphan\n", "MIIEowIBAAKCAQEA0rphan"),
    ("MIIEowIBAAKCAQEAtail\n-----END RSA PRIVATE KEY-----\nafter", "MIIEowIBAAKCAQEAtail"),
    (_COLOURED_PASSWORD_LINE, "-".join(["abc", "123", "def", "456"])),
    (f"export key {_GOOGLE_SHAPED_KEY}", _GOOGLE_SHAPED_KEY),
    (f"shell-exported {_SHELL_KEY}", _SHELL_KEY),
    # CLI flags, redis-cli -a, *_AUTH user/password, prose, escaped JSON quotes.
    ("psql --password Xy9!kLm2pq", "Xy9!kLm2pq"),
    ("redis-cli -h redis -a abc12 ping", "abc12"),
    ("NEO4J_AUTH=neo4j/wordlikepass", "wordlikepass"),
    ('connecting with secret "Xy9!kLm2pq"', "Xy9!kLm2pq"),
    ('{"password":"a\\"b tail-leak"}', "tail-leak"),
]
_KEPT = [
    # (text, content redaction must not destroy)
    ("vllm | tokenizer=meta-llama/Llama-3.1-8B", "meta-llama/Llama-3.1-8B"),
    ("max_tokens=4096 temperature=0.2", "max_tokens=4096"),
    ("Kong basic authentication gates this route", "basic authentication"),
    ('SUPABASE_JWT_SECRET: variable is not set', "variable is not set"),
    ("?access_token=x&model=llama3.2&port=11434", "&model=llama3.2&port=11434"),
    ("COMFYUI_SOURCE is disabled; check not required.", "is disabled; check not required."),
    ("token rotation happened", "token rotation happened"),
    # Auth-mode toggles are diagnostics, not credentials.
    ("BACKEND_KONG_AUTH=disabled", "BACKEND_KONG_AUTH=disabled"),
    ("BACKEND_IDENTITY_AUTH: required", "BACKEND_IDENTITY_AUTH: required"),
    ("Unexpected token '<'", "Unexpected token '<'"),
]


def _stock_redactor():
    """A redactor built the way a real stack builds it: from the shipped
    .env.example (toggles, placeholders) plus a key exported in the shell."""
    sb = _bundle_module()
    stock: dict[str, str] = {}
    for line in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.startswith("#"):
            stock[key.strip()] = value.split(" #", 1)[0].strip()
    shell = {"OPENAI_API_KEY": _SHELL_KEY}
    return sb.Redactor(shell, {**stock, "N8N_API_KEY": "n8n-key-value-123"})


@pytest.mark.parametrize(("text", "secret"), _LEAKS)
def test_support_bundle_redactor_removes_secret(text, secret) -> None:
    assert secret not in _stock_redactor().text(text)


@pytest.mark.parametrize(("text", "kept"), _KEPT)
def test_support_bundle_redactor_keeps_diagnostics(text, kept) -> None:
    assert kept in _stock_redactor().text(text)


def test_support_bundle_redactor_scrubs_keys_and_secret_named_values() -> None:
    redactor = _stock_redactor()

    assert redactor.value({
        "DB_PASSWORD": "short", "PORT": "5432",
        "postgres://atlas:hunter22@db/x": "seen",
    }) == {"DB_PASSWORD": "[REDACTED]", "PORT": "5432", "postgres://[REDACTED]@db/x": "seen"}
    # *_PASS / *_AUTH are secret-named too, and a lowercase passphrase is
    # scrubbed by value from free text.
    assert redactor.value({"SMTP_PASS": "Xy9!kLm2pq", "GRAPH_DB_AUTH": "neo4j/pw"}) == {
        "SMTP_PASS": "[REDACTED]", "GRAPH_DB_AUTH": "[REDACTED]",
    }
    sb = _bundle_module()
    assert "correcthorsebattery" not in sb.Redactor(
        {"GRAPH_DB_PASSWORD": "correcthorsebattery"}
    ).text("login correcthorsebattery")


def test_support_bundle_redaction_stays_linear_on_long_lines() -> None:
    """Review found an unbounded pattern that took ~1 minute on one 40k-char
    line; every pattern is now length-bounded."""
    import time

    redactor = _stock_redactor()
    adversarial = [("a-" * 131072), ("aB3_" * 65536), ("x://" + "a" * 262140)]
    started = time.monotonic()
    for line in adversarial:
        redactor.text(line)

    assert time.monotonic() - started < 10


def _manifest_stack(tmp_path: Path, monkeypatch) -> Path:
    import start as start_module

    _write_base_env(tmp_path, extra="BASE_PORT=63000\nLITELLM_DEFAULT_MODEL=base-model\n")
    manifest = tmp_path / "atlas.consumer.yml"
    manifest.write_text(
        "name: showcase\nproject_name: showcase\n"
        "env:\n  values:\n    BASE_PORT: 63000\n    LITELLM_DEFAULT_MODEL: wrong-model\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager, "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )
    return manifest


def _read_bundle(path: Path) -> dict:
    import io
    import tarfile

    with tarfile.open(path, mode="r:gz") as tar:
        body = tar.extractfile("atlas-support-bundle/bundle.json").read()
        names = tar.getnames()
    return {"body": json.loads(body), "names": names}


def _by_key(entries: list[dict], field: str) -> dict[str, dict]:
    return {entry[field]: entry for entry in entries}


def test_doctor_bundle_names_the_manifest_that_set_a_wrong_key(tmp_path, monkeypatch) -> None:
    """#1057 AC2: a port (and a model) set from a consumer manifest is reported
    with that manifest as its origin and an action that edits it there."""
    import start as start_module

    manifest = _manifest_stack(tmp_path, monkeypatch)
    out = tmp_path / "bundle.tar.gz"

    result = CliRunner().invoke(start_module.main, ["doctor", "--bundle", str(out)])

    assert out.is_file(), result.output
    body = _read_bundle(out)["body"]
    finding = _by_key(body["findings"], "id")["base-port"]
    port = _by_key(finding["keys"], "key")["BASE_PORT"]
    config = _by_key(body["config"], "key")
    assert port["origin"] == str(manifest)
    assert f"Change `env.values.BASE_PORT` in {manifest}" in port["action"]
    model = config["LITELLM_DEFAULT_MODEL"]
    assert (model["value"], model["origin"]) == ("wrong-model", str(manifest))
    assert "LITELLM_URL" not in config  # not allowlisted


def test_doctor_bundle_opt_in_only_adds_unlisted_fields(tmp_path, monkeypatch) -> None:
    """#1057 AC3: the default bundle is the allowlist; --include-unlisted adds
    check details and every other key, and removes nothing."""
    import start as start_module

    _manifest_stack(tmp_path, monkeypatch)
    default, opted = tmp_path / "default.tar.gz", tmp_path / "opted.tar.gz"
    runner = CliRunner()
    runner.invoke(start_module.main, ["doctor", "--bundle", str(default)])
    runner.invoke(start_module.main, ["doctor", "--bundle", str(opted), "--include-unlisted"])
    base, full = _read_bundle(default)["body"], _read_bundle(opted)["body"]

    base_keys = {e["key"] for e in base["config"]}
    full_keys = {e["key"] for e in full["config"]}
    assert base_keys < full_keys
    assert "LITELLM_URL" in full_keys - base_keys
    assert all("details" not in check for check in base["checks"])
    assert all("details" in check for check in full["checks"])
    assert [c["id"] for c in base["checks"]] == [c["id"] for c in full["checks"]]
    assert base["omitted"] == {
        "config_keys": len(full_keys) - len(base_keys),
        "check_details": len(base["checks"]),
        "opt_in": "--include-unlisted",
    }
    assert full["omitted"]["opt_in"] is None


def test_doctor_include_unlisted_requires_bundle(tmp_path, monkeypatch) -> None:
    import start as start_module

    result = CliRunner().invoke(start_module.main, ["doctor", "--include-unlisted"])

    assert result.exit_code == 2
    assert "--include-unlisted needs --bundle PATH" in result.output


def test_doctor_bundle_records_checks_that_could_not_run(tmp_path, monkeypatch) -> None:
    """#1057 AC4: a disabled service's check is kept as skipped/unavailable,
    and a check that raises is recorded rather than dropped."""
    import start as start_module

    _write_base_env(tmp_path, extra="COMFYUI_SOURCE=disabled\n")
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager, "validate_compose_config",
        lambda self: (_ for _ in ()).throw(RuntimeError("Docker is not installed")),
    )

    def broken(_starter):
        raise RuntimeError("probe exploded")

    broken.__name__ = "_doctor_check_broken_probe"
    monkeypatch.setattr(start_module, "DOCTOR_CHECKS", [*start_module.DOCTOR_CHECKS, broken])
    out = tmp_path / "bundle.tar.gz"

    result = CliRunner().invoke(start_module.main, ["doctor", "--bundle", str(out), "--format", "json"])

    checks = {c["id"]: c for c in _read_bundle(out)["body"]["checks"]}
    assert len(checks) == len(start_module.DOCTOR_CHECKS)
    assert (checks["comfyui-mps"]["status"], checks["comfyui-mps"]["available"],
            checks["comfyui-mps"]["message"]) == (
        "skipped", False, "COMFYUI_SOURCE is not managed-localhost-mps.")
    assert (checks["compose"]["status"], checks["compose"]["available"]) == ("skipped", False)
    assert checks["broken-probe"]["status"] == "unavailable"
    assert "RuntimeError: probe exploded" in checks["broken-probe"]["message"]
    # The JSON on stdout stays machine-clean; the preview went to stderr.
    assert json.loads(result.stdout)["checks"][-1]["id"] == "broken-probe"
    assert "Support bundle preview" in result.stderr


def _timed_checks(release):
    def _doctor_check_fast(_starter):
        return {"id": "fast", "status": "pass", "message": "ok", "details": {}}

    def _doctor_check_hangs(_starter):
        release.wait(5)
        return {"id": "hangs", "status": "pass", "message": "late", "details": {}}

    def _doctor_check_after(_starter):
        return {"id": "after", "status": "pass", "message": "ok", "details": {}}

    return [_doctor_check_fast, _doctor_check_hangs, _doctor_check_after]


def test_support_bundle_caps_logs_and_time_and_says_so(tmp_path) -> None:
    """#1057 AC5: an oversized log keeps only its tail under the cap, checks
    past the wall-time budget are recorded unavailable, and both
    truncations are written into the bundle."""
    import threading

    sb = _bundle_module()
    limits = sb.BundleLimits(max_log_bytes=2048, max_seconds=0.3)
    big = "".join(f"line {n:06d} filler filler filler\n" for n in range(20000))
    release = threading.Event()
    try:
        results, notes = sb.run_checks(_timed_checks(release), None, limits)
    finally:
        release.set()
    bundle = sb.build_bundle(sb.BundleRequest(
        env={}, checks=results, notes=notes, limits=limits,
        logs=[sb.LogSource("big.log", text=big)],
    ))

    log = bundle.body["logs"][0]
    kept = bundle.logs["big.log"]
    truncation = "\n".join(bundle.body["truncation"])
    assert {
        "statuses": [r["status"] for r in results],
        "hung": "did not finish" in results[1]["message"],
        "never_started": results[2]["message"].startswith("not run:"),
        "truncated": log["truncated"],
        "recorded_under_cap": log["kept_bytes"] <= 2048,
        "written_under_cap": len(kept.encode()) <= 2048,
        "original_bytes": log["original_bytes"],
        "tail_kept": kept.endswith("line 019999 filler filler filler\n"),
        "log_note": "log big.log: kept the last" in truncation,
        "time_note": "1 check(s) not run: after" in truncation,
    } == {
        "statuses": ["pass", "unavailable", "unavailable"],
        "hung": True, "never_started": True, "truncated": True,
        "recorded_under_cap": True, "written_under_cap": True,
        "original_bytes": len(big.encode()), "tail_kept": True,
        "log_note": True, "time_note": True,
    }


def _outbound_recorder(monkeypatch) -> list:
    """Record every socket connection that reaches the network stack, and make
    urllib/requests calls observable too."""
    import socket
    import urllib.request

    attempts: list = []
    real_connect = socket.socket.connect

    def connect(sock, address):
        attempts.append(address)
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *a, **k: attempts.append(("urlopen", a)) or (_ for _ in ()).throw(OSError("blocked")),
    )
    return attempts


def _phone_home_check(_starter):
    import socket

    try:
        socket.create_connection(("203.0.113.9", 443), timeout=1)
    except OSError as exc:
        return {"id": "phone-home", "status": "warn", "message": str(exc), "details": {}}
    return {"id": "phone-home", "status": "fail", "message": "connected out", "details": {}}


def _bundle_starter(tmp_path, monkeypatch, destination: Path):
    import start as start_module

    _write_base_env(tmp_path, extra=f"SUPABASE_DB_PASSWORD={_CANARIES['env']}\n")
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager, "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )
    monkeypatch.setattr(start_module, "DOCTOR_CHECKS", [_phone_home_check])
    starter = start_module.AtlasStarter()
    starter.support_bundle_path = destination
    return starter


def _failing_linear_start(_starter, _options):
    print("Starting Atlas…")
    print(f"ERROR: litellm exited; DATABASE_URL=postgres://a:{_CANARIES['url']}@db")
    return 1


def test_no_tui_failed_start_previews_then_writes_locally(tmp_path, monkeypatch, capsys) -> None:
    """#1057 AC6 + AC7 (--no-tui): a failing linear start previews the whole
    bundle in its transcript, then writes it locally; collection never
    reaches the network."""
    import start as start_module

    out = tmp_path / "support.tar.gz"
    starter = _bundle_starter(tmp_path, monkeypatch, out)
    attempts = _outbound_recorder(monkeypatch)
    monkeypatch.setattr(start_module, "run_linear_startup", _failing_linear_start)

    assert start_module._run_linear_with_support_bundle(starter, object()) == 1

    err = capsys.readouterr().err
    preview = err[err.index("Support bundle preview"):err.index("Wrote support bundle")]
    body = _read_bundle(out)
    assert {
        "mode": oct(out.stat().st_mode & 0o777),
        "log_previewed": "── atlas-support-bundle/logs/transcript.log" in preview,
        "failure_previewed": "ERROR: litellm exited" in preview,
        "canary_in_output": _CANARIES["url"] in err,
        "outbound_connections": attempts,
        "members": body["names"],
        "context": body["body"]["context"],
        "phone_home_refused": "collection is offline" in body["body"]["checks"][0]["message"],
    } == {
        "mode": "0o600", "log_previewed": True, "failure_previewed": True,
        "canary_in_output": False, "outbound_connections": [],
        "members": ["atlas-support-bundle/bundle.json",
                    "atlas-support-bundle/logs/transcript.log"],
        "context": {"command": "start", "interface": "no-tui", "exit_code": 1},
        "phone_home_refused": True,
    }


def test_no_tui_successful_start_writes_no_bundle(tmp_path, monkeypatch) -> None:
    import start as start_module

    out = tmp_path / "support.tar.gz"
    starter = _bundle_starter(tmp_path, monkeypatch, out)
    monkeypatch.setattr(start_module, "run_linear_startup", lambda _s, _o: 0)

    assert start_module._run_linear_with_support_bundle(starter, object()) == 0
    assert not out.exists()


def test_textual_failed_launch_previews_then_writes_locally(tmp_path, monkeypatch) -> None:
    """#1057 AC6 (Textual): the launch screen's failure hook exports once,
    streams the preview into the log pane before writing, and never reaches
    the network."""
    import asyncio

    from ui.textual.screens.wizard_screen import WizardScreen

    out = tmp_path / "support.tar.gz"
    starter = _bundle_starter(tmp_path, monkeypatch, out)
    screen = WizardScreen(steps=[], services=[], starter=starter)
    lines: list[str] = []
    workers: list = []
    monkeypatch.setattr(screen, "_safe_log", lambda msg, **_kw: lines.append(msg))
    monkeypatch.setattr(screen, "run_worker", lambda coro, **_kw: workers.append(coro))
    attempts = _outbound_recorder(monkeypatch)
    try:
        screen._tee_to_log(f"compose: Authorization: Bearer {_CANARIES['bearer']}", source="pipeline", level="error")
        screen._start_support_bundle_export()
        screen._start_support_bundle_export()
        assert len(workers) == 1
        asyncio.run(workers[0])
    finally:
        path = screen._launch_log_path
        screen._close_launch_log_tee()
        if path is not None:
            Path(path).unlink(missing_ok=True)

    text = "\n".join(lines)
    assert out.is_file()
    assert text.index("Support bundle preview") < text.index("Wrote support bundle")
    assert "atlas-support-bundle/logs/session.log" in text
    assert _CANARIES["bearer"] not in text
    assert attempts == []
    assert _read_bundle(out)["body"]["context"]["interface"] == "textual"


def test_support_bundle_docs_are_published_on_all_surfaces() -> None:
    """#1057 AC8: schema version, allowlist and best-effort redaction are
    documented on every surface."""
    sb = _bundle_module()
    for text in (
        (REPO_ROOT / "docs/operations/index.md").read_text(encoding="utf-8"),
        surface_text("docs/operations/index.md", "site"),
        surface_text("docs/operations/index.md", "wiki"),
    ):
        assert "./start.sh doctor --bundle" in text
        assert "--support-bundle" in text
        assert sb.SCHEMA_VERSION in text
        assert "best-effort" in text
        assert "--include-unlisted" in text
        for pattern in ("_SOURCE", "_PORT", "MODEL"):
            assert pattern in text


def test_doctor_bundle_from_the_stock_env_keeps_its_diagnostics(tmp_path, monkeypatch) -> None:
    """Review: a .env copied from .env.example turned 39 of 70 *_SOURCE values
    into [REDACTED], because toggles such as BACKEND_KONG_AUTH=disabled made
    'disabled' a known secret."""
    import start as start_module

    (tmp_path / ".env.example").write_text(
        (REPO_ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / ".env").write_text(
        (REPO_ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    _patch_starter_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager, "validate_compose_config",
        lambda self: (0, "", "", ["docker", "compose", "config", "-q"]),
    )
    out = tmp_path / "bundle.tar.gz"

    CliRunner().invoke(start_module.main, ["doctor", "--bundle", str(out)])

    body = _read_bundle(out)["body"]
    sources = {e["key"]: e["value"] for e in body["config"] if e["key"].endswith("_SOURCE")}
    messages = " ".join(check["message"] for check in body["checks"])
    shipped = {line.split("=", 1)[0]: line.split("=", 1)[1].split(" #")[0].strip()
               for line in (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
               if line.endswith("_SOURCE=disabled") or line.startswith("COMFYUI_SOURCE=")}
    assert (len(sources) > 30, [k for k, v in sources.items() if v == "[REDACTED]"],
            {k: sources[k] for k in shipped}, "[REDACTED]" in messages) == (True, [], shipped, False)


def test_bundle_paths_resolve_against_the_invoking_directory(tmp_path, monkeypatch) -> None:
    """Review: ./start.sh runs the bootstrapper from bootstrapper/, so a
    relative --bundle path landed there instead of where the user ran it."""
    import start as start_module

    _manifest_stack(tmp_path, monkeypatch)
    invoker = tmp_path / "invoker"
    invoker.mkdir()
    monkeypatch.setenv("ATLAS_INVOKER_CWD", str(invoker))
    monkeypatch.chdir(REPO_ROOT / "bootstrapper")

    result = CliRunner().invoke(start_module.main, ["doctor", "--bundle", "./rel.tar.gz"])

    assert ((invoker / "rel.tar.gz").is_file(), f"Wrote support bundle {invoker / 'rel.tar.gz'}"
            in result.output, (REPO_ROOT / "bootstrapper" / "rel.tar.gz").exists()) == (True, True, False)


def test_no_tui_log_follow_exit_after_success_writes_no_bundle(tmp_path, monkeypatch) -> None:
    """Review: Ctrl+C on the log stream after a successful start returns 130;
    that is not a failed start and must not produce a bundle."""
    import start as start_module

    out = tmp_path / "support.tar.gz"
    starter = _bundle_starter(tmp_path, monkeypatch, out)

    def followed_then_interrupted(started, _options):
        started.startup_reached_log_follow = True
        return 130

    monkeypatch.setattr(start_module, "run_linear_startup", followed_then_interrupted)

    assert start_module._run_linear_with_support_bundle(starter, object()) == 130
    assert not out.exists()


def test_support_bundle_fails_closed_without_env_and_reports_honestly(tmp_path, monkeypatch) -> None:
    """Review: an unreadable .env meant no known secrets were scrubbed while
    logs still went in; a small log holding a key block was also reported as
    truncated although nothing was cut."""
    sb = _bundle_module()
    starter = _bundle_starter(tmp_path, monkeypatch, tmp_path / "unused.tar.gz")
    monkeypatch.setattr(starter.config_parser, "parse_env_file",
                        lambda: (_ for _ in ()).throw(UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")))
    closed = starter.build_support_bundle(sb.BundleOptions(logs=(sb.LogSource("x.log", text="secret"),)))
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----\nok\n"
    honest = sb.build_bundle(sb.BundleRequest(env={}, logs=[sb.LogSource("k.log", text=pem)]))

    assert (closed.logs, "log excerpts left out" in " ".join(closed.body["truncation"]),
            honest.body["logs"][0]["truncated"], honest.body["truncation"]) == ({}, True, False, [])


def test_transcript_ignores_byte_probes_and_known_ids_survive(tmp_path) -> None:
    """Review: click's b"" stream probe broke Transcript.text(); and a check
    that never returns must keep the id its results use."""
    import io

    import start as start_module

    sb = _bundle_module()
    transcript = sb.Transcript()
    sink = io.StringIO()
    tee = sb._Tee(sink, transcript)
    tee.write("hello\n")
    try:
        tee.write(b"")
    except TypeError:
        pass  # StringIO rejects bytes; the transcript must still be intact

    assert (transcript.text(), sb.check_id(start_module._doctor_check_submodule_clean)) == (
        "hello\n", "submodule-cleanliness")


def test_textual_quit_waits_for_a_running_bundle_export(tmp_path, monkeypatch) -> None:
    """Review: Ctrl+Q right after a failure cancelled the export silently."""
    from ui.textual.screens.wizard_screen import WizardScreen

    starter = _bundle_starter(tmp_path, monkeypatch, tmp_path / "support.tar.gz")
    screen = WizardScreen(steps=[], services=[], starter=starter)
    notices: list[str] = []
    lines: list[str] = []
    monkeypatch.setattr(screen, "notify", lambda message, **_kw: notices.append(message))
    monkeypatch.setattr(screen, "_safe_log", lambda msg, **_kw: lines.append(msg))
    monkeypatch.setattr(screen, "run_worker", lambda coro, **_kw: coro.close())
    try:
        screen._phase = "launch"
        screen._launch_detach_ready = True
        screen._start_support_bundle_export()
        screen.action_quit_wizard()
    finally:
        path = screen._launch_log_path
        screen._close_launch_log_tee()
        if path is not None:
            Path(path).unlink(missing_ok=True)

    assert (lines[0].startswith("📦 Launch failed; collecting the support bundle"),
            notices) == (True, ["Writing the support bundle; Ctrl+Q works again when it is done."])


def test_a_raising_check_fails_the_json_report_instead_of_crashing(tmp_path, monkeypatch):
    """`doctor --format json` promises pure JSON and a non-zero exit on fail;
    a check that raised printed a traceback and no JSON at all."""
    from click.testing import CliRunner
    import start as start_module

    _write_base_env(tmp_path, extra="COMFYUI_SOURCE=disabled\n")
    _patch_starter_paths(monkeypatch, tmp_path)

    def broken(_starter):
        raise ValueError("invalid literal for int() with base 10: '8188x'")

    broken.__name__ = "_doctor_check_broken_probe"
    monkeypatch.setattr(start_module, "DOCTOR_CHECKS", [broken])

    result = CliRunner().invoke(start_module.main, ["doctor", "--format", "json"])

    payload = json.loads(result.stdout)
    assert result.exit_code == 1
    assert payload["ok"] is False
    assert payload["checks"][0]["id"] == "broken-probe"
    assert payload["checks"][0]["status"] == "fail"
    assert "ValueError" in payload["checks"][0]["message"]


@pytest.mark.parametrize(
    ("configured", "status"),
    [("/custom-models.yaml", "skipped"), ("/nonexistent/atlas-models.yaml", "fail")],
)
def test_doctor_fails_a_missing_operator_configured_model_sidecar(configured, status) -> None:
    """Only the shipped container-path default is expected to be missing; an
    operator path that is missing silently dropped its models."""
    from types import SimpleNamespace

    import start as start_module

    starter = SimpleNamespace(config_parser=SimpleNamespace(
        parse_env_file=lambda: {"COMFYUI_CUSTOM_MODELS_FILE": configured},
        root_dir=REPO_ROOT,
    ))
    assert start_module._doctor_check_model_sidecars(starter)["status"] == status


def test_custom_models_flag_resolves_against_the_invoking_directory(tmp_path, monkeypatch) -> None:
    import os

    import start as start_module

    monkeypatch.setenv("ATLAS_INVOKER_CWD", str(tmp_path))
    resolved = start_module._invoker_path_list(f"./a.yaml{os.pathsep}/abs/b.yaml")
    assert resolved.split(os.pathsep) == [str((tmp_path / "a.yaml").resolve()), "/abs/b.yaml"]


def test_preflight_base_port_override_recomputes_service_ports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """doctor's preflight wrote BASE_PORT alone, leaving *_PORT on the old
    block, so `endpoints export` emitted another stack's ports."""
    import start as start_module
    from tests.test_consumer_manifest import _patch_starter_root, _write_consumer, _write_minimal_root

    _write_minimal_root(tmp_path)
    with (tmp_path / ".env").open("a", encoding="utf-8") as env:
        env.write("BASE_PORT=63000\nLITELLM_PORT=63040\n")
    manifest = _write_consumer(tmp_path, "ports")
    with (manifest.parent / "atlas.env.user").open("a", encoding="utf-8") as user_env:
        user_env.write("BASE_PORT=20000\n")
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    _patch_starter_root(start_module, monkeypatch, tmp_path)

    starter = start_module.AtlasStarter()
    starter.materialize_consumer_env_for_preflight()

    expected = starter.port_manager.calculate_port_assignments(20000)["LITELLM_PORT"]
    parsed = starter.config_parser.parse_env_file()
    assert parsed["BASE_PORT"] == "20000"
    assert parsed["LITELLM_PORT"] == str(expected) != "63040"


def test_preflight_resolves_auto_under_the_applied_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stack launched with --profile prod (no manifest `profile:`) must not
    have `auto` sources re-resolved under "default" by doctor."""
    import start as start_module
    from tests.test_consumer_manifest import _patch_starter_root, _write_consumer, _write_minimal_root

    _write_minimal_root(tmp_path)
    with (tmp_path / ".env").open("a", encoding="utf-8") as env:
        env.write("ATLAS_PROFILE_APPLIED=prod\n")
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(_write_consumer(tmp_path, "applied")))
    _patch_starter_root(start_module, monkeypatch, tmp_path)

    starter = start_module.AtlasStarter()
    starter.materialize_consumer_env_for_preflight()
    assert starter.profile == "prod"
    assert start_module._known_applied_profile({"ATLAS_PROFILE_APPLIED": "dev"}) == "default"
    assert start_module._known_applied_profile({"ATLAS_PROFILE_APPLIED": "staging"}) == ""
    assert start_module._known_applied_profile({}) == ""


def test_preflight_invalid_base_port_keeps_stdout_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """update_env_ports prints on a bad base port; doctor --format json must
    not get that line ahead of its JSON."""
    import start as start_module
    from tests.test_consumer_manifest import _patch_starter_root, _write_consumer, _write_minimal_root

    _write_minimal_root(tmp_path)
    manifest = _write_consumer(tmp_path, "badport")
    with (manifest.parent / "atlas.env.user").open("a", encoding="utf-8") as user_env:
        user_env.write("BASE_PORT=80\n")
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    _patch_starter_root(start_module, monkeypatch, tmp_path)

    start_module.AtlasStarter().materialize_consumer_env_for_preflight()

    assert "Invalid base port" not in capsys.readouterr().out


@pytest.mark.parametrize("raw, status", [
    ("80", "fail"), ("70000", "fail"), ("-1", "fail"),  # start rejects them
    ("+64000", "pass"), ("64_000", "pass"),             # int() accepts these
    ("abc", "warn"),                    # start falls back to the default block
    ("auto", "pass"),                   # start resolves it itself
])
def test_doctor_base_port_matches_what_start_does(raw, status):
    from types import SimpleNamespace

    import start as start_module
    from core.port_manager import PortManager

    starter = SimpleNamespace(
        config_parser=SimpleNamespace(
            parse_env_file=lambda: {"BASE_PORT": raw}, get_project_name=lambda: "atlas",
        ),
        port_manager=PortManager(),
    )
    assert start_module._doctor_check_base_port(starter)["status"] == status


def test_consumer_artifacts_cannot_alias_stack_names(tmp_path: Path) -> None:
    """Consumer "asset" + store "baker" took over asset-baker's MinIO
    credentials; spark-history passed the bucket check; workflow id "plan"
    was overwritten by plan.json; a host service "litellm" overrode
    ATLAS_LITELLM_HOST_ENDPOINT."""
    from core.consumer_manifest import (
        ConsumerManifestError,
        StorageStore,
        _host_service_name,
        _parse_n8n_workflows_block,
        _validate_storage_collisions,
    )

    def store(consumer: str, name: str, bucket: str) -> StorageStore:
        key = f"{consumer}_{name}".upper().replace("-", "_")
        return StorageStore(consumer, name, key, f"{consumer}-{name}", bucket)

    with pytest.raises(ConsumerManifestError, match="MINIO_ASSET_BAKER_ACCESS_KEY"):
        _validate_storage_collisions([store("asset", "baker", "my-bucket")])
    with pytest.raises(ConsumerManifestError, match="built-in"):
        _validate_storage_collisions([store("demo", "logs", "spark-history")])
    _validate_storage_collisions([store("demo", "media", "demo-media")])  # fine
    with pytest.raises(ConsumerManifestError, match="reserved"):
        _parse_n8n_workflows_block(
            {"n8n_workflows": {"version": 1, "workflows": [{"id": "plan", "path": "w.json"}]}},
            "demo", tmp_path, tmp_path / "atlas.consumer.yml",
        )
    with pytest.raises(ConsumerManifestError, match="reserved"):
        _host_service_name({"name": "litellm"}, origin="m", seen=set())


def test_preflight_after_a_start_keeps_that_starts_cli_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A start with --base-port 64000 persisted 64000 over the manifest's
    20000; doctor re-merged the manifest and pointed .env at another block."""
    import start as start_module
    from tests.test_consumer_manifest import _patch_starter_root, _write_consumer, _write_minimal_root

    _write_minimal_root(tmp_path)
    with (tmp_path / ".env").open("a", encoding="utf-8") as env:
        env.write("BASE_PORT=64000\nATLAS_PROFILE_APPLIED=default\n")
    manifest = _write_consumer(tmp_path, "started")
    with (manifest.parent / "atlas.env.user").open("a", encoding="utf-8") as user_env:
        user_env.write("BASE_PORT=20000\n")
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    _patch_starter_root(start_module, monkeypatch, tmp_path)

    applied = start_module.AtlasStarter().materialize_consumer_env_for_preflight()

    parsed = start_module.AtlasStarter().config_parser.parse_env_file()
    assert parsed["BASE_PORT"] == "64000"
    assert "BASE_PORT" not in applied
    # Derived overlay paths and keys .env lacks still materialize.
    assert "EXTRA_CONSUMER_VALUE" in applied


def test_env_values_conflicting_with_derived_sidecar_key_is_an_error(tmp_path: Path) -> None:
    # env.values OLLAMA_CUSTOM_MODELS used to be overwritten by the
    # model_sidecars-derived list without a word.
    from core.consumer_manifest import ConsumerManifestError, load_consumer_config

    from tests.test_consumer_manifest import _write_consumer, _write_minimal_root

    _write_minimal_root(tmp_path)
    one = _write_consumer(tmp_path, "one", project_name="shared", include_brand=False)
    one.write_text(
        one.read_text(encoding="utf-8").replace(
            "    EXTRA_CONSUMER_VALUE: enabled\n",
            "    EXTRA_CONSUMER_VALUE: enabled\n    OLLAMA_CUSTOM_MODELS: qwen3:8b\n",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConsumerManifestError, match="OLLAMA_CUSTOM_MODELS"):
        load_consumer_config(tmp_path, explicit_paths=[str(one)])


def test_doctor_overlay_env_scan_skips_compose_dollar_escapes() -> None:
    from start import _doctor_compose_var_refs

    assert _doctor_compose_var_refs("curl localhost:$${PORT} ${A:-x} ${B}") == [
        ("A", True), ("B", False),
    ]


# --- redis-aof (#1343) -------------------------------------------------------

_REDIS_ENV = {"PROJECT_NAME": "demo", "REDIS_IMAGE": "redis:7.2.14-alpine"}
_MANIFEST_LAST = "manifest-last: file appendonly.aof.1.incr.aof seq 1 type i\n"
_CORRUPT_OUTPUT = (
    _MANIFEST_LAST
    + "AOF analyzed: filename=appendonly.aof.1.base.rdb, size=89, ok_up_to=89, "
    "ok_up_to_line=1, diff=0\n"
    "AOF appendonly.aof.1.incr.aof format error\n"
    "AOF analyzed: filename=appendonly.aof.1.incr.aof, size=6899, ok_up_to=6370, "
    "ok_up_to_line=1399, diff=529\n"
    "AOF appendonly.aof.1.incr.aof is not valid. Use the --fix option to try fixing it."
)
# Output captured from redis-check-aof 7.2.14 on a cut-off last command.
_TRUNCATED_TAIL = (
    "0x             452: Expected to read 9 bytes, got 8 bytes\n"
    "AOF analyzed: filename=appendonly.aof.1.incr.aof, size=1114, ok_up_to=1078, "
    "ok_up_to_line=215, diff=36\n"
    "AOF appendonly.aof.1.incr.aof is not valid. Use the --fix option to try fixing it."
)


def _scripted_docker(monkeypatch, replies: dict[str, tuple[int, str]]) -> list[list[str]]:
    import start as start_module

    calls: list[list[str]] = []

    def fake(args, timeout):
        calls.append(args)
        return replies[args[0]]

    monkeypatch.setattr(start_module, "_docker_text", fake)
    return calls


class _EnvStarter:
    def __init__(self, env: dict) -> None:
        self.config_parser = type("P", (), {"parse_env_file": lambda _self: dict(env)})()


@pytest.mark.parametrize(
    ("replies", "state"),
    [
        ({"inspect": (0, "true false")}, "running"),
        ({"inspect": (1, "No such object"), "volume": (1, "Error: no such volume")}, "no-volume"),
        ({"inspect": (0, "false true"), "volume": (0, "[]"), "run": (3, "")}, "no-aof"),
        ({"inspect": (0, "false false"), "volume": (0, "[]"), "run": (0, "valid")}, "ok"),
        ({"inspect": (0, "false true"), "volume": (0, "[]"), "run": (1, _CORRUPT_OUTPUT)}, "corrupt"),
        ({"inspect": (0, "false true"), "volume": (0, "[]"),
          "run": (1, _MANIFEST_LAST + _TRUNCATED_TAIL)}, "truncated"),
        ({"inspect": (1, "x"), "volume": (1, "Cannot connect to the Docker daemon")}, "unavailable"),
        ({"inspect": (1, "x"), "volume": (0, "[]"), "run": (125, "No such image")}, "unavailable"),
    ],
)
def test_redis_aof_probe_classifies_docker_replies(monkeypatch, replies, state) -> None:
    import start as start_module

    _scripted_docker(monkeypatch, replies)
    assert start_module._redis_aof_probe(_REDIS_ENV)[0] == state


def test_redis_aof_probe_checks_a_copy_of_a_read_only_mount(monkeypatch) -> None:
    import start as start_module

    calls = _scripted_docker(
        monkeypatch, {"inspect": (1, "x"), "volume": (0, "[]"), "run": (0, "")}
    )
    start_module._redis_aof_probe(_REDIS_ENV)
    run = calls[-1]
    assert "demo-redis-data:/data:ro" in run
    assert ["--pull", "never"] == run[2:4] and "--network" in run
    assert "cp -a /data/appendonlydir /tmp/aof" in run[-1]
    assert "--fix" not in run[-1]
    assert calls[0][-1] == "demo-redis"


def test_redis_aof_doctor_fails_with_backup_first_repair_steps(monkeypatch) -> None:
    import start as start_module

    _scripted_docker(
        monkeypatch,
        {"inspect": (0, "false true"), "volume": (0, "[]"), "run": (1, _CORRUPT_OUTPUT)},
    )
    result = start_module._doctor_check_redis_aof(_EnvStarter(_REDIS_ENV))

    assert result["id"] == "redis-aof"
    assert result["status"] == "fail"
    message = result["message"]
    assert "demo-redis-data" in message
    order = [message.index(step) for step in (
        "./stop.sh", "tar czf", "redis-check-aof --fix appendonly.aof.manifest", "./start.sh",
    )]
    assert order == sorted(order)
    assert result["details"]["findings"] == [_CORRUPT_OUTPUT.splitlines()[3]]
    assert "ok_up_to=6370" in message
    assert len(result["details"]["repair"]) == 4


@pytest.mark.parametrize(
    ("code", "output", "state"),
    [
        (0, "All AOF files and manifest are valid", "ok"),
        (3, "", "no-aof"),
        (1, _MANIFEST_LAST + _TRUNCATED_TAIL, "truncated"),
        # Redis tolerates a cut-off command only in the last incr file.
        (1, "manifest-last: file appendonly.aof.2.incr.aof seq 2 type i\n" + _TRUNCATED_TAIL,
         "corrupt"),
        (1, _CORRUPT_OUTPUT, "corrupt"),
        (1, "RDB preamble of AOF file is not sane, aborting.", "corrupt-base"),
        (1, _MANIFEST_LAST + "Cannot open file ./appendonly.aof.1.incr.aof: "
         "No such file or directory, aborting...", "corrupt"),
        (1, "cp: can't create directory '/tmp/aof': No space left on device", "unavailable"),
        (125, "Unable to find image 'redis:7.2.14-alpine' locally", "unavailable"),
        (-1, "timed out", "unavailable"),
    ],
)
def test_redis_aof_classify_separates_load_failures_from_tolerated_tails(
    code, output, state
) -> None:
    import start as start_module

    assert start_module._redis_aof_classify(code, output) == state


def test_redis_aof_doctor_points_a_bad_base_snapshot_at_a_backup(monkeypatch) -> None:
    import start as start_module

    _scripted_docker(
        monkeypatch,
        {"inspect": (1, "x"), "volume": (0, "[]"),
         "run": (1, "RDB preamble of AOF file is not sane, aborting.")},
    )
    result = start_module._doctor_check_redis_aof(_EnvStarter(_REDIS_ENV))

    assert result["status"] == "fail"
    assert "--fix cannot repair the base snapshot" in result["message"]
    assert "redis-check-aof --fix" not in result["message"]


@pytest.mark.parametrize(
    ("env", "replies", "status"),
    [
        (_REDIS_ENV, {"inspect": (0, "true false")}, "pass"),
        (_REDIS_ENV, {"inspect": (1, "x"), "volume": (0, "[]"),
                      "run": (1, _MANIFEST_LAST + _TRUNCATED_TAIL)}, "pass"),
        (_REDIS_ENV, {"inspect": (1, "x"), "volume": (1, "Cannot connect")}, "skipped"),
        ({"PROJECT_NAME": "demo"}, {}, "skipped"),
    ],
)
def test_redis_aof_doctor_never_fails_without_a_corrupt_file(
    monkeypatch, env, replies, status
) -> None:
    import start as start_module

    _scripted_docker(monkeypatch, replies)
    assert start_module._doctor_check_redis_aof(_EnvStarter(env))["status"] == status


def test_redis_aof_doctor_is_registered() -> None:
    import start as start_module

    assert start_module._doctor_check_redis_aof in start_module.DOCTOR_CHECKS


def _local_redis_image() -> str | None:
    import shutil
    import subprocess

    image = "redis:7.2.14-alpine"
    if shutil.which("docker") is None:
        return None
    try:
        probe = subprocess.run(
            ["docker", "image", "inspect", image], capture_output=True, check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return image if probe.returncode == 0 else None


_ZERO_GAP = (
    "head -c 600 /dev/zero >> $f; printf '*1\\r\\n$4\\r\\nPING\\r\\n' >> $f"
)
_CUT_TAIL = "s=$(stat -c %s $f); head -c $((s-5)) $f > /tmp/t; cat /tmp/t > $f"


@pytest.mark.skipif(_local_redis_image() is None, reason="docker or redis image unavailable")
@pytest.mark.parametrize(("damage", "status"), [(_ZERO_GAP, "fail"), (_CUT_TAIL, "pass")])
def test_redis_aof_doctor_against_a_real_volume(monkeypatch, damage, status) -> None:
    """End to end against a throwaway volume: zeros before a later write are a
    load failure, a cut-off last command is not, and the volume is untouched."""
    import subprocess
    import uuid

    import start as start_module

    image = _local_redis_image()
    project = f"atlasaoftest{uuid.uuid4().hex[:10]}"
    volume = f"{project}-redis-data"
    monkeypatch.undo()  # drop the autouse stub; this test talks to Docker
    seed = (
        "redis-server --appendonly yes --daemonize yes >/dev/null; sleep 1; "
        "for i in 1 2 3 4 5; do redis-cli set k$i v$i >/dev/null; done; "
        "redis-cli shutdown >/dev/null 2>&1; sleep 1; "
        f"f=$(ls /data/appendonlydir/*.incr.aof); {damage}; md5sum $f"
    )
    subprocess.run(["docker", "volume", "create", volume], check=True, capture_output=True)
    try:
        before = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{volume}:/data", image, "sh", "-c", seed],
            check=True, capture_output=True, text=True,
        ).stdout.split()[0]
        env = {"PROJECT_NAME": project, "REDIS_IMAGE": image}
        result = start_module._doctor_check_redis_aof(_EnvStarter(env))
        after = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{volume}:/data", image, "sh", "-c",
             "md5sum /data/appendonlydir/*.incr.aof"],
            check=True, capture_output=True, text=True,
        ).stdout.split()[0]
    finally:
        subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True)
    assert result["status"] == status, result
    assert before == after


# --- derived keys whose source disappears are cleared (#1368) ---------------


def test_manifest_records_which_derived_keys_it_wrote(tmp_path):
    from core.consumer_manifest import DERIVED_KEYS_MARKER, load_consumer_config
    from tests.test_consumer_manifest import _write_consumer, _write_minimal_root

    _write_minimal_root(tmp_path)
    config = load_consumer_config(tmp_path, explicit_paths=[str(_write_consumer(tmp_path, "rag-showcase"))])

    owned = set(config.env_overrides[DERIVED_KEYS_MARKER].split(","))
    assert {"OLLAMA_CUSTOM_MODELS", "COMFYUI_CUSTOM_MODELS_FILE", "BACKEND_PLUGINS_DIR"} <= owned


def test_a_removed_ollama_sidecar_clears_its_derived_key_on_the_next_start():
    import start as start_module

    previous_env = {
        "ATLAS_DERIVED_KEYS": "BACKEND_PLUGINS_DIR,OLLAMA_CUSTOM_MODELS",
        "OLLAMA_CUSTOM_MODELS": "llama3.2:latest",
        "BACKEND_PLUGINS_DIR": "/plugins",
    }
    # The manifest no longer declares model_sidecars.ollama.
    overrides = {"BACKEND_PLUGINS_DIR": "/plugins", "ATLAS_DERIVED_KEYS": "BACKEND_PLUGINS_DIR"}

    merged = start_module._with_stale_derived_keys_cleared(overrides, previous_env)

    assert merged["OLLAMA_CUSTOM_MODELS"] == ""
    assert merged["ATLAS_DERIVED_KEYS"] == "BACKEND_PLUGINS_DIR"
    assert merged["BACKEND_PLUGINS_DIR"] == "/plugins"


def test_a_hand_set_derived_key_is_never_cleared():
    import start as start_module

    hand_set = {"OLLAMA_CUSTOM_MODELS": "mistral:7b"}  # no ATLAS_DERIVED_KEYS marker
    assert start_module._with_stale_derived_keys_cleared({}, hand_set) == {}
    # A marker naming an unknown key cannot blank arbitrary settings.
    forged = {"ATLAS_DERIVED_KEYS": "PROJECT_NAME", "PROJECT_NAME": "atlas"}
    assert start_module._with_stale_derived_keys_cleared({}, forged) == {}


def test_removing_every_derived_source_also_clears_the_marker():
    import start as start_module

    merged = start_module._with_stale_derived_keys_cleared(
        {}, {"ATLAS_DERIVED_KEYS": "OLLAMA_CUSTOM_MODELS", "OLLAMA_CUSTOM_MODELS": "x"}
    )
    assert merged == {"OLLAMA_CUSTOM_MODELS": "", "ATLAS_DERIVED_KEYS": ""}


def _start_with_manifest(tmp_path, monkeypatch, manifest_body: str, env_extra: str = ""):
    import start as start_module

    _write_base_env(tmp_path, env_extra)
    manifest = tmp_path / "atlas.consumer.yml"
    manifest.write_text(manifest_body, encoding="utf-8")
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    _patch_starter_paths(monkeypatch, tmp_path)
    starter = start_module.AtlasStarter()
    starter.banner.show_status_message = lambda *_a, **_k: None
    return starter


def test_start_clears_ollama_models_after_the_sidecar_is_removed(tmp_path, monkeypatch):
    """#1368 AC: drop model_sidecars.ollama from the manifest; the next start
    blanks the OLLAMA_CUSTOM_MODELS an earlier start derived."""
    starter = _start_with_manifest(
        tmp_path, monkeypatch, "name: showcase\nmodel_sidecars:\n  ollama:\n    - llama3.2:latest\n"
    )
    starter._apply_env_user_overlay()
    env = starter.config_parser.parse_env_file()
    assert env["OLLAMA_CUSTOM_MODELS"] == "llama3.2:latest"
    assert "OLLAMA_CUSTOM_MODELS" in env["ATLAS_DERIVED_KEYS"]

    (tmp_path / "atlas.consumer.yml").write_text("name: showcase\n", encoding="utf-8")
    applied = starter._apply_env_user_overlay()

    env = starter.config_parser.parse_env_file()
    assert env["OLLAMA_CUSTOM_MODELS"] == ""
    assert env["ATLAS_DERIVED_KEYS"] == ""
    assert "OLLAMA_CUSTOM_MODELS" not in applied  # housekeeping, not an operator pin


def test_no_manifest_or_env_user_value_is_never_blanked(tmp_path, monkeypatch):
    import start as start_module

    marker = "ATLAS_DERIVED_KEYS=OLLAMA_CUSTOM_MODELS\nOLLAMA_CUSTOM_MODELS=llama3.2:latest\n"
    no_manifest = type("C", (), {"consumers": ()})()
    env = {"ATLAS_DERIVED_KEYS": "OLLAMA_CUSTOM_MODELS", "OLLAMA_CUSTOM_MODELS": "llama3.2:latest"}
    # A plain doctor/start without --consumer leaves a consumer stack's keys alone.
    assert start_module._with_stale_derived_keys_cleared({}, env, no_manifest) == {}

    starter = _start_with_manifest(tmp_path, monkeypatch, "name: showcase\n", marker)
    (tmp_path / ".env.user").write_text("OLLAMA_CUSTOM_MODELS=mistral:7b\n", encoding="utf-8")
    monkeypatch.setattr(starter, "_env_user_overlay_path", lambda: tmp_path / ".env.user")
    starter._apply_env_user_overlay()

    assert starter.config_parser.parse_env_file()["OLLAMA_CUSTOM_MODELS"] == "mistral:7b"


# ─── managed Blender MCP pool (#851) ─────────────────────────────────────


def _blender_pool_env(tmp_path: Path, instances: int) -> dict:
    return {"BLENDER_MCP_SOURCE": "managed-localhost", "BLENDER_MCP_STATE_DIR": str(tmp_path / "bmcp"),
            "BLENDER_MCP_LOCALHOST_PORT": "9900", "BLENDER_MCP_INSTANCES": str(instances)}


def test_consumer_manifest_blender_mcp_block_sets_the_pool_size(tmp_path: Path) -> None:
    from core.consumer_manifest import ConsumerManifestError, load_consumer_config

    def load(block: str):
        (tmp_path / "atlas.consumer.yml").write_text(f"name: demo\n{block}", encoding="utf-8")
        return load_consumer_config(tmp_path, explicit_paths=[str(tmp_path / "atlas.consumer.yml")])

    assert load("blender_mcp:\n  instances: 3\n").env_overrides["BLENDER_MCP_INSTANCES"] == "3"
    assert "BLENDER_MCP_INSTANCES" not in load("").env_overrides
    assert "BLENDER_MCP_INSTANCES" not in load("blender_mcp:\n").env_overrides  # null declares nothing
    assert "BLENDER_MCP_INSTANCES" not in load("blender_mcp: {}\n").env_overrides
    for bad in ("blender_mcp:\n  instances: 3\n  ports: [1, 2]\n", "blender_mcp:\n  instances: 0\n",
                "blender_mcp:\n  instances: true\n", "blender_mcp: 3\n"):
        with pytest.raises(ConsumerManifestError, match="blender_mcp"):
            load(bad)


def test_blender_mcp_pool_starts_health_gates_and_tears_down_n_instances(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    import start as start_module
    import stop as stop_module
    from services import blender_mcp_manager as bm

    running: dict = {}
    monkeypatch.setattr(bm.BlenderMcpManager, "ensure_running",
                        lambda self: (running.setdefault(self.port, self.state_dir), True) and
                        (SimpleNamespace(running=True, pid=1), True))
    monkeypatch.setattr(bm.BlenderMcpManager, "health", lambda self: {"reachable": self.port in running})
    monkeypatch.setattr(bm.BlenderMcpManager, "stop", lambda self: running.pop(self.port, None) is not None)
    monkeypatch.setattr(bm.BlenderMcpManager, "status",
                        lambda self: SimpleNamespace(running=self.port in running, pid=None))
    env = _blender_pool_env(tmp_path, 3)
    starter = start_module.AtlasStarter.__new__(start_module.AtlasStarter)
    starter.config_parser = SimpleNamespace(parse_env_file=lambda: env)
    starter.banner = SimpleNamespace(show_status_message=lambda *_a, **_k: None)
    starter._managed_hosts_started_this_run = []

    assert starter._finalize_managed_blender_mcp() is True
    assert sorted(running) == [9900, 9901, 9902]  # Atlas-allocated, distinct
    assert len({str(d) for d in running.values()}) == 3  # one state dir (pid file) each
    assert [label for label, _m in starter._managed_hosts_started_this_run] == [
        "Blender MCP", "Blender MCP #1", "Blender MCP #2"]

    stopper = stop_module.AtlasStopper.__new__(stop_module.AtlasStopper)
    stopper.config_parser = SimpleNamespace(env_file_exists=lambda: True, parse_env_file=lambda: env)
    stopper.banner = starter.banner
    (tmp_path / "bmcp" / "instances" / "1").mkdir(parents=True)
    (tmp_path / "bmcp" / "instances" / "2").mkdir(parents=True)
    assert stopper.stop_managed_blender_mcp() is True and running == {}


def test_blender_mcp_status_lists_every_pool_instance(tmp_path, monkeypatch) -> None:
    import start as start_module
    from services import blender_mcp_manager as bm

    env = _blender_pool_env(tmp_path, 3)
    monkeypatch.setattr(bm.BlenderMcpManager, "_port_in_use", lambda self: False)  # no real probes
    monkeypatch.setattr(start_module, "_blender_mcp_manager", lambda: bm.manager_from_env(env))
    result = CliRunner().invoke(start_module.main, ["blender-mcp", "status"])
    rows = json.loads(result.output)
    assert result.exit_code == 0 and [(r["instance"], r["port"]) for r in rows] == [(0, 9900), (1, 9901), (2, 9902)]
    monkeypatch.setattr(start_module, "_blender_mcp_manager", lambda: bm.manager_from_env({**env, "BLENDER_MCP_INSTANCES": "1"}))
    single = json.loads(CliRunner().invoke(start_module.main, ["blender-mcp", "status"]).output)
    assert set(single) == {"running", "pid", "port_open"}  # unchanged single-instance shape
    (tmp_path / "bmcp" / "instances" / "2").mkdir(parents=True)  # a stopped stray: no pid file
    again = json.loads(CliRunner().invoke(start_module.main, ["blender-mcp", "status"]).output)
    assert set(again) == {"running", "pid", "port_open"}


def test_manual_pool_start_restarts_a_moved_pool_together(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    import start as start_module
    from services import blender_mcp_manager as bm

    calls = []
    env = _blender_pool_env(tmp_path, 2)
    monkeypatch.setattr(start_module, "_blender_mcp_manager", lambda: bm.manager_from_env(env))
    monkeypatch.setattr(bm, "pool_moves", lambda _pool: True)
    monkeypatch.setattr(bm.BlenderMcpManager, "stop", lambda self: calls.append(("stop", self.port)) or True)
    monkeypatch.setattr(bm.BlenderMcpManager, "ensure_running", lambda self: calls.append(("start", self.port))
                        or (SimpleNamespace(to_dict=lambda: {"running": True}), True))
    assert CliRunner().invoke(start_module.main, ["blender-mcp", "start"]).exit_code == 0
    assert calls == [("stop", 9900), ("stop", 9901), ("start", 9900), ("start", 9901)]


def test_launch_blocker_checks_every_pool_instance_before_the_stack_stops(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    import start as start_module
    from services import blender_mcp_manager as bm

    env = _blender_pool_env(tmp_path, 3)
    monkeypatch.setattr(bm.BlenderMcpManager, "preflight", lambda self: SimpleNamespace(ok=True, checks=[]))
    monkeypatch.setattr(bm.BlenderMcpManager, "_port_in_use", lambda self: self.port == 9901)
    assert start_module._managed_host_pool_blocker(bm, env, "Blender MCP") == (
        "instance 1: port 9901 is already in use by an unmanaged process")
    monkeypatch.setattr(bm, "pool_held_ports", lambda _pool: {9901})  # held by the pool itself
    assert start_module._managed_host_pool_blocker(bm, env, "Blender MCP") is None


def test_pool_ports_past_65535_fail_preflight(tmp_path) -> None:
    from services import blender_mcp_manager as bm

    env = {**_blender_pool_env(tmp_path, 10), "BLENDER_MCP_LOCALHOST_PORT": "65530"}
    checks = {c["name"]: c["status"] for c in bm.pool_from_env(env)[-1].preflight().checks}
    assert checks["port"] == "fail"
    assert "port" not in {c["name"] for c in bm.pool_from_env(env)[0].preflight().checks}


def test_doctor_warns_on_a_stray_pool_instance_and_start_reaps_it(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    import start as start_module
    from services import blender_mcp_manager as bm

    env = _blender_pool_env(tmp_path, 1)
    stray = bm.pool_members(bm.manager_from_env({**env, "BLENDER_MCP_INSTANCES": "3"}))[2]
    stray.state_dir.mkdir(parents=True)
    stray.pid_file.write_text("999999\n", encoding="utf-8")  # a process that is long gone
    monkeypatch.setattr(bm.BlenderMcpManager, "preflight", lambda self: SimpleNamespace(
        status="ok", checks=[{"name": "blender", "detail": "ok"}], to_dict=lambda: {}))
    starter = start_module.AtlasStarter.__new__(start_module.AtlasStarter)
    starter.config_parser = SimpleNamespace(parse_env_file=lambda: env)

    row = start_module._doctor_check_blender_mcp(starter)
    assert row["status"] == "warn" and "instance 2 has a stale pid file" in row["message"]
    bad = start_module._doctor_check_blender_mcp(SimpleNamespace(config_parser=SimpleNamespace(
        parse_env_file=lambda: {**env, "BLENDER_MCP_INSTANCES": "²"})))
    assert "BLENDER_MCP_INSTANCES='²' is not 1 to 16" in bad["message"]

    stopped = []
    monkeypatch.setattr(bm.BlenderMcpManager, "stop", lambda self: stopped.append(self.pool_index) or True)
    starter.banner = SimpleNamespace(show_status_message=lambda *_a, **_k: None)
    starter._reap_stray_blender_mcp(env)
    assert stopped == [2]


def test_endpoints_export_advertises_every_pool_instance() -> None:
    from core.endpoints_contract import build_export

    env = {"BLENDER_MCP_SOURCE": "managed-localhost", "BLENDER_MCP_LOCALHOST_PORT": "9900", "BLENDER_MCP_INSTANCES": "3"}
    d = {f.name: f.value for f in build_export(env)}
    assert d["ATLAS_BLENDER_MCP_HOST_ENDPOINT"] == "tcp://localhost:9900"
    assert d["ATLAS_BLENDER_MCP_HOST_ENDPOINTS"] == "tcp://localhost:9900,tcp://localhost:9901,tcp://localhost:9902"
    single = {f.name for f in build_export({**env, "BLENDER_MCP_INSTANCES": "1"})}
    assert "ATLAS_BLENDER_MCP_HOST_ENDPOINTS" not in single


def test_sigterm_during_a_linear_start_rolls_back_its_managed_hosts(monkeypatch) -> None:
    """SIGTERM/SIGHUP used to kill the --no-tui start with the default action,
    leaving managed hosts it started (own session) running."""
    import os
    import signal
    from types import SimpleNamespace

    import start as start_module

    rolled_back = []
    starter = SimpleNamespace(support_bundle_path=None,
                              rollback_managed_host_processes=lambda: rolled_back.append(True))

    def terminated(_starter, _options):
        os.kill(os.getpid(), signal.SIGTERM)
        return 0

    monkeypatch.setattr(start_module, "run_linear_startup", terminated)
    with pytest.raises(SystemExit) as exc:
        start_module._run_linear_with_support_bundle(starter, object())
    assert exc.value.code == 128 + signal.SIGTERM and rolled_back == [True]
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL or callable(signal.getsignal(signal.SIGTERM))


def test_url_credentials_with_an_at_sign_are_fully_redacted() -> None:
    """The URL pattern stopped at the first '@', half-replacing the password
    before the known-value pass could match it."""
    from core.support_bundle import Redactor

    redactor = Redactor({"POSTGRES_PASSWORD": "Xy7@kq9Lmn2"})
    text = redactor.text("postgresql://postgres:Xy7@kq9Lmn2@db:5432/x")
    assert "kq9Lmn2" not in text and "Xy7" not in text
    assert text.endswith("@db:5432/x")


def test_a_stray_instances_port_is_not_foreign_and_manual_start_reaps_it(tmp_path, monkeypatch) -> None:
    """A pool shrunk 2→1 with its base moved onto the stray's port was refused
    as an "unmanaged process", and `blender-mcp start` left the stray running."""
    from types import SimpleNamespace

    import start as start_module
    from services import blender_mcp_manager as bm

    env = {**_blender_pool_env(tmp_path, 1), "BLENDER_MCP_LOCALHOST_PORT": "9901"}
    base = bm.manager_from_env(env)
    stray = bm._pool_member(base, 1)
    for member, pid, port in ((base, 1111, 9900), (stray, 2222, 9901)):
        member.state_dir.mkdir(parents=True, exist_ok=True)
        member.pid_file.write_text(f"{pid}\n")
        member.launch_file.write_text(json.dumps({"pid": pid, "port": port, "bind": "127.0.0.1"}))
    monkeypatch.setattr(bm.BlenderMcpManager, "preflight", lambda self: SimpleNamespace(ok=True, checks=[]))
    monkeypatch.setattr(bm.BlenderMcpManager, "_pid_alive", staticmethod(lambda pid: True))
    monkeypatch.setattr(bm.BlenderMcpManager, "_managed_process_alive", lambda self, pid: True)
    monkeypatch.setattr(bm.BlenderMcpManager, "_pid_is_stranger", lambda self, pid: False)
    monkeypatch.setattr(bm.BlenderMcpManager, "_port_in_use", lambda self: self.port in {9900, 9901})
    assert start_module._managed_host_pool_blocker(bm, env, "Blender MCP") is None

    calls = []
    monkeypatch.setattr(start_module, "_blender_mcp_manager", lambda: bm.manager_from_env(env))
    monkeypatch.setattr(bm.BlenderMcpManager, "stop", lambda self: calls.append(("stop", self.pool_index)) or True)
    monkeypatch.setattr(bm.BlenderMcpManager, "ensure_running", lambda self: calls.append(("start", self.pool_index))
                        or (SimpleNamespace(to_dict=lambda: {"running": True}), True))
    assert CliRunner().invoke(start_module.main, ["blender-mcp", "start"]).exit_code == 0
    assert calls[0] == ("stop", 1) and ("start", 0) in calls


def test_a_cancelled_tui_says_what_the_phase_left(capsys) -> None:
    """Ctrl+C before the launch said containers keep running; after the
    wizard's cold cleanup it said no data was deleted."""
    from types import SimpleNamespace

    import start as start_module

    assert start_module._report_tui_exit(130, SimpleNamespace(tui_launch_started=False)) == 130
    out = capsys.readouterr().out
    assert "nothing was started" in out and "keep running" not in out
    cold = SimpleNamespace(tui_launch_started=True, tui_cold_cleanup_ran=True)
    start_module._report_tui_exit(130, cold)
    assert "--cold start was interrupted" in capsys.readouterr().out
    start_module._report_tui_exit(130, SimpleNamespace(), cold=True)  # CLI --cold
    assert "--cold start was interrupted" in capsys.readouterr().out


def test_wizard_overview_previews_a_typed_localhost_port() -> None:
    """The typed host port was ignored by the overview, which kept showing
    the .env port while the launch wrote the typed one."""
    from types import SimpleNamespace

    from ui.textual.screens.wizard_screen import WizardScreen

    screen = WizardScreen.__new__(WizardScreen)
    screen._selections = {"__secondary__:OLLAMA_LOCALHOST_PORT": "11500"}
    localhost = SimpleNamespace(secondary_number=SimpleNamespace(env_var="OLLAMA_LOCALHOST_PORT"))
    assert screen._typed_host_port(localhost) == "11500"
    assert screen._typed_host_port(SimpleNamespace(secondary_number=None)) == ""


@pytest.mark.parametrize("wid,ok", [("a" * 21, True), ("a" * 22, False)])
def test_n8n_workflow_ids_fit_n8n_id_column(tmp_path, wid, ok):
    """n8n stores workflow ids as varchar(36); a longer atlas-consumer-<id>
    failed the import, which the seed logs and exits 0 on, so the workflow
    was silently missing."""
    from core.consumer_manifest import ConsumerManifestError, _parse_n8n_workflows_block

    (tmp_path / "wf.json").write_text('{"name": "w", "nodes": [], "connections": {}}', encoding="utf-8")
    data = {"n8n_workflows": {"version": 1, "workflows": [{"id": wid, "path": "wf.json"}]}}
    manifest = tmp_path / "atlas.consumer.yml"
    if ok:
        _parse_n8n_workflows_block(data, "acme", tmp_path, manifest)
    else:
        with pytest.raises(ConsumerManifestError, match="too long"):
            _parse_n8n_workflows_block(data, "acme", tmp_path, manifest)
