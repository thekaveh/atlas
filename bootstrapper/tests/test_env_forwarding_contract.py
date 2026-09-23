"""Generic manifest→Compose environment forwarding contract (#1175).

The bootstrapper computes runtime values into ``.env`` (adaptive endpoints,
scales, derived settings); each consuming Compose fragment must separately
forward them via ``${VAR}`` interpolation. Before this contract, adding an
adaptive variable without its consuming injection failed silently — the
value existed in ``.env`` and no container ever saw it (the class behind the
previously-inert plugin/Ray settings this ticket cites).

The check: every key ``generate_service_environment()`` can emit is either
referenced by some fragment (``${KEY``) or carried in the explicit
exceptions table below with a reviewed reason (host-only, derived input,
consumer-contract export, …). Stale-exception guards keep the table honest
in both directions. No Compose content is generated — fragments stay
hand-owned; this only proves the hand-off exists.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bootstrapper"))

#: Generated keys that intentionally have NO ${...} consumer in any Compose
#: fragment. Every entry carries the reviewed reason; the stale guards below
#: fail when an entry gains a consumer (drop it) or stops being generated.
FORWARDING_EXCEPTIONS: dict[str, str] = {
    # -- consumed by the docker compose CLI itself, not by a container
    "COMPOSE_PROFILES": "read by the docker compose CLI to select profiles",
    # -- derivation INPUTS: service_config folds these into the main image
    #    var per source variant; fragments interpolate only the main var
    "RAY_GPU_IMAGE": "variant input folded into RAY_IMAGE by service_config",
    "SPEACHES_GPU_IMAGE": "variant input folded into the speaches image var",
    "TEI_RERANKER_CPU_IMAGE": "variant input folded into TEI_RERANKER_IMAGE",
    "TEI_RERANKER_CPU_ARM64_IMAGE": "arch variant input for TEI_RERANKER_IMAGE",
    "TEI_RERANKER_GPU_IMAGE": "variant input folded into TEI_RERANKER_IMAGE",
    "MULTI2VEC_CLIP_SIGLIP2_IMAGE": (
        "documented opt-in variant the operator swaps into "
        "MULTI2VEC_CLIP_IMAGE by hand (SigLIP2 upgrade path)"
    ),
    # -- deploy-resource dicts applied through runtime_sc deploy slices,
    #    which Compose interpolation cannot express
    "CLIP_DEPLOY_RESOURCES": "runtime_sc deploy slice input, not interpolable",
    "COMFYUI_DEPLOY_RESOURCES": "runtime_sc deploy slice input, not interpolable",
    "OLLAMA_DEPLOY_RESOURCES": "runtime_sc deploy slice input, not interpolable",
    # -- consumer-contract / host-facing exports: read by downstream repos,
    #    the wizard, or `endpoints export`, not by in-stack containers
    "GRAFANA_ENDPOINT": "endpoint export for consumers; Grafana reads Prometheus, nothing reads Grafana in-stack",
    "MLFLOW_ENDPOINT": "endpoint export; JupyterHub gets a literal tracking URI",
    "OPENCLAW_ENDPOINT": "endpoint export; no in-stack consumer",
    "VERBA_ENDPOINT": "endpoint export; no in-stack consumer",
    "TRUEFORGE_ENDPOINT": "endpoint export; no in-stack consumer yet (#1159)",
    "LLM_GRAPH_BUILDER_ENDPOINT": "endpoint export; no in-stack consumer",
    "LLM_GRAPH_BUILDER_BACKEND_ENDPOINT": "endpoint export; no in-stack consumer",
    "OTEL_COLLECTOR_ENDPOINT": "endpoint export; exporters get literal URLs",
    "OTEL_COLLECTOR_OTLP_GRPC_ENDPOINT": "endpoint export; exporters get literal URLs",
    "MINIO_PUBLIC_ENDPOINT": "consumer-contract export (#345 endpoint wiring)",
    "REDPANDA_BROKERS": "consumer-contract export; in-stack clients use literal broker addresses",
    "LITELLM_BASE_URL": "fragments pin the literal http://litellm:4000; the var serves localhost consumers and docs",
    "LITELLM_ENABLED_PROVIDERS": "wizard/informational summary of enabled providers",
    # -- families without containers (virtual/engine manifests)
    "STT_PROVIDER_SCALE": "virtual engine family; no container to scale",
    "TTS_PROVIDER_SCALE": "virtual engine family; no container to scale",
    "VLLM_METAL_SCALE": "managed-localhost family; no container to scale",
    # -- consumed through service_config's weaviate module wiring, not
    #    compose interpolation
    "WEAVIATE_LITELLM_API_KEY": (
        "manifest-declared, folded into weaviate module configuration by "
        "service_config (see service_config.py, weaviate module wiring)"
    ),
}


def _default_sources() -> dict[str, str]:
    sources: dict[str, str] = {}
    pattern = re.compile(r"^([A-Z][A-Z0-9_]*_SOURCE)=(.*)$")
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        match = pattern.match(line.strip())
        if match:
            sources[match.group(1)] = match.group(2)
    return sources


def _generated_keys() -> set[str]:
    from core.config_parser import ConfigParser
    from services.service_config import ServiceConfig

    parser = ConfigParser(str(ROOT))
    config = ServiceConfig(parser)
    config.yaml_config = parser.load_yaml_config()

    def generate(sources: dict[str, str]) -> set[str]:
        config.service_sources = dict(sources)
        return set(config.generate_service_environment().keys())

    defaults = _default_sources()
    keys = generate(defaults)
    enabled = {
        key: ("container" if value == "disabled" else value)
        for key, value in defaults.items()
    }
    keys |= generate(enabled)
    return keys


def _compose_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(ROOT.glob("services/*/compose.yml"))
    )


def unforwarded(keys: set[str], compose_text: str, exceptions: dict[str, str]) -> list[str]:
    """Generated keys neither interpolated by any fragment nor excepted."""
    return sorted(
        key
        for key in keys
        if ("${" + key) not in compose_text and key not in exceptions
    )


def test_every_generated_variable_is_forwarded_or_excepted():
    missing = unforwarded(_generated_keys(), _compose_text(), FORWARDING_EXCEPTIONS)
    assert missing == [], (
        "runtime-generated env vars with no ${...} consumer in any Compose "
        "fragment and no reviewed exception — forward them in the consuming "
        "fragment, or add a justified FORWARDING_EXCEPTIONS entry:\n  "
        + "\n  ".join(missing)
    )


def test_exceptions_are_current_in_both_directions():
    keys = _generated_keys()
    compose_text = _compose_text()
    now_forwarded = sorted(
        key for key in FORWARDING_EXCEPTIONS if ("${" + key) in compose_text
    )
    assert now_forwarded == [], (
        f"exceptions that gained a Compose consumer — drop them: {now_forwarded}"
    )
    no_longer_generated = sorted(set(FORWARDING_EXCEPTIONS) - keys)
    assert no_longer_generated == [], (
        f"exceptions for keys the bootstrapper no longer generates: {no_longer_generated}"
    )
    unreasoned = sorted(k for k, v in FORWARDING_EXCEPTIONS.items() if not v.strip())
    assert unreasoned == []


def test_the_checker_itself_catches_an_unforwarded_variable():
    """AC self-test: a new adaptive var without its consuming injection must
    fail the generic check, and forwarding or excepting it must satisfy it."""
    keys = {"BACKEND_FOO_ENDPOINT", "BACKEND_BAR_ENDPOINT"}
    compose = "environment:\n  BACKEND_BAR_ENDPOINT: ${BACKEND_BAR_ENDPOINT}\n"
    assert unforwarded(keys, compose, {}) == ["BACKEND_FOO_ENDPOINT"]
    assert unforwarded(keys, compose, {"BACKEND_FOO_ENDPOINT": "host-only"}) == []
