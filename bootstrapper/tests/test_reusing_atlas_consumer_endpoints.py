"""Drift guard: the auto-managed endpoint consumer-bridging pattern must stay
documented in reusing-atlas.md (issue #349).

A downstream consumer joining the Atlas network needs to know how to bridge
Atlas's computed endpoint variables (COMFYUI_ENDPOINT, OLLAMA_ENDPOINT,
LITELLM_BASE_URL, MINIO_ENDPOINT) into its own service variables. This test
asserts the documentation exists so it can't be accidentally deleted.
"""
from __future__ import annotations

from pathlib import Path

from tests.three_surface_test_utils import surface_text


REPO_ROOT = Path(__file__).resolve().parents[2]
REUSING_ATLAS = REPO_ROOT / "docs" / "deployment" / "reusing-atlas.md"
SUBMODULE_USAGE = REPO_ROOT / "docs" / "deployment" / "submodule-usage.md"
ENV_EXAMPLE = REPO_ROOT / ".env.example"


def test_reusing_atlas_documents_comfyui_endpoint_bridging() -> None:
    """reusing-atlas.md must mention COMFYUI_ENDPOINT and the consumer
    bridging pattern — so downstream consumers know to use it instead of
    hard-coding URLs."""
    text = REUSING_ATLAS.read_text(encoding="utf-8")
    assert "COMFYUI_ENDPOINT" in text, (
        "reusing-atlas.md must document COMFYUI_ENDPOINT as the canonical "
        "consumer URL variable (issue #349). The endpoint-bridging section "
        "appears to have been removed."
    )
    assert "${COMFYUI_ENDPOINT" in text, (
        "reusing-atlas.md must show the consumer-bridging pattern "
        "(${CONSUMER_VAR:-${COMFYUI_ENDPOINT:-<default>}})."
    )


def test_reusing_atlas_documents_other_auto_managed_endpoints() -> None:
    """The bridging pattern section should also cover at least one other
    auto-managed endpoint beyond ComfyUI, so the pattern is documented as
    general (not ComfyUI-specific)."""
    text = REUSING_ATLAS.read_text(encoding="utf-8")
    assert "LITELLM_BASE_URL" in text or "OLLAMA_ENDPOINT" in text, (
        "reusing-atlas.md must document the bridging pattern for at least "
        "one other auto-managed endpoint (LITELLM_BASE_URL or "
        "OLLAMA_ENDPOINT), per issue #349 acceptance criteria."
    )


def test_env_example_comfyui_endpoint_mentions_consumer() -> None:
    """The .env.example comment for COMFYUI_ENDPOINT must mention the
    consumer/overlay use case, so operators discover the pattern from .env
    without reading the full reusing-atlas.md guide."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    # Find the COMFYUI_ENDPOINT block
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "COMFYUI_ENDPOINT=":
            # The description comment is above the var (the block comment)
            context = "\n".join(lines[max(0, i - 3):i + 1])
            assert "consumer" in context.lower() or "overlay" in context.lower(), (
                f"The .env.example comment for COMFYUI_ENDPOINT must mention "
                f"the consumer/overlay pattern (issue #349). Found:\n{context}"
            )
            return
    # If we reach here, COMFYUI_ENDPOINT= wasn't found at all
    raise AssertionError("COMFYUI_ENDPOINT= not found in .env.example")


def test_submodule_docs_cover_parent_repo_reference_layout() -> None:
    """Issue #421: submodule consumers need the parent-owned overlay layout,
    force-set SOURCE gotcha, track override rule, and validation checklist."""
    reusing = REUSING_ATLAS.read_text(encoding="utf-8")
    submodule = SUBMODULE_USAGE.read_text(encoding="utf-8")

    assert "parent-repo consumer reference layout" in submodule.lower()
    assert "compose/<name>-overlay.yml" in submodule
    assert "services/_user/<name>/compose.yml" in submodule
    assert "setup-overlay.sh" in submodule
    assert "start-infra.sh" in submodule
    assert "set_env_default" in submodule
    assert "force-set" in submodule
    assert "Explicit `--<service>-source` flags override the selected `--track`" in submodule
    assert "Validation checklist" in submodule
    assert "RAG-showcase-style" in submodule
    assert "DayDreams-style" in submodule
    assert "Explicit `--<service>-source` flags override track membership" in reusing

    for rendered in (
        surface_text("docs/development.md", "site"),
        surface_text("docs/development.md", "wiki"),
    ):
        assert "Parent-Repo Consumer Layout" in rendered
        assert "compose/<name>-overlay.yml" in rendered
        assert "scripts/setup-overlay.sh" in rendered
        assert "force-set" in rendered
