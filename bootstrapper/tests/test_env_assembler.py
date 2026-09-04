"""Tests for bootstrapper.services.env_assembler."""

from __future__ import annotations

from pathlib import Path

from services.env_assembler import assemble_env_example
from services.manifests import load_manifests


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SYNTHETIC_CAPABILITY_YAML = (
    "capabilities:\n"
    "  - name: Synthetic environment contract\n"
    "    status: supported\n"
    "    verification: tested\n"
    "    note: Tests exercise this synthetic environment manifest.\n"
)


def test_emits_generated_header(services_root, write_manifest, minimal_manifest_dict):
    write_manifest("redis", minimal_manifest_dict("redis"))
    manifests = load_manifests(services_root)
    out = assemble_env_example(manifests)
    assert "GENERATED" in out
    assert "service.yml" in out


def test_empty_manifest_list_still_emits_header():
    out = assemble_env_example([])
    assert "GENERATED" in out
    # Even with no manifests, the file should be a valid env-file (zero declarations).


def test_configless_virtual_graph_node_does_not_emit_empty_banner(
    services_root, write_manifest, minimal_manifest_dict
):
    manifest = minimal_manifest_dict("internal-adapter")
    manifest.update({"virtual": True, "env": []})
    write_manifest("internal-adapter", manifest)

    out = assemble_env_example(
        load_manifests(services_root), services_root=services_root
    )

    assert "services/internal-adapter/service.yml" not in out


def test_emits_env_var_with_default_and_description(
    services_root, write_manifest, minimal_manifest_dict
):
    m = minimal_manifest_dict("redis")
    m["env"] = [
        {
            "name": "REDIS_PORT",
            "default": 6379,
            "description": "Host port for Redis.",
        }
    ]
    write_manifest("redis", m)
    manifests = load_manifests(services_root)
    out = assemble_env_example(manifests, services_root=services_root)
    # Port default comes from topology slot allocator (data category, first slot:
    # base_port=63000 + data_offset=10 + slot=0 → 63010), not the manifest default.
    assert "REDIS_PORT=63010" in out
    assert "Host port for Redis." in out


def test_omits_default_for_auto_managed_vars(
    services_root, write_manifest, minimal_manifest_dict
):
    m = minimal_manifest_dict("ollama")
    m["category"] = "llm"
    m["env"] = [
        {"name": "OLLAMA_SCALE", "auto_managed": True, "description": "Computed."},
        {"name": "OLLAMA_PORT", "default": 11434},
    ]
    write_manifest("ollama", m)
    manifests = load_manifests(services_root)
    out = assemble_env_example(manifests, services_root=services_root)
    # auto_managed vars are documented but their value is left empty (no default shown).
    assert "OLLAMA_SCALE=" in out
    assert "auto_managed" in out.lower() or "auto-managed" in out.lower()
    # Non-auto-managed port value comes from topology slot allocator, not manifest default.
    # llm category, first slot: base_port=63000 + llm_offset=40 + slot=0 → 63040.
    assert "OLLAMA_PORT=63040" in out


def test_emits_image_vars(services_root, write_manifest, full_manifest_dict):
    write_manifest("ollama", full_manifest_dict("ollama"))
    manifests = load_manifests(services_root)
    out = assemble_env_example(manifests)
    assert "LLM_PROVIDER_IMAGE=ollama/ollama:0.30.11" in out
    assert "OLLAMA_PULL_IMAGE=alpine/curl:latest" in out


def test_emits_source_var_with_options_comment(
    services_root, write_manifest, full_manifest_dict
):
    write_manifest("ollama", full_manifest_dict("ollama"))
    manifests = load_manifests(services_root)
    out = assemble_env_example(manifests)
    assert "LLM_PROVIDER_SOURCE=ollama-container-cpu" in out
    # Comment lists every option id so users editing .env see their choices.
    assert "ollama-container-cpu" in out
    assert "ollama-localhost" in out


def test_per_manifest_banner_includes_label_and_path(
    services_root, write_manifest, minimal_manifest_dict
):
    write_manifest("redis", minimal_manifest_dict("redis"))
    manifests = load_manifests(services_root)
    out = assemble_env_example(manifests)
    assert "Redis service" in out
    assert "services/redis/service.yml" in out


def test_ordering_respects_provided_order(
    services_root, write_manifest, minimal_manifest_dict
):
    write_manifest("redis", minimal_manifest_dict("redis"))
    write_manifest("backend", minimal_manifest_dict("backend") | {"category": "apps"})
    write_manifest("ollama", minimal_manifest_dict("ollama") | {"category": "llm"})
    manifests = load_manifests(services_root)
    # Without an order arg, alphabetical by folder name.
    out_default = assemble_env_example(manifests)
    p_backend = out_default.index("services/backend/service.yml")
    p_ollama = out_default.index("services/ollama/service.yml")
    p_redis = out_default.index("services/redis/service.yml")
    assert p_backend < p_ollama < p_redis

    # With explicit order, output respects it.
    out_ordered = assemble_env_example(manifests, order=["ollama", "redis", "backend"])
    q_ollama = out_ordered.index("services/ollama/service.yml")
    q_redis = out_ordered.index("services/redis/service.yml")
    q_backend = out_ordered.index("services/backend/service.yml")
    assert q_ollama < q_redis < q_backend


def test_order_with_missing_service_falls_back_to_alphabetical(
    services_root, write_manifest, minimal_manifest_dict
):
    """Services not mentioned in `order` are appended at the end, alphabetically."""
    write_manifest("redis", minimal_manifest_dict("redis"))
    write_manifest("backend", minimal_manifest_dict("backend") | {"category": "apps"})
    manifests = load_manifests(services_root)
    out = assemble_env_example(manifests, order=["redis"])
    p_redis = out.index("services/redis/service.yml")
    p_backend = out.index("services/backend/service.yml")
    assert p_redis < p_backend


def test_secret_var_emits_manifest_default_as_placeholder(
    services_root, write_manifest, minimal_manifest_dict
):
    """`secret: true` controls LOGGING, not whether the manifest's
    `default` flows into .env.example.

    Earlier behavior blanked every secret unconditionally, which broke
    fresh installs (supabase-db-init couldn't authenticate with a blank
    SUPABASE_DB_PASSWORD). Now the manifest's `default` is treated as a
    development placeholder — a manifest that wants a runtime-generated
    secret leaves `default: ""` (e.g., LITELLM_MASTER_KEY).
    """
    m = minimal_manifest_dict("redis")
    m["env"] = [
        {"name": "REDIS_PASSWORD", "default": "redis_password", "secret": True},
        {"name": "REDIS_AUTOGEN", "default": "", "secret": True},
    ]
    write_manifest("redis", m)
    manifests = load_manifests(services_root)
    out = assemble_env_example(manifests)
    # Placeholder default flows through for the dev-bootstrap case.
    assert "REDIS_PASSWORD=redis_password" in out
    # Empty manifest default → empty .env.example entry (autogen at runtime).
    assert "REDIS_AUTOGEN=\n" in out or out.endswith("REDIS_AUTOGEN=\n")


def test_output_is_deterministic(services_root, write_manifest, minimal_manifest_dict):
    """Re-rendering the same manifests must produce byte-identical output."""
    write_manifest("redis", minimal_manifest_dict("redis"))
    write_manifest("backend", minimal_manifest_dict("backend") | {"category": "apps"})
    manifests = load_manifests(services_root)
    a = assemble_env_example(manifests)
    b = assemble_env_example(manifests)
    assert a == b


def test_committed_env_example_matches_assembler_output():
    """The committed ``.env.example`` at the repo root must be byte-identical
    to ``assemble_env_example(load_manifests(services/))``.

    This catches the "someone hand-edited .env.example without regenerating
    it" failure mode. If this test fails, run:

        uv run --project bootstrapper python -m services.env_assembler

    and commit the resulting .env.example.
    """
    env_example_path = _REPO_ROOT / ".env.example"
    # `.env.example` is a committed artifact regenerated from the manifests
    # by `bootstrapper/services/env_assembler.py`. Missing it is not a
    # CI-safe skip condition — it indicates a broken repo. Fail loudly.
    if not env_example_path.is_file():
        import pytest as _pytest
        _pytest.fail(
            f"{env_example_path} is missing. Regenerate with:\n"
            f"  uv run --project bootstrapper python -m services.env_assembler"
        )

    expected = assemble_env_example(load_manifests(_REPO_ROOT / "services"))
    actual = env_example_path.read_text()
    assert actual == expected, (
        ".env.example is out of sync with the manifests. "
        "Regenerate with: uv run --project bootstrapper python -m services.env_assembler"
    )


def test_host_bind_ip_defaults_to_loopback_with_empty_value_upgrade_guidance():
    manifests = load_manifests(_REPO_ROOT / "services")
    assembled = assemble_env_example(manifests)

    assert "HOST_BIND_IP=127.0.0.1:" in assembled
    assert "Existing .env files with HOST_BIND_IP= are upgraded" in assembled


def test_backend_declares_and_forwards_comfyui_trusted_image_origins():
    manifests = {
        manifest.name: manifest
        for manifest in load_manifests(_REPO_ROOT / "services")
    }
    backend_env = {decl.name: decl for decl in manifests["backend"].env}

    trusted_origins = backend_env["COMFYUI_INIT_IMAGE_TRUSTED_ORIGINS"]
    assert trusted_origins.default == ""
    assert "exact HTTPS origins" in trusted_origins.description

    compose = (_REPO_ROOT / "services/backend/compose.yml").read_text()
    assert (
        "COMFYUI_INIT_IMAGE_TRUSTED_ORIGINS: "
        "${COMFYUI_INIT_IMAGE_TRUSTED_ORIGINS:-}"
    ) in compose

    assembled = assemble_env_example(manifests.values())
    assert "COMFYUI_INIT_IMAGE_TRUSTED_ORIGINS=\n" in assembled


def test_multiline_description_each_line_commented(tmp_path):
    """Multi-line description must have every line prefixed with '# '."""
    services_root = tmp_path / "services"
    (services_root / "demo").mkdir(parents=True)
    (services_root / "demo" / "service.yml").write_text(
        "name: demo\n"
        "label: Demo\n"
        "category: data\n"
        "env:\n"
        "  - name: DEMO_VAR\n"
        "    default: value\n"
        "    description: |\n"
        "      Line one.\n"
        "      Line two.\n"
        "      Line three.\n"
        + _SYNTHETIC_CAPABILITY_YAML
    )
    manifests = load_manifests(services_root)
    output = assemble_env_example(manifests, services_root=services_root)
    # Every line of the description must be commented.
    assert "# Line one." in output
    assert "# Line two." in output
    assert "# Line three." in output
    # Bare uncommented continuation lines must NOT appear.
    lines = output.splitlines()
    for line in lines:
        if "Line two" in line or "Line three" in line:
            assert line.startswith("#"), f"description line not commented: {line!r}"


def test_secret_var_with_manifest_default_emits_placeholder(tmp_path):
    """Secret-marked vars still emit their manifest default (development
    placeholder) into .env.example. The `secret: true` flag governs
    logging behavior, not whether the value flows into the example.

    Regression: a previous refactor emitted `SUPABASE_DB_PASSWORD=`
    (blank) for every `secret: true` entry, breaking the stack on
    first start because supabase-db-init couldn't authenticate.
    """
    services_root = tmp_path / "services"
    (services_root / "demo").mkdir(parents=True)
    (services_root / "demo" / "service.yml").write_text(
        "name: demo\n"
        "label: Demo\n"
        "category: data\n"
        "env:\n"
        "  - name: DEMO_PASSWORD\n"
        "    default: placeholder\n"
        "    secret: true\n"
        "  - name: DEMO_TRUE_SECRET\n"
        "    default: \"\"\n"
        "    secret: true\n"
        + _SYNTHETIC_CAPABILITY_YAML
    )
    from services.env_assembler import assemble_env_example
    from services.manifests import load_manifests
    out = assemble_env_example(load_manifests(services_root),
                                services_root=services_root)
    # Manifest default flows through even with `secret: true`.
    assert "DEMO_PASSWORD=placeholder" in out
    # Empty default still emits blank (auto-generated at runtime).
    assert "DEMO_TRUE_SECRET=" in out
    assert "DEMO_TRUE_SECRET=placeholder" not in out
