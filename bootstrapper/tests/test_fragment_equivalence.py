"""
Byte-equivalence proof for the modular layout.

Phase D landed the cutover: `docker-compose.yml` is now the thin
`include:`-shell that pulls in services/<name>/compose.yml fragments. This
test renders that shell via `docker compose config` and diffs the output
against the golden baseline captured at
`bootstrapper/tests/fixtures/rendered_config_baseline.yml`.

To stay deterministic across machines, the test does NOT use the user's
local `.env` — it generates a controlled fixture `.env` on the fly from
`.env.example` (which has empty secrets) and forces the SCALE/SOURCE values
that match the captured baseline. This way:

  - The baseline contains empty secrets (committed cleanly to git).
  - Whether the user has run `./start.sh` or not, the test renders the same
    structure.
  - User-`.env`-specific drift (different active source variants, generated
    secret values) cannot poison the comparison.

Skipped if `docker` is not on PATH (CI lint job) or `.env.example` is missing.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import json
import sys
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE = REPO_ROOT / "docker-compose.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
BASELINE = (
    Path(__file__).resolve().parent / "fixtures" / "rendered_config_baseline.yml"
)

# Path placeholders used in the committed baseline so the fixture stays
# portable across machines / CI / different REPO_ROOT layouts. We swap real
# absolute paths in the rendered output for these tokens before comparing.
_REPO_ROOT_TOKEN = "{REPO_ROOT}"
_HOME_TOKEN = "{HOME}"


def _normalize_paths(data):
    """Recursively replace machine-specific absolute paths with placeholders.

    `docker compose config` interpolates relative bind-mount sources and
    build contexts to absolute paths rooted at REPO_ROOT, and expands `~`
    in env-file values to the user's home directory. To keep the baseline
    fixture portable, we substitute those two prefixes with sentinel tokens
    on every comparison.
    """
    repo_str = str(REPO_ROOT)
    home_str = str(Path.home())

    def _walk(node):
        if isinstance(node, str):
            return (
                node.replace(repo_str, _REPO_ROOT_TOKEN)
                    .replace(home_str, _HOME_TOKEN)
            )
        if isinstance(node, list):
            return [_walk(x) for x in node]
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        return node

    return _walk(data)


def _strip_volatile_defaults(data):
    """Strip Compose-version-dependent default fields so renders compare equal
    across Compose versions.

    Newer `docker compose config` emits a few spec defaults that older
    versions omit (e.g. `bind.create_host_path: true` on volume entries).
    Different contributor machines and CI runners can have different
    Compose versions; we normalize them out so the byte-equivalence assertion
    tracks meaningful structural drift, not Compose-version drift.

    Add new entries here when a future Compose version starts emitting
    another spec default that wasn't previously serialized.
    """
    # Arch-specific images: the bootstrapper picks `cpu-1.9` on amd64 and
    # `cpu-arm64-latest` on arm64 hosts for tei-reranker (the amd64 image is
    # ORT-only; the arm64 image's candle
    # backend loads safetensors). Normalize both to a sentinel so the test
    # passes on either arch.
    _TEI_IMAGE_SENTINEL = "__TEI_RERANKER_CPU_IMAGE__"
    _tei_image_variants = {
        "ghcr.io/huggingface/text-embeddings-inference:cpu-1.9",
        "ghcr.io/huggingface/text-embeddings-inference:cpu-1.9@sha256:ad950d30878eceb72aaf32024d26fa2b1d04a75304fa0b4776b49aa1941fea07",
        "ghcr.io/huggingface/text-embeddings-inference:cpu-arm64-latest",
        "ghcr.io/huggingface/text-embeddings-inference:cpu-arm64-latest@sha256:35c50d7494de22deecdb783b8f5b7e1d05765709bd90071b03469b9440d28656",
    }

    def _walk(node):
        if isinstance(node, list):
            return [_walk(x) for x in node]
        if isinstance(node, dict):
            cleaned = {k: _walk(v) for k, v in node.items()}
            # Volume entries: `bind: {create_host_path: true}` defaults to {} on older Compose.
            bind = cleaned.get("bind")
            if isinstance(bind, dict) and bind.get("create_host_path") is True:
                bind_copy = {k: v for k, v in bind.items() if k != "create_host_path"}
                cleaned["bind"] = bind_copy
            # Normalize arch-specific tei-reranker image
            img = cleaned.get("image")
            if isinstance(img, str) and img in _tei_image_variants:
                cleaned["image"] = _TEI_IMAGE_SENTINEL
            return cleaned
        return node

    return _walk(data)

# SCALE / SOURCE values the captured baseline reflects.
#
# `.env.example` ships scales=0 for adaptive services (they're meant to be
# computed by the bootstrapper at startup). The baseline, however, was
# captured against a fully-bootstrapped stack where the relevant services
# were on. We re-impose those values here so renders match the baseline
# regardless of where the user is in their setup flow.
_BASELINE_OVERRIDES: dict[str, str] = {
    "SPEACHES_SCALE": "1",
    "STT_PROVIDER_SCALE": "1",
    "TTS_PROVIDER_SCALE": "1",
    "STT_ENDPOINT": "",
    "TTS_ENDPOINT": "",
}


def _docker_available() -> bool:
    return shutil.which("docker") is not None


pytestmark = pytest.mark.skipif(
    not _docker_available(),
    reason="docker not on PATH",
)


def _build_test_env() -> Path:
    """Materialize a deterministic env file from .env.example + overrides.

    Returns the path to a tempfile that callers should pass to
    `docker compose --env-file ...`. The file is leaked on purpose (pytest
    cleans up the temp dir at session end); short-lived enough not to matter.
    """
    if not ENV_EXAMPLE.is_file():
        pytest.skip(f".env.example missing at {ENV_EXAMPLE}")
    src = ENV_EXAMPLE.read_text().splitlines(keepends=True)
    overridden_keys: set[str] = set()
    out_lines: list[str] = []
    for line in src:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out_lines.append(line)
            continue
        # Plain "KEY=value" line (with optional inline comment after value)
        if "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in _BASELINE_OVERRIDES:
                out_lines.append(f"{key}={_BASELINE_OVERRIDES[key]}\n")
                overridden_keys.add(key)
                continue
        out_lines.append(line)
    # Any override key not already in .env.example gets appended.
    for key, value in _BASELINE_OVERRIDES.items():
        if key not in overridden_keys:
            out_lines.append(f"{key}={value}\n")
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".env", delete=False, encoding="utf-8"
    )
    handle.writelines(out_lines)
    handle.close()
    return Path(handle.name)


def _render(compose_file: Path) -> dict:
    env_file = _build_test_env()
    # -p atlas matches the runtime invocation (./start.sh passes the project
    # name from .env). Without it, the rendered `name:` field defaults to the
    # parent directory, which causes baseline drift when the worktree is on a
    # different path than where the baseline was captured.
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-p",
            "atlas",
            "-f",
            str(compose_file),
            "config",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"`docker compose config` failed for {compose_file}:\n{result.stderr}"
    )
    return _strip_volatile_defaults(_normalize_paths(yaml.safe_load(result.stdout)))


def _load_baseline() -> dict:
    if not BASELINE.is_file():
        raise FileNotFoundError(
            f"required byte-equivalence baseline fixture missing at {BASELINE}"
        )
    return _strip_volatile_defaults(yaml.safe_load(BASELINE.read_text()))


def test_missing_baseline_is_a_hard_gate_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sys.modules[__name__], "BASELINE", tmp_path / "deleted-baseline.yml"
    )
    with pytest.raises(FileNotFoundError, match="required byte-equivalence"):
        _load_baseline()


def test_full_stack_matches_baseline():
    """The thin-shell modular compose must render byte-identically to the
    pre-refactor monolithic baseline (captured in fixtures/)."""
    rendered = _render(COMPOSE)
    baseline = _load_baseline()
    assert rendered == baseline, (
        "Rendered modular config diverges from the monolithic baseline. "
        "Run `docker compose -f docker-compose.yml config > /tmp/actual.yml && "
        f"diff bootstrapper/tests/fixtures/rendered_config_baseline.yml /tmp/actual.yml` to inspect."
    )


def test_full_stack_services_match():
    """Service-by-service equality (gives a cleaner error message when one
    service drifts)."""
    rendered = _render(COMPOSE)
    baseline = _load_baseline()
    rendered_services = set(rendered["services"].keys())
    baseline_services = set(baseline["services"].keys())
    assert rendered_services == baseline_services, (
        f"Service set drift.\n"
        f"  Only in rendered: {sorted(rendered_services - baseline_services)}\n"
        f"  Only in baseline: {sorted(baseline_services - rendered_services)}"
    )
    drifted = []
    for name in sorted(rendered_services):
        if rendered["services"][name] != baseline["services"][name]:
            drifted.append(name)
    assert not drifted, f"Services with shape drift: {drifted}"


def test_full_stack_volumes_match():
    rendered = _render(COMPOSE)
    baseline = _load_baseline()
    assert rendered.get("volumes", {}) == baseline.get("volumes", {})


def test_full_stack_networks_match():
    rendered = _render(COMPOSE)
    baseline = _load_baseline()
    assert rendered.get("networks", {}) == baseline.get("networks", {})


def test_supabase_database_remote_bind_requires_an_explicit_override() -> None:
    env_file = _build_test_env()

    def render_host_ip(path: Path) -> str:
        result = subprocess.run(
            [
                "docker", "compose", "--env-file", str(path), "-p", "atlas",
                "-f", str(COMPOSE), "config", "--format", "json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        return payload["services"]["supabase-db"]["ports"][0]["host_ip"]

    assert render_host_ip(env_file) == "127.0.0.1"
    remote_env = env_file.with_name(f"{env_file.name}-remote")
    remote_env.write_text(
        env_file.read_text(encoding="utf-8").replace(
            "HOST_BIND_IP=\n", "HOST_BIND_IP=0.0.0.0:\n", 1
        ),
        encoding="utf-8",
    )
    assert render_host_ip(remote_env) == "0.0.0.0"
