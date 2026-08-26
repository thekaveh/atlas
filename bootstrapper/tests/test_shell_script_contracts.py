from __future__ import annotations

import json
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_weaviate_generated_env_quotes_embedding_model() -> None:
    script = (
        REPO_ROOT / "services" / "weaviate" / "init" / "scripts" / "init-weaviate.sh"
    ).read_text(encoding="utf-8")

    assert "quote_shell_env_value()" in script
    assert "LITELLM_EMBEDDING_MODEL=$model" not in script
    assert "LITELLM_EMBEDDING_MODEL=$(quote_shell_env_value \"$model\")" in script


def test_n8n_install_nodes_uses_locked_prestart_install() -> None:
    script = (
        REPO_ROOT / "services" / "n8n" / "init" / "scripts" / "install-nodes.sh"
    ).read_text(encoding="utf-8")

    assert "/rest/community-packages" not in script
    assert "npm ci" in script
    assert "--ignore-scripts" in script
    assert "package-lock.json" in script


def test_n8n_custom_node_specs_must_be_exactly_versioned() -> None:
    script = (
        REPO_ROOT / "services" / "n8n" / "init" / "scripts" / "install-nodes.sh"
    ).read_text(encoding="utf-8")

    assert "must pin an exact version" in script
    assert "validate_exact_spec" in script


def test_n8n_init_completes_before_runtime_starts() -> None:
    compose = yaml.safe_load(
        (REPO_ROOT / "services" / "n8n" / "compose.yml").read_text(
            encoding="utf-8"
        )
    )["services"]

    assert compose["n8n"]["depends_on"]["n8n-init"] == {
        "condition": "service_completed_successfully"
    }
    assert "depends_on" not in compose["n8n-init"]
    assert compose["n8n-init"]["volumes"][-1] == "n8n-data:/home/node/.n8n"


def test_n8n_default_community_packages_are_exactly_locked() -> None:
    config = REPO_ROOT / "services" / "n8n" / "init" / "config"
    package = json.loads((config / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((config / "package-lock.json").read_text(encoding="utf-8"))

    assert lock["packages"][""]["dependencies"] == package["dependencies"]
    assert package["dependencies"]["n8n-workflow"] == "2.36.3"
    for path, metadata in lock["packages"].items():
        if not path or metadata.get("link"):
            continue
        assert metadata.get("version")
        assert metadata.get("integrity"), path


def test_local_deep_researcher_litellm_poll_has_per_attempt_timeout() -> None:
    script = (
        REPO_ROOT
        / "services"
        / "local-deep-researcher"
        / "build"
        / "scripts"
        / "docker-entrypoint.sh"
    ).read_text(encoding="utf-8")

    assert (
        'curl -s --fail --max-time 5 "$LITELLM_URL/health/liveliness"'
        in script
    )


def test_local_deep_researcher_patches_litellm_provider_before_config() -> None:
    script = (
        REPO_ROOT
        / "services"
        / "local-deep-researcher"
        / "build"
        / "scripts"
        / "docker-entrypoint.sh"
    ).read_text(encoding="utf-8")

    assert "patch-litellm-openai-provider.py" in script
    assert script.index("patch-litellm-openai-provider.py") < script.index("init-config.py")


def test_local_deep_researcher_installs_pyproject_as_project() -> None:
    script = (
        REPO_ROOT
        / "services"
        / "local-deep-researcher"
        / "build"
        / "scripts"
        / "docker-entrypoint.sh"
    ).read_text(encoding="utf-8")

    assert (
        'uv pip sync --python "$VENV_PYTHON" --require-hashes "$RUNTIME_LOCK"'
        in script
    )
    assert (
        'uv pip install --python "$VENV_PYTHON" --no-deps --no-build-isolation -e /app'
        in script
    )
    assert "uv pip install --system" not in script


def test_local_deep_researcher_uses_pinned_source_and_cli() -> None:
    script = (
        REPO_ROOT
        / "services"
        / "local-deep-researcher"
        / "build"
        / "scripts"
        / "docker-entrypoint.sh"
    ).read_text(encoding="utf-8")
    runtime_lib = (
        REPO_ROOT
        / "services/local-deep-researcher/build/scripts/runtime-lib.sh"
    ).read_text(encoding="utf-8")

    assert 'LOCAL_DEEP_RESEARCHER_REF:?LOCAL_DEEP_RESEARCHER_REF is required' in script
    assert 'LOCAL_DEEP_RESEARCHER_LANGGRAPH_CLI_VERSION:?' in script
    assert 'LOCAL_DEEP_RESEARCHER_UPSTREAM_LOCK_SHA256:?' in script
    assert 'git -C "$REPO_DIR" pull' not in script
    assert 'git -C "$REPO_DIR" fetch --depth 1 origin "$REPO_REF"' in script
    assert 'git -C "$REPO_DIR" checkout --detach --force FETCH_HEAD' in script
    assert 'git -C "$REPO_DIR" rev-parse HEAD' in script
    assert 'ensure_git_repo "$REPO_DIR" "$REPO_URL"' in script
    assert 'git -C "$repo_dir" remote get-url origin' in runtime_lib
    assert 'sha256sum -c -' in script
    assert script.index("rm -rf -- /app/src") < script.index('cp -r "$REPO_DIR"/src /app/')
    assert 'grep -Fqx "# upstream-ref: $REPO_REF" "$RUNTIME_LOCK"' in script
    assert (
        'grep -Fqx "# upstream-lock-sha256: $UPSTREAM_LOCK_SHA256" "$RUNTIME_LOCK"'
        in script
    )
    assert "uvx" not in script
    assert 'exec "$VENV_DIR/bin/langgraph" dev' in script
