from __future__ import annotations

import json
import sys
from pathlib import Path

from click.testing import CliRunner
from tests.three_surface_test_utils import surface_text


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))
REUSING_ATLAS = REPO_ROOT / "docs" / "deployment" / "reusing-atlas.md"


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
    _write_base_env(tmp_path)  # WEAVIATE_URL unset + on_unavailable=fail → warn
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
    assert "WEAVIATE_URL" in " ".join(checks["rag-ingestion-profiles"]["details"]["warnings"])
    assert payload["ok"] is True


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
    _write_base_env(tmp_path, extra="LIGHTRAG_ENDPOINT=http://lightrag:9621\n")
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
    _write_base_env(tmp_path)  # no LIGHTRAG_ENDPOINT → warn
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
    assert "LIGHTRAG_ENDPOINT" in checks["lightrag-query-profiles"]["message"]
    assert payload["ok"] is True  # warn does not fail the run


def test_doctor_lightrag_profiles_reports_alias(tmp_path, monkeypatch) -> None:
    import start as start_module

    manifest = _write_lightrag_profile_consumer(
        tmp_path, "rag-showcase", alias="graph-rag-hybrid"
    )
    _write_base_env(tmp_path, extra="LIGHTRAG_ENDPOINT=http://lightrag:9621\n")
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
        surface_text("docs/operations.md", "site"),
        surface_text("docs/operations.md", "wiki"),
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

    class _Starter:
        def __init__(self, env, project):
            self.config_parser = _CP(env, project)

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
