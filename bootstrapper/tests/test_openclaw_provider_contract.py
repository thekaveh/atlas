from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = ROOT / "services" / "openclaw"
COMPOSE = SERVICE_DIR / "compose.yml"
MANIFEST = SERVICE_DIR / "service.yml"
README = SERVICE_DIR / "README.md"
ENV_EXAMPLE = ROOT / ".env.example"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def test_openclaw_direct_provider_keys_do_not_fall_back_to_stack_openai_key() -> None:
    gateway_env = _compose()["services"]["openclaw-gateway"]["environment"]

    assert gateway_env["OPENAI_API_KEY"] == "${OPENCLAW_OPENAI_API_KEY:-}"
    assert gateway_env["ANTHROPIC_API_KEY"] == "${OPENCLAW_ANTHROPIC_API_KEY:-}"
    assert "OPENAI_API_KEY:-${OPENAI_API_KEY" not in COMPOSE.read_text(encoding="utf-8")

    service_env = {entry["name"]: entry for entry in _manifest()["env"]}
    description = service_env["OPENCLAW_OPENAI_API_KEY"]["description"]
    assert "bypass" in description
    assert "stack-wide OPENAI_API_KEY" not in description


def test_openclaw_container_ports_follow_topology_defaults() -> None:
    ports = _compose()["services"]["openclaw-gateway"]["ports"]

    assert "${HOST_BIND_IP-127.0.0.1:}${OPENCLAW_GATEWAY_PORT:-63076}:18789" in ports
    assert "${HOST_BIND_IP-127.0.0.1:}${OPENCLAW_BRIDGE_PORT:-63077}:18790" in ports

    readme = README.read_text(encoding="utf-8")
    assert "default 63076" in readme
    assert "63076/63077" in readme
    assert "defaults to 63065" in readme  # localhost source stays intentionally separate.


def test_openclaw_current_data_flow_excludes_future_hermes_bridge() -> None:
    manifest = _manifest()
    readme = README.read_text(encoding="utf-8")

    # #1273: operators set the provider baseUrl by hand, so the edge is optional.
    assert [(entry["target"], entry["status"]) for entry in manifest["data_flow"]["calls"]] == [
        ("litellm", "optional")
    ]
    assert "| hermes | agents |" not in readme
    assert "openclaw ↔ hermes" in readme
    assert "only the bridge wiring is missing" in readme


def test_openclaw_env_example_documents_gateway_and_localhost_ports_separately() -> None:
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")

    assert "OPENCLAW_GATEWAY_PORT=63076" in env_example
    assert "OPENCLAW_BRIDGE_PORT=63077" in env_example
    assert "OPENCLAW_LOCALHOST_PORT=63065" in env_example
    assert "stack-wide OPENAI_API_KEY" not in env_example


def test_key_generator_creates_and_keeps_the_openclaw_gateway_token(tmp_path: Path) -> None:
    """Empty, the LAN-bound gateway minted a random per-start token nobody
    could read back, locking the dashboard and API."""
    from core.config_parser import ConfigParser
    from utils.key_generator import KeyGenerator

    (tmp_path / ".env").write_text("PROJECT_NAME=atlas-test\nOPENCLAW_GATEWAY_TOKEN=\n")
    assert KeyGenerator(str(tmp_path)).generate_missing_keys()["OPENCLAW_GATEWAY_TOKEN"] is True
    token = ConfigParser(str(tmp_path)).parse_env_file()["OPENCLAW_GATEWAY_TOKEN"]
    assert len(token) >= 32

    KeyGenerator(str(tmp_path)).generate_missing_keys()
    assert ConfigParser(str(tmp_path)).parse_env_file()["OPENCLAW_GATEWAY_TOKEN"] == token


def test_key_generator_creates_the_shared_meta_crypto_key(tmp_path: Path) -> None:
    """Unset, Studio and Postgres Meta fall back to the public "SAMPLE_KEY"."""
    from core.config_parser import ConfigParser
    from utils.key_generator import KeyGenerator

    (tmp_path / ".env").write_text("PROJECT_NAME=atlas-test\nSUPABASE_META_CRYPTO_KEY=\n")
    assert KeyGenerator(str(tmp_path)).generate_missing_keys()["SUPABASE_META_CRYPTO_KEY"] is True
    key = ConfigParser(str(tmp_path)).parse_env_file()["SUPABASE_META_CRYPTO_KEY"]
    assert len(key) >= 32
    compose = yaml.safe_load((Path(__file__).resolve().parents[2] / "services/supabase/compose.yml").read_text())
    # Each image's own name: postgres-meta reads CRYPTO_KEY, Studio
    # PG_META_CRYPTO_KEY. Meta under Studio's name fell back to SAMPLE_KEY.
    assert compose["services"]["supabase-meta"]["environment"]["CRYPTO_KEY"] == "${SUPABASE_META_CRYPTO_KEY:-}"
    assert "PG_META_CRYPTO_KEY" not in compose["services"]["supabase-meta"]["environment"]
    assert compose["services"]["supabase-studio"]["environment"]["PG_META_CRYPTO_KEY"] == "${SUPABASE_META_CRYPTO_KEY:-}"


def test_openclaw_init_points_the_bundled_litellm_provider_at_the_gateway() -> None:
    # The bundled litellm plugin reads LITELLM_API_KEY but defaults its base
    # URL to localhost:4000 (unreachable in-container); set it only if unset.
    import json
    import shutil
    import subprocess

    init = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["openclaw-init"]
    script = init["entrypoint"][-1]
    filter_line = next(line for line in script.splitlines() if ".models.providers.litellm.baseUrl" in line)
    program = filter_line.split("jq '", 1)[1].split("'", 1)[0]
    assert program == (
        '.models.providers.litellm.baseUrl //= "http://litellm:4000" '
        "| .models.providers.litellm.models //= []"
    )
    if shutil.which("jq"):
        kept = '{"models":{"providers":{"litellm":{"baseUrl":"http://x","models":[{"id":"a"}]}}}}'
        out = subprocess.run(["jq", "-c", program], input=kept, capture_output=True, text=True, check=True)
        assert json.loads(out.stdout) == json.loads(kept)
