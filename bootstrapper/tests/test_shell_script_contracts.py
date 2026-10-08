from __future__ import annotations

import json
from pathlib import Path

import pytest
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


def test_n8n_warm_restart_does_not_refetch_an_installed_locked_set() -> None:
    """`npm ci` on every start made n8n-init (and so n8n) fail offline."""
    script = (
        REPO_ROOT / "services" / "n8n" / "init" / "scripts" / "install-nodes.sh"
    ).read_text(encoding="utf-8")

    guard = script.index('cmp -s /config/package-lock.json "$NODES_DIR/package-lock.json"')
    assert 'node_modules/.package-lock.json' in script[guard:guard + 200]
    assert guard < script.index("npm ci \\\n")


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
    assert package["dependencies"]["n8n-workflow"] == "2.42.2"
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


def test_n8n_custom_node_set_survives_an_offline_restart(tmp_path) -> None:
    """A custom N8N_INIT_NODES set was wiped and reinstalled on every start,
    so an offline restart left no nodes and failed n8n-init."""
    import os
    import subprocess

    script = REPO_ROOT / "services" / "n8n" / "init" / "scripts" / "install-nodes.sh"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npm = bin_dir / "npm"
    npm.write_text(
        '#!/bin/sh\n[ -f "$NPM_FAIL" ] && exit 1\n'
        'while [ "$1" != "--prefix" ]; do shift; done; p=$2\n'
        'mkdir -p "$p/node_modules" && touch "$p/node_modules/.package-lock.json" "$p/package-lock.json"\n'
    )
    npm.chmod(0o755)
    fail = tmp_path / "offline"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "N8N_USER_FOLDER": str(tmp_path / "n8n"),
           "N8N_INIT_NODES": "n8n-nodes-x@1.2.3", "NPM_FAIL": str(fail)}
    run = lambda: subprocess.run(["sh", str(script)], env=env, capture_output=True, text=True)  # noqa: E731
    assert run().returncode == 0
    fail.touch()
    assert run().returncode == 0  # same set, offline: nothing to fetch
    env["N8N_INIT_NODES"] = "n8n-nodes-y@2.0.0"
    assert run().returncode != 0  # a new set offline fails, but keeps the old one
    assert (tmp_path / "n8n" / "nodes" / "node_modules" / ".package-lock.json").is_file()


def test_airflow_init_keeps_operator_connections_for_localhost_sources(tmp_path) -> None:
    """The orphan pass deleted spark/minio/weaviate/neo4j_default on every
    start, including the ones an operator made for a localhost source."""
    import os
    import subprocess

    script = (REPO_ROOT / "services/airflow/init/scripts/init-airflow.sh").read_text(encoding="utf-8")
    start = script.index("for pair in ")
    loop = script[start:script.index("done", start) + 4]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "airflow").write_text(f'#!/bin/sh\necho "$@" >> {tmp_path}/calls\n')
    (bin_dir / "airflow").chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "SPARK_SOURCE": "disabled",
           "MINIO_SOURCE": "container", "WEAVIATE_SOURCE": "localhost", "NEO4J_GRAPH_DB_SOURCE": "localhost"}
    subprocess.run(["sh", "-c", loop], env=env, check=True)
    calls = (tmp_path / "calls").read_text()
    assert "delete spark_default" in calls and "delete minio_default" in calls
    assert "weaviate_default" not in calls and "neo4j_default" not in calls


def test_openclaw_init_leaves_a_json5_config_unpatched(tmp_path) -> None:
    """OpenClaw reads JSON5; failing init on a commented / trailing-comma
    config kept the gateway from starting (7fe2483e), and the older `&& mv`
    form also exited 0, but only by accident. Now: warn, leave the file
    byte-identical, exit 0. A strict-JSON config is still patched."""
    import shutil
    import subprocess

    if not shutil.which("jq"):
        pytest.skip("jq not installed")
    compose = yaml.safe_load((REPO_ROOT / "services/openclaw/compose.yml").read_text(encoding="utf-8"))
    script = compose["services"]["openclaw-init"]["entrypoint"][-1].replace("$$", "$")
    config = tmp_path / "openclaw.json"
    script = script.replace("/home/node/.openclaw/openclaw.json", str(config)).replace(
        "chown -R 1000:1000 /home/node/.openclaw", "true")
    json5 = '{\n  // operator note\n  "agents": {"defaults": {"model": "litellm/gpt-4o"}},\n}\n'
    config.write_text(json5, encoding="utf-8")
    result = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0 and "not strict JSON" in result.stdout
    assert config.read_text(encoding="utf-8") == json5
    config.write_text('{"agents": {}}', encoding="utf-8")
    assert subprocess.run(["sh", "-c", script], capture_output=True).returncode == 0
    patched = config.read_text(encoding="utf-8")
    assert "dangerouslyAllowHostHeaderOriginFallback" in patched and "litellm:4000" in patched



def test_returning_to_a_custom_n8n_node_set_reinstalls_it(tmp_path) -> None:
    """The locked-set install left the custom set's stamp behind, so going
    back to that custom set skipped its install over the locked node_modules."""
    import os
    import subprocess

    script = REPO_ROOT / "services" / "n8n" / "init" / "scripts" / "install-nodes.sh"
    config = tmp_path / "config"
    config.mkdir()
    for name in ("package.json", "package-lock.json"):
        (config / name).write_text("{}", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "npm").write_text(
        '#!/bin/sh\nwhile [ "$1" != "--prefix" ]; do shift; done; p=$2\n'
        'rm -rf "$p/node_modules"; mkdir -p "$p/node_modules" && touch "$p/node_modules/.package-lock.json"\n'
        'echo "$NPM_MARK" > "$p/node_modules/MARK"\n'
    )
    (bin_dir / "npm").chmod(0o755)
    script_text = script.read_text(encoding="utf-8").replace("/config/", f"{config}/")
    runner = tmp_path / "install-nodes.sh"
    runner.write_text(script_text, encoding="utf-8")
    base = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "N8N_USER_FOLDER": str(tmp_path / "n8n")}

    def run(specs, mark):
        env = {**base, "NPM_MARK": mark}
        if specs:
            env["N8N_INIT_NODES"] = specs
        assert subprocess.run(["sh", str(runner)], env=env, capture_output=True).returncode == 0

    run("n8n-nodes-x@1.0.0", "custom")
    run(None, "locked")
    run("n8n-nodes-x@1.0.0", "custom-again")
    assert (tmp_path / "n8n" / "nodes" / "node_modules" / "MARK").read_text().strip() == "custom-again"


def test_trueforge_init_reads_the_catalog_before_rotating_its_key() -> None:
    """Minting deletes the stored provider's key; a catalog failure after that
    left TrueForge holding a deleted key (401) until a later init succeeded."""
    text = (REPO_ROOT / "services/trueforge/init/scripts/init.mjs").read_text(encoding="utf-8")
    main = text[text.index("async function main()"):]
    assert main.index("await fetchModelIds()") < main.index("await mintVirtualKey()")
