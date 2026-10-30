"""
Source-permutation matrix.

For every SOURCE-configurable service, iterate every valid SOURCE value, write
it into a throwaway .env, and assert `docker compose config -q` succeeds. This
proves that:

  - Every declared source variant produces a parseable compose shape.
  - The `${VAR:-default}` patterns and `replicas: ${X_SCALE:-N}` fallbacks
    behave correctly when AUTO-MANAGED env vars are at their defaults.
  - No fragment hard-codes a source-specific value that breaks parse-time.

Skipped if `docker` is not on PATH or `.env.example` is missing.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE = REPO_ROOT / "docker-compose.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"


def _docker_available() -> bool:
    return shutil.which("docker") is not None


pytestmark = pytest.mark.skipif(
    not _docker_available() or not ENV_EXAMPLE.is_file(),
    reason="docker not on PATH or .env.example missing",
)


def _manifest_permutations() -> list[tuple[str, list[str]]]:
    """Derive the matrix from the manifest contract so new sources cannot be omitted."""
    from services.manifests import load_manifests

    return [
        (manifest.sources.var, [option.id for option in manifest.sources.options])
        for manifest in load_manifests(REPO_ROOT / "services")
        if manifest.sources is not None
    ]


# Cloud providers predate the single-``sources`` block: one virtual manifest
# deliberately owns three independent toggles. Keep only this irreducible
# multi-source exception explicit; every normal source comes from its manifest.
_CLOUD_PROVIDER_PERMUTATIONS = [
    ("CLOUD_OPENAI_SOURCE", ["enabled", "disabled"]),
    ("CLOUD_ANTHROPIC_SOURCE", ["enabled", "disabled"]),
    ("CLOUD_OPENROUTER_SOURCE", ["enabled", "disabled"]),
]

_PERMUTATIONS = _manifest_permutations() + _CLOUD_PROVIDER_PERMUTATIONS


def _write_env_with_override(target: Path, var: str, value: str) -> None:
    """Copy .env.example to `target` with one variable overridden."""
    lines = ENV_EXAMPLE.read_text().splitlines()
    out = []
    found = False
    for line in lines:
        if line.startswith(f"{var}=") and not found:
            out.append(f"{var}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{var}={value}")
    target.write_text("\n".join(out) + "\n")


def _compose_config_ok(env_file: Path) -> tuple[bool, str]:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(COMPOSE),
            "config",
            "-q",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "var,value",
    [(v, val) for v, vals in _PERMUTATIONS for val in vals],
    ids=[f"{v}={val}" for v, vals in _PERMUTATIONS for val in vals],
)
def test_source_value_produces_valid_compose(var: str, value: str, tmp_path: Path):
    """Every source value, when written to .env, must produce a parseable
    compose shape via `docker compose -f docker-compose.yml config -q`."""
    env_file = tmp_path / ".env"
    _write_env_with_override(env_file, var, value)
    ok, stderr = _compose_config_ok(env_file)
    assert ok, f"{var}={value} produced invalid compose:\n{stderr}"


def test_source_permutation_matrix_matches_every_manifest_option():
    from services.manifests import load_manifests

    declared = {
        (manifest.sources.var, option.id)
        for manifest in load_manifests(REPO_ROOT / "services")
        if manifest.sources is not None
        for option in manifest.sources.options
    }
    exercised = {
        (source_var, value)
        for source_var, values in _PERMUTATIONS
        for value in values
    }
    cloud = {
        (source_var, value)
        for source_var, values in _CLOUD_PROVIDER_PERMUTATIONS
        for value in values
    }
    assert exercised == declared | cloud


def test_explicit_remote_host_bind_remains_a_valid_compose_override(tmp_path: Path):
    env_file = tmp_path / ".env"
    _write_env_with_override(env_file, "HOST_BIND_IP", "0.0.0.0:")

    ok, stderr = _compose_config_ok(env_file)
    assert ok, f"HOST_BIND_IP=0.0.0.0: produced invalid compose:\n{stderr}"


# ─── Scaled-family rendering tests ──────────────────────────────────────
# These exercise multi-replica scales that ``test_source_value_produces_valid_compose``
# can't catch — that test only flips SOURCE vars; the auto-managed *_SCALE
# vars stay empty (= 0 at render time). When a user actually enables a
# scaled service (``--spark-source container --spark-workers 2``), the
# bootstrapper sets the *_SCALE values in .env, and Compose then evaluates
# rules like "no container_name when replicas > 1".

@pytest.mark.parametrize("worker_count", ["1", "2", "8"])
def test_spark_renders_at_every_supported_worker_count(worker_count: str, tmp_path: Path):
    """Spark workers can scale 1-8 via SPARK_WORKER_COUNT. At every
    supported count the merged compose shape must render — in particular,
    ``container_name`` MUST NOT be set on spark-worker because Compose
    forbids it when ``deploy.replicas > 1`` (Docker can't make multiple
    containers share the same name). Ray's ray-worker has the same
    constraint and is the precedent for the no-container_name pattern."""
    env_file = tmp_path / ".env"
    # Apply all five scales the bootstrapper would write when source=container.
    # (Pass 2 added the dedicated spark-connect sidecar — SPARK_CONNECT_SCALE
    # must be 1 alongside the rest for the merged compose to render the
    # full topology that ships at runtime.)
    env_text = ENV_EXAMPLE.read_text(encoding="utf-8")
    out_lines = []
    for line in env_text.splitlines():
        if line.startswith("SPARK_SOURCE="):
            out_lines.append("SPARK_SOURCE=container")
        elif line.startswith("SPARK_MASTER_SCALE="):
            out_lines.append("SPARK_MASTER_SCALE=1")
        elif line.startswith("SPARK_WORKER_SCALE="):
            out_lines.append(f"SPARK_WORKER_SCALE={worker_count}")
        elif line.startswith("SPARK_HISTORY_SCALE="):
            out_lines.append("SPARK_HISTORY_SCALE=1")
        elif line.startswith("SPARK_INIT_SCALE="):
            out_lines.append("SPARK_INIT_SCALE=1")
        elif line.startswith("SPARK_CONNECT_SCALE="):
            out_lines.append("SPARK_CONNECT_SCALE=1")
        else:
            out_lines.append(line)
    env_file.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    ok, stderr = _compose_config_ok(env_file)
    assert ok, f"SPARK_WORKER_SCALE={worker_count} produced invalid compose:\n{stderr}"
# Endpoint vars a manifest blanks for `disabled` must not be re-defaulted by a
# consumer. `${VAR:-x}` substitutes on EMPTY, not merely unset, so a non-empty
# default there overwrites the very signal that says "provider is off".
# Each entry below is a known instance awaiting its consumer being checked; the
# fix is only safe where that consumer branches on the empty value. This list
# may shrink, never grow.
_KNOWN_ENDPOINT_DEFAULTS = {
    "services/asset-baker/compose.yml: ASSET_BAKER_MINIO_ENDPOINT",
    "services/asset-worker/compose.yml: ASSET_WORKER_MINIO_ENDPOINT",
    "services/grafana/compose.yml: PROMETHEUS_ENDPOINT",
    "services/grafana/compose.yml: TEMPO_ENDPOINT",
    "services/grafana/compose.yml: LOKI_ENDPOINT",
}


def _env_vars_blanked_when_disabled() -> set[str]:
    """Env vars a manifest explicitly sets to "" in its `disabled` slice."""
    import yaml

    blanked: set[str] = set()
    for manifest_path in sorted((REPO_ROOT / "services").glob("*/service.yml")):
        doc = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        for family in (doc.get("runtime_sc") or {}).values():
            environment = ((family or {}).get("disabled") or {}).get("environment") or {}
            blanked.update(key for key, value in environment.items() if value == "")
    return blanked


def _has_non_empty_default(line: str, var: str) -> bool:
    marker = "${" + var + ":-"
    index = line.find(marker)
    # `${VAR:-}` is the established "empty when unset" idiom and preserves blank.
    return index != -1 and not line[index + len(marker):].startswith("}")


def _endpoint_defaults_that_override_a_blank(blanked: set[str]) -> set[str]:
    found: set[str] = set()
    for fragment in sorted((REPO_ROOT / "services").glob("*/compose.yml")):
        relative = fragment.relative_to(REPO_ROOT)
        for line in fragment.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            found.update(
                f"{relative}: {var}"
                for var in blanked
                if _has_non_empty_default(stripped, var)
            )
    return found


def test_no_consumer_default_resurrects_an_endpoint_its_manifest_blanks():
    """An empty auto-managed endpoint is how a consumer learns a provider is off.

    weaviate's manifest sets `WEAVIATE_URL: ""` for `disabled` on purpose and the
    backend branches on the blank; a `${VAR:-url}` default in the consumer handed
    it an endpoint for a container that was not running, so a disabled provider
    failed like a broken one.
    """
    blanked = _env_vars_blanked_when_disabled()
    assert blanked, "no manifest blanks an endpoint on `disabled` -- re-derive this contract"

    offenders = _endpoint_defaults_that_override_a_blank(blanked)

    assert not (offenders - _KNOWN_ENDPOINT_DEFAULTS), (
        "a consumer fragment defaults an endpoint its owning manifest blanks for "
        "`disabled`, so the not-configured signal never reaches it:\n  "
        + "\n  ".join(sorted(offenders - _KNOWN_ENDPOINT_DEFAULTS))
    )
    assert not (_KNOWN_ENDPOINT_DEFAULTS - offenders), (
        "these were fixed -- drop them from _KNOWN_ENDPOINT_DEFAULTS so the list "
        f"keeps ratcheting down: {sorted(_KNOWN_ENDPOINT_DEFAULTS - offenders)}"
    )


_REDIS_URL_FIELDS = (
    ("backend", "REDIS_URL", 0), ("celery-worker", "REDIS_URL", 0),
    ("backend", "CELERY_BROKER_URL", 4), ("celery-worker", "CELERY_BROKER_URL", 4),
    ("celery-worker", "CELERY_RESULT_BACKEND", 4), ("flower", "CELERY_BROKER_URL", 4),
    ("jupyterhub", "REDIS_URL", 3), ("open-web-ui", "WEBSOCKET_REDIS_URL", 2),
    ("trueforge", "REDIS_URL", 5), ("lightrag", "REDIS_URI", 2),
)


def _redis_generated_env(tmp_path, monkeypatch, settings):
    from core.config_parser import ConfigParser
    from services.service_config import ServiceConfig
    from utils.atomic_write import render_env_assignment, set_env_assignment
    from utils.key_generator import KeyGenerator
    content = ENV_EXAMPLE.read_text()
    defaults = {"REDIS_PASSWORD": "synthetic-file-secret", "REDIS_SOURCE": "container",
                "CELERY_SOURCE": "container", "LIGHTRAG_SOURCE": "container",
                "JUPYTERHUB_SOURCE": "container", "OPEN_WEB_UI_SOURCE": "container",
                "TRUEFORGE_SOURCE": "container"}
    for key, value in dict(defaults, **settings).items():
        content = set_env_assignment(content, key, render_env_assignment(key, value))
    path = tmp_path / ".env"
    path.write_text(content)
    parser = ConfigParser(str(REPO_ROOT))
    parser.env_file_path = path
    generator = KeyGenerator(str(REPO_ROOT))
    generator.env_file_path = path
    assert generator.generate_and_update_redis_password()
    monkeypatch.setattr("services.service_config.resolve_host_gateway_ip", lambda: "host-gateway")
    sc = ServiceConfig(parser)
    assert sc.generate_and_update_env(create_backup=False)
    return path


def _redis_merged_config(path):
    import json
    from utils.system import compose_env
    argv = ["docker", "compose", "--env-file=" + str(path), "-p", "atlas-redis-fixture", "-f", str(COMPOSE), "config", "--format", "json"]
    result = subprocess.run(argv, env=compose_env(argv), capture_output=True, text=True, timeout=35, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["services"]


def _assert_redis_password_urls(services, password):
    from urllib.parse import unquote, urlsplit
    for service, key, db in _REDIS_URL_FIELDS:
        uri = services[service]["environment"][key]
        parsed = urlsplit(uri)
        assert (parsed.hostname, parsed.port, unquote(parsed.password or ""), parsed.path) == ("redis", 6379, password, "/" + str(db)), (service, key)
        assert not parsed.query and not parsed.fragment


@pytest.mark.parametrize("raw", ["synthetic-safe", "synthetic%2Fsecret", "synthetic/secret", "synthetic#secret", "synthetic?secret", "synthetic'é雪", "synthetic two words", 'synthetic"quoted"secret', "synthetic\\secret"])
def test_redis_credentials_survive_complete_generation_and_compose(tmp_path, monkeypatch, raw):
    path = _redis_generated_env(tmp_path, monkeypatch, {"REDIS_PASSWORD": raw})
    services = _redis_merged_config(path)
    _assert_redis_password_urls(services, raw)
    assert services["redis"]["command"] == ["redis-server", "--appendonly", "yes", "--requirepass", raw, "--maxmemory", "0", "--maxmemory-policy", "volatile-lru"]
    for service, key in [("redis", "REDISCLI_AUTH"), ("redis-exporter", "REDIS_PASSWORD"), ("litellm", "REDIS_PASSWORD"), ("n8n", "QUEUE_BULL_REDIS_PASSWORD"), ("n8n-worker", "QUEUE_BULL_REDIS_PASSWORD")]:
        assert services[service]["environment"][key] == raw


@pytest.mark.parametrize("shell_password", ["synthetic-shell%2Fonly", ""])
def test_redis_shell_password_is_runtime_only(tmp_path, monkeypatch, shell_password):
    from urllib.parse import quote
    monkeypatch.setenv("REDIS_PASSWORD", shell_password)
    path = _redis_generated_env(tmp_path, monkeypatch, {})
    persisted = path.read_text()
    assert "REDIS_PASSWORD=synthetic-file-secret" in persisted
    if shell_password:
        assert shell_password not in persisted
        assert quote(shell_password, safe="") not in persisted
    services = _redis_merged_config(path)
    _assert_redis_password_urls(services, shell_password)
    assert services["redis"]["command"][4] == shell_password


def test_redis_compose_preserves_explicit_url_and_custom_db_overrides(tmp_path, monkeypatch):
    operator_url = "rediss://operator:explicit%252F@operator.invalid:6380/12"
    path = _redis_generated_env(tmp_path, monkeypatch, {"REDIS_URL": operator_url, "OPEN_WEB_UI_REDIS_DB": "11"})
    services = _redis_merged_config(path)
    assert services["backend"]["environment"]["REDIS_URL"] == operator_url
    assert services["celery-worker"]["environment"]["REDIS_URL"] == operator_url
    assert services["open-web-ui"]["environment"]["WEBSOCKET_REDIS_URL"].endswith("/11")


@pytest.mark.parametrize("disabled", [False, True])
@pytest.mark.parametrize("final_url", [None, "", "redis://:operator%2Fsecret@operator.invalid:6380/9"])
def test_redis_child_env_preserves_final_exports_and_disabled_blanks(tmp_path, monkeypatch, disabled, final_url):
    from utils.system import compose_env
    monkeypatch.setenv("REDIS_PASSWORD", "synthetic-shell-only")
    settings = {"CELERY_SOURCE": "disabled", "LIGHTRAG_SOURCE": "disabled"} if disabled else {}
    path = _redis_generated_env(tmp_path, monkeypatch, settings)
    fields = ("CELERY_BROKER_URL", "CELERY_RESULT_BACKEND", "LIGHTRAG_REDIS_URI")
    if final_url is not None:
        for key in fields:
            monkeypatch.setenv(key, final_url)
    child = compose_env(["docker", "compose", "--env-file", str(path), "config"])
    assert child["REDIS_PASSWORD_URI"] == "synthetic-shell-only"
    expected = {"CELERY_BROKER_URL": "redis://:synthetic-shell-only@redis:6379/4",
                "CELERY_RESULT_BACKEND": "redis://:synthetic-shell-only@redis:6379/4",
                "LIGHTRAG_REDIS_URI": "redis://:synthetic-shell-only@redis:6379/2"}
    if disabled:
        expected = dict.fromkeys(fields, "")
    if final_url is not None:
        expected = dict.fromkeys(fields, final_url)
    assert {key: child.get(key, "") for key in fields} == expected
    assert "synthetic-shell-only" not in path.read_text()



def test_redis_encoded_shell_companion_is_runtime_only(tmp_path, monkeypatch):
    monkeypatch.delenv("REDIS_PASSWORD", raising=False)
    monkeypatch.setenv("REDIS_PASSWORD_URI", "synthetic-encoded%252Fonly")
    path = _redis_generated_env(tmp_path, monkeypatch, {})
    assert "synthetic-encoded%252Fonly" not in path.read_text()
    services = _redis_merged_config(path)
    _assert_redis_password_urls(services, "synthetic-encoded%2Fonly")
    # An explicit companion override does not rewrite the raw server secret.
    assert services["redis"]["command"][4] == "synthetic-file-secret"


def test_redis_legacy_template_keeps_custom_database_index(tmp_path, monkeypatch):
    from urllib.parse import unquote, urlsplit
    path = _redis_generated_env(tmp_path, monkeypatch, {"REDIS_PASSWORD": "synthetic%2Fsecret", "REDIS_URL": "redis://:${REDIS_PASSWORD}@redis:6379/13"})
    services = _redis_merged_config(path)
    parsed = urlsplit(services["backend"]["environment"]["REDIS_URL"])
    assert (parsed.hostname, parsed.port, unquote(parsed.password), parsed.path) == ("redis", 6379, "synthetic%2Fsecret", "/13")



def test_redis_double_quoted_legacy_url_keeps_quote_and_custom_index(tmp_path, monkeypatch):
    from urllib.parse import unquote, urlsplit
    from core.config_parser import ConfigParser
    from services.service_config import ServiceConfig
    from utils.atomic_write import env_assignment_pattern
    from utils.key_generator import KeyGenerator
    path = _redis_generated_env(tmp_path, monkeypatch, {"REDIS_PASSWORD": "synthetic%2Fsecret"})
    legacy = '  export REDIS_URL="redis://:${REDIS_PASSWORD}@redis:6379/13" # preserved comment'
    path.write_text(env_assignment_pattern("REDIS_URL").sub(lambda match: legacy, path.read_text()))
    parser = ConfigParser(str(REPO_ROOT))
    parser.env_file_path = path
    assert parser.parse_env_file()["REDIS_URL"] == "redis://:${REDIS_PASSWORD}@redis:6379/13"
    generator = KeyGenerator(str(REPO_ROOT))
    generator.env_file_path = path
    assert generator.generate_and_update_redis_password()
    assert ServiceConfig(parser).generate_and_update_env(create_backup=False)
    services = _redis_merged_config(path)
    parsed = urlsplit(services["backend"]["environment"]["REDIS_URL"])
    assert (unquote(parsed.password), parsed.path) == ("synthetic%2Fsecret", "/13")
    assert '  export REDIS_URL="redis://:${REDIS_PASSWORD_URI}@redis:6379/13" # preserved comment' in path.read_text()



def test_redis_literal_password_keeps_single_quotes_and_comment(tmp_path, monkeypatch):
    from core.config_parser import ConfigParser
    from services.service_config import ServiceConfig
    from utils.atomic_write import env_assignment_pattern
    from utils.key_generator import KeyGenerator
    literal = "synthetic${ATLAS_REDIS_LITERAL_NOT_SET}secret"
    monkeypatch.delenv("ATLAS_REDIS_LITERAL_NOT_SET", raising=False)
    path = _redis_generated_env(tmp_path, monkeypatch, {})
    raw_line = "  export REDIS_PASSWORD='" + literal + "' # preserved password comment"
    duplicate = "REDIS_PASSWORD='" + literal + "'\n" + raw_line
    path.write_text("\ufeff" + env_assignment_pattern("REDIS_PASSWORD").sub(lambda match: duplicate, path.read_text()))
    parser = ConfigParser(str(REPO_ROOT))
    parser.env_file_path = path
    assert parser.parse_env_file()["REDIS_PASSWORD"] == literal
    generator = KeyGenerator(str(REPO_ROOT))
    generator.env_file_path = path
    assert generator.generate_and_update_redis_password()
    assert ServiceConfig(parser).generate_and_update_env(create_backup=False)
    services = _redis_merged_config(path)
    _assert_redis_password_urls(services, literal)
    # Compose config escapes dollars for reload after serializing the model.
    rendered_literal = literal.replace("$", "$$")
    assert services["redis"]["command"][4] == rendered_literal
    assert services["redis"]["environment"]["REDISCLI_AUTH"] == rendered_literal
    assert parser.parse_env_file()["REDIS_PASSWORD"] == literal
    assert raw_line in path.read_text()
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert "\nREDIS_PASSWORD='" + literal + "'\n" in path.read_text()


@pytest.mark.parametrize("case", [
    ("${ATLAS_REDIS_REFERENCE}", "synthetic%2Ffile", None, "synthetic%2Ffile"),
    ('"${ATLAS_REDIS_REFERENCE}"', "synthetic-file", "synthetic%2Fshell", "synthetic%2Fshell"),
    ("${PROJECT_NAME}", "unused", None, "atlas-redis-fixture"),
    ("${REDIS_PASSWORD_URI}", "unused", None, "%24%7BREDIS_PASSWORD_URI%7D"),
])
def test_redis_expanding_file_password_is_resolved_only_for_runtime(tmp_path, monkeypatch, case):
    from core.config_parser import ConfigParser
    from services.service_config import ServiceConfig
    from utils.atomic_write import env_assignment_pattern
    from utils.key_generator import KeyGenerator
    raw, reference, shell, expected = case
    monkeypatch.delenv("REDIS_PASSWORD", raising=False)
    monkeypatch.delenv("REDIS_PASSWORD_URI", raising=False)
    if shell is not None:
        monkeypatch.setenv("ATLAS_REDIS_REFERENCE", shell)
    path = _redis_generated_env(tmp_path, monkeypatch, {})
    raw_line = "  export REDIS_PASSWORD=" + raw + " # preserved reference"
    content = env_assignment_pattern("REDIS_PASSWORD").sub(lambda match: raw_line, path.read_text())
    path.write_text("ATLAS_REDIS_REFERENCE=" + reference + "\n" + content)
    parser = ConfigParser(str(REPO_ROOT))
    parser.env_file_path = path
    generator = KeyGenerator(str(REPO_ROOT))
    generator.env_file_path = path
    assert generator.generate_and_update_redis_password()
    assert ServiceConfig(parser).generate_and_update_env(create_backup=False)
    persisted = path.read_bytes()
    services = _redis_merged_config(path)
    _assert_redis_password_urls(services, expected)
    assert services["redis"]["command"][4] == expected
    assert services["redis"]["environment"]["REDISCLI_AUTH"] == expected
    _assert_redis_reference_not_persisted(path, persisted, raw_line, shell)


def _assert_redis_reference_not_persisted(path, persisted, raw_line, shell):
    assert path.read_bytes() == persisted
    assert raw_line in path.read_text()
    if shell is not None:
        from urllib.parse import quote
        assert shell not in path.read_text()
        assert quote(shell, safe="") not in path.read_text()
