"""Stable consumer endpoint export contract (#345).

`./start.sh endpoints export` emits canonical, distinct container/host/Kong/
public endpoints + active SOURCE modes per consumer-relevant service, plus
per-consumer storage fields, with secret masking by default.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.endpoints_contract import (
    build_export,
    render_env,
    render_json,
)


def _base_env(base_port: int = 63000) -> dict[str, str]:
    """A resolved .env for the container source at a given BASE_PORT.

    Mirrors a real ``.env``: every consumer-relevant service's SOURCE knob is
    populated (they all carry a default in ``.env.example``), so the export sees
    active modes exactly as an operator would.
    """
    return {
        "KONG_HTTP_PORT": str(base_port),
        "BACKEND_SOURCE": "container",
        "BACKEND_PORT": str(base_port + 93),
        "LITELLM_SOURCE": "container",
        "LITELLM_BASE_URL": "http://litellm:4000",
        "LITELLM_PORT": str(base_port + 40),
        "COMFYUI_SOURCE": "container",
        "COMFYUI_ENDPOINT": "http://comfyui:18188",
        "COMFYUI_PORT": str(base_port + 54),
        "ASSET_WORKER_SOURCE": "container",
        "ASSET_WORKER_ENDPOINT": "http://asset-worker:8095",
        "ASSET_WORKER_PORT": str(base_port + 53),
        "LLM_PROVIDER_SOURCE": "ollama-container-cpu",
        "OLLAMA_ENDPOINT": "http://ollama:11434",
        "OLLAMA_PORT": str(base_port + 78),
        "MINIO_SOURCE": "container",
        "MINIO_ENDPOINT": "http://minio:9000",
        "MINIO_PUBLIC_ENDPOINT": f"http://localhost:{base_port + 20}",
        "MINIO_PORT": str(base_port + 20),
        "WEAVIATE_SOURCE": "container",
        "WEAVIATE_URL": "http://weaviate:8080",
        "WEAVIATE_PORT": str(base_port + 30),
        "NEO4J_GRAPH_DB_SOURCE": "container",
        "NEO4J_URI": "bolt://neo4j:7687",
        "REDIS_SOURCE": "container",
        "REDIS_URL": "redis://:${REDIS_PASSWORD}@redis:6379/0",
        "REDIS_PASSWORD": "s3cr3t-should-never-appear",
        "REDIS_PORT": str(base_port + 25),
        "SUPABASE_DB_SOURCE": "container",
        "SUPABASE_API_PORT": str(base_port + 17),
        "N8N_SOURCE": "container",
        "N8N_PORT": str(base_port + 75),
    }


def _as_dict(fields) -> dict[str, str]:
    return {f.name: f.value for f in fields}


# ── canonical, distinct endpoint kinds ──────────────────────────────

def test_distinct_container_host_kong_public_and_source_fields() -> None:
    d = _as_dict(build_export(_base_env()))
    assert d["ATLAS_KONG_GATEWAY"] == "http://localhost:63000"
    # MinIO: distinct internal vs public vs host vs kong
    assert d["ATLAS_MINIO_CONTAINER_ENDPOINT"] == "http://minio:9000"
    assert d["ATLAS_MINIO_PUBLIC_ENDPOINT"] == "http://localhost:63020"
    assert d["ATLAS_MINIO_HOST_ENDPOINT"] == "http://localhost:63020"
    assert d["ATLAS_MINIO_KONG_ENDPOINT"] == "http://s3.minio.localhost:63000"
    assert d["ATLAS_MINIO_SOURCE"] == "container"
    # distinct internal vs public (the #404 pitfall the export makes explicit)
    assert d["ATLAS_MINIO_CONTAINER_ENDPOINT"] != d["ATLAS_MINIO_PUBLIC_ENDPOINT"]
    # LiteLLM canonical base + host + kong
    assert d["ATLAS_LITELLM_CONTAINER_ENDPOINT"] == "http://litellm:4000"
    assert d["ATLAS_LITELLM_HOST_ENDPOINT"] == "http://localhost:63040"
    assert d["ATLAS_LITELLM_KONG_ENDPOINT"] == "http://litellm.localhost:63000"
    # backend has no canonical container var → host + kong only
    assert d["ATLAS_BACKEND_HOST_ENDPOINT"] == "http://localhost:63093"
    assert d["ATLAS_BACKEND_KONG_ENDPOINT"] == "http://api.localhost:63000"


# ── source flips (ComfyUI + MinIO) ──────────────────────────────────

def test_source_flip_reflected_in_export() -> None:
    env = _base_env()
    env["COMFYUI_SOURCE"] = "localhost"
    env["COMFYUI_ENDPOINT"] = "http://host.docker.internal:8188"
    d = _as_dict(build_export(env))
    assert d["ATLAS_COMFYUI_SOURCE"] == "localhost"
    assert d["ATLAS_COMFYUI_CONTAINER_ENDPOINT"] == "http://host.docker.internal:8188"


# ── #643: source-aware HOST_ENDPOINT for host-process sources ────────────────

def test_comfyui_host_endpoint_container_uses_published_port() -> None:
    """Container sources keep today's behavior: HOST_ENDPOINT renders from the
    compose published COMFYUI_PORT (no regression)."""
    for source in ("container", "container-cpu", "container-gpu"):
        env = _base_env()
        env["COMFYUI_SOURCE"] = source
        d = _as_dict(build_export(env))
        # _base_env sets COMFYUI_PORT = 63054
        assert d["ATLAS_COMFYUI_HOST_ENDPOINT"] == "http://localhost:63054", source


def test_comfyui_host_endpoint_managed_mps_uses_mps_localhost_port() -> None:
    """managed-localhost-mps runs a host process on COMFYUI_MPS_LOCALHOST_PORT
    (default 8188); the compose published COMFYUI_PORT is dead there. Regression
    for Symptom 1 (dead :BASE+54 exported)."""
    env = _base_env()
    env["COMFYUI_SOURCE"] = "managed-localhost-mps"
    env["COMFYUI_MPS_LOCALHOST_PORT"] = "8188"
    d = _as_dict(build_export(env))
    assert d["ATLAS_COMFYUI_HOST_ENDPOINT"] == "http://localhost:8188"
    assert d["ATLAS_COMFYUI_HOST_ENDPOINT"] != f"http://localhost:{env['COMFYUI_PORT']}"


def test_comfyui_host_endpoint_managed_mps_default_when_port_unset() -> None:
    """The MPS host port carries a fixed manifest default (8188); if the resolved
    .env omits it, the export falls back to that default rather than emitting
    nothing."""
    env = _base_env()
    env["COMFYUI_SOURCE"] = "managed-localhost-mps"
    env.pop("COMFYUI_MPS_LOCALHOST_PORT", None)
    d = _as_dict(build_export(env))
    assert d["ATLAS_COMFYUI_HOST_ENDPOINT"] == "http://localhost:8188"


def test_comfyui_host_endpoint_localhost_uses_localhost_port() -> None:
    """The localhost source serves on COMFYUI_LOCALHOST_PORT (default 8000)."""
    env = _base_env()
    env["COMFYUI_SOURCE"] = "localhost"
    env["COMFYUI_LOCALHOST_PORT"] = "8000"
    d = _as_dict(build_export(env))
    assert d["ATLAS_COMFYUI_HOST_ENDPOINT"] == "http://localhost:8000"


def test_ollama_host_endpoint_localhost_emitted() -> None:
    """Under ollama-localhost the host Ollama endpoint is knowable (default
    :11434) and must be emitted — Symptom 2 (field silently omitted because
    OLLAMA_PORT is unset under this source)."""
    env = _base_env()
    env["LLM_PROVIDER_SOURCE"] = "ollama-localhost"
    env["OLLAMA_LOCALHOST_PORT"] = "11434"
    d = _as_dict(build_export(env))
    assert d["ATLAS_OLLAMA_SOURCE"] == "ollama-localhost"
    assert d["ATLAS_OLLAMA_HOST_ENDPOINT"] == "http://localhost:11434"


def test_ollama_host_endpoint_localhost_default_when_port_unset() -> None:
    """OLLAMA_LOCALHOST_PORT carries a fixed default (11434); the export falls
    back to it when the resolved .env omits the var."""
    env = _base_env()
    env["LLM_PROVIDER_SOURCE"] = "ollama-localhost"
    env.pop("OLLAMA_LOCALHOST_PORT", None)
    env.pop("OLLAMA_PORT", None)
    d = _as_dict(build_export(env))
    assert d["ATLAS_OLLAMA_HOST_ENDPOINT"] == "http://localhost:11434"


def test_asset_worker_host_endpoint_exported_under_container_source() -> None:
    """ASSET_WORKER gained a ServiceEndpoints entry (#643): its HOST_ENDPOINT and
    in-network CONTAINER_ENDPOINT export under the container source instead of
    consumers hardcoding :BASE+53."""
    d = _as_dict(build_export(_base_env()))
    assert d["ATLAS_ASSET_WORKER_SOURCE"] == "container"
    assert d["ATLAS_ASSET_WORKER_HOST_ENDPOINT"] == "http://localhost:63053"
    assert d["ATLAS_ASSET_WORKER_CONTAINER_ENDPOINT"] == "http://asset-worker:8095"


def test_asset_worker_disabled_emits_only_source() -> None:
    """When disabled (its default), ASSET_WORKER advertises only its SOURCE."""
    env = _base_env()
    env["ASSET_WORKER_SOURCE"] = "disabled"
    env["ASSET_WORKER_ENDPOINT"] = ""
    d = _as_dict(build_export(env))
    assert d["ATLAS_ASSET_WORKER_SOURCE"] == "disabled"
    assert "ATLAS_ASSET_WORKER_HOST_ENDPOINT" not in d
    assert "ATLAS_ASSET_WORKER_CONTAINER_ENDPOINT" not in d


# ── disabled services ───────────────────────────────────────────────

def test_disabled_service_emits_only_source() -> None:
    env = _base_env()
    env["MINIO_SOURCE"] = "disabled"
    env["MINIO_ENDPOINT"] = ""
    env["MINIO_PUBLIC_ENDPOINT"] = ""
    d = _as_dict(build_export(env))
    assert d["ATLAS_MINIO_SOURCE"] == "disabled"
    assert "ATLAS_MINIO_CONTAINER_ENDPOINT" not in d
    assert "ATLAS_MINIO_PUBLIC_ENDPOINT" not in d
    assert "ATLAS_MINIO_HOST_ENDPOINT" not in d


def test_ollama_cloud_only_none_omits_endpoints_and_signals_source() -> None:
    """Cloud-only (LLM_PROVIDER_SOURCE=none): Ollama endpoints must NOT be
    advertised, but the SOURCE=none signal must be emitted (regression for the
    disabled-service leak — Ollama uses LLM_PROVIDER_SOURCE, not OLLAMA_SOURCE)."""
    env = _base_env()
    env["LLM_PROVIDER_SOURCE"] = "none"
    d = _as_dict(build_export(env))
    assert d["ATLAS_OLLAMA_SOURCE"] == "none"
    assert "ATLAS_OLLAMA_CONTAINER_ENDPOINT" not in d
    assert "ATLAS_OLLAMA_HOST_ENDPOINT" not in d
    assert "ATLAS_OLLAMA_KONG_ENDPOINT" not in d


def test_disabled_neo4j_omits_endpoints_and_signals_source() -> None:
    """Neo4j disabled (NEO4J_GRAPH_DB_SOURCE=disabled) must omit endpoints and
    still emit its SOURCE signal."""
    env = _base_env()
    env["NEO4J_GRAPH_DB_SOURCE"] = "disabled"
    d = _as_dict(build_export(env))
    assert d["ATLAS_NEO4J_SOURCE"] == "disabled"
    assert "ATLAS_NEO4J_CONTAINER_ENDPOINT" not in d
    assert "ATLAS_NEO4J_KONG_ENDPOINT" not in d


def test_every_service_emits_a_source_field() -> None:
    """Each consumer-relevant service advertises its active SOURCE mode so a
    consumer can distinguish active vs disabled services (AC #2)."""
    d = _as_dict(build_export(_base_env()))
    for svc in ("BACKEND", "LITELLM", "COMFYUI", "ASSET_WORKER", "OLLAMA", "MINIO",
                "WEAVIATE", "NEO4J", "N8N", "REDIS", "SUPABASE"):
        assert f"ATLAS_{svc}_SOURCE" in d, f"missing SOURCE for {svc}"


# ── BASE_PORT change ────────────────────────────────────────────────

def test_host_and_kong_urls_track_base_port() -> None:
    a = _as_dict(build_export(_base_env(63000)))
    b = _as_dict(build_export(_base_env(64000)))
    assert a["ATLAS_MINIO_HOST_ENDPOINT"] != b["ATLAS_MINIO_HOST_ENDPOINT"]
    assert b["ATLAS_MINIO_HOST_ENDPOINT"] == "http://localhost:64020"
    assert b["ATLAS_KONG_GATEWAY"] == "http://localhost:64000"
    assert b["ATLAS_BACKEND_KONG_ENDPOINT"] == "http://api.localhost:64000"


# ── multiple consumers (storage passthrough) ────────────────────────

def test_multiple_consumer_storage_fields_included() -> None:
    env = _base_env()
    env.update({
        "ATLAS_STORE_ALPHA_ART_BUCKET": "alpha-art",
        "ATLAS_STORE_ALPHA_ART_PUBLIC_ENDPOINT": "http://localhost:63020",
        "ATLAS_STORE_ALPHA_ART_ACCESS_KEY_VAR": "MINIO_ALPHA_ART_ACCESS_KEY",
        "ATLAS_STORE_BETA_DOCS_BUCKET": "beta-docs",
    })
    d = _as_dict(build_export(env))
    assert d["ATLAS_STORE_ALPHA_ART_BUCKET"] == "alpha-art"
    assert d["ATLAS_STORE_BETA_DOCS_BUCKET"] == "beta-docs"
    # credential reference (var name), not a resolved secret, by default
    assert d["ATLAS_STORE_ALPHA_ART_ACCESS_KEY_VAR"] == "MINIO_ALPHA_ART_ACCESS_KEY"


# ── secret masking ──────────────────────────────────────────────────

def test_secrets_are_references_by_default() -> None:
    fields = build_export(_base_env())
    d = _as_dict(fields)
    # Redis container endpoint is a reference, not a resolved URL with password
    assert d["ATLAS_REDIS_CONTAINER_ENDPOINT"] == "${REDIS_URL}"
    # the infra password must appear NOWHERE in the default output
    joined = render_env(fields) + render_json(fields)
    assert "s3cr3t-should-never-appear" not in joined
    assert "REDIS_PASSWORD" not in joined  # not exported at all


def test_with_secrets_resolves_only_consumer_scoped_credentials() -> None:
    env = _base_env()
    env.update({
        "ATLAS_STORE_ALPHA_ART_ACCESS_KEY_VAR": "MINIO_ALPHA_ART_ACCESS_KEY",
        "MINIO_ALPHA_ART_ACCESS_KEY": "scoped-consumer-key",
    })
    d = _as_dict(build_export(env, with_secrets=True))
    # consumer-scoped credential resolved to a value field
    assert d["ATLAS_STORE_ALPHA_ART_ACCESS_KEY"] == "scoped-consumer-key"
    # reference retained
    assert d["ATLAS_STORE_ALPHA_ART_ACCESS_KEY_VAR"] == "MINIO_ALPHA_ART_ACCESS_KEY"
    # infra secret (Redis) is STILL a reference even with --with-secrets
    assert d["ATLAS_REDIS_CONTAINER_ENDPOINT"] == "${REDIS_URL}"
    assert "s3cr3t-should-never-appear" not in render_env(build_export(env, with_secrets=True))


def test_bare_hand_authored_credential_value_is_masked_by_default() -> None:
    """A mis-named operator var (Atlas never emits a bare ATLAS_STORE_*_SECRET_KEY
    — only the _VAR reference) must not leak its plaintext value in default
    output (Finding 2 hardening)."""
    env = _base_env()
    env["ATLAS_STORE_FOO_SECRET_KEY"] = "RAW-SECRET-should-never-appear"
    fields = build_export(env)
    joined = render_env(fields) + render_json(fields)
    assert "RAW-SECRET-should-never-appear" not in joined
    d = _as_dict(fields)
    assert d.get("ATLAS_STORE_FOO_SECRET_KEY") == ""  # masked to empty


def test_with_secrets_refuses_to_resolve_a_reference_to_an_infra_secret() -> None:
    """A hand-mangled _VAR pointing outside the consumer-scoped MINIO_* namespace
    (e.g. at REDIS_PASSWORD) must stay an unresolved reference even under
    --with-secrets (Finding 3 — enforced invariant, not trusted input)."""
    env = _base_env()
    env["ATLAS_STORE_EVIL_ACCESS_KEY_VAR"] = "REDIS_PASSWORD"
    fields = build_export(env, with_secrets=True)
    joined = render_env(fields) + render_json(fields)
    assert "s3cr3t-should-never-appear" not in joined
    d = _as_dict(fields)
    # the reference is kept verbatim; NO resolved ATLAS_STORE_EVIL_ACCESS_KEY
    assert d["ATLAS_STORE_EVIL_ACCESS_KEY_VAR"] == "REDIS_PASSWORD"
    assert "ATLAS_STORE_EVIL_ACCESS_KEY" not in d


# ── deterministic / byte-stable output ──────────────────────────────

def test_env_and_json_output_are_byte_stable() -> None:
    env = _base_env()
    assert render_env(build_export(env)) == render_env(build_export(env))
    assert render_json(build_export(env)) == render_json(build_export(env))


def test_render_env_and_json_shapes() -> None:
    import json

    fields = build_export(_base_env())
    env_text = render_env(fields)
    assert env_text.endswith("\n")
    assert "ATLAS_KONG_GATEWAY=http://localhost:63000\n" in env_text
    obj = json.loads(render_json(fields))
    assert obj["ATLAS_MINIO_PUBLIC_ENDPOINT"] == "http://localhost:63020"


# ── CLI behavior ────────────────────────────────────────────────────

def test_cli_export_env_to_stdout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from click.testing import CliRunner
    import start as start_module

    env_file = tmp_path / ".env"
    env_file.write_text(render_env([]), encoding="utf-8")  # placeholder
    lines = "".join(f"{k}={v}\n" for k, v in _base_env().items())
    env_file.write_text(lines, encoding="utf-8")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env_file))

    result = CliRunner().invoke(start_module.main, ["endpoints", "export", "--format", "env"])
    assert result.exit_code == 0, result.output
    assert "ATLAS_MINIO_PUBLIC_ENDPOINT=http://localhost:63020" in result.output
    assert "s3cr3t-should-never-appear" not in result.output


def test_cli_with_secrets_refuses_stdout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from click.testing import CliRunner
    import start as start_module

    env_file = tmp_path / ".env"
    env_file.write_text("KONG_HTTP_PORT=63000\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env_file))

    result = CliRunner().invoke(start_module.main, ["endpoints", "export", "--with-secrets"])
    assert result.exit_code == 2
    assert "Refusing to write secrets to stdout" in result.output


def test_cli_export_json_to_output_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from click.testing import CliRunner
    import json
    import start as start_module

    env_file = tmp_path / ".env"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in _base_env().items()), encoding="utf-8")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env_file))
    out = tmp_path / "atlas-consumer.json"

    result = CliRunner().invoke(
        start_module.main,
        ["endpoints", "export", "--format", "json", "--output", str(out)],
    )
    assert result.exit_code == 0, result.output
    obj = json.loads(out.read_text(encoding="utf-8"))
    assert obj["ATLAS_LITELLM_CONTAINER_ENDPOINT"] == "http://litellm:4000"
    assert out.stat().st_mode & 0o777 == 0o600


def test_cli_secret_export_replaces_existing_file_as_owner_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from click.testing import CliRunner
    import start as start_module

    env_file = tmp_path / ".env"
    env_file.write_text(
        "".join(f"{key}={value}\n" for key, value in _base_env().items()),
        encoding="utf-8",
    )
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env_file))
    out = tmp_path / "consumer.env"
    out.write_text("stale\n", encoding="utf-8")
    out.chmod(0o644)

    result = CliRunner().invoke(
        start_module.main,
        ["endpoints", "export", "--with-secrets", "--output", str(out)],
    )

    assert result.exit_code == 0, result.output
    assert out.stat().st_mode & 0o777 == 0o600
    assert "stale" not in out.read_text(encoding="utf-8")


# ── #612: surface the managed-localhost-mps output dir ──────────────────────
def test_comfyui_output_dir_emitted_for_managed_localhost_mps() -> None:
    """Under the managed-MPS source, the host output dir is a known filesystem
    path consumers read images from — surface it (don't make them hardcode it).
    The tilde default is expanded at export time (#644): consumers read the
    artifact programmatically, so a literal `~` would be treated as a
    directory name, and the emitted path must equal the directory the managed
    process actually writes to (the manager expanduser()s the same value)."""
    env = _base_env()
    env["COMFYUI_SOURCE"] = "managed-localhost-mps"
    d = _as_dict(build_export(env))
    expected = f"{Path('~/.atlas/comfyui-mps').expanduser()}/ComfyUI/output"
    assert d["ATLAS_COMFYUI_OUTPUT_DIR"] == expected
    assert "~" not in d["ATLAS_COMFYUI_OUTPUT_DIR"]
    assert d["ATLAS_COMFYUI_OUTPUT_DIR"].startswith("/")


def test_comfyui_output_dir_expands_explicit_tilde_override() -> None:
    """#644: an explicitly-set COMFYUI_MPS_STATE_DIR containing `~` expands too."""
    env = _base_env()
    env["COMFYUI_SOURCE"] = "managed-localhost-mps"
    env["COMFYUI_MPS_STATE_DIR"] = "~/custom/atlas-mps"
    d = _as_dict(build_export(env))
    expected = f"{Path('~/custom/atlas-mps').expanduser()}/ComfyUI/output"
    assert d["ATLAS_COMFYUI_OUTPUT_DIR"] == expected
    assert "~" not in d["ATLAS_COMFYUI_OUTPUT_DIR"]


def test_comfyui_output_dir_respects_state_dir_override() -> None:
    """An absolute COMFYUI_MPS_STATE_DIR passes through unchanged."""
    env = _base_env()
    env["COMFYUI_SOURCE"] = "managed-localhost-mps"
    env["COMFYUI_MPS_STATE_DIR"] = "/opt/atlas/comfyui-mps"
    d = _as_dict(build_export(env))
    assert d["ATLAS_COMFYUI_OUTPUT_DIR"] == "/opt/atlas/comfyui-mps/ComfyUI/output"


def test_comfyui_output_dir_absent_for_container_and_localhost_sources() -> None:
    """Container outputs land in a Docker volume; localhost output dir is the
    user's unknown ComfyUI install — neither is a known host path to surface."""
    for source in ("container", "container-cpu", "localhost", "disabled"):
        env = _base_env()
        env["COMFYUI_SOURCE"] = source
        d = _as_dict(build_export(env))
        assert "ATLAS_COMFYUI_OUTPUT_DIR" not in d, (
            f"output dir should not be surfaced for COMFYUI_SOURCE={source}"
        )
