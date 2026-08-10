"""#849: ollama container-* sources must expose OLLAMA_NUM_PARALLEL +
OLLAMA_MAX_LOADED_MODELS so multi-agent consumers (8+ concurrent requests)
get parallel serving instead of Ollama's default 1-sequential-slot behavior.

Part 1 (container source) of #849. Part 2 — the ollama-localhost doctor lint
— now ships too; see test_ollama_parallel_doctor.py. The cross-platform
concern recorded here was resolved by scope rather than by solving it: the
probe reads the host config only where that is actually verifiable (macOS
`launchctl`) and reports "unknown" everywhere else, and an unknown never
warns. That keeps the check advisory and safe to ship without the live
validation CI cannot provide.
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE = REPO_ROOT / "services" / "ollama" / "compose.yml"
MANIFEST = REPO_ROOT / "services" / "ollama" / "service.yml"


def test_ollama_container_exposes_parallel_serving_env():
    """The ollama container service must inject OLLAMA_NUM_PARALLEL and
OLLAMA_MAX_LOADED_MODELS, overridable via the Atlas env (manifest-defaulted),
    so a multi-agent consumer isn't serialized onto Ollama's default of 1."""
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    env = doc["services"]["ollama"]["environment"]
    assert env.get("OLLAMA_NUM_PARALLEL") == "${OLLAMA_NUM_PARALLEL:-8}", (
        f"ollama.OLLAMA_NUM_PARALLEL should be ${{OLLAMA_NUM_PARALLEL:-8}} "
        f"(manifest-overridable parallel slots), got {env.get('OLLAMA_NUM_PARALLEL')!r}"
    )
    assert env.get("OLLAMA_MAX_LOADED_MODELS") == "${OLLAMA_MAX_LOADED_MODELS:-2}", (
        f"ollama.OLLAMA_MAX_LOADED_MODELS should be "
        f"${{OLLAMA_MAX_LOADED_MODELS:-2}} (resident-model bound), got "
        f"{env.get('OLLAMA_MAX_LOADED_MODELS')!r}"
    )


def test_ollama_container_sources_carry_parallel_serving_in_runtime_sc():
    """Both container sources (cpu + gpu) declare the parallel-serving vars in
    their runtime_sc environment (dual-write with compose.yml); localhost does
    NOT (the host daemon owns them there)."""
    doc = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    variants = doc["runtime_sc"]["llm_provider"]
    for source in ("ollama-container-cpu", "ollama-container-gpu"):
        env = variants[source]["environment"]
        assert env.get("OLLAMA_NUM_PARALLEL") == "${OLLAMA_NUM_PARALLEL:-8}", (
            f"{source} runtime_sc must carry OLLAMA_NUM_PARALLEL"
        )
        assert env.get("OLLAMA_MAX_LOADED_MODELS") == "${OLLAMA_MAX_LOADED_MODELS:-2}", (
            f"{source} runtime_sc must carry OLLAMA_MAX_LOADED_MODELS"
        )
    # localhost must NOT carry them — the host daemon owns parallel serving.
    assert "OLLAMA_NUM_PARALLEL" not in variants["ollama-localhost"]["environment"], (
        "ollama-localhost runtime_sc must not set OLLAMA_NUM_PARALLEL — the host "
        "daemon owns it for that source (#849 Part 2 covers the doctor lint)."
    )
