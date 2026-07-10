from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))
REUSING_ATLAS = REPO_ROOT / "docs" / "deployment" / "reusing-atlas.md"
SITE_DEVELOPMENT = REPO_ROOT / "docs" / "site" / "development.md"
WIKI_DEVELOPMENT = REPO_ROOT / "docs" / "wiki" / "Development.md"


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

    for path in (SITE_DEVELOPMENT, WIKI_DEVELOPMENT):
        text = path.read_text(encoding="utf-8")
        assert "atlas.consumer.yml" in text
        assert "--consumer" in text
