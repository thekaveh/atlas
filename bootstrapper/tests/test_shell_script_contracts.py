from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_weaviate_generated_env_quotes_embedding_model() -> None:
    script = (
        REPO_ROOT / "services" / "weaviate" / "init" / "scripts" / "init-weaviate.sh"
    ).read_text(encoding="utf-8")

    assert "quote_shell_env_value()" in script
    assert "LITELLM_EMBEDDING_MODEL=$model" not in script
    assert "LITELLM_EMBEDDING_MODEL=$(quote_shell_env_value \"$model\")" in script


def test_n8n_install_nodes_fails_when_required_nodes_fail() -> None:
    script = (
        REPO_ROOT / "services" / "n8n" / "init" / "scripts" / "install-nodes.sh"
    ).read_text(encoding="utf-8")

    failure_block_start = script.index("if [ $failure_count -gt 0 ]; then")
    failure_block_end = script.index("else", failure_block_start)
    failure_block = script[failure_block_start:failure_block_end]

    assert "exit 1" in failure_block


def test_n8n_install_nodes_skips_after_owner_setup_auth_gate() -> None:
    script = (
        REPO_ROOT / "services" / "n8n" / "init" / "scripts" / "install-nodes.sh"
    ).read_text(encoding="utf-8")

    assert "401|403)" in script
    assert "Assuming owner setup already completed" in script
    assert "skipping first-boot community-node init" in script


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
