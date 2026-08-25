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
        for name in {
            "airflow",
            "iceberg-rest",
            "jupyterhub",
            "lightrag",
            "mcp-servers",
            "n8n",
            "redis",
            "supabase",
            "vllm-metal",
        }
    }

    assert "transparently falls back to in-process file backends" not in readmes["lightrag"]
    assert "In-process fallback (when source disabled)" not in readmes["lightrag"]
    assert "does not select file-backed storage automatically" in readmes["lightrag"]

    assert "DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` protect the Kong" in readmes["supabase"]
    assert "Direct `SUPABASE_STUDIO_PORT` access bypasses Kong" in readmes["supabase"]

    assert "intentionally internal-only" not in readmes["iceberg-rest"]
    assert "host-published API runs without Atlas authentication" in readmes["iceberg-rest"]

    assert "Currently unset — Redis runs with the default `noeviction`" not in readmes["redis"]
    assert "defaults to `volatile-lru`" in readmes["redis"]

    assert "Minimum unified memory the preflight requires" not in readmes["vllm-metal"]
    assert "warning floor" in readmes["vllm-metal"]
    assert "an unreadable Python version warns without blocking" in readmes["vllm-metal"]
    assert "stop the existing process before restarting Atlas" in readmes["vllm-metal"]

    assert "legacy bundled POST `/research` fixture has no webhook authentication" in readmes["n8n"]
    assert "Only `BACKEND_N8N_API_TOKEN` is route-scoped" in readmes["n8n"]
    assert "LiteLLM master key and provider-level credentials" in readmes["n8n"]

    assert "shared `supabase_admin` owner bypasses RLS" in readmes["mcp-servers"]
    assert "no schema, table, or column allowlist or output redaction" in readmes["mcp-servers"]
    assert "privileged SQL functions and Neo4j procedures can still cause" in readmes["mcp-servers"]
    assert "backend-network containers can call the unauthenticated" in readmes["mcp-servers"]

    assert "Only trusted authors may supply DAGs" in readmes["airflow"]
    assert "LocalExecutor does not sandbox DAG code" in readmes["airflow"]

    assert "MCP_SERVERS_URL` is not injected by Compose" in readmes["jupyterhub"]
    assert "reports the MCP service as disabled" in readmes["jupyterhub"]
    assert "receives high-privilege database and service credentials" in readmes["jupyterhub"]
    assert "receives powerful database and service credentials" not in readmes["jupyterhub"]


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
