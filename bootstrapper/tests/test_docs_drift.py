"""CI gate: committed README deps sections + architecture artifacts must
match what `python -m docs.regen --all --check` would produce.

Parallels test_env_example_consistency. Fails if any manifest change leaves
generated artifacts stale.
"""

from __future__ import annotations

from dataclasses import replace
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _assert_text_contract(text, *, contains=(), excludes=()):
    missing = tuple(fragment for fragment in contains if fragment not in text)
    unexpected = tuple(fragment for fragment in excludes if fragment in text)
    assert (missing, unexpected) == ((), ())


_REVIEWED_README_CONTRACTS = {
    "lightrag": {
        "contains": ("does not select file-backed storage automatically",),
        "excludes": (
            "transparently falls back to in-process file backends",
            "In-process fallback (when source disabled)",
        ),
    },
    "supabase": {
        "contains": (
            "DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` protect the Kong",
            "Direct `SUPABASE_STUDIO_PORT` access bypasses Kong",
        )
    },
    "iceberg-rest": {
        "contains": ("host-published API runs without Atlas authentication",),
        "excludes": ("intentionally internal-only",),
    },
    "redis": {
        "contains": ("defaults to `volatile-lru`",),
        "excludes": ("Currently unset — Redis runs with the default `noeviction`",),
    },
    "vllm-metal": {
        "contains": (
            "warning floor",
            "an unreadable Python version warns without blocking",
            "stop the existing process before restarting Atlas",
        ),
        "excludes": ("Minimum unified memory the preflight requires",),
    },
    "n8n": {
        "contains": (
            "legacy bundled POST `/research` fixture has no webhook authentication",
            "Only `BACKEND_N8N_API_TOKEN` is route-scoped",
            "LiteLLM master key and provider-level credentials",
        )
    },
    "mcp-servers": {
        "contains": (
            "shared `supabase_admin` owner bypasses RLS",
            "no schema, table, or column allowlist or output redaction",
            "privileged SQL functions and Neo4j procedures can still cause",
            "backend-network containers can call the unauthenticated",
        )
    },
    "airflow": {
        "contains": (
            "Only trusted authors may supply DAGs",
            "LocalExecutor does not sandbox DAG code",
        )
    },
    "jupyterhub": {
        "contains": (
            "MCP_SERVERS_URL` only when `MCP_SERVERS_SOURCE=container`",
            "leaves it empty when the curated package is disabled",
            "including `MCP_SERVERS_URL`",
            "bypasses Kong authentication",
            "receives high-privilege database and service credentials",
        ),
        "excludes": (
            "receives powerful database and service credentials",
            "MCP_SERVERS_URL` is not injected by Compose",
            "reports the MCP service as disabled",
            "optional gaps such as the current MCP endpoint remain explicit",
        ),
    },
}


def test_capability_readme_exception_and_aggregate_sets_are_closed():
    from docs.capabilities_resolver import CAPABILITY_SECTION_EXCEPTIONS

    manifest_folders = {
        folder.name
        for folder in (REPO_ROOT / "services").iterdir()
        if (folder / "service.yml").is_file()
    }
    readme_folders = {
        folder.name
        for folder in (REPO_ROOT / "services").iterdir()
        if (folder / "README.md").is_file()
    }

    assert readme_folders - manifest_folders == {
        "doc-processor",
        "stt-provider",
        "multi2vec-clip",
    }
    assert CAPABILITY_SECTION_EXCEPTIONS == frozenset({"multi2vec-clip"})


def test_generated_capability_output_changes_when_a_contract_changes():
    from docs.capabilities_resolver import resolve_capability_rows
    from docs.capabilities_section_writer import render_capabilities_section
    from services.manifests import load_manifests

    manifests = load_manifests(REPO_ROOT / "services")
    redis = next(manifest for manifest in manifests if manifest.name == "redis")
    original = render_capabilities_section(
        resolve_capability_rows("redis", manifests),
        position=10,
        aggregate=False,
    )
    changed_capability = replace(
        redis.capabilities[0],
        note=redis.capabilities[0].note + " Contract mutation sentinel.",
    )
    changed_manifest = replace(
        redis,
        capabilities=[changed_capability, *redis.capabilities[1:]],
    )
    changed = render_capabilities_section(
        resolve_capability_rows(
            "redis",
            [changed_manifest, *(m for m in manifests if m.name != "redis")],
        ),
        position=10,
        aggregate=False,
    )

    assert changed != original
    assert "Contract mutation sentinel." in changed


def test_canonical_readmes_reconcile_reviewed_capability_boundaries():
    readmes = {
        name: (REPO_ROOT / f"services/{name}/README.md").read_text(encoding="utf-8")
        for name in _REVIEWED_README_CONTRACTS
    }
    for name, contract in _REVIEWED_README_CONTRACTS.items():
        _assert_text_contract(readmes[name], **contract)


def test_task4_reviewed_contract_wording_is_published():
    required_rows = {
        "chatterbox": "GPU voice-cloning text-to-speech",
        "cloud-providers": "Live cloud completion validation",
        "comfyui": "Authenticated ComfyUI ingress",
        "docling-lightrag-adapter": "Docling credential isolation",
        "fal": "LiteLLM text-to-image passthrough",
        "tei-reranker": "Arbitrary reranker model portability",
        "vllm-metal": "Single-model host lifecycle",
    }

    for service, capability in required_rows.items():
        readme = (REPO_ROOT / f"services/{service}/README.md").read_text(
            encoding="utf-8"
        )
        assert "Capabilities & limitations" in readme, service
        assert capability in readme, service

    env_reference = (REPO_ROOT / "docs/reference/env-vars.md").read_text(
        encoding="utf-8"
    )
    assert "Minimum unified memory (GB) the preflight requires" not in env_reference
    assert "Unified-memory warning floor (GB) checked before lifecycle work" in env_reference


def test_no_drift_between_manifests_and_committed_artifacts():
    cmd = [sys.executable, "-m", "docs.regen", "--all", "--check"]
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "bootstrapper")}
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, env=env)
    if result.returncode == 2:
        pytest.fail(
            "Drift between committed docs and current manifests. Run:\n"
            "  python -m bootstrapper.docs.regen --all\n"
            "and commit the result.\n\n" + result.stdout
        )
    assert result.returncode == 0, result.stdout + result.stderr
