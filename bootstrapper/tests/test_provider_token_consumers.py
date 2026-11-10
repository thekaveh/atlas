"""Provider credentials reach only trusted server-side consumers."""

from __future__ import annotations

import json
import os
import re
import socketserver
import shutil
import stat
import subprocess
import sys
import threading
from string import Template
from pathlib import Path

import pytest
import yaml

from core.config_parser import ConfigParser
from services.service_config import ServiceConfig


ROOT = Path(__file__).resolve().parents[2]


def _yaml(relative: str) -> dict:
    return yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))


def _service_config(env_path: Path) -> ServiceConfig:
    parser = ConfigParser(str(ROOT))
    parser.env_file_path = env_path
    config = ServiceConfig(config_parser=parser)
    config.localhost_host = "localhost"
    return config


def test_server_consumers_receive_both_provider_tokens_but_lightrag_does_not():
    expected = {
        "DOCLING_API_TOKEN": "${DOCLING_API_TOKEN}",
        "PARAKEET_API_TOKEN": "${PARAKEET_API_TOKEN}",
    }
    for relative, services in (
        ("services/backend/compose.yml", ("backend",)),
        ("services/n8n/compose.yml", ("n8n", "n8n-worker")),
        ("services/jupyterhub/compose.yml", ("jupyterhub",)),
    ):
        compose = _yaml(relative)
        for service in services:
            environment = compose["services"][service]["environment"]
            for name, value in expected.items():
                assert environment[name] == value, f"{relative}:{service}:{name}"

    lightrag = _yaml("services/lightrag/compose.yml")["services"]["lightrag"]
    assert "DOCLING_API_TOKEN" not in lightrag["environment"]
    assert "PARAKEET_API_TOKEN" not in lightrag["environment"]


def test_manifests_declare_server_side_token_adaptation():
    for relative, service in (
        ("services/backend/service.yml", "backend"),
        ("services/n8n/service.yml", "n8n"),
        ("services/jupyterhub/service.yml", "jupyterhub"),
    ):
        manifest = _yaml(relative)
        adaptation = manifest["runtime_adaptive"][service]["environment_adaptation"]
        assert adaptation["DOCLING_API_TOKEN"] == "${DOCLING_API_TOKEN}"
        assert adaptation["PARAKEET_API_TOKEN"] == "${PARAKEET_API_TOKEN}"


@pytest.mark.parametrize(
    "source,expected",
    [
        ("parakeet-container-gpu", "parakeet-test-token"),
        ("parakeet-localhost", "parakeet-test-token"),
        ("speaches-container-cpu", "speaches-test-key"),
        ("speaches-container-gpu", "speaches-test-key"),
        ("whisper-cpp-localhost", "sk-unused"),
        ("disabled", ""),
    ],
)
def test_stt_consumer_key_is_source_aware(env_with_overrides, source, expected):
    config = _service_config(
        env_with_overrides(
            {
                "STT_PROVIDER_SOURCE": source,
                "PARAKEET_API_TOKEN": "parakeet-test-token",
                "SPEACHES_API_KEY": "speaches-test-key",
            }
        )
    )

    generated = config.generate_service_environment()

    assert generated["OPEN_WEB_UI_STT_API_KEY"] == expected
    assert generated["STT_INTERNAL_API_KEY"] == expected


@pytest.mark.parametrize(
    "source,expected",
    [
        ("speaches-container-cpu", "speaches-test-key"),
        ("speaches-container-gpu", "speaches-test-key"),
        ("chatterbox-container-gpu", "sk-unused"),
        ("disabled", ""),
    ],
)
def test_tts_consumer_key_is_source_aware(env_with_overrides, source, expected):
    """Speaches requires API_KEY since #1458, so TTS clients send it."""
    config = _service_config(
        env_with_overrides(
            {"TTS_PROVIDER_SOURCE": source, "SPEACHES_API_KEY": "speaches-test-key"}
        )
    )

    generated = config.generate_service_environment()

    assert generated["OPEN_WEB_UI_TTS_API_KEY"] == expected
    assert generated["TTS_INTERNAL_API_KEY"] == expected


def test_speaches_requires_its_key_and_every_client_sends_it(tmp_path):
    """Without API_KEY, Speaches serves model download and load to any web
    page (#1458). Its clients read the source-aware keys above."""
    speaches = yaml.safe_load((ROOT / "services/speaches/compose.yml").read_text())
    assert speaches["services"]["speaches"]["environment"]["API_KEY"] == "${SPEACHES_API_KEY}"
    webui = yaml.safe_load((ROOT / "services/open-webui/compose.yml").read_text())
    audio = webui["services"]["open-web-ui"]["environment"]
    assert audio["AUDIO_TTS_OPENAI_API_KEY"] == "${OPEN_WEB_UI_TTS_API_KEY:-}"
    assert audio["AUDIO_STT_OPENAI_API_KEY"] == "${OPEN_WEB_UI_STT_API_KEY:-}"
    adaptation = _yaml("services/hermes/service.yml")["runtime_adaptive"]["hermes-init"][
        "environment_adaptation"]
    assert adaptation["TTS_INTERNAL_API_KEY"] == "${TTS_INTERNAL_API_KEY}"

    from utils.key_generator import KeyGenerator
    (tmp_path / ".env").write_text("SPEACHES_API_KEY=\n")
    generator = KeyGenerator(str(tmp_path))
    assert generator.generate_and_update_speaches_api_key()
    key = generator.get_current_env_value("SPEACHES_API_KEY")
    assert len(key) >= 32
    assert generator.generate_and_update_speaches_api_key()
    assert generator.get_current_env_value("SPEACHES_API_KEY") == key


@pytest.mark.parametrize("api_key", ["parakeet-test-token", "sk-unused"])
def test_hermes_template_renders_server_side_stt_key(api_key):
    template = ROOT / "services" / "hermes" / "init" / "templates" / "config.yaml.tmpl"
    environment = os.environ.copy()
    environment.update(
        {
            "HERMES_DEFAULT_MODEL": "test-model",
            "HERMES_CONTEXT_LENGTH": "65536",
            "LITELLM_MASTER_KEY": "litellm-test",
            "LITELLM_MASTER_KEY_YAML": json.dumps("litellm-test"),
            "TTS_INTERNAL_URL": "http://tts",
            "TTS_INTERNAL_MODEL": "tts-1-hd",
            "TTS_INTERNAL_VOICE": "alloy",
            "STT_INTERNAL_URL": "http://stt",
            "STT_INTERNAL_API_KEY": api_key,
            "STT_INTERNAL_API_KEY_YAML": json.dumps(api_key),
            "SEARXNG_INTERNAL_URL": "http://search",
            "LIGHTRAG_INTERNAL_URL": "http://lightrag",
            "LIGHTRAG_API_KEY": "lightrag-test",
            "LITELLM_MODELS_LIST": "[]",
        }
    )
    rendered = Template(template.read_text(encoding="utf-8")).substitute(environment)

    config = yaml.safe_load(rendered)
    # Hermes v2026.6.19 reads only the nested openai blocks; flat keys were
    # ignored and audio went to api.openai.com.
    assert config["stt"]["openai"] == {
        "base_url": "http://stt/v1", "api_key": api_key, "model": "whisper-1",
    }
    assert config["tts"]["openai"]["base_url"] == "http://tts/v1"
    assert "base_url" not in config["stt"] and "base_url" not in config["tts"]
    init = (ROOT / "services/hermes/init/scripts/init-hermes.sh").read_text(encoding="utf-8")
    assert 'STT_INTERNAL_API_KEY="${STT_INTERNAL_API_KEY:-not-required}"' in init
    compose = yaml.safe_load((ROOT / "services/hermes/compose.yml").read_text(encoding="utf-8"))
    assert compose["services"]["hermes"]["environment"]["VOICE_TOOLS_OPENAI_KEY"] == (
        "${TTS_INTERNAL_API_KEY:-not-required}"
    )
    # Hermes reads web.search_backend + the SEARXNG_URL env var (no `search:`),
    # has no config-declared HTTP tools, and indexes only <dir>/SKILL.md.
    assert config["web"] == {"search_backend": "searxng"} and "search" not in config
    assert "tools" not in config
    assert compose["services"]["hermes"]["environment"]["SEARXNG_URL"] == "${SEARXNG_INTERNAL_URL:-}"
    assert '"${SKILLS_DIR}/creative/atlas-comfyui-host/SKILL.md"' in init


def test_hermes_default_model_fallback_skips_non_chat_routes():
    # The name filter ('embed') let bge-m3 become Hermes's default model and
    # 500 every request; the fallback must drop LiteLLM non-chat modes too.
    import re
    import subprocess

    script = (ROOT / "services/hermes/init/scripts/init-hermes.sh").read_text(encoding="utf-8")
    assert 'litellm_get "/model/info"' in script  # the key goes on curl's stdin (--header @-)
    assert 'select((.model_info.mode // "chat") != "chat")' in script
    program = re.search(r"awk '(BEGIN \{ n = split\(ENVIRON\[\"NON_CHAT_IDS\"\].*?)'", script).group(1)
    kept = subprocess.run(
        ["awk", program], input="ollama/bge-m3\nollama/llama3.2:3b\n",
        env={**os.environ, "NON_CHAT_IDS": "ollama/bge-m3\nfal-image"},
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert kept == ["ollama/llama3.2:3b"]


@pytest.mark.parametrize(
    "published,expected",
    [
        (["ollama/qwen3x8:latest"], "ollama/qwen3x8:latest"),
        (["ollama/custom:latest", "ollama/qwen3.8:latest"], "ollama/qwen3.8:latest"),
        (["ollama/first:latest", "ollama/second:latest"], "ollama/first:latest"),
    ],
    ids=["regex-near-match", "exact-priority", "first-chat-fallback"],
)
def test_hermes_default_model_selects_a_published_id(published, expected):
    if not shutil.which("bash") or not shutil.which("jq"):
        pytest.skip("Hermes initializer selection requires bash and jq")
    script = (ROOT / "services/hermes/init/scripts/init-hermes.sh").read_text(encoding="utf-8")
    start = 'if [[ -z "${HERMES_DEFAULT_MODEL}" ]]; then'
    selection = start + script.split(start, 1)[1].split('export TTS_INTERNAL_URL=', 1)[0]
    # Execute the real selection block. Catalog metadata is all-chat; no HTTP
    # request, template write, model pull or mounted workspace is involved.
    harness = (
        'set -euo pipefail\n'
        'log() { :; }\n'
        'litellm_get() { test "$1" = /model/info; printf \'{"data":[]}\'; }\n'
        + selection + '\nprintf "%s" "$HERMES_DEFAULT_MODEL"\n'
    )
    result = subprocess.run(
        ["bash", "-c", harness],
        env={**os.environ, "HERMES_DEFAULT_MODEL": "", "available_ids": "\n".join(published)},
        capture_output=True, text=True, timeout=5, check=True,
    )

    assert result.stderr == ""
    assert result.stdout == expected


def test_hermes_tts_uses_the_engine_voice_open_webui_uses():
    # Speaches' Kokoro has no OpenAI "alloy" voice; hermes-init receives the
    # same per-engine model/voice the bootstrapper computes for Open WebUI.
    template = (ROOT / "services/hermes/init/templates/config.yaml.tmpl").read_text(encoding="utf-8")
    assert "model: ${TTS_INTERNAL_MODEL}" in template
    assert "voice: ${TTS_INTERNAL_VOICE}" in template
    compose = yaml.safe_load((ROOT / "services/hermes/compose.yml").read_text(encoding="utf-8"))
    env = compose["services"]["hermes-init"]["environment"]
    assert env["TTS_INTERNAL_VOICE"] == "${OPEN_WEB_UI_TTS_VOICE:-alloy}"
    assert env["TTS_INTERNAL_MODEL"] == "${OPEN_WEB_UI_TTS_MODEL:-tts-1-hd}"


def _hermes_init_tools(tmp_path, serializer_status):
    tools = {name: shutil.which(name) for name in ("bash", "jq", "envsubst")}
    if not all(tools.values()):
        pytest.skip("Full Hermes init requires bash, jq and envsubst")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("bash", "envsubst"):
        (fake_bin / name).symlink_to(tools[name])
    programs = {
        "curl": (
            "import json, os, sys\n"
            "with open(os.environ['INIT_RECEIPT'], 'a') as output:\n"
            "    output.write(json.dumps({'argv': sys.argv[1:], 'stdin': sys.stdin.read(), "
            "'raw_key': os.environ['LITELLM_MASTER_KEY']}) + '\\n')\n"
            "print(os.environ['INIT_CATALOG'])\n"
        ),
        "jq": (
            "import os, sys\n"
            f"if sys.argv[1:2] == ['-cn'] and {serializer_status}: sys.exit({serializer_status})\n"
            f"os.execv({tools['jq']!r}, [{tools['jq']!r}, *sys.argv[1:]])\n"
        ),
        "chown": (
            "import json, os, sys\n"
            "with open(os.environ['OWNERSHIP_RECEIPT'], 'w') as output:\n"
            "    json.dump(sys.argv[1:], output)\n"
        ),
    }
    for name, program in programs.items():
        executable = fake_bin / name
        executable.write_text(f"#!{sys.executable}\n" + program, encoding="utf-8")
        executable.chmod(0o755)
    return fake_bin


def _run_hermes_init_fixture(tmp_path, overrides, serializer_status=0):
    fake_bin = _hermes_init_tools(tmp_path, serializer_status)
    data = tmp_path / "data"
    (data / "memories").mkdir(parents=True)
    (data / "memories" / "MEMORY.md").write_text("owned memory\n", encoding="utf-8")
    (data / "config.yaml").write_text("previous config\n", encoding="utf-8")
    source = (ROOT / "services/hermes/init/scripts/init-hermes.sh").read_text(encoding="utf-8")
    template_dir = ROOT / "services/hermes/init/templates"
    assert source.count("DATA_DIR=/opt/data") == source.count("TEMPLATE_DIR=/templates") == 1
    source = source.replace("DATA_DIR=/opt/data", f'DATA_DIR="{data}"')
    source = source.replace("TEMPLATE_DIR=/templates", f'TEMPLATE_DIR="{template_dir}"')
    script = tmp_path / "init-hermes.sh"
    script.write_text(source, encoding="utf-8")
    env = {
        "PATH": f"{fake_bin}:{os.defpath}",
        "HERMES_DEFAULT_MODEL": "ollama/qwen3.8:latest",
        "LITELLM_MASTER_KEY": "synthetic-master-key",
        "STT_INTERNAL_URL": "http://synthetic-stt",
        "STT_INTERNAL_API_KEY": "synthetic-stt-key",
        "TTS_INTERNAL_URL": "http://synthetic-tts",
        "SEARXNG_INTERNAL_URL": "http://synthetic-search",
        "INIT_RECEIPT": str(tmp_path / "requests.jsonl"),
        "OWNERSHIP_RECEIPT": str(tmp_path / "ownership.json"),
        "INIT_CATALOG": json.dumps({"data": [{"id": name} for name in (
            "ollama/qwen3.8:latest", "qwen3.8:latest", "hermes-agent",
            "openrouter/openai/gpt-5", "gpt-5",
        )]}),
    }
    env.update({key: value for key, value in overrides.items() if value is not None})
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
    result = subprocess.run(
        ["/bin/sh", "-c", 'umask 027\nexec /bin/sh "$1"', "hermes-fixture", str(script)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15,
    )
    requests = [json.loads(line) for line in (tmp_path / "requests.jsonl").read_text().splitlines()]
    return result, data, requests


@pytest.mark.parametrize("field", ["LITELLM_MASTER_KEY", "STT_INTERNAL_API_KEY"])
@pytest.mark.parametrize("value", [
    "synthetic-key", "true", "12345678901234567890", "synthetic #suffix",
    "synthetic: suffix", 'synthetic"quote', r"synthetic\backslash",
    "synthetic$HOME", "synthetic'quote", "synthetic-雪",
])
def test_hermes_full_init_preserves_literal_yaml_credentials(tmp_path, field, value):
    result, data, requests = _run_hermes_init_fixture(tmp_path, {field: value})
    assert result.returncode == 0, result.stderr
    config = yaml.safe_load((data / "config.yaml").read_text(encoding="utf-8"))
    expected_master = value if field == "LITELLM_MASTER_KEY" else "synthetic-master-key"
    expected_stt = value if field == "STT_INTERNAL_API_KEY" else "synthetic-stt-key"
    assert config["model"]["api_key"] == expected_master
    assert config["stt"]["openai"]["api_key"] == expected_stt
    assert all(isinstance(key, str) for key in (config["model"]["api_key"], config["stt"]["openai"]["api_key"]))
    assert requests == [{
        "argv": ["-fsS", "--max-time", "15", "--header", "@-", "http://litellm:4000/v1/models"],
        "stdin": f"Authorization: Bearer {expected_master}\n", "raw_key": expected_master,
    }]
    assert value not in result.stdout.replace(str(tmp_path), "<fixture>") + result.stderr


@pytest.mark.parametrize("stt_key", [None, ""])
@pytest.mark.parametrize("master_key", [None, ""])
def test_hermes_full_init_keeps_blank_credential_defaults(tmp_path, stt_key, master_key):
    result, data, requests = _run_hermes_init_fixture(
        tmp_path, {"STT_INTERNAL_API_KEY": stt_key, "LITELLM_MASTER_KEY": master_key},
    )
    assert result.returncode == 0, result.stderr
    config = yaml.safe_load((data / "config.yaml").read_text(encoding="utf-8"))
    assert config["model"]["api_key"] == ""
    assert config["stt"]["openai"]["api_key"] == "not-required"
    assert requests[0]["raw_key"] == "" and requests[0]["stdin"] == 'Authorization: Bearer \n'


def _assert_hermes_init_artifacts(tmp_path, data):
    assert (data / "memories" / "MEMORY.md").read_text() == "owned memory\n"
    assert stat.S_IMODE((data / "config.yaml").stat().st_mode) == 0o640
    assert not (data / "config.yaml.tmp").exists()
    assert json.loads((tmp_path / "ownership.json").read_text()) == ["-R", "10000:10000", str(data)]


def test_hermes_full_init_preserves_models_blocks_output_and_ownership(tmp_path):
    result, data, requests = _run_hermes_init_fixture(tmp_path, {
        "HERMES_DEFAULT_MODEL": "", "STT_INTERNAL_URL": "", "TTS_INTERNAL_URL": "",
        "SEARXNG_INTERNAL_URL": "", "STT_INTERNAL_API_KEY": "true",
    })
    assert result.returncode == 0, result.stderr
    config = yaml.safe_load((data / "config.yaml").read_text(encoding="utf-8"))
    assert config["model"]["default"] == "ollama/qwen3.8:latest"
    assert config["providers"]["litellm"]["models"] == ["ollama/qwen3.8:latest", "openrouter/openai/gpt-5", "gpt-5"]
    assert not {"tts", "stt", "web"}.intersection(config)
    _assert_hermes_init_artifacts(tmp_path, data)
    assert result.stdout.endswith("[hermes-init] done\n") and result.stderr == ""
    assert len(requests) == 1


def test_hermes_full_init_serializer_failure_preserves_published_config(tmp_path):
    result, data, requests = _run_hermes_init_fixture(tmp_path, {}, serializer_status=43)
    assert result.returncode == 43
    assert (data / "config.yaml").read_text() == "previous config\n"
    assert not (data / "config.yaml.tmp").exists()
    assert (data / "memories" / "MEMORY.md").read_text() == "owned memory\n"
    assert not (tmp_path / "ownership.json").exists()
    assert "wrote " not in result.stdout and not result.stdout.endswith("[hermes-init] done\n")
    assert len(requests) == 1


class _HermesAuthorizationRecorder(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(2)
        headers = []
        for _ in range(64):
            line = self.rfile.readline(8193)
            if not line or line in (b"\r\n", b"\n"):
                break
            if line.lower().startswith(b"authorization:"):
                value = line.rstrip(b"\r\n").split(b":", 1)[1]
                headers.append(value[1:] if value.startswith(b" ") else value)
        self.server.observed.append(headers)
        status = self.server.status if headers == [self.server.expected] else 401
        body = b'{"data":[]}'
        self.wfile.write(
            f"HTTP/1.1 {status} Owned\r\nContent-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n".encode() + body
        )


@pytest.fixture
def hermes_owned_http():
    server = socketserver.TCPServer(("127.0.0.1", 0), _HermesAuthorizationRecorder)
    server.observed = []
    server.status = 200
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
        assert not thread.is_alive()
        assert server.socket.fileno() == -1


def _hermes_http_program(route, endpoint):
    if route == "init":
        source = (ROOT / "services/hermes/init/scripts/init-hermes.sh").read_text()
        function = re.search(r"^litellm_get\(\) \{\n.*?^\}", source, re.M | re.S).group()
        return ["/bin/bash", "-c", (
            'set -euo pipefail\nlitellm_url="$LITELLM_BASE_URL"\n'
            + function + '\nlitellm_get "/v1/models"\n'
        )]
    health = _yaml("services/hermes/compose.yml")["services"]["hermes"]["healthcheck"]["test"]
    assert health[0] == "CMD-SHELL" and health[1].count("http://127.0.0.1:8642") == 1
    return ["/bin/sh", "-c", health[1].replace("$$", "$").replace("http://127.0.0.1:8642", endpoint)]


def _run_hermes_http(tmp_path, server, route, value):
    curl = shutil.which("curl")
    if not curl:
        pytest.skip("Hermes HTTP wire controls require native curl")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    receipt = tmp_path / "curl-argv.json"
    wrapper = fake_bin / "curl"
    wrapper.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        "with open(os.environ['CURL_ARGV_RECEIPT'], 'w') as output:\n"
        "    json.dump(sys.argv[1:], output)\n"
        f"os.execv({curl!r}, [{curl!r}, *sys.argv[1:]])\n"
    )
    wrapper.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    endpoint = f"http://127.0.0.1:{server.server_address[1]}"
    server.expected = b"Bearer " + value.encode()
    env = {
        "PATH": f"{fake_bin}:{os.defpath}", "HOME": str(home), "CURL_HOME": str(home),
        "NO_PROXY": "*", "no_proxy": "*", "LITELLM_MASTER_KEY": value,
        "API_SERVER_KEY": value, "LITELLM_BASE_URL": endpoint,
        "CURL_ARGV_RECEIPT": str(receipt),
    }
    result = subprocess.run(
        _hermes_http_program(route, endpoint), cwd=tmp_path, env=env,
        capture_output=True, timeout=5,
    )
    argv = json.loads(receipt.read_text())
    assert argv[-1] == endpoint + "/v1/models"
    assert not any(value and value in argument for argument in argv)
    return result


@pytest.mark.parametrize("route", ["init", "healthcheck"])
@pytest.mark.parametrize("value", [
    "synthetic-ordinary-key", "synthetic#hash-key", "synthetic$dollar-key",
    "synthetic space key", "synthetic'quote-key", 'synthetic"quote-key',
    r"synthetic\backslash-key", r"synthetic\\backslash-key", r"synthetic\tliteral-key",
    " leading-key", "trailing-key ", "",
])
def test_hermes_actual_curl_preserves_authorization(tmp_path, hermes_owned_http, route, value):
    from utils.atomic_write import decode_env_assignment, render_env_assignment

    key = "LITELLM_MASTER_KEY" if route == "init" else "HERMES_API_KEY"
    assert decode_env_assignment(key + "=" + render_env_assignment(key, value), origin="owned-private.env") == (key, value)
    result = _run_hermes_http(tmp_path, hermes_owned_http, route, value)
    assert hermes_owned_http.observed == [[b"Bearer " + value.encode()]]
    assert result.returncode == 0, result.stderr
    assert result.stderr == b""
    assert result.stdout == (b'{"data":[]}' if route == "init" else b"")


@pytest.mark.parametrize("route", ["init", "healthcheck"])
@pytest.mark.parametrize("status", [401, 503])
def test_hermes_actual_curl_preserves_http_failure(tmp_path, hermes_owned_http, route, status):
    hermes_owned_http.status = status
    result = _run_hermes_http(tmp_path, hermes_owned_http, route, 'synthetic-failure-key')
    assert hermes_owned_http.observed == [[hermes_owned_http.expected]]
    assert result.returncode == 22
    assert str(status).encode() in result.stderr
    assert result.stdout == b""


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("provider,key_var", [
    ("OPENAI", "OPENAI_API_KEY"), ("ANTHROPIC", "ANTHROPIC_API_KEY"), ("OPENROUTER", "OPENROUTER_API_KEY"),
])
def test_cloud_keys_reach_hermes_and_graph_builder_only_when_enabled(
    env_with_overrides, provider, key_var, enabled
):
    """Disabling a provider used to remove only its LiteLLM routes (#1886)."""
    config = _service_config(env_with_overrides({
        f"CLOUD_{provider}_SOURCE": "enabled" if enabled else "disabled",
        key_var: "sk-test-provider-key",
    }))

    generated = config.generate_service_environment()

    expected = "sk-test-provider-key" if enabled else ""
    assert generated[f"HERMES_{key_var}"] == expected
    if key_var == "OPENAI_API_KEY":
        assert generated["LLM_GRAPH_BUILDER_OPENAI_API_KEY"] == expected


def test_hermes_and_graph_builder_read_only_the_gated_cloud_keys():
    hermes = yaml.safe_load((ROOT / "services/hermes/compose.yml").read_text())
    hermes_env = hermes["services"]["hermes"]["environment"]
    for key_var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        assert hermes_env[key_var] == f"${{HERMES_{key_var}:-}}"
    builder = yaml.safe_load((ROOT / "services/llm-graph-builder/compose.yml").read_text())
    builder_env = next(iter(builder["services"].values()))["environment"]
    assert builder_env["OPENAI_API_KEY"] == "${LLM_GRAPH_BUILDER_OPENAI_API_KEY:-}"


# ── #1885: Weaviate holds an embedding-only LiteLLM key, not the master key ──

_WEAVIATE_INIT = ROOT / "services/weaviate/init/scripts/init-weaviate.sh"
# A fake busybox nc: record argv and the request it receives on stdin, then
# answer with the status line named by $FAKE_NC_STATUS (one per call).
_FAKE_NC = r"""#!/bin/sh
# Answer as soon as the request is complete, before stdin closes: the init
# script keeps stdin open until it sees the status line, as LiteLLM needs.
n=$(cat "$NC_DIR/count" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$NC_DIR/count"
printf '%s\n' "$*" > "$NC_DIR/argv-$n"
req="$NC_DIR/request-$n"; : > "$req"; cr=$(printf '\r'); len=0
while IFS= read -r line; do
  printf '%s\n' "$line" >> "$req"
  case "$line" in Content-Length:*) len=$(printf '%s' "${line#Content-Length: }" | tr -d '\r') ;; esac
  [ "$line" = "$cr" ] && break
done
[ "$len" -gt 0 ] && head -c "$len" >> "$req"
status=$(echo "$FAKE_NC_STATUS" | cut -d, -f"$n")
printf 'HTTP/1.1 %s\r\n\r\n{}' "$status"
cat > /dev/null
"""


def _run_weaviate_init(tmp_path: Path, statuses: str, **env: str) -> subprocess.CompletedProcess[str]:
    bin_dir, shared = tmp_path / "bin", tmp_path / "shared"
    bin_dir.mkdir()
    shared.mkdir()
    fake = bin_dir / "nc"
    fake.write_text(_FAKE_NC, encoding="utf-8")
    fake.chmod(0o755)
    script = (tmp_path / "init.sh")
    script.write_text(_WEAVIATE_INIT.read_text(encoding="utf-8").replace("/shared/", f"{shared}/"),
                      encoding="utf-8")
    run_env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "NC_DIR": str(tmp_path),
               "FAKE_NC_STATUS": statuses, "LITELLM_MASTER_KEY": "sk-master-1885",
               "WEAVIATE_LITELLM_API_KEY": "sk-atlas-weaviate-1885", **env}
    return subprocess.run(["/bin/sh", str(script)], env=run_env, text=True,
                          capture_output=True, check=False)


def test_weaviate_gets_the_scoped_key_and_init_gets_what_it_needs_to_register_it():
    compose = _yaml("services/weaviate/compose.yml")["services"]
    assert compose["weaviate"]["environment"]["OPENAI_APIKEY"] == "${WEAVIATE_LITELLM_API_KEY:-}"
    init_env = compose["weaviate-init"]["environment"]
    assert init_env["WEAVIATE_LITELLM_API_KEY"] == "${WEAVIATE_LITELLM_API_KEY}"
    assert init_env["LITELLM_MASTER_KEY"] == "${LITELLM_MASTER_KEY}"
    assert "LITELLM_MASTER_KEY" not in json.dumps(compose["weaviate"]["environment"])


def _request(tmp_path: Path, n: int) -> tuple[str, dict]:
    head, body = (tmp_path / f"request-{n}").read_bytes().decode().split("\r\n\r\n", 1)
    return head, json.loads(body)


def test_weaviate_init_refreshes_its_embedding_only_key_without_secrets_in_argv(tmp_path):
    result = _run_weaviate_init(tmp_path, "200 OK")
    head, body = _request(tmp_path, 1)

    assert result.returncode == 0, result.stderr
    assert head.startswith("POST /key/update HTTP/1.0")
    assert "Authorization: Bearer sk-master-1885" in head
    assert body == {"key": "sk-atlas-weaviate-1885", "models": ["atlas-embeddings"]}
    assert (tmp_path / "argv-1").read_text().split() == ["-w", "30", "litellm", "4000"]
    assert not (tmp_path / "request-2").exists()


def test_weaviate_init_replaces_a_missing_or_rotated_key_and_stops_on_failure(tmp_path):
    for name in ("new", "fail", "missing"):
        (tmp_path / name).mkdir()

    created = _run_weaviate_init(tmp_path / "new", "404 Not Found,200 OK,200 OK")
    assert created.returncode == 0, created.stderr
    assert [_request(tmp_path / "new", n)[0].split()[1] for n in (1, 2, 3)] == [
        "/key/update", "/key/delete", "/key/generate"]
    assert _request(tmp_path / "new", 2)[1] == {"key_aliases": ["atlas-weaviate"]}
    assert _request(tmp_path / "new", 3)[1] == {
        "key": "sk-atlas-weaviate-1885", "key_alias": "atlas-weaviate",
        "models": ["atlas-embeddings"]}

    failed = _run_weaviate_init(tmp_path / "fail", "500 Error,500 Error,500 Error")
    assert (failed.returncode, "could not register" in failed.stderr) == (1, True)
    missing = _run_weaviate_init(tmp_path / "missing", "200 OK", WEAVIATE_LITELLM_API_KEY="")
    assert missing.returncode == 1


def test_weaviate_litellm_key_is_generated_once_with_the_sk_prefix(tmp_path):
    from utils.key_generator import KeyGenerator
    (tmp_path / ".env").write_text("WEAVIATE_LITELLM_API_KEY=\n")
    generator = KeyGenerator(str(tmp_path))
    assert generator.generate_and_update_weaviate_litellm_api_key()
    key = generator.get_current_env_value("WEAVIATE_LITELLM_API_KEY")
    assert key.startswith("sk-atlas-weaviate-") and len(key) > 40
    assert generator.generate_and_update_weaviate_litellm_api_key()
    assert generator.get_current_env_value("WEAVIATE_LITELLM_API_KEY") == key
