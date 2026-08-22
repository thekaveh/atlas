from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from tests.three_surface_test_utils import surface_text


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))
REUSING_ATLAS = REPO_ROOT / "docs" / "deployment" / "reusing-atlas.md"


def _write_minimal_root(root: Path) -> None:
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (root / "services").mkdir(exist_ok=True)
    (root / ".env.example").write_text(
        "PROJECT_NAME=atlas\n"
        "BRAND_NAME=Atlas\n"
        "BRAND_TAGLINE=Atlas stack\n"
        "WEAVIATE_MEMORY_LIMIT=2g\n"
        "OLLAMA_CUSTOM_MODELS=\n"
        "COMFYUI_CUSTOM_MODELS_FILE=/custom-models.yaml\n",
        encoding="utf-8",
    )
    (root / ".env").write_text(
        "PROJECT_NAME=atlas\n"
        "BRAND_NAME=Atlas\n"
        "BRAND_TAGLINE=Atlas stack\n"
        "WEAVIATE_MEMORY_LIMIT=2g\n"
        "OLLAMA_CUSTOM_MODELS=\n"
        "COMFYUI_CUSTOM_MODELS_FILE=/custom-models.yaml\n",
        encoding="utf-8",
    )


def _write_consumer(
    root: Path,
    name: str,
    *,
    project_name: str = "showcase",
    include_brand: bool = True,
    extra: str = "",
) -> Path:
    consumer = root / name
    (consumer / "compose").mkdir(parents=True)
    (consumer / "models").mkdir()
    (consumer / "backend" / "plugins").mkdir(parents=True)
    (consumer / "compose" / "overlay.yml").write_text(
        "services:\n  demo:\n    image: alpine:3.20\n",
        encoding="utf-8",
    )
    (consumer / "models" / "custom-models.yaml").write_text("models: []\n", encoding="utf-8")
    (consumer / "atlas.env.user").write_text(
        "WEAVIATE_MEMORY_LIMIT=4g\n",
        encoding="utf-8",
    )
    manifest = consumer / "atlas.consumer.yml"
    brand_block = (
        f"""brand:
  name: {name.title()}
  tagline: "{name} consumer"
"""
        if include_brand
        else ""
    )
    manifest.write_text(
        f"""
project_name: {project_name}
{brand_block}name: {name}
env:
  file: ./atlas.env.user
  values:
    EXTRA_CONSUMER_VALUE: enabled
compose_overlays:
  - ./compose/overlay.yml
backend_plugins:
  - ./backend/plugins
model_sidecars:
  comfyui:
    - ./models/custom-models.yaml
  ollama:
    - llama3.2:latest
{extra}
""",
        encoding="utf-8",
    )
    return manifest


def test_consumer_manifest_resolves_paths_and_env_from_manifest_dir(tmp_path: Path) -> None:
    from core.consumer_manifest import load_consumer_config

    _write_minimal_root(tmp_path)
    manifest = _write_consumer(tmp_path, "rag-showcase")

    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])

    assert config.env_overrides["PROJECT_NAME"] == "showcase"
    assert config.env_overrides["BRAND_NAME"] == "Rag-Showcase"
    assert config.env_overrides["BRAND_TAGLINE"] == "rag-showcase consumer"
    assert config.env_overrides["WEAVIATE_MEMORY_LIMIT"] == "4g"
    assert config.env_overrides["EXTRA_CONSUMER_VALUE"] == "enabled"
    assert config.env_overrides["BACKEND_PLUGINS_DIR"] == str(
        manifest.parent / "backend" / "plugins"
    )
    assert config.compose_overlays == [manifest.parent / "compose" / "overlay.yml"]
    assert config.consumers[0].name == "rag-showcase"


def test_unknown_top_level_key_raises_and_names_it(tmp_path: Path) -> None:
    """#649: a typo'd top-level key (the ticket's `compose_overlay` missing the
    trailing `s`) must fail loudly instead of being silently ignored — naming
    the offending key, the allowed set, and the manifest origin."""
    from core.consumer_manifest import ConsumerManifestError, load_consumer_config

    _write_minimal_root(tmp_path)
    manifest = _write_consumer(
        tmp_path,
        "rag-showcase",
        extra="compose_overlay:\n  - ./compose/overlay.yml\n",
    )

    with pytest.raises(ConsumerManifestError) as excinfo:
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])

    message = str(excinfo.value)
    assert "unknown top-level key" in message
    assert "compose_overlay" in message  # names the offending typo
    assert "compose_overlays" in message  # shows the allowed key
    assert str(manifest) in message  # names the origin


def test_model_sidecar_typo_top_level_key_raises(tmp_path: Path) -> None:
    """A second typo the ticket calls out: `model_sidecar` (singular)."""
    from core.consumer_manifest import ConsumerManifestError, load_consumer_config

    _write_minimal_root(tmp_path)
    manifest = _write_consumer(
        tmp_path,
        "rag-showcase",
        extra="model_sidecar:\n  ollama:\n    - llama3.2:latest\n",
    )

    with pytest.raises(ConsumerManifestError) as excinfo:
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])

    assert "model_sidecar" in str(excinfo.value)


def test_no_allowed_key_is_rejected_by_top_level_guard(tmp_path: Path) -> None:
    """AC#3: none of the documented top-level keys trips the unknown-key guard.

    Exercising each key in isolation keeps the test independent of the strict
    nested-block schemas (which the per-block suites already cover): an empty
    value makes every block parser a no-op, so the ONLY thing that could raise
    is the top-level guard — which must never fire for an allowed key. If a key
    were dropped from the allow-set, this fails and names it."""
    from core.consumer_manifest import (
        _CONSUMER_ALLOWED_TOP_LEVEL_KEYS,
        ConsumerManifestError,
        load_consumer_config,
    )

    _write_minimal_root(tmp_path)
    for key in sorted(_CONSUMER_ALLOWED_TOP_LEVEL_KEYS):
        consumer = tmp_path / f"probe-{key}"
        consumer.mkdir()
        manifest = consumer / "atlas.consumer.yml"
        # `name` alone for the name-key probe; otherwise a probe name plus the
        # key under test with an empty (no-op) value.
        body = f"{key}:\n" if key == "name" else f"name: probe\n{key}:\n"
        manifest.write_text(body, encoding="utf-8")
        try:
            load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
        except ConsumerManifestError as exc:  # nested no-op errors are fine
            assert "unknown top-level key" not in str(exc), (
                f"allowed key {key!r} was rejected by the top-level guard: {exc}"
            )


def test_docker_manager_includes_manifest_overlays_without_user_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.docker_manager import DockerManager

    _write_minimal_root(tmp_path)
    manifest = _write_consumer(tmp_path, "parent")
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))

    manager = DockerManager(str(tmp_path))

    assert manager._compose_file_args() == [
        "-f",
        "docker-compose.yml",
        "-f",
        str(manifest.parent / "compose" / "overlay.yml"),
    ]
    assert not (tmp_path / "services" / "_user").exists()


def test_consumer_manifest_cli_option_reaches_compose_validate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import start as start_module

    _write_minimal_root(tmp_path)
    manifest = _write_consumer(tmp_path, "cli")

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
    monkeypatch.setattr(
        start_module.DockerManager,
        "detect_docker_compose_command",
        lambda self: "docker compose",
    )

    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return Result()

    monkeypatch.setattr(start_module.subprocess, "run", fake_run)
    monkeypatch.setattr("core.docker_manager.run_with_deadline", fake_run)

    result = CliRunner().invoke(
        start_module.main,
        ["--consumer", str(manifest), "compose", "validate"],
    )

    assert result.exit_code == 0, result.output
    assert str(manifest.parent / "compose" / "overlay.yml") in calls[0]


def test_consumer_manifest_unions_list_values_and_fails_scalar_conflicts(
    tmp_path: Path,
) -> None:
    from core.consumer_manifest import ConsumerManifestError, load_consumer_config

    _write_minimal_root(tmp_path)
    one = _write_consumer(tmp_path, "one", project_name="shared", include_brand=False)
    two = _write_consumer(
        tmp_path,
        "two",
        project_name="shared",
        include_brand=False,
        extra="""
model_sidecars:
  comfyui:
    - ./models/custom-models.yaml
  ollama:
    - llama3.2:latest
    - qwen2.5:latest
""",
    )

    config = load_consumer_config(tmp_path, explicit_paths=[str(one), str(two)])

    assert config.env_overrides["OLLAMA_CUSTOM_MODELS"] == "llama3.2:latest,qwen2.5:latest"
    comfyui_paths = config.env_overrides["COMFYUI_CUSTOM_MODELS_FILE"].split(os.pathsep)
    assert comfyui_paths == [
        str(one.parent / "models" / "custom-models.yaml"),
        str(two.parent / "models" / "custom-models.yaml"),
    ]

    conflicting = _write_consumer(tmp_path, "conflicting", project_name="other")
    with pytest.raises(ConsumerManifestError, match="PROJECT_NAME"):
        load_consumer_config(tmp_path, explicit_paths=[str(one), str(conflicting)])


def test_starter_applies_consumer_manifest_env_on_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import start as start_module

    _write_minimal_root(tmp_path)
    manifest = _write_consumer(tmp_path, "tableau")
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))

    starter = start_module.AtlasStarter()
    starter.config_parser.root_dir = tmp_path
    starter.config_parser.env_file_path = tmp_path / ".env"
    starter.config_parser.env_example_path = tmp_path / ".env.example"
    starter.docker_manager.root_dir = tmp_path
    starter.docker_manager.config_parser.root_dir = tmp_path
    starter.docker_manager.config_parser.env_file_path = tmp_path / ".env"
    starter.docker_manager.config_parser.env_example_path = tmp_path / ".env.example"

    assert starter.setup_env_file(cold_start=False)

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "PROJECT_NAME=showcase" in env_text
    assert "BRAND_NAME=Tableau" in env_text
    assert "WEAVIATE_MEMORY_LIMIT=4g" in env_text
    assert f"BACKEND_PLUGINS_DIR={manifest.parent / 'backend' / 'plugins'}" in env_text


def test_app_state_lists_registered_consumers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.config_parser import ConfigParser
    from ui.state_builder import build_app_state

    _write_minimal_root(tmp_path)
    manifest = _write_consumer(tmp_path, "daydreams")
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))

    parser = ConfigParser(str(tmp_path))
    state = build_app_state(parser)

    assert [consumer.name for consumer in state.consumers] == ["daydreams"]
    assert state.consumers[0].compose_overlays == [str(manifest.parent / "compose" / "overlay.yml")]
    assert state.consumers[0].backend_plugins == [str(manifest.parent / "backend" / "plugins")]


def test_consumer_manifest_docs_are_published_on_all_surfaces() -> None:
    reusing = REUSING_ATLAS.read_text(encoding="utf-8")
    assert "atlas.consumer.yml" in reusing
    assert "./start.sh --consumer" in reusing
    assert "scalar conflicts" in reusing

    for text in (
        surface_text("docs/development.md", "site"),
        surface_text("docs/development.md", "wiki"),
    ):
        assert "atlas.consumer.yml" in text
        assert "--consumer" in text


def _patch_starter_root(start_module, monkeypatch, tmp_path: Path) -> None:
    """Route a freshly-constructed AtlasStarter at tmp_path (repo-root fixture)."""
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


def test_materialize_consumer_env_for_preflight_writes_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#451 helper: consumer env_overrides are persisted into .env, quietly."""
    import start as start_module

    _write_minimal_root(tmp_path)
    manifest = _write_consumer(tmp_path, "preflight")
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    _patch_starter_root(start_module, monkeypatch, tmp_path)

    starter = start_module.AtlasStarter()
    applied = starter.materialize_consumer_env_for_preflight()

    assert applied["BACKEND_PLUGINS_DIR"] == str(manifest.parent / "backend" / "plugins")
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"BACKEND_PLUGINS_DIR={manifest.parent / 'backend' / 'plugins'}" in env_text
    assert (
        f"COMFYUI_CUSTOM_MODELS_FILE={manifest.parent / 'models' / 'custom-models.yaml'}"
        in env_text
    )


def test_materialize_consumer_env_for_preflight_noop_without_env_or_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#451 helper: no .env → no-op (never creates one); no manifest → no writes."""
    import start as start_module

    _write_minimal_root(tmp_path)
    monkeypatch.delenv("ATLAS_CONSUMER_MANIFEST", raising=False)
    _patch_starter_root(start_module, monkeypatch, tmp_path)

    # No manifest registered → .env unchanged.
    before = (tmp_path / ".env").read_text(encoding="utf-8")
    starter = start_module.AtlasStarter()
    assert starter.materialize_consumer_env_for_preflight() == {}
    assert (tmp_path / ".env").read_text(encoding="utf-8") == before

    # Fresh checkout with no .env at all → no-op, and no .env is created.
    (tmp_path / ".env").unlink()
    manifest = _write_consumer(tmp_path, "noenv")
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))
    starter = start_module.AtlasStarter()
    assert starter.materialize_consumer_env_for_preflight() == {}
    assert not (tmp_path / ".env").exists()


def test_compose_validate_materializes_consumer_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#451: `compose validate --consumer` persists the derived env before
    invoking the compose subprocess, so `${BACKEND_PLUGINS_DIR}`-interpolating
    overlays resolve on a fresh checkout that never ran a full start."""
    import start as start_module

    _write_minimal_root(tmp_path)
    manifest = _write_consumer(tmp_path, "validate")
    _patch_starter_root(start_module, monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "detect_docker_compose_command",
        lambda self: "docker compose",
    )

    env_at_call: dict[str, str] = {}

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **_kwargs):
        env_at_call["env_text"] = (tmp_path / ".env").read_text(encoding="utf-8")
        return Result()

    monkeypatch.setattr(start_module.subprocess, "run", fake_run)
    monkeypatch.setattr("core.docker_manager.run_with_deadline", fake_run)

    result = CliRunner().invoke(
        start_module.main,
        ["--consumer", str(manifest), "compose", "validate"],
    )

    assert result.exit_code == 0, result.output
    # The derived env was already in .env when the compose subprocess ran.
    assert (
        f"BACKEND_PLUGINS_DIR={manifest.parent / 'backend' / 'plugins'}"
        in env_at_call["env_text"]
    )


def test_doctor_materializes_consumer_env_and_keeps_json_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#451: `doctor --format json` persists the derived env (so its compose
    check validates the same config a real start produces) while stdout stays
    pure JSON (no banner noise from the quiet helper)."""
    import json as json_module

    import start as start_module

    _write_minimal_root(tmp_path)
    manifest = _write_consumer(tmp_path, "doctor")
    _patch_starter_root(start_module, monkeypatch, tmp_path)
    monkeypatch.setattr(
        start_module.DockerManager,
        "detect_docker_compose_command",
        lambda self: "docker compose",
    )

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        start_module.subprocess, "run", lambda cmd, **_kwargs: Result()
    )

    result = CliRunner().invoke(
        start_module.main,
        ["--consumer", str(manifest), "doctor", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"BACKEND_PLUGINS_DIR={manifest.parent / 'backend' / 'plugins'}" in env_text
    # stdout must be parseable JSON — the quiet helper adds no banner lines.
    payload = json_module.loads(result.output)
    assert payload["ok"] is True


def _write_conditional_consumer(root: Path, env_values_yaml: str, name: str = "gated") -> Path:
    """Write a minimal valid consumer manifest with a custom env.values block."""
    consumer = root / name
    (consumer / "compose").mkdir(parents=True)
    (consumer / "backend" / "plugins").mkdir(parents=True)
    (consumer / "compose" / "overlay.yml").write_text(
        "services:\n  demo:\n    image: alpine:3.20\n", encoding="utf-8"
    )
    manifest = consumer / "atlas.consumer.yml"
    manifest.write_text(
        f"project_name: {name}\nname: {name}\nenv:\n  values:\n{env_values_yaml}"
        "compose_overlays:\n  - ./compose/overlay.yml\n"
        "backend_plugins:\n  - ./backend/plugins\n",
        encoding="utf-8",
    )
    return manifest


def test_env_values_conditional_enabled_if_env(tmp_path: Path, monkeypatch) -> None:
    """#722: a key-gated env value resolves to 'enabled' when the named env var is
    set+non-empty, and to `else` when unset/empty — so a consumer can gate a paid
    provider on its key without a wrapper script."""
    from core.consumer_manifest import load_consumer_config

    _write_minimal_root(tmp_path)
    manifest = _write_conditional_consumer(
        tmp_path,
        "    FAL_SOURCE:\n      enabled_if_env: FAL_API_KEY\n      else: disabled\n",
    )

    monkeypatch.setenv("FAL_API_KEY", "fal-xxx")
    cfg = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert cfg.env_overrides["FAL_SOURCE"] == "enabled"

    monkeypatch.delenv("FAL_API_KEY", raising=False)
    cfg = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert cfg.env_overrides["FAL_SOURCE"] == "disabled"

    monkeypatch.setenv("FAL_API_KEY", "   ")  # present-but-blank == absent
    cfg = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert cfg.env_overrides["FAL_SOURCE"] == "disabled"


def test_env_values_conditional_explicit_then(tmp_path: Path, monkeypatch) -> None:
    from core.consumer_manifest import load_consumer_config

    _write_minimal_root(tmp_path)
    manifest = _write_conditional_consumer(
        tmp_path,
        "    COMFYUI_SOURCE:\n      enabled_if_env: HAS_GPU\n"
        "      then: container-gpu\n      else: container-cpu\n",
    )
    monkeypatch.setenv("HAS_GPU", "1")
    cfg = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert cfg.env_overrides["COMFYUI_SOURCE"] == "container-gpu"
    monkeypatch.delenv("HAS_GPU", raising=False)
    cfg = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert cfg.env_overrides["COMFYUI_SOURCE"] == "container-cpu"


def test_env_values_conditional_malformed_raises(tmp_path: Path) -> None:
    """#722 AC2: malformed conditional forms fail loudly."""
    from core.consumer_manifest import ConsumerManifestError, load_consumer_config

    _write_minimal_root(tmp_path)
    cases = {
        "noelse": "    FAL_SOURCE:\n      enabled_if_env: FAL_API_KEY\n",
        "unknown": "    FAL_SOURCE:\n      enabled_if_env: FAL_API_KEY\n      else: disabled\n      bogus: x\n",
        "badvar": "    FAL_SOURCE:\n      enabled_if_env: fal-api-key\n      else: disabled\n",
    }
    for name, block in cases.items():
        manifest = _write_conditional_consumer(tmp_path, block, name=name)
        with pytest.raises(ConsumerManifestError):
            load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


# ---- custom_nodes.comfyui (#905) --------------------------------------------
#
# Mirror of model_sidecars.comfyui: a consumer declares ComfyUI custom-node
# files (same schema as services/comfyui/custom-nodes.yaml). Atlas strict-parses
# them at load time, joins the resolved paths into COMFYUI_CUSTOM_NODES_FILE
# (os.pathsep-joined, appended after the always-present Atlas allowlist), and
# rejects names that collide with Atlas-shipped nodes or diverge across consumers.

_VALID_CONSUMER_NODE_YAML = (
    "custom_nodes:\n"
    "  - name: ComfyUI-ConsumerDemo\n"
    "    repo: https://github.com/consumer/ComfyUI-ConsumerDemo.git\n"
    "    ref: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
)


def _write_consumer_with_nodes(
    root: Path,
    name: str,
    *,
    nodes_yaml: str = _VALID_CONSUMER_NODE_YAML,
    project_name: str = "showcase",
    include_brand: bool = True,
) -> Path:
    """Write a consumer manifest that declares a ``custom_nodes.comfyui`` file.

    The referenced ./nodes.yaml is materialized from ``nodes_yaml`` so the
    resolver + strict parser have a real file to read (mirror of how
    ``_write_consumer`` materializes ./models/custom-models.yaml for the
    model_sidecars.comfyui block)."""
    manifest = _write_consumer(
        root,
        name,
        project_name=project_name,
        include_brand=include_brand,
        extra="custom_nodes:\n  comfyui:\n    - ./nodes.yaml\n",
    )
    (manifest.parent / "nodes.yaml").write_text(nodes_yaml, encoding="utf-8")
    return manifest


def test_custom_nodes_comfyui_sets_env_overrides_path(tmp_path: Path) -> None:
    """A top-level ``custom_nodes.comfyui`` block resolves the listed file(s) and
    joins them into env_overrides['COMFYUI_CUSTOM_NODES_FILE'] via os.pathsep —
    mirroring how model_sidecars.comfyui populates COMFYUI_CUSTOM_MODELS_FILE."""
    from core.consumer_manifest import load_consumer_config

    _write_minimal_root(tmp_path)
    manifest = _write_consumer_with_nodes(tmp_path, "rag-showcase")

    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])

    nodes_paths = config.env_overrides["COMFYUI_CUSTOM_NODES_FILE"].split(os.pathsep)
    assert nodes_paths == [str(manifest.parent / "nodes.yaml")]


def test_custom_nodes_missing_file_raises(tmp_path: Path) -> None:
    """A ``custom_nodes.comfyui`` path that does not resolve to a real file fails
    loud at load time via _resolve_existing_file (a dropped consumer node file
    must never silently degrade into a missing workflow node at runtime)."""
    from core.consumer_manifest import ConsumerManifestError, load_consumer_config

    _write_minimal_root(tmp_path)
    # Reuse the helper to write a valid manifest, then DELETE the referenced
    # nodes.yaml so the resolver cannot find it on the next load.
    manifest = _write_consumer_with_nodes(tmp_path, "missing")
    (manifest.parent / "nodes.yaml").unlink()

    with pytest.raises(ConsumerManifestError, match="does not exist or is not a file"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_custom_node_name_collides_with_atlas_shipped_node(tmp_path: Path) -> None:
    """A consumer node whose name matches an Atlas-shipped node (here
    ComfyUI-GGUF from services/comfyui/custom-nodes.yaml) is rejected: Atlas
    wins on name collision because catalog models reference its nodes via
    requires_custom_node. load_custom_nodes({}) reads the real Atlas file."""
    from core.consumer_manifest import ConsumerManifestError, load_consumer_config

    _write_minimal_root(tmp_path)
    collider_yaml = (
        "custom_nodes:\n"
        "  - name: ComfyUI-GGUF\n"
        "    repo: https://github.com/consumer/ComfyUI-GGUF-fork.git\n"
        "    ref: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n"
    )
    manifest = _write_consumer_with_nodes(tmp_path, "collider", nodes_yaml=collider_yaml)

    with pytest.raises(ConsumerManifestError, match="collides with an Atlas-shipped"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_custom_nodes_divergent_repo_ref_across_consumers_raises(tmp_path: Path) -> None:
    """Two consumers declaring the same node name with DIVERGENT repo/ref raise;
    identical repo+ref does NOT (benign multi-consumer reuse dedupes)."""
    from core.consumer_manifest import ConsumerManifestError, load_consumer_config

    _write_minimal_root(tmp_path)
    sha_a = "a" * 40
    sha_b = "b" * 40
    same_yaml = (
        "custom_nodes:\n"
        "  - name: ComfyUI-SharedNode\n"
        "    repo: https://github.com/consumer/ComfyUI-SharedNode.git\n"
        f"    ref: {sha_a}\n"
    )
    divergent_yaml = (
        "custom_nodes:\n"
        "  - name: ComfyUI-SharedNode\n"
        "    repo: https://github.com/consumer/ComfyUI-SharedNode.git\n"
        f"    ref: {sha_b}\n"
    )

    one = _write_consumer_with_nodes(
        tmp_path, "one", nodes_yaml=same_yaml, project_name="shared", include_brand=False
    )
    two_same = _write_consumer_with_nodes(
        tmp_path, "two-same", nodes_yaml=same_yaml, project_name="shared", include_brand=False
    )
    # Identical repo+ref does NOT raise — both node files make it into the env join.
    config = load_consumer_config(tmp_path, explicit_paths=[str(one), str(two_same)])
    assert config.env_overrides["COMFYUI_CUSTOM_NODES_FILE"].count(os.pathsep) == 1

    two_diff = _write_consumer_with_nodes(
        tmp_path, "two-diff", nodes_yaml=divergent_yaml, project_name="shared", include_brand=False
    )
    with pytest.raises(ConsumerManifestError, match="divergent repo/ref"):
        load_consumer_config(tmp_path, explicit_paths=[str(one), str(two_diff)])


def test_consumer_record_carries_comfyui_custom_node_files(tmp_path: Path) -> None:
    """ConsumerRecord.comfyui_custom_node_files holds the per-consumer resolved
    paths (mirror of comfyui_sidecars on the same dataclass)."""
    from core.consumer_manifest import load_consumer_config

    _write_minimal_root(tmp_path)
    manifest = _write_consumer_with_nodes(tmp_path, "record")

    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])

    assert config.consumers[0].comfyui_custom_node_files == (
        manifest.parent / "nodes.yaml",
    )


def test_multi_line_env_value_is_rejected_not_written_into_dotenv(tmp_path: Path) -> None:
    """`.env` is line-oriented and resolved last-wins.

    A YAML block scalar is the natural way to write a newline by accident, and
    the value is emitted as `KEY=value`, so a newline appends further
    assignments. A consumer could land a second `SUPABASE_SERVICE_KEY` below the
    generated one and win. It is permanent, too: the rewrite pattern
    `^KEY=.*$` is MULTILINE but not DOTALL, so a later run rewrites only the
    first line and steps over the injected remainder.
    """
    from core.consumer_manifest import ConsumerManifestError, load_consumer_config

    _write_minimal_root(tmp_path)
    manifest = _write_consumer(
        tmp_path,
        "injector",
        extra=(
            "  # a block scalar smuggling a second assignment\n"
        ),
    )
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "    EXTRA_CONSUMER_VALUE: enabled\n",
            "    EXTRA_CONSUMER_VALUE: |-\n"
            "      enabled\n"
            "      SUPABASE_SERVICE_KEY=attacker-key\n",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConsumerManifestError) as excinfo:
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])

    message = str(excinfo.value)
    assert "EXTRA_CONSUMER_VALUE" in message
    assert "multi-line" in message


def test_env_override_writer_refuses_a_multi_line_value(tmp_path: Path) -> None:
    """Second line of defence, at the write boundary.

    Not every override reaches `.env` through `_set_scalar` — derived keys and
    the brand/project paths build values directly — so the writer refuses a
    newline regardless of which path produced it.
    """
    from core.config_parser import ConfigParser
    from start import AtlasStarter

    env_file = tmp_path / ".env"
    env_file.write_text(
        "BASE_PORT=63000\nSUPABASE_SERVICE_KEY=generated-real-key\n",
        encoding="utf-8",
    )
    starter = AtlasStarter.__new__(AtlasStarter)
    starter.config_parser = ConfigParser(str(tmp_path))
    starter.config_parser.env_file_path = env_file

    with pytest.raises(ValueError, match="multi-line"):
        starter._merge_env_file_overrides(
            {"BRAND_TAGLINE": "Atlas\nSUPABASE_SERVICE_KEY=attacker-key"}
        )

    # The real key must be untouched and unduplicated.
    body = env_file.read_text(encoding="utf-8")
    assert body.count("SUPABASE_SERVICE_KEY=") == 1
    assert "attacker-key" not in body


def test_n8n_workflow_active_false_is_honoured_not_turned_into_fromJson(
    tmp_path: Path,
) -> None:
    """`active: false` is the one value that means "keep this workflow OFF".

    Unquoted it is a YAML boolean, and `False or "fromJson"` is "fromJson" —
    the policy that honours the workflow file's own `"active": true`, i.e. a
    live webhook the manifest explicitly asked to keep disabled. The asymmetry
    gives it away: `active: true` renders "True", which is not a policy and is
    loudly rejected.
    """
    from core.consumer_manifest import load_consumer_config

    _write_minimal_root(tmp_path)
    manifest = _write_consumer(tmp_path, "flows")
    workflow = tmp_path / "flows" / "wf.json"
    workflow.write_text('{"name": "wf", "active": true, "nodes": [], "connections": {}}\n',
                        encoding="utf-8")
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + "n8n_workflows:\n"
          "  version: 1\n"
          "  workflows:\n"
          "    - id: wf\n"
          "      path: ./wf.json\n"
          "      active: false\n",
        encoding="utf-8",
    )

    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    entry = config.n8n_workflows[0]
    assert entry.active == "false", (
        "an explicit `active: false` must stay false, not become the "
        "file-derived fromJson policy"
    )


def test_dev_and_default_profile_overrides_conflict_instead_of_racing(
    tmp_path: Path,
) -> None:
    """`dev` aliases `default`, so blocks for both target one profile.

    Bucketing by the raw name hid that from the conflict detector, leaving YAML
    key order to decide which value won.
    """
    from core.consumer_manifest import ConsumerManifestError, load_consumer_config

    _write_minimal_root(tmp_path)
    manifest = _write_consumer(tmp_path, "profiles")
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + "profile_overrides:\n"
          "  dev:\n"
          "    env:\n"
          "      LOG_MAX_SIZE: 1m\n"
          "  default:\n"
          "    env:\n"
          "      LOG_MAX_SIZE: 99m\n",
        encoding="utf-8",
    )

    with pytest.raises(ConsumerManifestError, match="conflicting"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_a_newline_in_an_env_KEY_is_rejected_too(tmp_path: Path) -> None:
    """The value guard is only half the job.

    The writer emits `KEY=VALUE`, so a newline in the KEY injects a second
    assignment exactly as effectively as one in the value — and last-wins
    makes it the effective value.
    """
    from core.consumer_manifest import ConsumerManifestError, load_consumer_config

    _write_minimal_root(tmp_path)
    manifest = _write_consumer(tmp_path, "keyinjector")
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "    EXTRA_CONSUMER_VALUE: enabled\n",
            '    "HARMLESS_LOOKING\\nSUPABASE_SERVICE_KEY": attacker-key\n',
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConsumerManifestError, match="not a valid environment variable name"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_env_override_writer_refuses_a_malformed_key(tmp_path: Path) -> None:
    from core.config_parser import ConfigParser
    from start import AtlasStarter

    env_file = tmp_path / ".env"
    env_file.write_text("SUPABASE_SERVICE_KEY=generated-real-key\n", encoding="utf-8")
    starter = AtlasStarter.__new__(AtlasStarter)
    starter.config_parser = ConfigParser(str(tmp_path))
    starter.config_parser.env_file_path = env_file

    for bad in ("HARMLESS\nSUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_KEY=x #", "HAS SPACE"):
        with pytest.raises(ValueError, match="not a valid environment variable name"):
            starter._merge_env_file_overrides({bad: "attacker-key"})

    body = env_file.read_text(encoding="utf-8")
    assert body.count("SUPABASE_SERVICE_KEY=") == 1
    assert "attacker-key" not in body


def test_env_override_writer_coerces_a_non_string_value(tmp_path: Path) -> None:
    """An int override used to be coerced silently by the f-string.

    Guarding the raw value directly would turn that into an unhandled
    TypeError instead of a clean write.
    """
    from core.config_parser import ConfigParser
    from start import AtlasStarter

    env_file = tmp_path / ".env"
    env_file.write_text("BASE_PORT=63000\n", encoding="utf-8")
    starter = AtlasStarter.__new__(AtlasStarter)
    starter.config_parser = ConfigParser(str(tmp_path))
    starter.config_parser.env_file_path = env_file

    starter._merge_env_file_overrides({"BASE_PORT": 64000})
    assert "BASE_PORT=64000\n" in env_file.read_text(encoding="utf-8")


def test_env_overlay_parser_agrees_with_the_canonical_env_reader(tmp_path: Path) -> None:
    """`env: {file: ...}` and `.env` must parse identically.

    They are two readers of the same format, and `_read_env_overlay` runs
    BEFORE the write guard — so any disagreement is a way to smuggle a value
    past a check the other path applies. Line splitting was one such
    disagreement: an overlay containing `A=x\\x0bSUPABASE_SERVICE_KEY=y` yielded
    two keys through the overlay reader and one through `parse_env_file`.
    """
    from core.config_parser import ConfigParser
    from core.consumer_manifest import _read_env_overlay

    samples = [
        "A=1\n",
        'A="1"\n',
        "A='1'\n",
        "A=1 # note\n",
        "  A=1\n",
        "A = 1\n",
        "A=\n",
        "A=b=c\n",
        "A=1\nA=2\n",
        "A=p#ss\n",
        "A=1   \n",
        "A=1\nB=2",
        "",
        # the separators that split under `splitlines()` but not for the reader
        "A=x\x0bSUPABASE_SERVICE_KEY=y\n",
        "A=x\x0cB=y\n",
        "A=x\x85B=y\n",
        "A=x B=y\n",
    ]
    for text in samples:
        overlay = tmp_path / "overlay.env"
        overlay.write_text(text, encoding="utf-8")
        env = tmp_path / ".env"
        env.write_text(text, encoding="utf-8")

        parser = ConfigParser(str(tmp_path))
        parser.env_file_path = env

        assert _read_env_overlay(overlay) == parser.parse_env_file(), repr(text)


# ── pass 15: malformed / null / falsy consumer input ─────────────────


@pytest.mark.parametrize("shape", [
    "    env: notamap\n",
    "    sources: notamap\n",
    "    env:\n      - FOO=1\n",
    "    sources:\n      - FOO=1\n",
])
def test_a_malformed_profile_overrides_block_is_a_manifest_error(tmp_path, shape):
    """It escaped as a raw AttributeError out of `./start.sh`.

    `_parse_bundle` does `(raw.get("env") or {}).items()` with no type check,
    and the `except ProfileConfigError` around the load-time validation is the
    wrong net. That call exists precisely so a typo fails at manifest load
    rather than at profile-apply time — an unhandled traceback is not that, and
    `load_consumer_config` is unguarded at its call site.
    """
    from core.consumer_manifest import ConsumerManifestError, load_consumer_config

    _write_minimal_root(tmp_path)
    manifest = _write_consumer(tmp_path, "malformed")
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "profile_overrides:\n  prod:\n" + shape,
        encoding="utf-8",
    )

    with pytest.raises(ConsumerManifestError, match="malformed|mapping"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_a_non_utf8_env_file_is_a_manifest_error(tmp_path):
    """Every neighbouring failure mode is a clean ConsumerManifestError."""
    from core.consumer_manifest import ConsumerManifestError, load_consumer_config

    _write_minimal_root(tmp_path)
    manifest = _write_consumer(tmp_path, "latin1")
    (tmp_path / "latin1" / "atlas.env.user").write_bytes(b"FOO=caf\xe9\n")

    with pytest.raises(ConsumerManifestError, match="not valid UTF-8"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_a_yaml_null_env_value_is_empty_not_the_string_None(tmp_path):
    """`env.values: {FOO: null}` wrote the literal `FOO=None` into .env.

    Both sibling paths already handled it — the `profile_overrides` scalar
    branch and the `brand` block — so this was an inconsistency, not a policy.
    """
    from core.consumer_manifest import load_consumer_config

    _write_minimal_root(tmp_path)
    manifest = _write_consumer(tmp_path, "nulls")
    # extend the template's existing `env.values` block
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "    EXTRA_CONSUMER_VALUE: enabled\n",
            "    EXTRA_CONSUMER_VALUE: enabled\n    NULLED: null\n    TRUTHY: true\n",
            1,
        ),
        encoding="utf-8",
    )

    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert config.env_overrides["NULLED"] == ""
    assert config.env_overrides["TRUTHY"] == "True"


def test_a_null_profile_override_leaf_is_empty_not_the_string_None(tmp_path):
    """The accumulated block used `str(v)`; the raw block used `"" if None`.

    The load-time validation path and the apply-time path therefore disagreed
    about the same manifest.
    """
    from core.consumer_manifest import load_consumer_config

    _write_minimal_root(tmp_path)
    manifest = _write_consumer(tmp_path, "nullleaf")
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + "profile_overrides:\n  prod:\n    env:\n      NULLED: null\n",
        encoding="utf-8",
    )

    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert config.profile_overrides["prod"]["env"]["NULLED"] == ""
