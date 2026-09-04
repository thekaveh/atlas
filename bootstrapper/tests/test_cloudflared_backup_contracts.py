"""Credential and source-mode contracts for edge and backup runners."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import ctypes
import hashlib
import hmac
import importlib.util
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid

import pytest
import yaml

from core.config_parser import ConfigParser
from services.source_validator import SourceValidator
from tests import test_database_backup_live_integration as live_integration
from tests.seed_harness import (
    begin_reconciliation_after_interruption,
    cleanup_deadline_expired,
    defer_cleanup_failures,
    establish_cleanup_deadline,
    raise_deferred_cleanup_error,
    sleep_for_cleanup,
)


REPO = Path(__file__).resolve().parents[2]


def _stage_backup_script_siblings(tmp_path: Path) -> None:
    """Copy the shell libraries that the backup/restore scripts source.

    Both scripts resolve ``s3-client.sh`` and ``database-snapshots.sh`` relative
    to their own directory, so a copy staged into ``tmp_path`` has to carry them
    as well. The repo-root fallback inside the scripts only fires when pytest is
    invoked from the repository root, which is not how CI runs the suite.
    """
    for sibling in ("s3-client.sh", "database-snapshots.sh"):
        destination = tmp_path / sibling
        if not destination.exists():
            destination.write_text(
                (REPO / "services/backup/init/scripts" / sibling).read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
MINIO_IMAGE = "minio/minio:RELEASE.2025-09-07T16-13-09Z"
MINIO_CLIENT_IMAGE = "minio/mc:RELEASE.2025-08-13T08-35-41Z"
BACKUP_PRODUCTION_IMAGE = "atlas-backup:local"
MC_RELEASE = "RELEASE.2025-08-13T08-35-41Z"
MC_SHA256_BY_ARCH = {
    "amd64": "01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891",
    "arm64": "14c8c9616cfce4636add161304353244e8de383b2e2752c0e9dad01d4c27c12c",
}
_CONTAINMENT_TOKEN_ENV = "ATLAS_TEST_CONTAINMENT_ID"
_CONTAINMENT_STABILIZE_SECONDS = 0.5
_PID_HINT_MAX_BYTES = 4096
_PID_HINT_MAX_TOKENS = 256
_EXTERNAL_S3_FIXTURE_OWNER_LABEL = "com.atlas.external-s3-fixture-token"
_EXTERNAL_S3_RECONCILE_SECONDS = 90
DATABASE_ORCHESTRATOR = REPO / "services/backup/database_orchestrator.py"


def _database_orchestrator_module():
    spec = importlib.util.spec_from_file_location(
        "atlas_database_orchestrator_ownership", DATABASE_ORCHESTRATOR
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _DarwinProcBsdInfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32), ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32), ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32), ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32), ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32), ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32), ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16), ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32), ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32), ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32), ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


_DARWIN_LIBPROC = (
    ctypes.CDLL("/usr/lib/libproc.dylib") if sys.platform == "darwin" else None
)


class _OwnedProcesses(dict[int, tuple[int, int, str]]):
    def __init__(self, token: str | None = None) -> None:
        super().__init__()
        self.token = token
        self.verified_generations: dict[int, str] = {}
        self.deadline: float | None = None
        self.root_pid: int | None = None


def _write_fake_publication(path: Path, timestamp: str = "20260714_000000") -> None:
    (path / "postgres.dump").write_bytes(b"")
    (path / "postgres.tables").write_text("7075626c\t7374617465\n")
    (path / "postgres.objects").write_text("261; 1259 1 TABLE public state postgres\n")
    table_bytes = (path / "postgres.tables").stat().st_size
    object_bytes = (path / "postgres.objects").stat().st_size
    completion_bytes = 0
    for _ in range(5):
        manifest = "\n".join([
            "format_version=3", f"backup_timestamp={timestamp}", "backup_id=" + "1" * 32,
            "deployment_id_hex=61746c61732d746573742d6465706c6f796d656e74",
            "database_name_hex=706f737467726573", "dump_sha256=" + "0" * 64,
            "dump_bytes=0", "tables_sha256=" + "0" * 64, f"tables_bytes={table_bytes}", "table_count=1",
            "objects_sha256=" + "0" * 64, f"objects_bytes={object_bytes}", "object_count=1",
            f"completion_bytes={completion_bytes}", "server_version_num=170010",
            "hmac_sha256=" + "0" * 64, "",
        ])
        completion = "\n".join([
            "completion_format=1", f"backup_timestamp={timestamp}", "backup_id=" + "1" * 32,
            "manifest_sha256=" + "0" * 64, f"manifest_bytes={len(manifest.encode())}",
            "dump_bytes=0", f"tables_bytes={table_bytes}", f"objects_bytes={object_bytes}", "hmac_sha256=" + "0" * 64, "",
        ])
        new_size = len(completion.encode())
        if new_size == completion_bytes:
            break
        completion_bytes = new_size
    (path / "postgres.manifest").write_text(manifest)
    (path / "postgres.complete").write_text(completion)


def _write_real_publication(root: Path, timestamp: str, key_hex: str) -> Path:
    path = root / timestamp
    path.mkdir()
    (path / "postgres.dump").write_bytes(b"dump")
    (path / "postgres.tables").write_text("7075626c\t7374617465\n")
    (path / "postgres.objects").write_text("261; 1259 1 TABLE public state postgres\n")
    key = bytes.fromhex(key_hex)
    sizes = {name: (path / f"postgres.{name}").stat().st_size for name in ("dump", "tables", "objects")}
    completion_bytes = 0
    for _ in range(5):
        payload = "\n".join([
            "format_version=3", f"backup_timestamp={timestamp}", "backup_id=" + "2" * 32,
            "deployment_id_hex=61746c61732d746573742d6465706c6f796d656e74", "database_name_hex=706f737467726573",
            f"dump_sha256={hashlib.sha256((path / 'postgres.dump').read_bytes()).hexdigest()}", f"dump_bytes={sizes['dump']}",
            f"tables_sha256={hashlib.sha256((path / 'postgres.tables').read_bytes()).hexdigest()}", f"tables_bytes={sizes['tables']}", "table_count=1",
            f"objects_sha256={hashlib.sha256((path / 'postgres.objects').read_bytes()).hexdigest()}", f"objects_bytes={sizes['objects']}", "object_count=1",
            f"completion_bytes={completion_bytes}", "server_version_num=170010", "",
        ])
        manifest = f"{payload}hmac_sha256={hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()}\n"
        complete_payload = "\n".join([
            "completion_format=1", f"backup_timestamp={timestamp}", "backup_id=" + "2" * 32,
            f"manifest_sha256={hashlib.sha256(manifest.encode()).hexdigest()}", f"manifest_bytes={len(manifest.encode())}",
            f"dump_bytes={sizes['dump']}", f"tables_bytes={sizes['tables']}", f"objects_bytes={sizes['objects']}", "",
        ])
        complete = f"{complete_payload}hmac_sha256={hmac.new(key, complete_payload.encode(), hashlib.sha256).hexdigest()}\n"
        new_size = len(complete.encode())
        if new_size == completion_bytes:
            break
        completion_bytes = new_size
    (path / "postgres.manifest").write_text(manifest)
    (path / "postgres.complete").write_text(complete)
    artifact_dir = path / ("2" * 32)
    artifact_dir.mkdir()
    for name in ("dump", "tables", "objects", "manifest"):
        (path / f"postgres.{name}").rename(artifact_dir / f"postgres.{name}")
    return path


def _validator(env_path: Path) -> SourceValidator:
    parser = ConfigParser(str(REPO))
    parser.env_file_path = env_path
    return SourceValidator(config_parser=parser)


def test_cloudflared_container_source_requires_token(env_with_overrides) -> None:
    missing = _validator(
        env_with_overrides(
            {"CLOUDFLARED_SOURCE": "container", "CLOUDFLARE_TUNNEL_TOKEN": ""}
        )
    )
    assert missing.validate_all_sources() is False
    assert any(
        "CLOUDFLARE_TUNNEL_TOKEN" in error
        for error in missing.get_validation_errors()
    )

    disabled = _validator(
        env_with_overrides(
            {"CLOUDFLARED_SOURCE": "disabled", "CLOUDFLARE_TUNNEL_TOKEN": ""}
        )
    )
    assert disabled.validate_all_sources() is True

    configured = _validator(
        env_with_overrides(
            {
                "CLOUDFLARED_SOURCE": "container",
                "CLOUDFLARE_TUNNEL_TOKEN": "configured-token",
            }
        )
    )
    assert configured.validate_all_sources() is True


def test_cloudflared_public_edge_docs_require_host_routing_and_access() -> None:
    guide = (REPO / "services/cloudflared/README.md").read_text(encoding="utf-8")
    security = (REPO / "SECURITY.md").read_text(encoding="utf-8")

    assert "httpHostHeader" in guide
    assert "api.localhost" in guide
    assert "Cloudflare Access is required" in guide
    assert "optional Cloudflare Tunnel" in security
    assert "Cloudflare Access policy" in security


def test_backup_compose_passes_source_to_runner() -> None:
    compose = yaml.safe_load(
        (REPO / "services/backup/compose.yml").read_text(encoding="utf-8")
    )
    assert compose["services"]["backup"]["environment"]["BACKUP_SOURCE"] == (
        "${BACKUP_SOURCE:-disabled}"
    )
    assert compose["services"]["backup"]["environment"][
        "BACKUP_COMMAND_TIMEOUT_SECONDS"
    ] == "${BACKUP_COMMAND_TIMEOUT_SECONDS:-900}"
    assert compose["services"]["backup"]["environment"][
        "BACKUP_RESTORE_GLOBAL_TIMEOUT_SECONDS"
    ] == "${BACKUP_RESTORE_GLOBAL_TIMEOUT_SECONDS:-28800}"
    assert compose["services"]["backup"]["environment"][
        "BACKUP_MANIFEST_HMAC_KEY"
    ] == "${BACKUP_MANIFEST_HMAC_KEY}"
    assert compose["services"]["backup"]["environment"][
        "BACKUP_DEPLOYMENT_ID"
    ] == "${BACKUP_DEPLOYMENT_ID}"
    assert compose["services"]["backup"]["environment"][
        "BACKUP_MAX_POSTGRES_DUMP_BYTES"
    ] == "${BACKUP_MAX_POSTGRES_DUMP_BYTES:-10737418240}"
    assert compose["services"]["backup"]["environment"][
        "BACKUP_RESTORE_MAX_CANDIDATES"
    ] == "${BACKUP_RESTORE_MAX_CANDIDATES:-100}"

    manifest = yaml.safe_load(
        (REPO / "services/backup/service.yml").read_text(encoding="utf-8")
    )
    env = {entry["name"]: entry for entry in manifest["env"]}
    assert env["BACKUP_MANIFEST_HMAC_KEY"]["secret"] is True
    assert env["BACKUP_MANIFEST_HMAC_KEY"]["default"] == ""
    assert env["BACKUP_DEPLOYMENT_ID"]["default"] == ""
    assert env["BACKUP_MAX_POSTGRES_DUMP_BYTES"]["default"] == 10737418240
    assert env["BACKUP_RESTORE_MAX_CANDIDATES"]["default"] == 100


def _render_backup_service(tmp_path: Path, overrides: dict[str, str]) -> dict:
    env_text = (REPO / ".env.example").read_text(encoding="utf-8")
    values = dict(overrides)
    rendered_lines: list[str] = []
    for line in env_text.splitlines():
        key = line.split("=", 1)[0] if "=" in line else ""
        if key in values:
            rendered_lines.append(f"{key}={values.pop(key)}")
        else:
            rendered_lines.append(line)
    rendered_lines.extend(f"{key}={value}" for key, value in values.items())
    env_file = tmp_path / "backup.env"
    env_file.write_text("\n".join(rendered_lines) + "\n", encoding="utf-8")
    result = subprocess.run(
        [
            "docker", "compose", "--env-file", str(env_file), "-p", "atlas-backup-contract",
            "-f", str(REPO / "docker-compose.yml"), "config", "--format", "json",
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["services"]


def test_rendered_external_backup_uses_separate_credentials_and_disables_local_minio(
    tmp_path: Path,
) -> None:
    services = _render_backup_service(
        tmp_path,
        {
            "BACKUP_SOURCE": "container",
            "BACKUP_S3_MODE": "external",
            "BACKUP_S3_ENDPOINT": "https://s3.example.test",
            "BACKUP_S3_ACCESS_KEY": "external-access",
            "BACKUP_S3_SECRET_KEY": "external-secret",
            "BACKUP_S3_REGION": "us-test-1",
            "BACKUP_S3_SESSION_TOKEN": "external-session",
            "BACKUP_S3_TLS_VERIFY": "true",
            "MINIO_SOURCE": "disabled",
            "MINIO_SCALE": "0",
            "MINIO_INIT_SCALE": "0",
            "MINIO_ROOT_USER": "local-root-access",
            "MINIO_ROOT_PASSWORD": "local-root-secret",
        },
    )
    backup = services["backup"]
    assert backup["environment"]["BACKUP_S3_MODE"] == "external"
    assert backup["environment"]["BACKUP_S3_ACCESS_KEY"] == "external-access"
    assert backup["environment"]["BACKUP_S3_SECRET_KEY"] == "external-secret"
    assert backup["environment"]["BACKUP_S3_SESSION_TOKEN"] == "external-session"
    assert backup["environment"]["BACKUP_S3_ENDPOINT"] == "https://s3.example.test"
    assert backup["environment"]["BACKUP_S3_TLS_VERIFY"] == "true"
    assert "depends_on" not in backup
    assert services["minio"]["deploy"]["replicas"] == 0
    assert services["minio-init"]["deploy"]["replicas"] == 0


def test_backup_manifest_owns_external_s3_contract_and_minio_is_optional() -> None:
    manifest = yaml.safe_load(
        (REPO / "services/backup/service.yml").read_text(encoding="utf-8")
    )
    env = {entry["name"]: entry for entry in manifest["env"]}
    assert env["BACKUP_S3_MODE"]["default"] == "local"
    assert env["BACKUP_S3_ENDPOINT"]["default"] == "http://minio:9000"
    assert env["BACKUP_S3_ACCESS_KEY"] == {
        "name": "BACKUP_S3_ACCESS_KEY",
        "default": "",
        "secret": True,
        "description": "Dedicated S3 access key. Required for external endpoints; local MinIO may fall back to MINIO_ROOT_USER for upgrade compatibility.",
    }
    assert env["BACKUP_S3_SECRET_KEY"]["secret"] is True
    assert env["BACKUP_S3_SESSION_TOKEN"]["secret"] is True
    assert env["BACKUP_S3_REGION"]["default"] == "us-east-1"
    assert env["BACKUP_S3_TLS_VERIFY"]["default"] is True
    assert "minio" not in manifest["depends_on"]["required"]
    assert "minio" in manifest["depends_on"]["optional"]
    rule = manifest["runtime_deps"]["backup"]["conditional_requires"][0]
    assert rule["when"] == {
        "BACKUP_SOURCE": "container", "BACKUP_S3_MODE": "local"
    }
    assert rule["requires"] == ["minio"]
    assert rule["auto_resolve"] is False


@pytest.mark.parametrize(
    ("mode", "requires_minio"),
    [(None, True), ("", True), ("local", True), ("external", False)],
)
def test_backup_dependency_validation_is_mode_aware_before_start(
    tmp_path: Path, mode: str | None, requires_minio: bool,
) -> None:
    from services.dependency_manager import DependencyManager

    env_file = tmp_path / ".env"
    lines = ["BACKUP_SOURCE=container", "BACKUP_SCALE=0"]
    if mode is not None:
        lines.append(f"BACKUP_S3_MODE={mode}")
    lines.extend(["MINIO_SOURCE=disabled", "MINIO_SCALE=0", "MINIO_INIT_SCALE=0"])
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    parser = ConfigParser(str(REPO))
    parser.env_file_path = env_file
    manager = DependencyManager(parser)

    result = manager.check_service_dependencies()
    violations = [
        violation for violation in manager.get_dependency_violations()
        if violation["service"] == "backup"
        and violation["required_service"] == "minio"
    ]
    assert bool(violations) is requires_minio
    if requires_minio:
        assert result is False
    if violations:
        assert violations[0]["auto_resolve"] is False
        assert "backup" not in manager.auto_resolve_dependency_violations()
        assert "BACKUP_SCALE=0" in env_file.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("mode", "requires_minio"),
    [(None, True), ("", True), ("local", True), ("external", False)],
)
def test_real_atlas_start_validation_applies_backup_mode_default_like_compose(
    env_with_overrides, mode: str | None, requires_minio: bool,
) -> None:
    from start import AtlasStarter

    overrides = {
        "BACKUP_SOURCE": "container", "BACKUP_SCALE": "0",
        "MINIO_SOURCE": "disabled", "MINIO_SCALE": "0", "MINIO_INIT_SCALE": "0",
    }
    if mode is not None:
        overrides["BACKUP_S3_MODE"] = mode
    env_file = env_with_overrides(overrides)
    if mode is None:
        env_file.write_text(
            "\n".join(
                line for line in env_file.read_text(encoding="utf-8").splitlines()
                if not line.startswith("BACKUP_S3_MODE=")
            ) + "\n",
            encoding="utf-8",
        )
    starter = AtlasStarter()
    starter.config_parser.env_file_path = env_file

    result = starter.check_service_dependencies()
    violations = [
        violation["service"] == "backup"
        for violation in starter.dependency_manager.get_dependency_violations()
    ]
    assert any(violations) is requires_minio
    if requires_minio:
        assert result is False


def test_generic_conditional_defaults_do_not_override_nonblank_values(tmp_path: Path) -> None:
    from services.dependency_manager import DependencyManager

    parser = ConfigParser(str(REPO))
    manager = DependencyManager(parser)
    manager.get_service_scale = lambda _name: 0  # type: ignore[method-assign]
    config = {
        "conditional_requires": [{
            "when": {"SYNTHETIC_MODE": "local"},
            "when_defaults": {"SYNTHETIC_MODE": "local"},
            "requires": ["minio"],
        }]
    }
    assert manager._conditional_dependency_violations("synthetic", config, {})
    assert manager._conditional_dependency_violations(
        "synthetic", config, {"SYNTHETIC_MODE": ""}
    )
    assert not manager._conditional_dependency_violations(
        "synthetic", config, {"SYNTHETIC_MODE": "external"}
    )


def _fake_s3_boundary(tmp_path: Path) -> tuple[Path, Path]:
    trace = tmp_path / "s3-trace"
    timeout = tmp_path / "timeout"
    timeout.write_text('#!/bin/sh\nshift 5\nexec "$@"\n', encoding="utf-8")
    timeout.chmod(0o755)
    mc = tmp_path / "mc"
    mc.write_text(
        """#!/bin/sh
printf 'region=%s\ninsecure=%s\nconfig_dir=%s\ncmd=%s\n' \
  "${MC_REGION:-}" "${MC_INSECURE:-}" "${MC_CONFIG_DIR:-}" "$*" >>"$TRACE"
env | sort >>"$TRACE"
case "$*" in
  *"alias import s3 "*)
    for credentials do :; done
    python3 - "$credentials" <<'PY' || exit 91
import json
import sys

credentials = json.loads(open(sys.argv[1], encoding="utf-8").read())
assert credentials["accessKey"] == "AK:ID@"
assert credentials["secretKey"] == 'se/cr et+$"\\\\value'
assert credentials["sessionToken"] == "tok:@/+="
PY
    [ "$(stat -c '%a' "$credentials" 2>/dev/null || stat -f '%Lp' "$credentials" 2>/dev/null)" = 600 ] || exit 94
    config_dir=${credentials%/*}
    [ "$(stat -c '%a' "$config_dir" 2>/dev/null || stat -f '%Lp' "$config_dir" 2>/dev/null)" = 700 ] || exit 95
    printf 'credentials=ok\n' >>"$TRACE"
    exit 0
    ;;
esac
case "$1" in
  mb) exit 0 ;;
  ls) printf 'existing-object\n' ;;
esac
exit 0
""",
        encoding="utf-8",
    )
    mc.chmod(0o755)
    return trace, tmp_path


def _backup_s3_env(bin_dir: Path, trace: Path) -> dict[str, str]:
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "TRACE": str(trace),
        "SUPABASE_DB_USER": "postgres",
        "SUPABASE_DB_PASSWORD": "database-secret",
        "SUPABASE_DB_NAME": "postgres",
        "BACKUP_MANIFEST_HMAC_KEY": "5" * 64,
        "BACKUP_DEPLOYMENT_ID": "atlas-test-deployment",
        "BACKUP_TIMESTAMP": "20260829_120000",
    }


def test_external_s3_credentials_are_uri_safe_and_never_fall_back_to_minio_root(
    tmp_path: Path,
) -> None:
    trace, bin_dir = _fake_s3_boundary(tmp_path)
    external_access = "AK:ID@"
    external_secret = 'se/cr et+$"\\value'
    external_token = "tok:@/+="
    result = subprocess.run(
        ["sh", str(REPO / "services/backup/init/scripts/backup-all.sh")],
        env={
            **_backup_s3_env(bin_dir, trace),
            "BACKUP_S3_MODE": "external",
            "BACKUP_S3_ENDPOINT": "https://s3.example.test",
            "BACKUP_S3_ACCESS_KEY": external_access,
            "BACKUP_S3_SECRET_KEY": external_secret,
            "BACKUP_S3_SESSION_TOKEN": external_token,
            "BACKUP_S3_REGION": "us-test-1",
            "BACKUP_S3_TLS_VERIFY": "true",
            "MINIO_ROOT_USER": "must-not-be-used",
            "MINIO_ROOT_PASSWORD": "must-not-be-used-secret",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "backup publication lock" in result.stderr
    calls = trace.read_text(encoding="utf-8")
    assert "credentials=ok" in calls
    assert "region=us-test-1" in calls
    assert "MC_HOST_s3=" not in calls
    assert "must-not-be-used" not in calls
    combined = result.stdout + result.stderr
    for secret in (external_access, external_secret, external_token, "must-not-be-used-secret"):
        assert secret not in combined
        assert secret not in calls


def test_external_s3_missing_dedicated_credentials_fails_before_s3_or_database(
    tmp_path: Path,
) -> None:
    trace, bin_dir = _fake_s3_boundary(tmp_path)
    result = subprocess.run(
        ["sh", str(REPO / "services/backup/init/scripts/backup-all.sh")],
        env={
            **_backup_s3_env(bin_dir, trace),
            "BACKUP_S3_MODE": "external",
            "BACKUP_S3_ENDPOINT": "https://s3.example.test",
            "BACKUP_S3_ACCESS_KEY": "",
            "BACKUP_S3_SECRET_KEY": "",
            "MINIO_ROOT_USER": "local-root-access",
            "MINIO_ROOT_PASSWORD": "local-root-secret",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 64
    assert "BACKUP_S3_ACCESS_KEY and BACKUP_S3_SECRET_KEY are required for external endpoints" in result.stderr
    assert not trace.exists()
    assert "local-root" not in result.stdout + result.stderr


def test_local_s3_mode_refuses_to_send_minio_root_credentials_to_remote_endpoint(
    tmp_path: Path,
) -> None:
    trace, bin_dir = _fake_s3_boundary(tmp_path)
    result = subprocess.run(
        ["sh", str(REPO / "services/backup/init/scripts/backup-all.sh")],
        env={
            **_backup_s3_env(bin_dir, trace),
            "BACKUP_S3_MODE": "local",
            "BACKUP_S3_ENDPOINT": "https://remote.example.test",
            "MINIO_ROOT_USER": "local-root-access",
            "MINIO_ROOT_PASSWORD": "local-root-secret",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 64
    assert "BACKUP_S3_MODE=local requires BACKUP_S3_ENDPOINT=http://minio:9000" in result.stderr
    assert not trace.exists()
    assert "local-root" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("endpoint", "tls_verify", "region", "message"),
    [
        ("s3.example.test", "true", "us-east-1", "BACKUP_S3_ENDPOINT"),
        ("https://user@s3.example.test", "true", "us-east-1", "BACKUP_S3_ENDPOINT"),
        ("https://s3.example.test/path", "true", "us-east-1", "BACKUP_S3_ENDPOINT"),
        ("https://2001:db8::1", "true", "us-east-1", "BACKUP_S3_ENDPOINT"),
        ("https://[2001:db8::1]:080", "true", "us-east-1", "BACKUP_S3_ENDPOINT"),
        ("https://s3.example.test:65536", "true", "us-east-1", "BACKUP_S3_ENDPOINT"),
        ("https://[:::]", "true", "us-east-1", "BACKUP_S3_ENDPOINT"),
        ("https://[127.0.0.1]", "true", "us-east-1", "BACKUP_S3_ENDPOINT"),
        ("https://999.1.2.3", "true", "us-east-1", "BACKUP_S3_ENDPOINT"),
        ("https://a..b", "true", "us-east-1", "BACKUP_S3_ENDPOINT"),
        (f"https://{'a' * 64}.example", "true", "us-east-1", "BACKUP_S3_ENDPOINT"),
        (f"https://{'a.' * 126}aa", "true", "us-east-1", "BACKUP_S3_ENDPOINT"),
        ("https://2001:db8::1", "true", "us-east-1", "BACKUP_S3_ENDPOINT"),
        ("https://s3.example.test:0443", "true", "us-east-1", "BACKUP_S3_ENDPOINT"),
        ("http://s3.example.test", "false", "us-east-1", "BACKUP_S3_TLS_VERIFY"),
        ("https://s3.example.test", "maybe", "us-east-1", "BACKUP_S3_TLS_VERIFY"),
        ("https://s3.example.test", "TRUE", "us-east-1", "BACKUP_S3_TLS_VERIFY"),
        ("https://s3.example.test", "true", "bad region", "BACKUP_S3_REGION"),
        ("https://s3.example.test", "true", "us-east-1\n", "BACKUP_S3_REGION"),
    ],
)
def test_backup_rejects_invalid_s3_endpoint_tls_and_region_before_io(
    tmp_path: Path, endpoint: str, tls_verify: str, region: str, message: str,
) -> None:
    trace, bin_dir = _fake_s3_boundary(tmp_path)
    result = subprocess.run(
        ["sh", str(REPO / "services/backup/init/scripts/backup-all.sh")],
        env={
            **_backup_s3_env(bin_dir, trace),
            "BACKUP_S3_MODE": "external",
            "BACKUP_S3_ENDPOINT": endpoint,
            "BACKUP_S3_ACCESS_KEY": "external-access",
            "BACKUP_S3_SECRET_KEY": "external-secret",
            "BACKUP_S3_REGION": region,
            "BACKUP_S3_TLS_VERIFY": tls_verify,
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 64
    assert message in result.stderr
    assert not trace.exists()


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://s3.example.test",
        "https://s3.example.test:443",
        "http://127.0.0.1:9000",
        "http://[2001:db8::1]:9000",
        "https://[::1]",
    ],
)
def test_backup_accepts_deliberate_s3_origin_forms(tmp_path: Path, endpoint: str) -> None:
    trace, bin_dir = _fake_s3_boundary(tmp_path)
    result = subprocess.run(
        ["sh", str(REPO / "services/backup/init/scripts/backup-all.sh")],
        env={
            **_backup_s3_env(bin_dir, trace),
            "BACKUP_S3_MODE": "external",
            "BACKUP_S3_ENDPOINT": endpoint,
            "BACKUP_S3_ACCESS_KEY": "AK:ID@",
            "BACKUP_S3_SECRET_KEY": 'se/cr et+$"\\value',
            "BACKUP_S3_SESSION_TOKEN": "tok:@/+=",
            "BACKUP_S3_REGION": "eu-central-2",
            "BACKUP_S3_TLS_VERIFY": "true",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 64, result.stderr
    assert "credentials=ok" in trace.read_text(encoding="utf-8")


def test_backup_credential_json_preserves_unicode_utf8_exactly(tmp_path: Path) -> None:
    trace, bin_dir = _fake_s3_boundary(tmp_path)
    mc = bin_dir / "mc"
    mc.write_text(
        """#!/bin/sh
for credentials do :; done
python3 - "$credentials" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["accessKey"] == "Å鍵🔐"
assert value["secretKey"] == "令牌"
assert value["sessionToken"] == "Å鍵🔐/令牌"
PY
""",
        encoding="utf-8",
    )
    mc.chmod(0o755)
    result = subprocess.run(
        ["sh", str(REPO / "services/backup/init/scripts/backup-all.sh")],
        env={
            **_backup_s3_env(bin_dir, trace),
            "BACKUP_S3_MODE": "external",
            "BACKUP_S3_ENDPOINT": "https://s3.example.test",
            "BACKUP_S3_ACCESS_KEY": "Å鍵🔐",
            "BACKUP_S3_SECRET_KEY": "令牌",
            "BACKUP_S3_SESSION_TOKEN": "Å鍵🔐/令牌",
        },
        text=True, capture_output=True, check=False,
    )
    assert result.returncode != 64, result.stderr


def test_backup_rejects_invalid_utf8_before_io(tmp_path: Path) -> None:
    trace, bin_dir = _fake_s3_boundary(tmp_path)
    env = {
        key.encode(): value.encode()
        for key, value in {
            **_backup_s3_env(bin_dir, trace),
            "BACKUP_S3_MODE": "external",
            "BACKUP_S3_ENDPOINT": "https://s3.example.test",
            "BACKUP_S3_ACCESS_KEY": "external-access",
            "BACKUP_S3_SECRET_KEY": "placeholder",
        }.items()
    }
    env[b"BACKUP_S3_SECRET_KEY"] = b"invalid-\xff-secret"
    result = subprocess.run(
        [b"sh", os.fsencode(REPO / "services/backup/init/scripts/backup-all.sh")],
        env=env, capture_output=True, check=False,
    )
    assert result.returncode == 64
    assert b"valid UTF-8 without control bytes" in result.stderr
    assert not trace.exists()


def test_unicode_credentials_roundtrip_in_actual_pinned_client_shell(
    tmp_path: Path,
) -> None:
    _require_disposable_s3_images()
    values = {
        "url": "https://s3.example.test",
        "accessKey": "Å鍵🔐access",
        "secretKey": "令牌-secret-123",
        "sessionToken": "Å鍵🔐/令牌",
        "api": "S3v4",
        "path": "auto",
    }
    expected = json.dumps(values, ensure_ascii=False, separators=(",", ":")) + "\n"
    expected_sha = hashlib.sha256(expected.encode("utf-8")).hexdigest()
    env_file = tmp_path / "unicode.env"
    env_file.write_text(
        "BACKUP_S3_MODE=external\n"
        "BACKUP_S3_ENDPOINT=https://s3.example.test\n"
        "BACKUP_S3_ACCESS_KEY=Å鍵🔐access\n"
        "BACKUP_S3_SECRET_KEY=令牌-secret-123\n"
        "BACKUP_S3_SESSION_TOKEN=Å鍵🔐/令牌\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    bin_dir = tmp_path / "bin"
    result_dir = tmp_path / "result"
    bin_dir.mkdir()
    result_dir.mkdir()
    wrapper = bin_dir / "mc"
    wrapper.write_text(
        """#!/bin/sh
case "$*" in
  "alias import s3 "*)
    for credentials do :; done
    sha256sum "$credentials" | while IFS=' ' read -r digest rest; do
      printf '%s\n' "$digest" >/result/credentials.sha256
    done
    ;;
esac
exec /usr/bin/mc "$@"
""",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    probe = tmp_path / "unicode-probe.sh"
    probe.write_text(
        """#!/bin/sh
set -eu
PATH=/probe-bin:/usr/bin:/bin
export PATH
. /scripts/s3-client.sh
run_bounded() { "$@"; }
prepare_backup_s3 probe
configure_backup_s3 /tmp/atlas-unicode-mc
/usr/bin/mc --config-dir /tmp/atlas-unicode-mc alias export s3 >/dev/null
rm -rf /tmp/atlas-unicode-mc
""",
        encoding="utf-8",
    )
    probe.chmod(0o755)
    name = f"atlas-backup-unicode-{uuid.uuid4().hex[:12]}"
    result = _docker_command(
        "run", "--pull=never", "--rm", "--name", name,
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--env-file", str(env_file),
        "--mount", f"type=bind,src={bin_dir},dst=/probe-bin,readonly",
        "--mount", f"type=bind,src={result_dir},dst=/result",
        "--mount", f"type=bind,src={REPO / 'services/backup/init/scripts'},dst=/scripts,readonly",
        "--mount", f"type=bind,src={probe},dst=/probe.sh,readonly",
        "--tmpfs", "/tmp:rw,size=16m", "--entrypoint", "sh",
        MINIO_CLIENT_IMAGE, "/probe.sh", timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result_dir.joinpath("credentials.sha256").read_text().strip() == expected_sha
    combined = result.stdout + result.stderr
    for secret in values.values():
        assert secret not in combined


def _write_fake_setsid(fake_bin: Path) -> None:
    setsid = fake_bin / "setsid"
    setsid.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "os.setsid()\n"
        "os.execvp(sys.argv[1], sys.argv[1:])\n",
        encoding="utf-8",
    )
    setsid.chmod(0o755)


@contextmanager
def _contained_probe(
    command: list[str],
    *,
    env: dict[str, str],
    timeout: float,
    pid_files: tuple[Path, ...] = (),
) -> Iterator[subprocess.CompletedProcess[str]]:
    """Run a hostile-process probe and kill every published process on exit."""
    if _process_identity(os.getpid()) is None:
        pytest.skip("host process identity inspection is unavailable")
    containment_token = uuid.uuid4().hex
    probe_env = {**env, _CONTAINMENT_TOKEN_ENV: containment_token}
    process = subprocess.Popen(
        command,
        env=probe_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    cleanup_required = True
    owned = _OwnedProcesses(containment_token)
    owned.root_pid = process.pid
    root_identity = _direct_process_identity(process.pid)
    if root_identity is not None:
        owned[process.pid] = root_identity
        owned.verified_generations[process.pid] = root_identity[2]
    try:
        stdout, stderr = _communicate_contained_probe(
            process, timeout, pid_files, owned,
        )
        yield subprocess.CompletedProcess(
            command, process.returncode, stdout=stdout, stderr=stderr,
        )
        cleanup_required = False
    finally:
        if cleanup_required:
            _cleanup_contained_probe(process, pid_files, owned)


def _read_process_table(timeout: float = 2) -> dict[int, int]:
    if timeout <= 0:
        return {}
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,ppid="],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    table: dict[int, int] = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2 or not fields[0].isascii() or not fields[1].isascii():
            continue
        try:
            pid, parent = int(fields[0]), int(fields[1])
        except ValueError:
            continue
        if pid > 1:
            table[pid] = parent
    return table


def _process_identity(
    pid: int, table: dict[int, int] | None = None,
) -> tuple[int, int, str] | None:
    process_table = _read_process_table() if table is None else table
    if pid not in process_table:
        return None
    return _direct_process_identity(pid)


def _direct_process_identity(pid: int) -> tuple[int, int, str] | None:
    generation = _process_generation(pid)
    if generation is None:
        return None
    try:
        return os.getsid(pid), os.getpgid(pid), generation
    except (OSError, OverflowError, ValueError):
        return None


def _process_generation(pid: int) -> str | None:
    if sys.platform.startswith("linux"):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            fields = stat[stat.rfind(")") + 2:].split()
            return f"linux:{fields[19]}"
        except (IndexError, OSError, UnicodeError):
            return None
    if sys.platform == "darwin" and _DARWIN_LIBPROC is not None:
        info = _DarwinProcBsdInfo()
        size = ctypes.sizeof(info)
        read = _DARWIN_LIBPROC.proc_pidinfo(
            pid, 3, 0, ctypes.byref(info), size,
        )
        if read == size:
            return f"darwin:{info.pbi_start_tvsec}:{info.pbi_start_tvusec}"
    return None


def _safe_published_pids(
    pid_files: tuple[Path, ...], deadline: float | None = None,
) -> set[int]:
    pids: set[int] = set()
    for pid_file in pid_files:
        if deadline is not None and time.monotonic() >= deadline:
            break
        for raw_pid in _read_pid_hint_tokens(pid_file, deadline):
            if not raw_pid.isascii() or not raw_pid.isdecimal():
                continue
            try:
                pid = int(raw_pid)
            except (OverflowError, ValueError):
                continue
            if 1 < pid <= 2_147_483_647:
                pids.add(pid)
    return pids


def _read_pid_hint_tokens(
    pid_file: Path, deadline: float | None,
) -> list[str]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(pid_file, flags)
    except OSError:
        return []
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > _PID_HINT_MAX_BYTES
            or (deadline is not None and time.monotonic() >= deadline)
        ):
            return []
        try:
            encoded = os.read(descriptor, _PID_HINT_MAX_BYTES + 1)
        except OSError:
            return []
    finally:
        os.close(descriptor)
    if len(encoded) > _PID_HINT_MAX_BYTES:
        return []
    try:
        tokens = encoded.decode("utf-8").split()
    except UnicodeError:
        return []
    return tokens if len(tokens) <= _PID_HINT_MAX_TOKENS else []


def _capture_owned_processes(
    root_pid: int,
    pid_files: tuple[Path, ...],
    owned: dict[int, tuple[int, int, str]],
) -> None:
    table = _read_process_table(_remaining_owned_timeout(owned))
    for pid in _descendant_lineage(root_pid, table):
        identity = _process_identity(pid, table)
        if identity is not None:
            _record_owned_identity(pid, identity, owned)
    _capture_owned_pid_hints(pid_files, owned, table)


def _descendant_lineage(
    root_pid: int, table: dict[int, int],
) -> set[int]:
    lineage = {root_pid}
    while True:
        descendants = {
            pid for pid, parent in table.items()
            if parent in lineage
        }
        expanded = lineage | descendants
        if expanded == lineage:
            return lineage
        lineage = expanded


def _capture_owned_pid_hints(
    pid_files: tuple[Path, ...],
    owned: dict[int, tuple[int, int, str]],
    table: dict[int, int],
) -> None:
    owned_domains = {(identity[0], identity[1]) for identity in owned.values()}
    token = getattr(owned, "token", None)
    for pid in _safe_published_pids(
        pid_files, getattr(owned, "deadline", None),
    ):
        identity = _process_identity(pid, table) or _direct_process_identity(pid)
        if identity is None:
            continue
        token_matches = token is not None and _verify_owned_generation(
            pid, identity, owned,
        )
        if pid in owned or identity[:2] in owned_domains or token_matches:
            _record_owned_identity(pid, identity, owned)


def _record_owned_identity(
    pid: int,
    identity: tuple[int, int, str],
    owned: dict[int, tuple[int, int, str]],
) -> None:
    previous = owned.get(pid)
    generation_verified = _verify_owned_generation(pid, identity, owned)
    if previous is None or previous == identity:
        owned[pid] = identity
        return
    if (
        previous[2] == identity[2]
        and generation_verified
    ):
        owned[pid] = identity


def _verify_owned_generation(
    pid: int,
    identity: tuple[int, int, str],
    owned: dict[int, tuple[int, int, str]],
) -> bool:
    verified = getattr(owned, "verified_generations", {})
    if verified.get(pid) == identity[2]:
        return True
    token = getattr(owned, "token", None)
    if token is None or not _process_has_containment_token(
        pid, token, _remaining_owned_timeout(owned),
    ):
        return False
    verified[pid] = identity[2]
    return True


def _communicate_contained_probe(
    process: subprocess.Popen[str],
    timeout: float,
    pid_files: tuple[Path, ...],
    owned: dict[int, tuple[int, int, str]],
) -> tuple[str, str]:
    deadline = time.monotonic() + timeout
    if isinstance(owned, _OwnedProcesses):
        owned.deadline = deadline
    while True:
        _capture_owned_processes(process.pid, pid_files, owned)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout)
        try:
            return process.communicate(timeout=min(0.05, remaining))
        except subprocess.TimeoutExpired:
            continue


def _signal_owned_processes(
    owned: dict[int, tuple[int, int, str]], sent_signal: signal.Signals,
) -> None:
    signalled_groups: set[int] = set()
    for pid, identity in owned.items():
        current_identity = _direct_process_identity(pid)
        if current_identity is None:
            continue
        if current_identity != identity:
            _record_owned_identity(pid, current_identity, owned)
        identity = owned[pid]
        if current_identity != identity:
            continue
        group = identity[1]
        if group not in signalled_groups:
            try:
                os.killpg(group, sent_signal)
            except OSError:
                pass
            signalled_groups.add(group)
        try:
            os.kill(pid, sent_signal)
        except (OSError, OverflowError, ValueError):
            pass


def _process_has_containment_token(
    pid: int, token: str, timeout: float = 2,
) -> bool:
    if timeout <= 0:
        return False
    try:
        result = subprocess.run(
            ["ps", "eww", "-p", str(pid), "-o", "command="],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
        )
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return False
    marker = f"{_CONTAINMENT_TOKEN_ENV}={token}"
    return result.returncode == 0 and marker in result.stdout


def _remaining_owned_timeout(
    owned: dict[int, tuple[int, int, str]], cap: float = 2,
) -> float:
    deadline = getattr(owned, "deadline", None)
    if deadline is None:
        return cap
    return max(0.0, min(cap, deadline - time.monotonic()))


def _contained_probe_cleanup_complete(
    process: subprocess.Popen[str],
    owned: dict[int, tuple[int, int, str]],
) -> bool:
    return (
        process.poll() is not None
        and not any(
            _direct_process_identity(pid) == identity
            for pid, identity in owned.items()
        )
    )


def _cleanup_contained_probe(
    process: subprocess.Popen[str],
    pid_files: tuple[Path, ...],
    owned: dict[int, tuple[int, int, str]],
) -> None:
    cleanup_deadline = time.monotonic() + 1.5
    if isinstance(owned, _OwnedProcesses):
        owned.deadline = cleanup_deadline
    _kill_live_root_group(process, owned)
    stable_scans = 0
    stabilize_deadline = min(
        time.monotonic() + _CONTAINMENT_STABILIZE_SECONDS,
        cleanup_deadline,
    )
    while stable_scans < 2 and time.monotonic() < stabilize_deadline:
        prior = set(owned)
        _capture_owned_processes(process.pid, pid_files, owned)
        _signal_owned_processes(owned, signal.SIGSTOP)
        stable_scans = stable_scans + 1 if set(owned) == prior else 0
        time.sleep(0.01)

    _signal_owned_processes(owned, signal.SIGKILL)
    while time.monotonic() < cleanup_deadline:
        _capture_owned_processes(process.pid, pid_files, owned)
        _signal_owned_processes(owned, signal.SIGKILL)
        if _contained_probe_cleanup_complete(process, owned):
            break
        time.sleep(0.01)
    try:
        process.communicate(timeout=_remaining_owned_timeout(owned, 0.2))
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.communicate(timeout=0.1)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _kill_live_root_group(
    process: subprocess.Popen[str],
    owned: dict[int, tuple[int, int, str]],
) -> None:
    root_identity = owned.get(process.pid)
    current_root_identity = _direct_process_identity(process.pid)
    if root_identity is None or current_root_identity is None:
        return
    if root_identity[2] != current_root_identity[2]:
        return
    try:
        os.killpg(current_root_identity[1], signal.SIGKILL)
    except OSError:
        pass


def test_contained_probe_reaps_a_late_publishing_detached_child(
    tmp_path: Path,
) -> None:
    child_pid_file = tmp_path / "late-child.pid"
    control_pid_file = tmp_path / "control-child.pid"
    child = tmp_path / "late-child.py"
    child.write_text(
        "import os, signal, time\n"
        "os.setsid()\n"
        "signal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
        "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "open(os.environ['CONTROL_PID'], 'w').write(str(os.getpid()))\n"
        "open(os.environ['CHILD_PID'], 'w').write('1')\n"
        "time.sleep(1.3)\n"
        "open(os.environ['CHILD_PID'], 'w').write(str(os.getpid()))\n"
        "while True: time.sleep(60)\n",
        encoding="utf-8",
    )
    launcher = tmp_path / "late-launcher.sh"
    launcher.write_text(
        '"$PYTHON" "$CHILD" >/dev/null 2>&1 &\nwait\n',
        encoding="utf-8",
    )
    emergency_owned: dict[int, tuple[int, int, str]] = {}
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            with _contained_probe(
                ["sh", str(launcher)],
                env={
                    **os.environ,
                    "PYTHON": sys.executable,
                    "CHILD": str(child),
                    "CHILD_PID": str(child_pid_file),
                    "CONTROL_PID": str(control_pid_file),
                },
                timeout=0.1,
                pid_files=(child_pid_file,),
            ):
                pytest.fail("the intentionally blocking probe unexpectedly returned")

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not control_pid_file.exists():
            time.sleep(0.01)
        child_pid = int(control_pid_file.read_text(encoding="utf-8"))
        identity = _process_identity(child_pid)
        if identity is not None:
            emergency_owned[child_pid] = identity
        time.sleep(1.4)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        _signal_owned_processes(emergency_owned, signal.SIGKILL)


def test_contained_probe_tracks_a_child_across_setsid_transition(
    tmp_path: Path,
) -> None:
    child_pid_file = tmp_path / "transition-child.pid"
    child = tmp_path / "transition-child.py"
    child.write_text(
        "import os, signal, time\n"
        "open(os.environ['CHILD_PID'], 'w').write(str(os.getpid()))\n"
        "time.sleep(0.2)\n"
        "os.setsid()\n"
        "signal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
        "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "while True: time.sleep(60)\n",
        encoding="utf-8",
    )
    launcher = tmp_path / "transition-launcher.sh"
    launcher.write_text(
        '"$PYTHON" "$CHILD" >/dev/null 2>&1 &\nwait\n',
        encoding="utf-8",
    )
    emergency_owned: dict[int, tuple[int, int, str]] = {}
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            with _contained_probe(
                ["sh", str(launcher)],
                env={
                    **os.environ,
                    "PYTHON": sys.executable,
                    "CHILD": str(child),
                    "CHILD_PID": str(child_pid_file),
                },
                timeout=0.6,
                pid_files=(child_pid_file,),
            ):
                pytest.fail("the intentionally blocking probe unexpectedly returned")

        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        identity = _process_identity(child_pid)
        if identity is not None:
            emergency_owned[child_pid] = identity
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        _signal_owned_processes(emergency_owned, signal.SIGKILL)


def test_contained_probe_kills_a_token_verified_stopped_child(
    tmp_path: Path,
) -> None:
    child_pid_file = tmp_path / "stopped-child.pid"
    child = tmp_path / "stopped-child.py"
    child.write_text(
        "import os, signal, time\n"
        "open(os.environ['CHILD_PID'], 'w').write(str(os.getpid()))\n"
        "time.sleep(0.2)\n"
        "os.kill(os.getpid(), signal.SIGSTOP)\n"
        "while True: time.sleep(60)\n",
        encoding="utf-8",
    )
    launcher = tmp_path / "stopped-launcher.sh"
    launcher.write_text(
        '"$PYTHON" "$CHILD" >/dev/null 2>&1 &\nwait\n',
        encoding="utf-8",
    )
    emergency_owned: dict[int, tuple[int, int, str]] = {}
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            with _contained_probe(
                ["sh", str(launcher)],
                env={
                    **os.environ,
                    "PYTHON": sys.executable,
                    "CHILD": str(child),
                    "CHILD_PID": str(child_pid_file),
                },
                timeout=0.6,
                pid_files=(child_pid_file,),
            ):
                pytest.fail("the intentionally blocking probe unexpectedly returned")

        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        identity = _process_identity(child_pid)
        if identity is not None:
            emergency_owned[child_pid] = identity
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        _signal_owned_processes(emergency_owned, signal.SIGCONT)
        _signal_owned_processes(emergency_owned, signal.SIGKILL)


def test_contained_probe_preserves_timeout_when_a_pid_path_is_unreadable(
    tmp_path: Path,
) -> None:
    unreadable_pid_path = tmp_path / "pid-directory"
    unreadable_pid_path.mkdir()
    launcher_pid_file = tmp_path / "launcher.pid"
    launcher = tmp_path / "blocking-launcher.sh"
    launcher.write_text(
        'printf "%s" "$$" >"$LAUNCHER_PID"\nsleep 60\n',
        encoding="utf-8",
    )
    emergency_owned: dict[int, tuple[int, int, str]] = {}
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            with _contained_probe(
                ["sh", str(launcher)],
                env={**os.environ, "LAUNCHER_PID": str(launcher_pid_file)},
                timeout=0.1,
                pid_files=(unreadable_pid_path,),
            ):
                pytest.fail("the intentionally blocking probe unexpectedly returned")
        launcher_pid = int(launcher_pid_file.read_text(encoding="utf-8"))
        identity = _process_identity(launcher_pid)
        if identity is not None:
            emergency_owned[launcher_pid] = identity
        with pytest.raises(ProcessLookupError):
            os.kill(launcher_pid, 0)
    finally:
        _signal_owned_processes(emergency_owned, signal.SIGKILL)


def test_contained_probe_does_not_signal_stale_pids_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_pid_file = tmp_path / "stale.pid"
    stale_pid_file.write_text(str(os.getpid()), encoding="utf-8")
    cleanup_calls: list[int] = []
    monkeypatch.setattr(
        sys.modules[__name__],
        "_cleanup_contained_probe",
        lambda process, pid_files, owned: cleanup_calls.append(process.pid),
    )

    with _contained_probe(
        ["sh", "-c", "exit 0"],
        env=dict(os.environ),
        timeout=1,
        pid_files=(stale_pid_file,),
    ) as result:
        assert result.returncode == 0

    assert cleanup_calls == []


def test_contained_probe_rejects_unsafe_pids_during_failure_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    unsafe_pid_file = tmp_path / "unsafe.pids"
    unsafe_pid_file.write_text(
        f"0 1 {os.getpid()} {'9' * 100} ١٢\n", encoding="utf-8",
    )
    signals: list[tuple[str, int, int]] = []
    monkeypatch.setattr(
        os, "kill", lambda pid, sig: signals.append(("pid", pid, sig)),
    )
    monkeypatch.setattr(
        os, "killpg", lambda pgid, sig: signals.append(("group", pgid, sig)),
    )

    with pytest.raises(RuntimeError, match="force cleanup"):
        with _contained_probe(
            ["sh", "-c", "exit 0"],
            env=dict(os.environ),
            timeout=1,
            pid_files=(unsafe_pid_file,),
        ):
            raise RuntimeError("force cleanup")

    unsafe_targets = {0, 1, os.getpid()}
    assert all(target not in unsafe_targets for _kind, target, _sig in signals)


def test_safe_published_pids_rejects_invalid_utf8(tmp_path: Path) -> None:
    pid_file = tmp_path / "invalid.pid"
    pid_file.write_bytes(b"123\xff456")
    assert _safe_published_pids((pid_file,)) == set()


def test_safe_published_pids_rejects_a_fifo_without_blocking(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "hostile.pid"
    os.mkfifo(pid_file)

    started = time.monotonic()
    assert _safe_published_pids((pid_file,)) == set()
    assert time.monotonic() - started < 0.5


def test_safe_published_pids_rejects_oversized_files(tmp_path: Path) -> None:
    pid_file = tmp_path / "oversized.pid"
    pid_file.write_bytes(b"42 " * (_PID_HINT_MAX_BYTES // 3 + 1))

    assert _safe_published_pids((pid_file,)) == set()


def test_signal_owned_processes_rejects_a_reused_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[tuple[str, int]] = []
    monkeypatch.setattr(
        sys.modules[__name__],
        "_read_process_table",
        lambda: {42: 7},
    )
    monkeypatch.setattr(
        sys.modules[__name__], "_process_generation", lambda pid: "new-generation",
    )
    monkeypatch.setattr(os, "getsid", lambda pid: 42)
    monkeypatch.setattr(os, "getpgid", lambda pid: 42)
    monkeypatch.setattr(os, "kill", lambda pid, sig: signals.append(("pid", pid)))
    monkeypatch.setattr(
        os, "killpg", lambda pgid, sig: signals.append(("group", pgid)),
    )

    _signal_owned_processes({42: (42, 42, "old-generation")}, signal.SIGKILL)

    assert signals == []


def test_contained_probe_skips_before_launch_without_identity_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys.modules[__name__], "_read_process_table", lambda: {},
    )
    launched = False

    def reject_launch(*args, **kwargs):
        del args, kwargs
        nonlocal launched
        launched = True
        raise AssertionError("the hostile probe must not launch")

    monkeypatch.setattr(subprocess, "Popen", reject_launch)
    with pytest.raises(pytest.skip.Exception):
        with _contained_probe(
            ["sh", "-c", "exit 0"], env=dict(os.environ), timeout=1,
        ):
            pass
    assert launched is False


def test_contained_probe_retains_root_group_if_identity_backend_disappears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    child_pid_file = tmp_path / "backend-loss-child.pid"
    child = tmp_path / "backend-loss-child.py"
    child.write_text(
        "import os, signal, time\n"
        "signal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
        "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "open(os.environ['CHILD_PID'], 'w').write(str(os.getpid()))\n"
        "while True: time.sleep(60)\n",
        encoding="utf-8",
    )
    launcher = tmp_path / "backend-loss-launcher.sh"
    launcher.write_text('exec "$PYTHON" "$CHILD"\n', encoding="utf-8")
    real_process_table = _read_process_table
    calls = 0

    def disappearing_process_table(timeout=2):
        nonlocal calls
        calls += 1
        return real_process_table(timeout) if calls == 1 else {}

    monkeypatch.setattr(
        sys.modules[__name__], "_read_process_table", disappearing_process_table,
    )
    emergency_owned: dict[int, tuple[int, int, str]] = {}
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            with _contained_probe(
                ["sh", str(launcher)],
                env={
                    **os.environ,
                    "PYTHON": sys.executable,
                    "CHILD": str(child),
                    "CHILD_PID": str(child_pid_file),
                },
                timeout=0.2,
            ):
                pytest.fail("the intentionally blocking probe unexpectedly returned")

        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        identity = _direct_process_identity(child_pid)
        if identity is not None:
            emergency_owned[child_pid] = identity
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        _signal_owned_processes(emergency_owned, signal.SIGKILL)


def test_contained_probe_recovers_a_reparented_token_owned_pid(
    tmp_path: Path,
) -> None:
    daemon_pid_file = tmp_path / "daemon.pid"
    daemon = tmp_path / "daemon.py"
    daemon.write_text(
        "import os, signal, time\n"
        "pid = os.fork()\n"
        "if pid: raise SystemExit(0)\n"
        "os.setsid()\n"
        "signal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
        "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "open(os.environ['DAEMON_PID'], 'w').write(str(os.getpid()))\n"
        "while True: time.sleep(60)\n",
        encoding="utf-8",
    )
    launcher = tmp_path / "daemon-launcher.sh"
    launcher.write_text(
        '"$PYTHON" "$DAEMON" >/dev/null 2>&1\n'
        'while [ ! -s "$DAEMON_PID" ]; do sleep 0.01; done\n',
        encoding="utf-8",
    )
    emergency_owned: dict[int, tuple[int, int, str]] = {}
    try:
        with pytest.raises(RuntimeError, match="force cleanup"):
            with _contained_probe(
                ["sh", str(launcher)],
                env={
                    **os.environ,
                    "PYTHON": sys.executable,
                    "DAEMON": str(daemon),
                    "DAEMON_PID": str(daemon_pid_file),
                },
                timeout=2,
                pid_files=(daemon_pid_file,),
            ):
                raise RuntimeError("force cleanup")

        daemon_pid = int(daemon_pid_file.read_text(encoding="utf-8"))
        identity = _process_identity(daemon_pid)
        if identity is not None:
            emergency_owned[daemon_pid] = identity
        with pytest.raises(ProcessLookupError):
            os.kill(daemon_pid, 0)
    finally:
        _signal_owned_processes(emergency_owned, signal.SIGKILL)


def test_contained_probe_bounds_an_ever_changing_process_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = subprocess.Popen(["sh", "-c", "exit 0"])
    process.wait(timeout=1)
    next_pid = 10_000

    def grow_tree(root_pid, pid_files, owned):
        del root_pid, pid_files
        nonlocal next_pid
        next_pid += 1
        owned[next_pid] = (next_pid, next_pid, f"generation-{next_pid}")

    monkeypatch.setattr(
        sys.modules[__name__], "_capture_owned_processes", grow_tree,
    )
    monkeypatch.setattr(
        sys.modules[__name__], "_signal_owned_processes", lambda *args: None,
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "_contained_probe_cleanup_complete",
        lambda *args: False,
    )

    started = time.monotonic()
    _cleanup_contained_probe(process, (), _OwnedProcesses("test-token"))
    assert time.monotonic() - started < 2


def test_contained_probe_shares_one_deadline_with_slow_identity_scans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = subprocess.Popen(["sh", "-c", "exit 0"])
    process.wait(timeout=1)

    def slow_process_table(timeout=2):
        time.sleep(timeout)
        return {}

    monkeypatch.setattr(
        sys.modules[__name__], "_read_process_table", slow_process_table,
    )
    started = time.monotonic()
    _cleanup_contained_probe(process, (), _OwnedProcesses("test-token"))
    assert time.monotonic() - started < 2


def test_contained_probe_shares_execution_deadline_with_pid_hint_reads(
    tmp_path: Path,
) -> None:
    hostile_pid_file = tmp_path / "hostile.pid"
    os.mkfifo(hostile_pid_file)
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired):
        with _contained_probe(
            ["sh", "-c", "sleep 60"],
            env=dict(os.environ),
            timeout=0.1,
            pid_files=(hostile_pid_file,),
        ):
            pytest.fail("the intentionally blocking probe unexpectedly returned")

    assert time.monotonic() - started < 2


def test_contained_probe_does_not_signal_a_completed_reused_root_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = subprocess.Popen(["sh", "-c", "exit 0"])
    process.wait(timeout=1)
    owned = _OwnedProcesses("test-token")
    owned[process.pid] = (process.pid, process.pid, "retired-generation")
    groups: list[int] = []
    monkeypatch.setattr(
        os, "killpg", lambda pgid, sent_signal: groups.append(pgid),
    )

    _cleanup_contained_probe(process, (), owned)

    assert groups == []


def test_contained_probe_ignores_an_unrelated_detached_pid(
    tmp_path: Path,
) -> None:
    unrelated = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "time.sleep(60)",
        ],
        start_new_session=True,
    )
    unrelated_pid_file = tmp_path / "unrelated.pid"
    unrelated_pid_file.write_text(str(unrelated.pid), encoding="utf-8")
    try:
        with pytest.raises(RuntimeError, match="force cleanup"):
            with _contained_probe(
                ["sh", "-c", "exit 0"],
                env=dict(os.environ),
                timeout=1,
                pid_files=(unrelated_pid_file,),
            ):
                raise RuntimeError("force cleanup")
        assert unrelated.poll() is None
    finally:
        if unrelated.poll() is None:
            unrelated.kill()
        unrelated.wait(timeout=2)


def _term_resistant_stream_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    producer_pid = tmp_path / "producer.pid"
    descendant_pid = tmp_path / "descendant.pid"
    sleep_pids = tmp_path / "sleep.pids"
    _write_fake_setsid(fake_bin)
    real_sleep = shutil.which("sleep")
    assert real_sleep
    sleep = fake_bin / "sleep"
    sleep.write_text(
        f"#!{sys.executable}\n"
        "import os, signal, time, sys\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "with open(os.environ['SLEEP_PIDS'], 'a', encoding='utf-8') as stream:\n"
        "    stream.write(f'{os.getpid()}\\n')\n"
        "time.sleep(float(sys.argv[1]))\n",
        encoding="utf-8",
    )
    sleep.chmod(0o755)
    descendant = fake_bin / "descendant"
    descendant.write_text(
        "#!/bin/sh\n"
        "trap '' HUP INT TERM PIPE\n"
        "printf '%s\\n' \"$$\" >\"$DESCENDANT_PID\"\n"
        "while :; do printf x 2>/dev/null || true; done\n",
        encoding="utf-8",
    )
    descendant.chmod(0o755)
    mc = fake_bin / "mc"
    mc.write_text(
        "#!/bin/sh\n"
        "trap 'exit 0' HUP INT TERM\n"
        "printf '%s\\n' \"$$\" >\"$PRODUCER_PID\"\n"
        "descendant &\n"
        "dd if=/dev/zero bs=2049 count=1 2>/dev/null\n"
        "wait\n",
        encoding="utf-8",
    )
    mc.chmod(0o755)
    return fake_bin, producer_pid, descendant_pid, sleep_pids, real_sleep


def _orphaned_stream_fixture(tmp_path: Path) -> tuple[Path, Path, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    descendant_pid = tmp_path / "descendant.pid"
    _write_fake_setsid(fake_bin)
    real_sleep = shutil.which("sleep")
    assert real_sleep
    descendant = fake_bin / "descendant"
    descendant.write_text(
        "#!/bin/sh\n"
        "trap '' HUP INT TERM PIPE\n"
        "printf '%s\\n' \"$$\" >\"$DESCENDANT_PID\"\n"
        "while :; do sleep 60; done\n",
        encoding="utf-8",
    )
    descendant.chmod(0o755)
    mc = fake_bin / "mc"
    mc.write_text(
        "#!/bin/sh\n"
        "descendant &\n"
        "while [ ! -s \"$DESCENDANT_PID\" ]; do\n"
        "  \"$REAL_SLEEP\" 0.01\n"
        "done\n"
        "exit 0\n",
        encoding="utf-8",
    )
    mc.chmod(0o755)
    return fake_bin, descendant_pid, real_sleep


def _stopped_watchdog_fixture(tmp_path: Path) -> tuple[Path, tuple[Path, ...]]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_setsid(fake_bin)
    marker = tmp_path / "watchdog-stopped"
    watchdog_pid = tmp_path / "watchdog.pid"
    sleeper_pid = tmp_path / "watchdog-sleeper.pid"
    real_sleep = shutil.which("sleep")
    assert real_sleep
    sleep = fake_bin / "sleep"
    sleep.write_text(
        f"#!{sys.executable}\n"
        "import os, signal, sys, time\n"
        "if sys.argv[1] == '30':\n"
        "    parent = os.getppid()\n"
        "    open(os.environ['WATCHDOG_PID'], 'w').write(str(parent))\n"
        "    open(os.environ['SLEEPER_PID'], 'w').write(str(os.getpid()))\n"
        "    open(os.environ['STOPPED_MARKER'], 'w').close()\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "    os.kill(parent, signal.SIGSTOP)\n"
        "    time.sleep(60)\n"
        f"os.execv('{real_sleep}', ['sleep', *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    sleep.chmod(0o755)
    mc = fake_bin / "mc"
    mc.write_text(
        "#!/bin/sh\n"
        "while [ ! -e \"$STOPPED_MARKER\" ]; do \"$REAL_SLEEP\" 0.01; done\n"
        "exit 0\n",
        encoding="utf-8",
    )
    mc.chmod(0o755)
    return fake_bin, (marker, watchdog_pid, sleeper_pid)


def _presession_stopped_producer_fixture(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    producer_pid = tmp_path / "producer.pid"
    setsid = fake_bin / "setsid"
    setsid.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "if [ \"${4:-}\" = atlas-s3-producer ]; then\n"
        "  printf '%s' \"$$\" >\"$PRODUCER_PID\"\n"
        "  kill -STOP \"$$\"\n"
        "fi\n"
        "exit 70\n",
        encoding="utf-8",
    )
    setsid.chmod(0o755)
    mc = fake_bin / "mc"
    mc.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    mc.chmod(0o755)
    return fake_bin, producer_pid


def _failed_watchdog_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    producer_pid = tmp_path / "producer.pid"
    setsid = fake_bin / "setsid"
    setsid.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "if len(sys.argv) > 4 and sys.argv[4] == 'atlas-s3-watchdog':\n"
        "    raise SystemExit(70)\n"
        "os.setsid()\n"
        "os.execvp(sys.argv[1], sys.argv[1:])\n",
        encoding="utf-8",
    )
    setsid.chmod(0o755)
    mc = fake_bin / "mc"
    mc.write_text(
        f"#!{sys.executable}\n"
        "import os, time\n"
        "open(os.environ['PRODUCER_PID'], 'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    mc.chmod(0o755)
    real_sleep = Path(shutil.which("sleep") or pytest.fail("sleep is required"))
    sleep = fake_bin / "sleep"
    sleep.write_text(
        "#!/bin/sh\n"
        "if [ \"${1:-}\" = 0.01 ] && [ ! -s \"$PRODUCER_PID\" ]; then\n"
        "  exec \"$REAL_SLEEP\" 0.05\n"
        "fi\n"
        "exec \"$REAL_SLEEP\" \"$@\"\n",
        encoding="utf-8",
    )
    sleep.chmod(0o755)
    return fake_bin, producer_pid, real_sleep


def _failed_watchdog_mktemp_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    producer_pid = tmp_path / "producer.pid"
    mktemp_state = tmp_path / "mktemp.state"
    setsid = fake_bin / "setsid"
    setsid.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "if len(sys.argv) > 4 and sys.argv[4] == 'atlas-s3-producer':\n"
        "    open(os.environ['PRODUCER_PID'], 'w').write(str(os.getpid()))\n"
        "os.setsid()\n"
        "os.execvp(sys.argv[1], sys.argv[1:])\n",
        encoding="utf-8",
    )
    setsid.chmod(0o755)
    mktemp = fake_bin / "mktemp"
    mktemp.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, time\n"
        "state = pathlib.Path(os.environ['MKTEMP_STATE'])\n"
        "if state.exists():\n"
        "    producer = pathlib.Path(os.environ['PRODUCER_PID'])\n"
        "    deadline = time.monotonic() + 10\n"
        "    while not producer.exists() and time.monotonic() < deadline:\n"
        "        time.sleep(0.01)\n"
        "    if not producer.exists():\n"
        "        raise SystemExit(72)\n"
        "    raise SystemExit(1)\n"
        "state.write_text('producer')\n"
        "ready = state.with_suffix('.ready')\n"
        "ready.touch()\n"
        "print(ready)\n",
        encoding="utf-8",
    )
    mktemp.chmod(0o755)
    mc = fake_bin / "mc"
    mc.write_text(
        f"#!{sys.executable}\n"
        "import os, time\n"
        "open(os.environ['PRODUCER_PID'], 'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    mc.chmod(0o755)
    return fake_bin, producer_pid, mktemp_state


def _delayed_failed_producer_mktemp_fixture(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "mktemp-started"
    _write_fake_setsid(fake_bin)
    real_sleep = shutil.which("sleep")
    assert real_sleep
    mktemp = fake_bin / "mktemp"
    mktemp.write_text(
        "#!/bin/sh\n"
        ": >\"$MKTEMP_MARKER\"\n"
        "\"$REAL_SLEEP\" 0.5\n"
        "exit 1\n",
        encoding="utf-8",
    )
    mktemp.chmod(0o755)
    return fake_bin, marker


def _unreapable_producer_fixture(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    producer_pid = tmp_path / "producer.pid"
    _write_fake_setsid(fake_bin)
    mc = fake_bin / "mc"
    mc.write_text(
        f"#!{sys.executable}\n"
        "import os, signal, time\n"
        "signal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
        "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "open(os.environ['PRODUCER_PID'], 'w').write(str(os.getpid()))\n"
        "while True: time.sleep(60)\n",
        encoding="utf-8",
    )
    mc.chmod(0o755)
    return fake_bin, producer_pid


def _ineffective_watchdog_kill_fixture(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    producer_pid = tmp_path / "producer.pid"
    setsid = fake_bin / "setsid"
    setsid.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "if len(sys.argv) > 4 and sys.argv[4] == 'atlas-s3-watchdog':\n"
        "    sys.argv[3] = sys.argv[3].replace(\n"
        "        'kill \"-$1\" \"-$producer_target\" 2>/dev/null || true', 'true'\n"
        "    ).replace(\n"
        "        'delay_watchdog \"$stream_timeout\"',\n"
        "        'probe=0; while [ ! -s \"$PRODUCER_PID\" ]; do '\n"
        "        'probe=$((probe + 1)); [ \"$probe\" -lt 100 ] || exit 70; '\n"
        "        'sleep 0.01; done; delay_watchdog \"$stream_timeout\"'\n"
        "    ).replace('delay_watchdog 10', 'delay_watchdog 0.1')\n"
        "os.setsid()\n"
        "os.execvp(sys.argv[1], sys.argv[1:])\n",
        encoding="utf-8",
    )
    setsid.chmod(0o755)
    mc = fake_bin / "mc"
    mc.write_text(
        f"#!{sys.executable}\n"
        "import os, time\n"
        "open(os.environ['PRODUCER_PID'], 'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    mc.chmod(0o755)
    return fake_bin, producer_pid


def test_s3_stream_supervisor_kills_term_resistant_producer(tmp_path: Path) -> None:
    (
        fake_bin, producer_pid, descendant_pid, sleep_pids, real_sleep,
    ) = _term_resistant_stream_fixture(tmp_path)
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "#!/bin/sh\nset -eu\n"
        ". \"$S3_CLIENT\"\n"
        "fifo=$PROBE_ROOT/download.fifo\nmkfifo \"$fifo\"\n"
        "backup_s3_stream_command 30 mc cat object >\"$fifo\" & supervisor=$!\n"
        "head -c 2049 <\"$fifo\" >/dev/null\n"
        "attempt=0\nwhile [ ! -s \"$DESCENDANT_PID\" ]; do\n"
        "  attempt=$((attempt + 1)); [ \"$attempt\" -lt 100 ]\n"
        "  \"$REAL_SLEEP\" 0.01\ndone\n"
        "kill \"$supervisor\"\nstatus=0\nwait \"$supervisor\" || status=$?\n"
        "rm -f \"$fifo\"\n[ \"$status\" -eq 143 ]\n",
        encoding="utf-8",
    )
    probe.chmod(0o755)

    started = time.monotonic()
    with _contained_probe(
        ["sh", str(probe)],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
            "S3_CLIENT": str(REPO / "services/backup/init/scripts/s3-client.sh"),
            "PROBE_ROOT": str(tmp_path),
            "PRODUCER_PID": str(producer_pid),
            "DESCENDANT_PID": str(descendant_pid),
            "SLEEP_PIDS": str(sleep_pids),
            "REAL_SLEEP": real_sleep,
        },
        timeout=7,
        pid_files=(producer_pid, descendant_pid, sleep_pids),
    ) as result:
        assert result.returncode == 0, result.stderr
        assert time.monotonic() - started < 6
        owned_pids = [
            int(producer_pid.read_text(encoding="utf-8")),
            int(descendant_pid.read_text(encoding="utf-8")),
            *(int(pid) for pid in sleep_pids.read_text(encoding="utf-8").splitlines()),
        ]
        for pid in owned_pids:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        assert not (tmp_path / "download.fifo").exists()


def test_s3_stream_supervisor_force_kills_stopped_watchdog(tmp_path: Path) -> None:
    fake_bin, fixture_paths = _stopped_watchdog_fixture(tmp_path)
    marker, watchdog_pid, sleeper_pid = fixture_paths
    real_sleep = shutil.which("sleep")
    assert real_sleep
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "#!/bin/sh\nset -eu\n"
        ". \"$S3_CLIENT\"\n"
        "backup_s3_stream_command 30 mc cat object & supervisor=$!\n"
        "wait \"$supervisor\"\n",
        encoding="utf-8",
    )
    probe.chmod(0o755)
    with _contained_probe(
        ["sh", str(probe)],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
            "S3_CLIENT": str(REPO / "services/backup/init/scripts/s3-client.sh"),
            "STOPPED_MARKER": str(marker),
            "WATCHDOG_PID": str(watchdog_pid),
            "SLEEPER_PID": str(sleeper_pid),
            "REAL_SLEEP": real_sleep,
        },
        timeout=6,
        pid_files=(watchdog_pid, sleeper_pid),
    ) as result:
        assert result.returncode == 0, result.stderr
        for pid_file in (watchdog_pid, sleeper_pid):
            with pytest.raises(ProcessLookupError):
                os.kill(int(pid_file.read_text(encoding="utf-8")), 0)


def test_s3_stream_supervisor_owns_producer_before_session_exists(
    tmp_path: Path,
) -> None:
    fake_bin, producer_pid = _presession_stopped_producer_fixture(tmp_path)
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "#!/bin/sh\nset -eu\n"
        ". \"$S3_CLIENT\"\n"
        "backup_s3_stream_command 30 mc cat object & supervisor=$!\n"
        "marker_attempt=0\n"
        "while [ ! -s \"$PRODUCER_PID\" ] && kill -0 \"$supervisor\" 2>/dev/null "
        "&& [ \"$marker_attempt\" -lt 400 ]; do\n"
        "  marker_attempt=$((marker_attempt + 1))\n"
        "  sleep 0.01\n"
        "done\n"
        "[ -s \"$PRODUCER_PID\" ] || { wait \"$supervisor\" || true; exit 71; }\n"
        "status=0\nwait \"$supervisor\" || status=$?\n"
        "[ \"$status\" -eq 69 ]\n",
        encoding="utf-8",
    )
    probe.chmod(0o755)
    with _contained_probe(
        ["sh", str(probe)],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
            "S3_CLIENT": str(REPO / "services/backup/init/scripts/s3-client.sh"),
            "PRODUCER_PID": str(producer_pid),
        },
        timeout=10,
        pid_files=(producer_pid,),
    ) as result:
        assert result.returncode == 0, result.stderr
        assert "producer failed to establish its session" in result.stderr
        with pytest.raises(ProcessLookupError):
            os.kill(int(producer_pid.read_text(encoding="utf-8")), 0)


def test_s3_stream_supervisor_fails_closed_when_watchdog_launch_fails(
    tmp_path: Path,
) -> None:
    fake_bin, producer_pid, real_sleep = _failed_watchdog_fixture(tmp_path)
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "#!/bin/sh\nset -eu\n"
        ". \"$S3_CLIENT\"\n"
        "backup_s3_stream_command 1 mc cat object & supervisor=$!\n"
        "marker_attempt=0\n"
        "while [ ! -s \"$PRODUCER_PID\" ] && kill -0 \"$supervisor\" 2>/dev/null "
        "&& [ \"$marker_attempt\" -lt 400 ]; do\n"
        "  marker_attempt=$((marker_attempt + 1))\n"
        "  sleep 0.01\n"
        "done\n"
        "[ -s \"$PRODUCER_PID\" ] || { wait \"$supervisor\" || true; exit 71; }\n"
        "status=0\nwait \"$supervisor\" || status=$?\n"
        "[ \"$status\" -eq 69 ]\n",
        encoding="utf-8",
    )
    probe.chmod(0o755)
    with _contained_probe(
        ["sh", str(probe)],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
            "S3_CLIENT": str(REPO / "services/backup/init/scripts/s3-client.sh"),
            "PRODUCER_PID": str(producer_pid),
            "REAL_SLEEP": str(real_sleep),
        },
        timeout=15,
        pid_files=(producer_pid,),
    ) as result:
        assert result.returncode == 0, result.stderr
        assert "watchdog failed to become ready" in result.stderr
        with pytest.raises(ProcessLookupError):
            os.kill(int(producer_pid.read_text(encoding="utf-8")), 0)


def test_s3_stream_supervisor_cleans_producer_when_watchdog_mktemp_fails(
    tmp_path: Path,
) -> None:
    fake_bin, producer_pid, mktemp_state = _failed_watchdog_mktemp_fixture(tmp_path)
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "#!/bin/sh\nset -eu\n"
        ". \"$S3_CLIENT\"\n"
        "backup_s3_stream_command 1 mc cat object & supervisor=$!\n"
        "status=0\nwait \"$supervisor\" || status=$?\n"
        "[ \"$status\" -eq 69 ]\n",
        encoding="utf-8",
    )
    probe.chmod(0o755)
    with _contained_probe(
        ["sh", str(probe)],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
            "S3_CLIENT": str(REPO / "services/backup/init/scripts/s3-client.sh"),
            "PRODUCER_PID": str(producer_pid),
            "MKTEMP_STATE": str(mktemp_state),
        },
        timeout=12,
        pid_files=(producer_pid,),
    ) as result:
        assert result.returncode == 0, result.stderr
        assert "could not allocate S3 watchdog readiness file" in result.stderr
        with pytest.raises(ProcessLookupError):
            os.kill(int(producer_pid.read_text(encoding="utf-8")), 0)


def test_s3_stream_supervisor_preserves_signal_during_producer_mktemp_failure(
    tmp_path: Path,
) -> None:
    fake_bin, marker = _delayed_failed_producer_mktemp_fixture(tmp_path)
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "#!/bin/sh\nset -eu\n"
        ". \"$S3_CLIENT\"\n"
        "backup_s3_stream_command 1 should-not-run & supervisor=$!\n"
        "while [ ! -e \"$MKTEMP_MARKER\" ]; do \"$REAL_SLEEP\" 0.01; done\n"
        "kill -TERM \"$supervisor\"\n"
        "status=0\nwait \"$supervisor\" || status=$?\n"
        "[ \"$status\" -eq 143 ]\n",
        encoding="utf-8",
    )
    probe.chmod(0o755)
    with _contained_probe(
        ["sh", str(probe)],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
            "S3_CLIENT": str(REPO / "services/backup/init/scripts/s3-client.sh"),
            "MKTEMP_MARKER": str(marker),
            "REAL_SLEEP": str(shutil.which("sleep")),
        },
        timeout=3,
    ) as result:
        assert result.returncode == 0, result.stderr


def test_s3_stream_supervisor_deadline_interrupts_wait_if_watchdog_kill_fails(
    tmp_path: Path,
) -> None:
    fake_bin, producer_pid = _ineffective_watchdog_kill_fixture(tmp_path)
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "#!/bin/sh\nset -eu\n"
        ". \"$S3_CLIENT\"\n"
        "backup_s3_stream_command 0.1 mc cat object & supervisor=$!\n"
        "status=0\nwait \"$supervisor\" || status=$?\n"
        "[ \"$status\" -eq 137 ]\n",
        encoding="utf-8",
    )
    probe.chmod(0o755)
    with _contained_probe(
        ["sh", str(probe)],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
            "S3_CLIENT": str(REPO / "services/backup/init/scripts/s3-client.sh"),
            "PRODUCER_PID": str(producer_pid),
        },
        timeout=5,
        pid_files=(producer_pid,),
    ) as result:
        assert result.returncode == 0, result.stderr
        with pytest.raises(ProcessLookupError):
            os.kill(int(producer_pid.read_text(encoding="utf-8")), 0)


def test_s3_stream_supervisor_fails_closed_without_setsid(tmp_path: Path) -> None:
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    probe = tmp_path / "probe.sh"
    probe.write_text(
        ". \"$S3_CLIENT\"\n"
        "status=0\n"
        "backup_s3_stream_command 30 should-not-run || status=$?\n"
        "[ \"$status\" -eq 69 ]\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["/bin/sh", str(probe)],
        env={"PATH": str(empty_bin), "S3_CLIENT": str(
            REPO / "services/backup/init/scripts/s3-client.sh"
        )},
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "setsid is required" in result.stderr


def test_s3_stream_supervisor_preserves_first_signal_during_final_sweep(
    tmp_path: Path,
) -> None:
    fake_bin, descendant_pid, real_sleep = _orphaned_stream_fixture(tmp_path)
    sleep = fake_bin / "sleep"
    sleep.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = 1 ]; then\n"
        "  kill -HUP \"$PPID\"\n"
        "  \"$REAL_SLEEP\" 0.2\n"
        "  kill -TERM \"$PPID\" 2>/dev/null || true\n"
        "fi\n"
        "exec \"$REAL_SLEEP\" \"$@\"\n",
        encoding="utf-8",
    )
    sleep.chmod(0o755)
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "#!/bin/sh\nset -eu\n"
        ". \"$S3_CLIENT\"\n"
        "backup_s3_stream_command 30 mc cat object & supervisor=$!\n"
        "status=0\n"
        "wait \"$supervisor\" || status=$?\n"
        "[ \"$status\" -eq 129 ]\n",
        encoding="utf-8",
    )
    probe.chmod(0o755)

    with _contained_probe(
        ["sh", str(probe)],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
            "S3_CLIENT": str(REPO / "services/backup/init/scripts/s3-client.sh"),
            "DESCENDANT_PID": str(descendant_pid),
            "REAL_SLEEP": real_sleep,
        },
        timeout=5,
        pid_files=(descendant_pid,),
    ) as result:
        assert result.returncode == 0, result.stderr
        with pytest.raises(ProcessLookupError):
            os.kill(int(descendant_pid.read_text(encoding="utf-8")), 0)


def test_s3_stream_supervisor_fails_if_group_absence_cannot_be_proven(
    tmp_path: Path,
) -> None:
    fake_bin, descendant_pid, real_sleep = _orphaned_stream_fixture(tmp_path)
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "#!/bin/sh\nset -eu\n"
        ". \"$S3_CLIENT\"\n"
        "forced_alive=0\n"
        "kill() {\n"
        "  if [ \"${1-}\" = -KILL ] && [ \"${2#-}\" != \"${2-}\" ]; then\n"
        "    command kill \"$@\" 2>/dev/null || true\n"
        "    forced_alive=1\n"
        "    return 0\n"
        "  fi\n"
        "  if [ \"${1-}\" = -0 ] && [ \"$forced_alive\" -eq 1 ] "
        "&& [ \"${2#-}\" != \"${2-}\" ]; then\n"
        "    return 0\n"
        "  fi\n"
        "  command kill \"$@\"\n"
        "}\n"
        "backup_s3_stream_command 30 mc cat object & supervisor=$!\n"
        "status=0\n"
        "wait \"$supervisor\" || status=$?\n"
        "[ \"$status\" -eq 1 ]\n",
        encoding="utf-8",
    )
    probe.chmod(0o755)

    with _contained_probe(
        ["sh", str(probe)],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
            "S3_CLIENT": str(REPO / "services/backup/init/scripts/s3-client.sh"),
            "DESCENDANT_PID": str(descendant_pid),
            "REAL_SLEEP": real_sleep,
        },
        timeout=6,
        pid_files=(descendant_pid,),
    ) as result:
        assert result.returncode == 0, result.stderr
        assert "process group survived forced termination" in result.stderr
        with pytest.raises(ProcessLookupError):
            os.kill(int(descendant_pid.read_text(encoding="utf-8")), 0)


def test_s3_stream_supervisor_tolerates_loaded_session_startup(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    setsid = fake_bin / "setsid"
    setsid.write_text(
        f"#!{sys.executable}\n"
        "import os, sys, time\n"
        "if 'atlas-s3-producer' in sys.argv:\n"
        "    time.sleep(4)\n"
        "os.setsid()\n"
        "os.execvp(sys.argv[1], sys.argv[1:])\n",
        encoding="utf-8",
    )
    setsid.chmod(0o755)
    mc = fake_bin / "mc"
    mc.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    mc.chmod(0o755)
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "#!/bin/sh\nset -eu\n"
        ". \"$S3_CLIENT\"\n"
        "backup_s3_stream_command 30 mc cat object\n",
        encoding="utf-8",
    )
    probe.chmod(0o755)

    with _contained_probe(
        ["sh", str(probe)],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
            "S3_CLIENT": str(REPO / "services/backup/init/scripts/s3-client.sh"),
        },
        timeout=8,
    ) as result:
        assert result.returncode == 0, result.stderr
        assert "failed to establish its session" not in result.stderr


def test_s3_stream_supervisor_bounds_an_unreapable_direct_child(
    tmp_path: Path,
) -> None:
    fake_bin, producer_pid = _unreapable_producer_fixture(tmp_path)
    errors = tmp_path / "errors"
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "#!/bin/sh\nset -eu\n"
        ". \"$S3_CLIENT\"\n"
        "kill() {\n"
        "  [ \"${1-}\" != -KILL ] || return 0\n"
        "  command kill \"$@\"\n"
        "}\n"
        "backup_s3_stream_command 30 mc cat object >/dev/null 2>\"$ERRORS\" "
        "& supervisor=$!\n"
        "while [ ! -s \"$PRODUCER_PID\" ]; do sleep 0.01; done\n"
        "kill -TERM \"$supervisor\"\n"
        "status=0\nwait \"$supervisor\" || status=$?\n"
        "[ \"$status\" -eq 143 ]\n",
        encoding="utf-8",
    )
    probe.chmod(0o755)
    owned: dict[int, tuple[int, int, str]] = {}
    try:
        with _contained_probe(
            ["sh", str(probe)],
            env={
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                "S3_CLIENT": str(REPO / "services/backup/init/scripts/s3-client.sh"),
                "PRODUCER_PID": str(producer_pid),
                "ERRORS": str(errors),
            },
            timeout=8,
            pid_files=(producer_pid,),
        ) as result:
            pid = int(producer_pid.read_text(encoding="utf-8"))
            identity = _process_identity(pid)
            assert identity is not None
            owned[pid] = identity
            assert result.returncode == 0, result.stderr
            os.kill(pid, 0)
            assert "child survived forced termination" in errors.read_text()
    finally:
        _signal_owned_processes(owned, signal.SIGCONT)
        _signal_owned_processes(owned, signal.SIGKILL)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.01)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize("bad", ["line\nbreak", "tab\tvalue", "delete\x7fvalue"])
def test_backup_rejects_control_bytes_in_credentials_before_io(
    tmp_path: Path, bad: str,
) -> None:
    trace, bin_dir = _fake_s3_boundary(tmp_path)
    result = subprocess.run(
        ["sh", str(REPO / "services/backup/init/scripts/backup-all.sh")],
        env={
            **_backup_s3_env(bin_dir, trace),
            "BACKUP_S3_MODE": "external",
            "BACKUP_S3_ENDPOINT": "https://s3.example.test",
            "BACKUP_S3_ACCESS_KEY": "external-access",
            "BACKUP_S3_SECRET_KEY": bad,
        },
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 64
    assert "valid UTF-8 without control bytes" in result.stderr
    assert not trace.exists()


def test_entrypoint_uses_exact_checksum_verified_mc_release() -> None:
    entrypoint = (REPO / "services/backup/init/scripts/entrypoint.sh").read_text(
        encoding="utf-8"
    )
    assert "RELEASE.2025-08-13T08-35-41Z" in entrypoint
    assert "01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891" in entrypoint
    assert "14c8c9616cfce4636add161304353244e8de383b2e2752c0e9dad01d4c27c12c" in entrypoint
    assert "run_bounded sha256sum" in entrypoint
    assert 'run_bounded "$mc_candidate" --version' in entrypoint
    assert "command -v setsid" in entrypoint
    assert "minio-client" not in entrypoint


def test_entrypoint_artifact_override_is_test_only_and_keeps_official_default() -> None:
    entrypoint = (REPO / "services/backup/init/scripts/entrypoint.sh").read_text(
        encoding="utf-8"
    )
    override = "ATLAS_BACKUP_TEST_MC_ARTIFACT_BASE_URL"
    assert (
        "${ATLAS_BACKUP_TEST_MC_ARTIFACT_BASE_URL:-"
        "https://github.com/minio/mc/releases/download/${MC_RELEASE}}"
    ) in entrypoint
    assert override not in (REPO / "services/backup/compose.yml").read_text(
        encoding="utf-8"
    )
    assert override not in (REPO / "services/backup/service.yml").read_text(
        encoding="utf-8"
    )


def _entrypoint_installer_fixture(
    tmp_path: Path, *, arch: str = "x86_64", wget_rc: int = 0, sha_rc: int = 0,
) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    install_dir = tmp_path / "install"
    bin_dir.mkdir()
    install_dir.mkdir()
    (bin_dir / "openssl").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "uname").write_text(f"#!/bin/sh\nprintf '%s\\n' {arch!r}\n")
    (bin_dir / "wget").write_text(
        "#!/bin/sh\n"
        f"[ {wget_rc} -eq 0 ] || exit {wget_rc}\n"
        "while [ \"$1\" != -O ]; do shift; done; shift\n"
        "printf '%s\\n' '#!/bin/sh' "
        "\"printf '%s\\n' 'mc version RELEASE.2025-08-13T08-35-41Z'\" >\"$1\"\n"
    )
    checksum = (
        "01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891"
        if arch in {"x86_64", "amd64"}
        else "14c8c9616cfce4636add161304353244e8de383b2e2752c0e9dad01d4c27c12c"
    )
    (bin_dir / "sha256sum").write_text(
        f"#!/bin/sh\n[ {sha_rc} -eq 0 ] || exit {sha_rc}\nprintf '%s  %s\\n' {checksum} \"$1\"\n"
    )
    (bin_dir / "timeout").write_text("#!/bin/sh\nshift 5\nexec \"$@\"\n")
    for path in bin_dir.iterdir():
        path.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "BACKUP_SOURCE": "container",
        "ATLAS_BACKUP_MC_INSTALL_DIR": str(install_dir),
    }


@pytest.mark.parametrize(
    ("arch", "wget_rc", "sha_rc", "message"),
    [
        ("mips64", 0, 0, "unsupported architecture"),
        ("x86_64", 7, 0, "download failed"),
        ("x86_64", 0, 1, "checksum verification failed"),
    ],
)
def test_entrypoint_fails_closed_installing_pinned_mc(
    tmp_path: Path, arch: str, wget_rc: int, sha_rc: int, message: str,
) -> None:
    result = subprocess.run(
        ["sh", str(REPO / "services/backup/init/scripts/entrypoint.sh"), "/bin/true"],
        env=_entrypoint_installer_fixture(
            tmp_path, arch=arch, wget_rc=wget_rc, sha_rc=sha_rc
        ),
        text=True, capture_output=True, check=False, timeout=5,
    )
    assert result.returncode != 0
    assert message in result.stderr.lower()
    assert not list((tmp_path / "install").iterdir())


def test_entrypoint_installs_and_executes_exact_mc_release(tmp_path: Path) -> None:
    command = tmp_path / "command.sh"
    command.write_text("exit 0\n", encoding="utf-8")
    result = subprocess.run(
        ["sh", str(REPO / "services/backup/init/scripts/entrypoint.sh"), str(command)],
        env=_entrypoint_installer_fixture(tmp_path),
        text=True, capture_output=True, check=False, timeout=5,
    )
    assert result.returncode == 0, result.stderr
    installed = tmp_path / "install" / "mc"
    assert installed.exists()
    assert "RELEASE.2025-08-13T08-35-41Z" in subprocess.check_output(
        [str(installed), "--version"], text=True
    )


def _blocking_installer_fixture(tmp_path: Path, probe: str) -> dict[str, str]:
    bin_dir = tmp_path / "blocking-bin"
    install_dir = tmp_path / "blocking-install"
    bin_dir.mkdir()
    install_dir.mkdir()
    (bin_dir / "openssl").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "uname").write_text("#!/bin/sh\nprintf '%s\\n' aarch64\n")
    (bin_dir / "timeout").write_text(
        """#!/usr/bin/env python3
import os
import signal
import subprocess
import sys

limit = float(sys.argv[5])
child = subprocess.Popen(sys.argv[6:], start_new_session=True)
try:
    raise SystemExit(child.wait(timeout=limit))
except subprocess.TimeoutExpired:
    os.killpg(child.pid, signal.SIGTERM)
    try:
        child.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=0.5)
    raise SystemExit(124)
"""
    )
    owned_tmp_dir = Path(
        f"/tmp/atlas-mc-install.{uuid.uuid4().hex[:6]}"
    )
    assert not owned_tmp_dir.exists()
    (bin_dir / "mktemp").write_text(
        "#!/bin/sh\n"
        "[ \"$1\" = -d ] && "
        "[ \"$2\" = /tmp/atlas-mc-install.XXXXXX ] || exit 64\n"
        "mkdir \"$ATLAS_TEST_MC_TMP_DIR\" || exit 1\n"
        "printf '%s\\n' \"$ATLAS_TEST_MC_TMP_DIR\"\n"
    )
    if probe.startswith("existing"):
        body = "#!/bin/sh\n"
        if probe == "existing_version":
            body += "trap 'exit 143' TERM; while :; do :; done\n"
        else:
            body += "printf '%s\\n' 'mc version RELEASE.2025-08-13T08-35-41Z'\n"
        (bin_dir / "mc").write_text(body)
    sha_body = "#!/bin/sh\n"
    if probe in {"existing_hash", "downloaded_hash"}:
        sha_body += "trap 'exit 143' TERM; while :; do :; done\n"
    elif probe == "installed_hash":
        sha_body += "case \"$1\" in */blocking-install/mc) trap 'exit 143' TERM; while :; do :; done;; esac\n"
    sha_body += "printf '%s  %s\\n' 14c8c9616cfce4636add161304353244e8de383b2e2752c0e9dad01d4c27c12c \"$1\"\n"
    (bin_dir / "sha256sum").write_text(sha_body)
    wget_body = "#!/bin/sh\nwhile [ \"$1\" != -O ]; do shift; done; shift\n"
    if probe.startswith("existing"):
        wget_body += "exit 7\n"
    elif probe == "downloaded_version":
        wget_body += "printf '%s\\n' '#!/bin/sh' \"trap 'exit 143' TERM\" 'while :; do :; done' >\"$1\"\n"
    elif probe == "installed_version":
        wget_body += "printf '%s\\n' '#!/bin/sh' 'case \"$0\" in */blocking-install/mc) trap '\"'\"'exit 143'\"'\"' TERM; while :; do :; done;; esac' \"printf '%s\\\\n' 'mc version RELEASE.2025-08-13T08-35-41Z'\" >\"$1\"\n"
    else:
        wget_body += "printf '%s\\n' '#!/bin/sh' \"printf '%s\\\\n' 'mc version RELEASE.2025-08-13T08-35-41Z'\" >\"$1\"\n"
    (bin_dir / "wget").write_text(wget_body)
    for path in bin_dir.iterdir():
        path.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "BACKUP_SOURCE": "container",
        "BACKUP_COMMAND_TIMEOUT_SECONDS": "1",
        "ATLAS_BACKUP_MC_INSTALL_DIR": str(install_dir),
        "ATLAS_TEST_MC_TMP_DIR": str(owned_tmp_dir),
    }


@pytest.mark.parametrize(
    "probe",
    [
        "existing_hash", "existing_version", "downloaded_hash",
        "downloaded_version", "installed_hash", "installed_version",
    ],
)
def test_entrypoint_bounds_every_mc_hash_and_version_probe(
    tmp_path: Path, probe: str,
) -> None:
    command = tmp_path / "never.sh"
    command.write_text("exit 99\n", encoding="utf-8")
    env = _blocking_installer_fixture(tmp_path, probe)
    owned_tmp_dir = Path(env["ATLAS_TEST_MC_TMP_DIR"])
    started = time.monotonic()
    result = subprocess.run(
        ["sh", str(REPO / "services/backup/init/scripts/entrypoint.sh"), str(command)],
        env=env, text=True, capture_output=True, check=False, timeout=10,
    )
    elapsed = time.monotonic() - started
    assert result.returncode != 0
    # The script-level one-second deadline remains deliberately tiny.  This
    # wider wall-clock allowance covers repeated Python fixture-wrapper
    # startup on macOS while still proving that a blocked probe exits finitely.
    assert elapsed < 8
    assert not list((tmp_path / "blocking-install").iterdir())
    assert not owned_tmp_dir.exists()


def _assert_ci_provisions_exact_integration_images(pull_script: str) -> None:
    dockerfile = (REPO / "services/backup/init/Dockerfile").read_text(encoding="utf-8")
    pinned_base = next(
        line.removeprefix("ARG BASE_IMAGE=")
        for line in dockerfile.splitlines()
        if line.startswith("ARG BASE_IMAGE=")
    )
    assert "@sha256:" in pinned_base
    assert "timeout 10m docker build --pull" in pull_script
    assert "--build-arg BASE_IMAGE=" not in pull_script
    assert "--tag atlas-backup:local" in pull_script
    assert "services/backup/init" in pull_script
    required_images = (
        "postgres:17.10-alpine",
        MINIO_IMAGE,
        MINIO_CLIENT_IMAGE,
        "supabase/postgres:17.6.1.139",
        "postgres:15.18-alpine",
        "supabase/realtime:v2.112.0",
        "supabase/storage-api:v1.61.5",
        "supabase/supavisor:2.9.5",
    )
    for image in required_images:
        assert f"timeout 5m docker pull {image}" in pull_script


def test_services_lint_opts_into_exact_backup_production_image_integration() -> None:
    workflow = yaml.safe_load(
        (REPO / ".github/workflows/services-lint.yml").read_text(encoding="utf-8")
    )
    assert workflow["permissions"] == {"contents": "read"}
    steps = workflow["jobs"]["lint"]["steps"]
    by_name = {step.get("name"): (index, step) for index, step in enumerate(steps)}
    pull_index, pull_step = by_name["Pull exact backup integration images"]
    test_index, test_step = by_name[
        "Run unit tests (loader, validator, assembler, hooks, CLI)"
    ]
    assert pull_index < test_index
    assert test_step["env"]["ATLAS_BACKUP_PRODUCTION_IMAGE_INTEGRATION"] == "1"
    _assert_ci_provisions_exact_integration_images(pull_step["run"])


def test_opted_in_backup_integration_targets_rendered_local_image() -> None:
    assert BACKUP_PRODUCTION_IMAGE == "atlas-backup:local"


def _docker_command(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


S3_IMAGE_PROBE_FAILURES = (
    pytest.param(
        subprocess.TimeoutExpired(("docker", "image", "inspect"), 10),
        id="timeout",
    ),
    pytest.param(PermissionError("Docker image metadata denied"), id="launch-error"),
)


@pytest.mark.parametrize(
    "unavailable",
    ["cli", "daemon", MINIO_IMAGE, MINIO_CLIENT_IMAGE, BACKUP_PRODUCTION_IMAGE],
)
def test_explicit_production_image_opt_in_fails_closed_on_missing_prerequisite(
    monkeypatch: pytest.MonkeyPatch, unavailable: str,
) -> None:
    monkeypatch.setenv("ATLAS_BACKUP_PRODUCTION_IMAGE_INTEGRATION", "1")
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _name: None if unavailable == "cli" else "/usr/bin/docker")

    def fake_docker(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        del timeout
        if args[0] == "info":
            return subprocess.CompletedProcess(
                ["docker", *args], 1 if unavailable == "daemon" else 0, "",
                "Cannot connect to the Docker daemon" if unavailable == "daemon" else "",
            )
        assert args[:2] == ("image", "inspect")
        image = args[2]
        missing = image == unavailable
        return subprocess.CompletedProcess(
            ["docker", *args], 1 if missing else 0, "",
            f"No such image: {image}" if missing else "",
        )

    monkeypatch.setitem(globals(), "_docker_command", fake_docker)
    try:
        with pytest.raises(pytest.fail.Exception):
            _require_opted_in_backup_production_image()
    except pytest.skip.Exception as exc:
        pytest.fail(f"explicit production-image opt-in skipped: {exc}")


@pytest.mark.parametrize("probe_failure", S3_IMAGE_PROBE_FAILURES)
def test_explicit_production_image_normalizes_probe_launch_failures(
    monkeypatch: pytest.MonkeyPatch, probe_failure: BaseException
) -> None:
    monkeypatch.setenv("ATLAS_BACKUP_PRODUCTION_IMAGE_INTEGRATION", "1")
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/docker")

    def failed_production_image_probe(*args, **_kwargs):
        if args[:2] == ("image", "inspect") and args[2] == BACKUP_PRODUCTION_IMAGE:
            raise probe_failure
        return subprocess.CompletedProcess(("docker", *args), 0, "", "")

    monkeypatch.setitem(globals(), "_docker_command", failed_production_image_probe)
    with pytest.raises(pytest.fail.Exception, match="Docker image probe failed for"):
        _require_opted_in_backup_production_image()


def test_explicit_production_image_opt_in_executes_baked_openssl(
    monkeypatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setenv("ATLAS_BACKUP_PRODUCTION_IMAGE_INTEGRATION", "1")
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/docker")

    def fake_docker(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        del timeout
        calls.append(args)
        return subprocess.CompletedProcess(
            ["docker", *args], 0, "OpenSSL 3.5.8\n" if args[0] == "run" else "", ""
        )

    monkeypatch.setitem(globals(), "_docker_command", fake_docker)

    _require_opted_in_backup_production_image()

    assert (
        "run", "--pull=never", "--rm", "--entrypoint", "openssl",
        BACKUP_PRODUCTION_IMAGE, "version",
    ) in calls


def _require_opted_in_backup_production_image() -> None:
    if os.environ.get("ATLAS_BACKUP_PRODUCTION_IMAGE_INTEGRATION") != "1":
        pytest.skip("exact backup production-image integration is CI/explicit opt-in")
    _require_disposable_s3_images(required=True)
    try:
        image = _docker_command(
            "image", "inspect", BACKUP_PRODUCTION_IMAGE, timeout=10
        )
    except (subprocess.SubprocessError, OSError) as exc:
        pytest.fail(
            f"Docker image probe failed for {BACKUP_PRODUCTION_IMAGE}: "
            f"{type(exc).__name__}: {exc}"
        )
    if image.returncode != 0:
        pytest.fail(
            f"opted-in exact backup image is absent: {BACKUP_PRODUCTION_IMAGE}: "
            f"{image.stderr}"
        )
    openssl = _docker_command(
        "run", "--pull=never", "--rm", "--entrypoint", "openssl",
        BACKUP_PRODUCTION_IMAGE, "version", timeout=20,
    )
    if openssl.returncode != 0:
        pytest.fail(
            "opted-in backup image does not provide baked OpenSSL: "
            f"{openssl.stderr}"
        )


def test_exact_backup_image_bounds_term_ignoring_stream_producer() -> None:
    _require_opted_in_backup_production_image()
    scripts = REPO / "services/backup/init/scripts"
    started = time.monotonic()
    result = _docker_command(
        "run", "--pull=never", "--rm",
        "--mount", f"type=bind,src={scripts},dst=/probe-scripts,readonly",
        "--entrypoint", "sh", BACKUP_PRODUCTION_IMAGE, "-c",
        ". /probe-scripts/s3-client.sh; status=0; "
        "backup_s3_stream_command 1 sh -c "
        "'trap \"\" TERM; while :; do sleep 60; done' & supervisor=$!; "
        "wait \"$supervisor\" || status=$?; "
        "printf 'status=%s\\n' \"$status\"; [ \"$status\" -eq 137 ]",
        timeout=20,
    )
    elapsed = time.monotonic() - started
    assert result.returncode == 0, result.stderr
    assert "status=137" in result.stdout
    assert 9 <= elapsed < 16


def test_exact_backup_image_reaps_stopped_watchdog_group(tmp_path: Path) -> None:
    _require_opted_in_backup_production_image()
    fake_bin = tmp_path / "bin"
    result_dir = tmp_path / "result"
    fake_bin.mkdir()
    result_dir.mkdir()
    sleep = fake_bin / "sleep"
    sleep.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = 30 ]; then\n"
        "  printf '%s' \"$$\" >/result/sleeper.pid\n"
        "  printf '%s' \"$PPID\" >/result/watchdog.pid\n"
        "  : >/result/ready\n"
        "  trap '' TERM\n"
        "  kill -STOP \"$PPID\"\n"
        "  exec /bin/sleep 60\n"
        "fi\n"
        "exec /bin/sleep \"$@\"\n",
        encoding="utf-8",
    )
    sleep.chmod(0o755)
    mc = fake_bin / "mc"
    mc.write_text(
        "#!/bin/sh\n"
        "while [ ! -e /result/ready ]; do /bin/sleep 0.01; done\n"
        "exit 0\n",
        encoding="utf-8",
    )
    mc.chmod(0o755)
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "#!/bin/sh\nset -eu\n. /probe-scripts/s3-client.sh\n"
        "status=0\nbackup_s3_stream_command 30 mc cat object || status=$?\n"
        "alive=0\n"
        "for file in /result/watchdog.pid /result/sleeper.pid; do\n"
        "  pid=$(cat \"$file\"); kill -0 \"$pid\" 2>/dev/null && alive=1 || true\n"
        "done\n"
        "printf 'status=%s alive=%s\\n' \"$status\" \"$alive\"\n"
        "[ \"$status\" -eq 0 ] && [ \"$alive\" -eq 0 ]\n",
        encoding="utf-8",
    )
    probe.chmod(0o755)
    result = _docker_command(
        "run", "--pull=never", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
        "--env", "PATH=/probe-bin:/usr/bin:/bin",
        "--mount", f"type=bind,src={fake_bin},dst=/probe-bin,readonly",
        "--mount", f"type=bind,src={result_dir},dst=/result",
        "--mount", f"type=bind,src={REPO / 'services/backup/init/scripts'},dst=/probe-scripts,readonly",
        "--mount", f"type=bind,src={probe},dst=/probe.sh,readonly",
        "--entrypoint", "sh", BACKUP_PRODUCTION_IMAGE, "/probe.sh", timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "status=0 alive=0" in result.stdout


def _wait_for_s3_server(
    network: str, probe: str, seed_mount: str, owner_token: str,
) -> None:
    deadline = time.monotonic() + 30
    ready: subprocess.CompletedProcess[str] | None = None
    while time.monotonic() < deadline:
        ready = _docker_command(
            "run", "--pull=never", "--rm", "--name", probe,
            "--label", f"{_EXTERNAL_S3_FIXTURE_OWNER_LABEL}={owner_token}",
            "--network", network, "--mount", seed_mount,
            "--tmpfs", "/tmp:rw,size=16m", "--entrypoint", "sh",
            MINIO_CLIENT_IMAGE, "-c",
            "mc --config-dir /tmp/mc alias import s3 /credentials.json >/dev/null && mc --config-dir /tmp/mc ls s3",
            timeout=8,
        )
        if ready.returncode == 0:
            break
        _remove_exact_docker_fixture(
            (probe,), None, owner_token, uncertain=ready.returncode == 125
        )
        time.sleep(0.25)
    assert ready is not None and ready.returncode == 0, (
        ready.stderr if ready else "not ready"
    )


def _extract_pinned_mc_artifact(
    tmp_path: Path, suffix: str, owner_token: str,
) -> Path:
    architecture = _docker_command(
        "image", "inspect", "--format", "{{.Architecture}}", MINIO_CLIENT_IMAGE,
        timeout=10,
    )
    assert architecture.returncode == 0, architecture.stderr
    mc_arch = architecture.stdout.strip()
    assert mc_arch in MC_SHA256_BY_ARCH
    artifact_dir = tmp_path / "mc-artifacts"
    artifact_dir.mkdir()
    artifact = artifact_dir / f"mc.linux-{mc_arch}.{MC_RELEASE}"
    extractor = f"atlas-backup-production-mc-extractor-{suffix}"
    try:
        created = _docker_command(
            "create", "--name", extractor,
            "--label", f"{_EXTERNAL_S3_FIXTURE_OWNER_LABEL}={owner_token}",
            "--entrypoint", "sh", MINIO_CLIENT_IMAGE, "-c", "true", timeout=10,
        )
        assert created.returncode == 0, created.stderr
        copied = _docker_command(
            "cp", f"{extractor}:/usr/bin/mc", str(artifact), timeout=20,
        )
        assert copied.returncode == 0, copied.stderr
    finally:
        _remove_exact_docker_fixture((extractor,), None, owner_token)
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == MC_SHA256_BY_ARCH[mc_arch]
    return artifact


def _write_artifact_responder(tmp_path: Path, artifact: Path) -> Path:
    responder = tmp_path / "serve-mc-artifact.sh"
    responder.write_text(
        "#!/bin/sh\n"
        "request_cr=$(printf '\\r')\n"
        "while IFS= read -r request_header; do\n"
        "  [ \"$request_header\" = \"$request_cr\" ] && break\n"
        "done\n"
        f"printf 'HTTP/1.1 200 OK\\r\\nContent-Length: {artifact.stat().st_size}\\r\\n"
        "Connection: close\\r\\n\\r\\n'\n"
        f"exec cat /artifacts/{artifact.name}\n",
        encoding="utf-8",
    )
    responder.chmod(0o755)
    return responder


def test_artifact_responder_waits_for_complete_http_headers(tmp_path: Path) -> None:
    artifact = tmp_path / f"mc.linux-arm64.{MC_RELEASE}"
    artifact.write_bytes(b"pinned-mc-fixture")
    responder = _write_artifact_responder(tmp_path, artifact)
    process = subprocess.Popen(
        ["sh", str(responder)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    try:
        process.stdin.write(b"GET /artifact HTTP/1.1\r\nHost: mc-artifacts\r\n")
        process.stdin.flush()
        with pytest.raises(subprocess.TimeoutExpired):
            process.wait(timeout=0.2)
    finally:
        process.terminate()
        process.wait(timeout=2)


def _wait_for_artifact_server(
    network: str, probe: str, owner_token: str,
) -> None:
    deadline = time.monotonic() + 10
    ready: subprocess.CompletedProcess[str] | None = None
    while time.monotonic() < deadline:
        ready = _docker_command(
            "run", "--pull=never", "--rm", "--name", probe,
            "--label", f"{_EXTERNAL_S3_FIXTURE_OWNER_LABEL}={owner_token}",
            "--network", network, "--entrypoint", "nc", BACKUP_PRODUCTION_IMAGE,
            "-z", "-w", "1", "mc-artifacts", "8080", timeout=5,
        )
        if ready.returncode == 0:
            break
        _remove_exact_docker_fixture(
            (probe,), None, owner_token, uncertain=ready.returncode == 125
        )
        time.sleep(0.1)
    assert ready is not None and ready.returncode == 0, (
        ready.stderr if ready else "artifact server not ready"
    )


def _capture_fixture_cleanup(
    failures: list[tuple[str, BaseException]], operation: str, action,
) -> None:
    try:
        action()
    except BaseException as exc:
        failures.append((operation, exc))


def _exact_container_owner(name: str) -> str | None:
    listed = _docker_command(
        "ps", "-a", "--filter", f"name=^/{name}$",
        "--format", "{{.Names}}", timeout=10,
    )
    assert listed.returncode == 0
    if name not in listed.stdout.splitlines():
        return None
    inspected = _docker_command(
        "container", "inspect", "--format",
        f'{{{{index .Config.Labels "{_EXTERNAL_S3_FIXTURE_OWNER_LABEL}"}}}}',
        name, timeout=10,
    )
    assert inspected.returncode == 0
    return inspected.stdout.strip()


def _exact_network_owner(name: str) -> str | None:
    listed = _docker_command(
        "network", "ls", "--filter", f"name=^{name}$",
        "--format", "{{.Name}}", timeout=10,
    )
    assert listed.returncode == 0
    if name not in listed.stdout.splitlines():
        return None
    inspected = _docker_command(
        "network", "inspect", "--format",
        f'{{{{index .Labels "{_EXTERNAL_S3_FIXTURE_OWNER_LABEL}"}}}}',
        name, timeout=10,
    )
    assert inspected.returncode == 0
    return inspected.stdout.strip()


def _remove_owned_exact_container(name: str, owner_token: str) -> None:
    if _exact_container_owner(name) != owner_token:
        return
    removed = _docker_command("rm", "-f", name, timeout=10)
    assert removed.returncode == 0, removed.stderr


def _remove_owned_exact_network(name: str, owner_token: str) -> None:
    if _exact_network_owner(name) != owner_token:
        return
    removed = _docker_command("network", "rm", name, timeout=10)
    assert removed.returncode == 0, removed.stderr


def _assert_owned_container_absent(name: str, owner_token: str) -> None:
    assert _exact_container_owner(name) != owner_token


def _assert_owned_network_absent(name: str, owner_token: str) -> None:
    assert _exact_network_owner(name) != owner_token


def _add_exception_note(exc: BaseException, note: str) -> None:
    if hasattr(exc, "add_note"):
        exc.add_note(note)
        return
    notes = getattr(exc, "__notes__", None)
    if notes is None:
        notes = []
        exc.__notes__ = notes
    notes.append(note)


def _report_fixture_cleanup_failures(
    primary: BaseException | None, failures: list[tuple[str, BaseException]],
) -> None:
    detail = "; ".join(
        f"{operation}: {type(exc).__name__}: {exc}"
        for operation, exc in failures
    )
    note = f"Exact Docker fixture cleanup could not be proven: {detail}"
    if primary is not None:
        _add_exception_note(primary, note)
        return
    cleanup_error = failures[0][1]
    _add_exception_note(cleanup_error, note)
    raise cleanup_error


def _exact_fixture_cleanup_pass(
    resources: tuple[tuple[str, str], ...], owner_token: str,
) -> list[tuple[str, BaseException]]:
    removers = {
        "container": _remove_owned_exact_container,
        "network": _remove_owned_exact_network,
    }
    verifiers = {
        "container": _assert_owned_container_absent,
        "network": _assert_owned_network_absent,
    }
    failures: list[tuple[str, BaseException]] = []
    for kind, name in resources:
        _capture_fixture_cleanup(
            failures, f"{kind} removal {name}",
            lambda resource_kind=kind, resource_name=name: removers[resource_kind](
                resource_name, owner_token
            ),
        )
    for kind, name in resources:
        _capture_fixture_cleanup(
            failures, f"{kind} absence {name}",
            lambda resource_kind=kind, resource_name=name: verifiers[resource_kind](
                resource_name, owner_token
            ),
        )
    return failures


def _remove_exact_docker_fixture(
    names: tuple[str, ...], network: str | None, owner_token: str,
    *, uncertain: bool | None = None,
) -> None:
    primary = sys.exc_info()[1]
    deferred_error = primary
    if uncertain is None:
        uncertain = primary is not None
    settle_until, deferred_error = establish_cleanup_deadline(
        _EXTERNAL_S3_RECONCILE_SECONDS if uncertain else None, deferred_error
    )
    resources = tuple(("container", name) for name in names)
    if network is not None:
        resources += (("network", network),)
    while True:
        failures = _exact_fixture_cleanup_pass(resources, owner_token)
        deferred_error = defer_cleanup_failures(deferred_error, failures)
        settle_until, deferred_error = begin_reconciliation_after_interruption(
            settle_until,
            _EXTERNAL_S3_RECONCILE_SECONDS,
            deferred_error,
            failures,
        )
        expired, deferred_error = cleanup_deadline_expired(
            settle_until, deferred_error
        )
        if expired:
            if failures:
                _report_fixture_cleanup_failures(deferred_error, failures)
            raise_deferred_cleanup_error(primary, deferred_error)
            return
        deferred_error = sleep_for_cleanup(0.1, deferred_error)


def test_exact_docker_fixture_cleanup_reconciles_an_ambiguous_network_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    network_lists = 0
    network_exists = False

    def docker(*args, **_kwargs):
        nonlocal network_exists, network_lists
        calls.append(args)
        stdout = ""
        if args[:2] == ("network", "ls"):
            network_lists += 1
            if network_lists == 3:
                network_exists = True
            stdout = "ambiguous-network\n" if network_exists else ""
        elif args[:2] == ("network", "inspect"):
            stdout = "our-token\n"
        elif args == ("network", "rm", "ambiguous-network"):
            network_exists = False
        return subprocess.CompletedProcess(["docker", *args], 0, stdout, "")

    monkeypatch.setattr(
        sys.modules[__name__],
        "_docker_command",
        docker,
    )
    ticks = iter((0.0, 2.0, 3.0, 91.2))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    _remove_exact_docker_fixture(
        ("client", "server"), "ambiguous-network", "our-token", uncertain=True
    )
    assert calls.count(("network", "rm", "ambiguous-network")) == 1
    assert calls[-1][:2] == ("network", "ls")


def test_exact_docker_fixture_cleanup_reconciles_a_late_container_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    container_lists = 0
    container_exists = False

    def docker(*args, **_kwargs):
        nonlocal container_exists, container_lists
        calls.append(args)
        stdout = ""
        if args[:2] == ("ps", "-a"):
            container_lists += 1
            if container_lists == 3:
                container_exists = True
            stdout = "late-client\n" if container_exists else ""
        elif args[:2] == ("container", "inspect"):
            stdout = "our-token\n"
        elif args == ("rm", "-f", "late-client"):
            container_exists = False
        return subprocess.CompletedProcess(["docker", *args], 0, stdout, "")

    monkeypatch.setattr(sys.modules[__name__], "_docker_command", docker)
    ticks = iter((0.0, 2.0, 3.0, 91.2))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    _remove_exact_docker_fixture(
        ("late-client",), None, "our-token", uncertain=True
    )
    assert calls.count(("rm", "-f", "late-client")) == 1


def test_exact_docker_fixture_cleanup_preserves_foreign_name_collisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def docker(*args, **_kwargs):
        calls.append(args)
        if args[:2] == ("ps", "-a"):
            return subprocess.CompletedProcess(
                ["docker", *args], 0, "foreign-client\n", ""
            )
        if args[:2] == ("container", "inspect"):
            return subprocess.CompletedProcess(
                ["docker", *args], 0, "foreign-token\n", ""
            )
        if args[:2] == ("network", "ls"):
            return subprocess.CompletedProcess(
                ["docker", *args], 0, "foreign-network\n", ""
            )
        if args[:2] == ("network", "inspect"):
            return subprocess.CompletedProcess(
                ["docker", *args], 0, "foreign-token\n", ""
            )
        return subprocess.CompletedProcess(["docker", *args], 0, "", "")

    monkeypatch.setattr(sys.modules[__name__], "_docker_command", docker)
    ticks = iter((0.0, 0.4, 0.8, 91.2))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    _remove_exact_docker_fixture(
        ("foreign-client",), "foreign-network", "our-token", uncertain=True
    )

    assert ("rm", "-f", "foreign-client") not in calls
    assert ("network", "rm", "foreign-network") not in calls


def test_exact_docker_fixture_cleanup_preserves_primary_on_cleanup_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = iter((0.0, 91.0))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        sys.modules[__name__],
        "_docker_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(("docker", "inspect"), 10)
        ),
    )

    with pytest.raises(RuntimeError, match="primary fixture failure") as raised:
        try:
            raise RuntimeError("primary fixture failure")
        finally:
            _remove_exact_docker_fixture(
                ("client",), "network", "our-token", uncertain=True
            )

    assert "cleanup could not be proven" in "\n".join(raised.value.__notes__)


def _assert_raw_values_redacted(output: str, raw_values: tuple[str, ...]) -> None:
    for raw in raw_values:
        assert raw not in output


def _assert_external_production_render(rendered: dict) -> None:
    assert rendered["backup"]["image"] == BACKUP_PRODUCTION_IMAGE
    assert rendered["backup"]["entrypoint"] == ["sh", "/scripts/entrypoint.sh"]
    assert rendered["minio"]["deploy"]["replicas"] == 0
    assert rendered["minio-init"]["deploy"]["replicas"] == 0


def _require_disposable_s3_images(*, required: bool = False) -> None:
    in_ci = os.environ.get("CI", "").lower() in {"1", "true", "yes"}
    fail_closed = required or in_ci
    if shutil.which("docker") is None:
        if fail_closed:
            pytest.fail("docker CLI is required for this external S3 contract")
        pytest.skip("docker CLI unavailable")
    try:
        daemon = _docker_command("info", timeout=10)
    except (subprocess.SubprocessError, OSError) as exc:
        if fail_closed:
            pytest.fail(
                f"Docker daemon probe failed: {type(exc).__name__}: {exc}"
            )
        pytest.skip("docker daemon unavailable")
    if daemon.returncode != 0:
        if fail_closed:
            pytest.fail("docker daemon unavailable for required external S3 contract")
        pytest.skip("docker daemon unavailable")
    for image in (MINIO_IMAGE, MINIO_CLIENT_IMAGE):
        try:
            probe = _docker_command("image", "inspect", image, timeout=10)
        except (subprocess.SubprocessError, OSError) as exc:
            pytest.fail(
                f"Docker image probe failed for {image}: {type(exc).__name__}: {exc}"
            )
        if probe.returncode == 0:
            continue
        if "no such image" in probe.stderr.lower():
            if fail_closed:
                pytest.fail(f"required Docker image absent: {image}")
            pytest.skip(f"required local image absent: {image}")
        pytest.fail(f"Docker image probe failed for {image}: {probe.stderr}")


S3_DAEMON_PROBE_FAILURES = (
    pytest.param(
        subprocess.CompletedProcess(
            ("docker", "info"), 1, "", "unexpected daemon transport failure"
        ),
        id="nonzero",
    ),
    pytest.param(
        subprocess.TimeoutExpired(("docker", "info"), 10),
        id="timeout",
    ),
    pytest.param(PermissionError("docker socket denied"), id="launch-error"),
)
@pytest.mark.parametrize("probe_failure", S3_DAEMON_PROBE_FAILURES)
@pytest.mark.parametrize(
    "policy",
    [
        pytest.param(
            (False, False, pytest.skip.Exception), id="optional-local"
        ),
        pytest.param((True, False, pytest.fail.Exception), id="required-local"),
        pytest.param((False, True, pytest.fail.Exception), id="ci"),
    ],
)
def test_disposable_s3_daemon_probe_policy(
    monkeypatch: pytest.MonkeyPatch,
    probe_failure: object,
    policy: tuple[bool, bool, type[BaseException]],
) -> None:
    required, in_ci, expected_exception = policy
    if in_ci:
        monkeypatch.setenv("CI", "true")
    else:
        monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/docker")

    def failed_probe(*_args, **_kwargs):
        if isinstance(probe_failure, BaseException):
            raise probe_failure
        return probe_failure

    monkeypatch.setitem(globals(), "_docker_command", failed_probe)
    with pytest.raises(expected_exception, match="[Dd]ocker daemon"):
        _require_disposable_s3_images(required=required)


@pytest.mark.parametrize("probe_failure", S3_IMAGE_PROBE_FAILURES)
@pytest.mark.parametrize("image", (MINIO_IMAGE, MINIO_CLIENT_IMAGE))
def test_disposable_s3_normalizes_image_probe_launch_failures(
    monkeypatch: pytest.MonkeyPatch, image: str, probe_failure: BaseException
) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/docker")

    def failed_image_probe(*args, **_kwargs):
        if args[:2] == ("image", "inspect") and args[2] == image:
            raise probe_failure
        return subprocess.CompletedProcess(("docker", *args), 0, "", "")

    monkeypatch.setitem(globals(), "_docker_command", failed_image_probe)
    with pytest.raises(pytest.fail.Exception, match=f"Docker image probe failed for {image}"):
        _require_disposable_s3_images()


def test_external_s3_mode_uses_separate_credentials_with_pinned_minio_client(
    tmp_path: Path,
) -> None:
    _require_disposable_s3_images()
    owner_token = uuid.uuid4().hex
    suffix = owner_token[:12]
    network = f"atlas-backup-s3-test-{suffix}"
    server = f"atlas-backup-s3-{suffix}"
    client = f"atlas-backup-s3-client-{suffix}"
    probe = f"atlas-backup-s3-token-{suffix}"
    access = "externalAccess123"
    secret = "externalSecret456"
    token = "temporaryToken789"
    server_env = tmp_path / "server.env"
    server_env.write_text(
        f"MINIO_ROOT_USER={access}\nMINIO_ROOT_PASSWORD={secret}\n",
        encoding="utf-8",
    )
    server_env.chmod(0o600)
    seed_credentials = tmp_path / "seed-credentials.json"
    seed_credentials.write_text(
        json.dumps(
            {
                "url": "http://external-s3:9000",
                "accessKey": access,
                "secretKey": secret,
                "api": "S3v4",
                "path": "auto",
            }
        ),
        encoding="utf-8",
    )
    seed_credentials.chmod(0o600)
    runner_env = tmp_path / "runner.env"
    runner_env.write_text(
        "PATH=/test-bin:/usr/bin:/bin\n"
        "BACKUP_SOURCE=container\n"
        "BACKUP_S3_MODE=external\n"
        "BACKUP_S3_ENDPOINT=http://external-s3:9000\n"
        f"BACKUP_S3_ACCESS_KEY={access}\n"
        f"BACKUP_S3_SECRET_KEY={secret}\n"
        "BACKUP_S3_REGION=us-east-1\n"
        "BACKUP_S3_TLS_VERIFY=true\n"
        "BACKUP_COMMAND_TIMEOUT_SECONDS=5\n"
        "BACKUP_RESTORE_GLOBAL_TIMEOUT_SECONDS=20\n"
        "BACKUP_MANIFEST_HMAC_KEY=" + "a" * 64 + "\n"
        "BACKUP_DEPLOYMENT_ID=atlas-test-deployment\n"
        "BACKUP_TIMESTAMP=20260829_120000\n"
        "BACKUP_RESTORE_MAINTENANCE_MODE=confirmed\n"
        "SUPABASE_DB_USER=postgres\nSUPABASE_DB_PASSWORD=db-secret\n"
        "SUPABASE_DB_NAME=postgres\n",
        encoding="utf-8",
    )
    runner_env.chmod(0o600)
    test_bin = tmp_path / "test-bin"
    result_dir = tmp_path / "result"
    test_bin.mkdir()
    result_dir.mkdir()
    fake_tools = {
        "mc": """#!/bin/sh
case "$*" in
  "alias import s3 "*)
    [ -z "${BACKUP_S3_ACCESS_KEY+x}${BACKUP_S3_SECRET_KEY+x}${BACKUP_S3_SESSION_TOKEN+x}" ] || printf leak >/result/secret-env-leak
    [ -z "${MINIO_ROOT_USER+x}${MINIO_ROOT_PASSWORD+x}${AWS_ACCESS_KEY_ID+x}${AWS_SECRET_ACCESS_KEY+x}${AWS_SESSION_TOKEN+x}" ] || printf leak >/result/secret-env-leak
    ;;
esac
exec /usr/bin/mc "$@"
""",
        "openssl": "#!/bin/sh\nexit 0\n",
        # This client-shell fixture is not the production backup image.  Its
        # streams are single-process; production provides real /usr/bin/setsid.
        "setsid": "#!/bin/sh\nexec \"$@\"\n",
        "uname": "#!/bin/sh\nprintf '%s\\n' aarch64\n",
        "sha256sum": """#!/bin/sh
case "$1" in /test-bin/mc) printf '%s  %s\n' 14c8c9616cfce4636add161304353244e8de383b2e2752c0e9dad01d4c27c12c "$1";; *) exec /usr/bin/sha256sum "$@";; esac
""",
        "psql": "#!/bin/sh\nprintf 'busy\\n'\nexit 75\n",
        "grep": """#!/bin/sh
[ "$1" = -Fq ] || exit 2
wanted=$2
input=$(cat)
case "$input" in *"$wanted"*) exit 0;; *) exit 1;; esac
""",
        "sed": """#!/bin/sh
case "$1" in
  's/^0*//') IFS= read -r value || true; while [ "${value#0}" != "$value" ]; do value=${value#0}; done; printf '%s\n' "$value" ;;
  -n) IFS= read -r value <"$3" || true; printf '%s\n' "$value" ;;
  *) exit 2 ;;
esac
""",
    }
    for name, body in fake_tools.items():
        path = test_bin / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    try:
        created = _docker_command(
            "network", "create", "--label",
            f"{_EXTERNAL_S3_FIXTURE_OWNER_LABEL}={owner_token}", network,
        )
        assert created.returncode == 0, created.stderr
        started = _docker_command(
            "run", "--pull=never", "--detach", "--rm", "--name", server,
            "--label", f"{_EXTERNAL_S3_FIXTURE_OWNER_LABEL}={owner_token}",
            "--network", network, "--network-alias", "external-s3",
            "--env-file", str(server_env),
            "--tmpfs", "/data:rw,size=128m", MINIO_IMAGE,
            "server", "/data", "--address", ":9000",
            timeout=45,
        )
        assert started.returncode == 0, started.stderr

        seed_mount = f"type=bind,src={seed_credentials},dst=/credentials.json,readonly"
        deadline = time.monotonic() + 30
        ready: subprocess.CompletedProcess[str] | None = None
        while time.monotonic() < deadline:
            ready = _docker_command(
                "run", "--pull=never", "--rm", "--name", client,
                "--label", f"{_EXTERNAL_S3_FIXTURE_OWNER_LABEL}={owner_token}",
                "--network", network, "--mount", seed_mount,
                "--tmpfs", "/tmp:rw,size=16m", "--entrypoint", "sh", MINIO_CLIENT_IMAGE,
                "-c", "mc --config-dir /tmp/mc alias import s3 /credentials.json >/dev/null && mc --config-dir /tmp/mc ls s3",
                timeout=8,
            )
            if ready.returncode == 0:
                break
            _remove_exact_docker_fixture(
                (client,), None, owner_token, uncertain=ready.returncode == 125
            )
            time.sleep(0.25)
        assert ready is not None and ready.returncode == 0, ready.stderr if ready else "not ready"

        common_probe = [
            "--network", network, "--env-file", str(runner_env),
            "--mount", f"type=bind,src={test_bin},dst=/test-bin,readonly",
            "--mount", f"type=bind,src={result_dir},dst=/result",
            "--mount", f"type=bind,src={REPO / 'services/backup/init/scripts'},dst=/scripts,readonly",
            "--tmpfs", "/tmp:rw,size=64m", "--entrypoint", "sh",
        ]
        backed_up = _docker_command(
            "run", "--pull=never", "--rm", "--name", client,
            "--label", f"{_EXTERNAL_S3_FIXTURE_OWNER_LABEL}={owner_token}",
            *common_probe, MINIO_CLIENT_IMAGE,
            "/scripts/entrypoint.sh", "/scripts/backup-all.sh", timeout=20,
        )
        assert backed_up.returncode == 75, backed_up.stderr
        assert "another backup publication" in backed_up.stderr
        assert not result_dir.joinpath("secret-env-leak").exists()
        for raw in (access, secret, "db-secret"):
            assert raw not in backed_up.stdout + backed_up.stderr

        seeded = _docker_command(
            "run", "--pull=never", "--rm", "--name", client,
            "--label", f"{_EXTERNAL_S3_FIXTURE_OWNER_LABEL}={owner_token}",
            "--network", network, "--mount", seed_mount, "--tmpfs", "/tmp:rw,size=16m",
            "--entrypoint", "sh", MINIO_CLIENT_IMAGE, "-c",
            "mc --config-dir /tmp/mc alias import s3 /credentials.json >/dev/null && "
            "mc --config-dir /tmp/mc stat s3/atlas-backups >/dev/null && "
            "printf invalid-completion | mc --config-dir /tmp/mc pipe s3/atlas-backups/20260829_120000/postgres.complete",
            timeout=20,
        )
        assert seeded.returncode == 0, seeded.stderr

        restored = _docker_command(
            "run", "--pull=never", "--rm", "--name", client,
            "--label", f"{_EXTERNAL_S3_FIXTURE_OWNER_LABEL}={owner_token}",
            *common_probe, MINIO_CLIENT_IMAGE,
            "/scripts/entrypoint.sh", "/scripts/restore-postgres.sh",
            timeout=20,
        )
        assert restored.returncode == 1, restored.stderr
        assert "selected backup is incomplete or unauthenticated" in restored.stderr
        assert not result_dir.joinpath("secret-env-leak").exists()
        assert access not in restored.stdout + restored.stderr
        assert secret not in restored.stdout + restored.stderr

        token_env = tmp_path / "token.env"
        token_env.write_text(
            runner_env.read_text(encoding="utf-8") + f"BACKUP_S3_SESSION_TOKEN={token}\n",
            encoding="utf-8",
        )
        token_env.chmod(0o600)
        token_probe_args = list(common_probe)
        token_probe_args[token_probe_args.index("--env-file") + 1] = str(token_env)
        token_probe = _docker_command(
            "run", "--pull=never", "--rm", "--name", probe,
            "--label", f"{_EXTERNAL_S3_FIXTURE_OWNER_LABEL}={owner_token}",
            *token_probe_args, MINIO_CLIENT_IMAGE,
            "/scripts/entrypoint.sh", "/scripts/restore-postgres.sh",
            timeout=20,
        )
        assert token_probe.returncode != 0
        assert "security token" in token_probe.stderr.lower()
        assert access not in token_probe.stdout + token_probe.stderr
        assert secret not in token_probe.stdout + token_probe.stderr
        assert token not in token_probe.stdout + token_probe.stderr
    finally:
        _remove_exact_docker_fixture(
            (client, probe, server), network, owner_token
        )


def test_opted_in_ci_runs_exact_backup_image_entrypoint_and_real_mc_installer(
    tmp_path: Path,
) -> None:
    _require_opted_in_backup_production_image()

    owner_token = uuid.uuid4().hex
    suffix = owner_token[:12]
    network = f"atlas-backup-production-test-{suffix}"
    server = f"atlas-backup-production-s3-{suffix}"
    runner = f"atlas-backup-production-runner-{suffix}"
    probe = f"atlas-backup-production-probe-{suffix}"
    artifact_server = f"atlas-backup-production-artifact-{suffix}"
    access = "productionExternalAccess"
    secret = "productionExternalSecret"
    artifact = _extract_pinned_mc_artifact(tmp_path, suffix, owner_token)
    artifact_responder = _write_artifact_responder(tmp_path, artifact)
    server_env = tmp_path / "production-server.env"
    server_env.write_text(
        f"MINIO_ROOT_USER={access}\nMINIO_ROOT_PASSWORD={secret}\n", encoding="utf-8"
    )
    server_env.chmod(0o600)
    runner_env = tmp_path / "production-runner.env"
    runner_env.write_text(
        "PATH=/test-bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
        "BACKUP_SOURCE=container\nBACKUP_S3_MODE=external\n"
        "BACKUP_S3_ENDPOINT=http://external-s3:9000\n"
        f"BACKUP_S3_ACCESS_KEY={access}\nBACKUP_S3_SECRET_KEY={secret}\n"
        "BACKUP_S3_REGION=us-east-1\nBACKUP_S3_TLS_VERIFY=true\n"
        "BACKUP_COMMAND_TIMEOUT_SECONDS=30\n"
        "ATLAS_BACKUP_TEST_MC_ARTIFACT_BASE_URL=http://mc-artifacts:8080\n"
        "BACKUP_MANIFEST_HMAC_KEY=" + "b" * 64 + "\n"
        "BACKUP_DEPLOYMENT_ID=atlas-production-image-test\n"
        "BACKUP_TIMESTAMP=20260829_130000\n"
        "SUPABASE_DB_USER=postgres\nSUPABASE_DB_PASSWORD=production-db-boundary\n"
        "SUPABASE_DB_NAME=postgres\n",
        encoding="utf-8",
    )
    runner_env.chmod(0o600)
    seed_credentials = tmp_path / "production-seed.json"
    seed_credentials.write_text(
        json.dumps({
            "url": "http://external-s3:9000", "accessKey": access,
            "secretKey": secret, "api": "S3v4", "path": "auto",
        }),
        encoding="utf-8",
    )
    seed_credentials.chmod(0o600)
    test_bin = tmp_path / "production-test-bin"
    result_dir = tmp_path / "production-result"
    test_bin.mkdir()
    result_dir.mkdir()
    psql = test_bin / "psql"
    psql.write_text("#!/bin/sh\nprintf 'busy\\n'\nexit 75\n", encoding="utf-8")
    psql.chmod(0o755)
    openssl = test_bin / "openssl"
    openssl.write_text(
        "#!/bin/sh\nprintf unexpected > /result/openssl-invoked\nexit 97\n",
        encoding="utf-8",
    )
    openssl.chmod(0o755)
    launcher = tmp_path / "production-launcher.sh"
    launcher.write_text(
        "#!/bin/sh\nset -eu\nmc --version > /result/mc.version\nexec sh /scripts/backup-all.sh\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)

    rendered = _render_backup_service(
        tmp_path,
        {
            "BACKUP_SOURCE": "container", "BACKUP_S3_MODE": "external",
            "BACKUP_S3_ENDPOINT": "http://external-s3:9000",
            "BACKUP_S3_ACCESS_KEY": access, "BACKUP_S3_SECRET_KEY": secret,
            "MINIO_SOURCE": "disabled", "MINIO_SCALE": "0", "MINIO_INIT_SCALE": "0",
        },
    )
    _assert_external_production_render(rendered)

    try:
        created = _docker_command(
            "network", "create", "--internal", "--label",
            f"{_EXTERNAL_S3_FIXTURE_OWNER_LABEL}={owner_token}", network,
            timeout=10,
        )
        assert created.returncode == 0, created.stderr
        artifact_started = _docker_command(
            "run", "--pull=never", "--detach", "--rm", "--name", artifact_server,
            "--label", f"{_EXTERNAL_S3_FIXTURE_OWNER_LABEL}={owner_token}",
            "--network", network, "--network-alias", "mc-artifacts",
            "--mount", f"type=bind,src={artifact.parent},dst=/artifacts,readonly",
            "--mount", f"type=bind,src={artifact_responder},dst=/serve-mc-artifact.sh,readonly",
            "--tmpfs", "/tmp:rw,noexec,size=16m", "--entrypoint", "nc",
            BACKUP_PRODUCTION_IMAGE, "-lk", "-w", "5", "-p", "8080", "-e",
            "/serve-mc-artifact.sh", timeout=20,
        )
        assert artifact_started.returncode == 0, artifact_started.stderr
        _wait_for_artifact_server(network, probe, owner_token)
        started = _docker_command(
            "run", "--pull=never", "--detach", "--rm", "--name", server,
            "--label", f"{_EXTERNAL_S3_FIXTURE_OWNER_LABEL}={owner_token}",
            "--network", network, "--network-alias", "external-s3",
            "--env-file", str(server_env), "--tmpfs", "/data:rw,size=128m",
            MINIO_IMAGE, "server", "/data", "--address", ":9000", timeout=45,
        )
        assert started.returncode == 0, started.stderr
        seed_mount = f"type=bind,src={seed_credentials},dst=/credentials.json,readonly"
        _wait_for_s3_server(network, probe, seed_mount, owner_token)

        production = _docker_command(
            "run", "--pull=never", "--rm", "--name", runner,
            "--label", f"{_EXTERNAL_S3_FIXTURE_OWNER_LABEL}={owner_token}",
            "--network", network, "--env-file", str(runner_env),
            "--mount", f"type=bind,src={test_bin},dst=/test-bin,readonly",
            "--mount", f"type=bind,src={result_dir},dst=/result",
            "--mount", f"type=bind,src={REPO / 'services/backup/init/scripts'},dst=/scripts,readonly",
            "--mount", f"type=bind,src={launcher},dst=/production-launcher.sh,readonly",
            "--tmpfs", "/tmp:rw,exec,size=128m", "--entrypoint",
            rendered["backup"]["entrypoint"][0], BACKUP_PRODUCTION_IMAGE,
            *rendered["backup"]["entrypoint"][1:], "/production-launcher.sh",
            timeout=90,
        )
        assert production.returncode == 75, production.stderr
        assert "another backup publication" in production.stderr
        assert not result_dir.joinpath("openssl-invoked").exists()
        assert "mc version RELEASE.2025-08-13T08-35-41Z" in result_dir.joinpath(
            "mc.version"
        ).read_text(encoding="utf-8")
        _assert_raw_values_redacted(
            production.stdout + production.stderr,
            (access, secret, "production-db-boundary"),
        )
        bucket = _docker_command(
            "run", "--pull=never", "--rm", "--name", probe,
            "--label", f"{_EXTERNAL_S3_FIXTURE_OWNER_LABEL}={owner_token}",
            "--network", network, "--mount", seed_mount,
            "--tmpfs", "/tmp:rw,size=16m", "--entrypoint", "sh",
            MINIO_CLIENT_IMAGE, "-c",
            "mc --config-dir /tmp/mc alias import s3 /credentials.json >/dev/null && mc --config-dir /tmp/mc stat s3/atlas-backups >/dev/null",
            timeout=20,
        )
        assert bucket.returncode == 0, bucket.stderr
    finally:
        _remove_exact_docker_fixture(
            (runner, probe, server, artifact_server), network, owner_token,
        )


def _blocking_production_runner_fixture(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    trace = tmp_path / "trace"
    ready = tmp_path / "ready"
    (bin_dir / "timeout").write_text('#!/bin/sh\nshift 5\nexec "$@"\n')
    (bin_dir / "openssl").write_text('#!/bin/sh\nexit 0\n')
    (bin_dir / "uname").write_text("#!/bin/sh\nprintf '%s\\n' aarch64\n")
    (bin_dir / "sha256sum").write_text(
        '#!/bin/sh\nprintf "%s  %s\\n" 14c8c9616cfce4636add161304353244e8de383b2e2752c0e9dad01d4c27c12c "$1"\n'
    )
    (bin_dir / "mc").write_text(
        """#!/bin/sh
case "$1" in
  --version) printf '%s\n' 'mc version RELEASE.2025-08-13T08-35-41Z'; exit 0 ;;
  alias)
    [ -z "${BACKUP_S3_ACCESS_KEY+x}${BACKUP_S3_SECRET_KEY+x}${BACKUP_S3_SESSION_TOKEN+x}" ] || printf leak >"$LEAK"
    printf '%s\n' "$MC_CONFIG_DIR" >>"$TRACE"
    exit 0
    ;;
  mb|cat)
    printf ready >"$READY"
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
    while :; do sleep 1; done
    ;;
esac
exit 0
"""
    )
    _write_fake_setsid(bin_dir)
    for path in bin_dir.iterdir():
        path.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "TRACE": str(trace), "READY": str(ready), "LEAK": str(tmp_path / "leak"),
        "BACKUP_SOURCE": "container", "BACKUP_S3_MODE": "external",
        "BACKUP_S3_ENDPOINT": "https://s3.example.test",
        "BACKUP_S3_ACCESS_KEY": "signal-access", "BACKUP_S3_SECRET_KEY": "signal-secret",
        "BACKUP_COMMAND_TIMEOUT_SECONDS": "5", "BACKUP_RESTORE_GLOBAL_TIMEOUT_SECONDS": "20",
        "BACKUP_MANIFEST_HMAC_KEY": "a" * 64,
        "BACKUP_DEPLOYMENT_ID": "atlas-test-deployment",
        "BACKUP_TIMESTAMP": "20260829_120000",
        "BACKUP_RESTORE_MAINTENANCE_MODE": "confirmed",
        "SUPABASE_DB_USER": "postgres", "SUPABASE_DB_PASSWORD": "db-secret",
        "SUPABASE_DB_NAME": "postgres",
    }
    return env, trace, ready


@pytest.mark.parametrize("script_name", ["backup-all.sh", "restore-postgres.sh"])
@pytest.mark.parametrize("sent_signal", [signal.SIGHUP, signal.SIGINT, signal.SIGTERM])
def test_production_backup_restore_and_global_wrapper_clean_s3_config_on_signals(
    tmp_path: Path, script_name: str, sent_signal: signal.Signals,
) -> None:
    env, trace, ready = _blocking_production_runner_fixture(tmp_path)
    scripts = REPO / "services/backup/init/scripts"
    process = subprocess.Popen(
        ["sh", str(scripts / "entrypoint.sh"), str(scripts / script_name)],
        env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not ready.exists():
            if process.poll() is not None:
                break
            time.sleep(0.05)
        assert ready.exists(), process.communicate(timeout=1)
        config_dir = Path(trace.read_text(encoding="utf-8").splitlines()[0])
        assert config_dir.is_dir()
        os.killpg(process.pid, sent_signal)
        stdout, stderr = process.communicate(timeout=5)
        assert not config_dir.exists()
        assert not tmp_path.joinpath("leak").exists()
        for raw in ("signal-access", "signal-secret", "db-secret"):
            assert raw not in stdout + stderr
        if script_name == "backup-all.sh":
            work = config_dir.parent
            if work.name.startswith("atlas-backup-"):
                shutil.rmtree(work)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)


def test_concurrent_production_backups_use_unique_s3_config_directories(
    tmp_path: Path,
) -> None:
    env, trace, _ready = _blocking_production_runner_fixture(tmp_path)
    scripts = REPO / "services/backup/init/scripts"
    processes = [
        subprocess.Popen(
            ["sh", str(scripts / "backup-all.sh")], env={**env, "READY": str(tmp_path / f"ready-{i}")},
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
        for i in range(2)
    ]
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if trace.exists() and len(trace.read_text(encoding="utf-8").splitlines()) >= 2 and all(
                tmp_path.joinpath(f"ready-{i}").exists() for i in range(2)
            ):
                break
            time.sleep(0.05)
        dirs = [Path(value) for value in trace.read_text(encoding="utf-8").splitlines()]
        assert len(dirs) == 2
        assert len(set(dirs)) == 2
        for process in processes:
            os.killpg(process.pid, signal.SIGTERM)
        outputs = [process.communicate(timeout=5) for process in processes]
        assert all(not path.exists() for path in dirs)
        assert not tmp_path.joinpath("leak").exists()
        for stdout, stderr in outputs:
            for raw in ("signal-access", "signal-secret", "db-secret"):
                assert raw not in stdout + stderr
        for config_dir in dirs:
            work = config_dir.parent
            if work.name.startswith("atlas-backup-"):
                shutil.rmtree(work)
    finally:
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)


@pytest.mark.parametrize("script_name", ["backup-all.sh", "restore-postgres.sh"])
def test_entrypoint_production_paths_fail_before_s3_or_database_without_external_secrets(
    tmp_path: Path, script_name: str,
) -> None:
    env, trace, ready = _blocking_production_runner_fixture(tmp_path)
    env.update({"BACKUP_S3_ACCESS_KEY": "", "BACKUP_S3_SECRET_KEY": ""})
    scripts = REPO / "services/backup/init/scripts"
    result = subprocess.run(
        ["sh", str(scripts / "entrypoint.sh"), str(scripts / script_name)],
        env=env, text=True, capture_output=True, check=False, timeout=5,
    )
    assert result.returncode == 64
    assert "required for external endpoints" in result.stderr
    assert not trace.exists()
    assert not ready.exists()
    assert "db-secret" not in result.stdout + result.stderr


def test_backup_and_restore_use_bounded_publication_coordination() -> None:
    backup = (REPO / "services/backup/init/scripts/backup-all.sh").read_text()
    restore = (REPO / "services/backup/init/scripts/restore-postgres.sh").read_text()

    assert "pg_try_advisory_lock" in backup
    assert "atlas-backup-publication-" in backup
    assert "verify_backup_lock" in backup
    assert "exit 75" in backup
    assert 'listing="$(run_bounded mc ls --recursive' not in restore
    assert 'candidates="$(' not in restore
    assert "discover_candidates" in restore
    assert "BACKUP_RESTORE_MAX_CANDIDATES" in restore
    assert 'done <"$CANDIDATES"' in restore


@pytest.mark.parametrize("value", ["0", "01", "1001"])
def test_restore_rejects_invalid_candidate_window(value: str) -> None:
    result = subprocess.run(
        ["sh", str(REPO / "services/backup/init/scripts/restore-postgres.sh")],
        env={
            "PATH": "/usr/bin:/bin",
            "BACKUP_RESTORE_MAX_CANDIDATES": value,
            "ATLAS_RESTORE_GLOBAL_DEADLINE_ACTIVE": "1",
            "BACKUP_MANIFEST_HMAC_KEY": "a" * 64,
            "BACKUP_DEPLOYMENT_ID": "atlas-test-deployment",
            "SUPABASE_DB_USER": "postgres",
            "SUPABASE_DB_PASSWORD": "secret",
            "SUPABASE_DB_NAME": "postgres",
            "MINIO_ROOT_USER": "minio",
            "MINIO_ROOT_PASSWORD": "secret",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 64
    assert "BACKUP_RESTORE_MAX_CANDIDATES" in result.stderr


def _preflight_restore_env(bin_dir: Path, fixture: Path) -> dict[str, str]:
    _write_fake_setsid(bin_dir)
    return {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "FIXTURE": str(fixture),
        "ATLAS_RESTORE_GLOBAL_DEADLINE_ACTIVE": "1",
        "BACKUP_RESTORE_MAINTENANCE_MODE": "confirmed",
        "BACKUP_MANIFEST_HMAC_KEY": "a" * 64,
        "BACKUP_DEPLOYMENT_ID": "atlas-test-deployment",
        "SUPABASE_DB_USER": "postgres",
        "SUPABASE_DB_PASSWORD": "secret",
        "SUPABASE_DB_NAME": "postgres",
        "MINIO_ROOT_USER": "minio",
        "MINIO_ROOT_PASSWORD": "secret",
        "BACKUP_COMMAND_TIMEOUT_SECONDS": "2",
    }


def test_never_ending_completion_stream_is_capped_killed_and_cleaned(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture = tmp_path / "capture"
    killed = tmp_path / "producer-killed"
    timeout = bin_dir / "timeout"
    timeout.write_text('#!/bin/sh\nshift 5\nexec "$@"\n')
    timeout.chmod(0o755)
    mc = bin_dir / "mc"
    mc.write_text(
        """#!/bin/sh
case "$1" in
  alias) exit 0 ;;
  cat)
    trap '' PIPE
    trap 'printf killed >"$KILLED"; exit 143' TERM INT
    dd if=/dev/zero bs=2049 count=1 2>/dev/null
    while :; do printf x; done
    ;;
esac
"""
    )
    mc.chmod(0o755)
    _stage_backup_script_siblings(tmp_path)
    restore = tmp_path / "restore-postgres.sh"
    restore.write_text(
        (REPO / "services/backup/init/scripts/restore-postgres.sh").read_text().replace(
            '  rm -rf "$WORK"; record_cleanup_failure "$?"',
            '  wc -c <"$COMPLETE" >"$CAPTURE/bytes" 2>/dev/null || true; '
            '[ -p "$WORK/download.fifo" ] && printf fifo >"$CAPTURE/fifo" || true; '
            'rm -rf "$WORK"; record_cleanup_failure "$?"',
        )
    )
    capture.mkdir()

    result = subprocess.run(
        ["sh", str(restore)],
        env={
            **_preflight_restore_env(bin_dir, tmp_path),
            "BACKUP_TIMESTAMP": "20260714_000000",
            "CAPTURE": str(capture),
            "KILLED": str(killed),
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=8,
    )

    assert result.returncode != 0
    assert capture.joinpath("bytes").read_text().strip() == "2049"
    assert killed.read_text().endswith("killed")
    assert not capture.joinpath("fifo").exists()


def test_authenticated_dump_overflow_stores_only_signed_size_plus_one(
    tmp_path: Path,
) -> None:
    timestamp = "20260714_000000"
    publication = _write_real_publication(tmp_path, timestamp, "a" * 64)
    artifact = publication / ("2" * 32) / "postgres.dump"
    artifact.write_bytes(artifact.read_bytes() + b"x")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture = tmp_path / "capture"
    capture.mkdir()
    timeout = bin_dir / "timeout"
    timeout.write_text('#!/bin/sh\nshift 5\nexec "$@"\n')
    timeout.chmod(0o755)
    mc = bin_dir / "mc"
    mc.write_text(
        """#!/bin/sh
case "$1" in
  alias) exit 0 ;;
  cat) relative=${2#s3/atlas-backups/}; cat "$FIXTURE/$relative" ;;
esac
"""
    )
    mc.chmod(0o755)
    openssl_path = shutil.which("openssl")
    assert openssl_path
    openssl = bin_dir / "openssl"
    openssl.write_text(f'#!/bin/sh\nexec "{openssl_path}" "$@"\n')
    openssl.chmod(0o755)
    sha256sum = bin_dir / "sha256sum"
    sha256sum.write_text(
        "#!/usr/bin/env python3\n"
        "import hashlib, pathlib, sys\n"
        "p = pathlib.Path(sys.argv[1])\n"
        "print(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p}')\n"
    )
    sha256sum.chmod(0o755)
    _stage_backup_script_siblings(tmp_path)
    restore = tmp_path / "restore-postgres.sh"
    restore.write_text(
        (REPO / "services/backup/init/scripts/restore-postgres.sh").read_text().replace(
            '  rm -rf "$WORK"; record_cleanup_failure "$?"',
            '  wc -c <"$DUMP" >"$CAPTURE/dump-bytes" 2>/dev/null || true; '
            '[ -p "$WORK/download.fifo" ] && printf fifo >"$CAPTURE/fifo" || true; '
            'rm -rf "$WORK"; record_cleanup_failure "$?"',
        )
    )

    result = subprocess.run(
        ["sh", str(restore)],
        env={
            **_preflight_restore_env(bin_dir, tmp_path),
            "BACKUP_TIMESTAMP": timestamp,
            "CAPTURE": str(capture),
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=8,
    )

    assert result.returncode != 0
    assert "authenticated download limit" in result.stderr
    assert capture.joinpath("dump-bytes").read_text().strip() == "5"
    assert not capture.joinpath("fifo").exists()


def test_latest_discovery_streams_huge_listing_into_bounded_newest_window(
    tmp_path: Path,
) -> None:
    valid = "20260714_000000"
    _write_real_publication(tmp_path, valid, "a" * 64)
    for invalid in ("20260716_000000", "20260715_000000"):
        path = tmp_path / invalid
        path.mkdir()
        (path / "postgres.complete").write_text("invalid\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture = tmp_path / "capture"
    capture.mkdir()
    timeout = bin_dir / "timeout"
    timeout.write_text('#!/bin/sh\nshift 5\nexec "$@"\n')
    timeout.chmod(0o755)
    mc = bin_dir / "mc"
    mc.write_text(
        """#!/bin/sh
case "$1" in
  alias) exit 0 ;;
  ls)
    awk 'BEGIN { for (i=0; i<5000; i++) printf "20000101_%06d/postgres.complete\\n", i }'
    printf '20260714_000000/postgres.complete\n20260716_000000/postgres.complete\n20260715_000000/postgres.complete\n'
    ;;
  cat)
    printf '%s\n' "$2" >>"$CAPTURE/cat-order"
    relative=${2#s3/atlas-backups/}
    cat "$FIXTURE/$relative"
    ;;
esac
"""
    )
    mc.chmod(0o755)
    openssl_path = shutil.which("openssl")
    assert openssl_path
    openssl = bin_dir / "openssl"
    openssl.write_text(f'#!/bin/sh\nexec "{openssl_path}" "$@"\n')
    openssl.chmod(0o755)
    sha256sum = bin_dir / "sha256sum"
    sha256sum.write_text(
        "#!/usr/bin/env python3\n"
        "import hashlib, pathlib, sys\n"
        "p = pathlib.Path(sys.argv[1])\n"
        "print(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p}')\n"
    )
    sha256sum.chmod(0o755)
    pg_restore = bin_dir / "pg_restore"
    pg_restore.write_text("#!/bin/sh\nexit 81\n")
    pg_restore.chmod(0o755)
    _stage_backup_script_siblings(tmp_path)
    restore = tmp_path / "restore-postgres.sh"
    restore.write_text(
        (REPO / "services/backup/init/scripts/restore-postgres.sh").read_text().replace(
            '  rm -rf "$WORK"; record_cleanup_failure "$?"',
            '  [ -n "${CANDIDATES:-}" ] && [ -f "$CANDIDATES" ] && '
            'cp "$CANDIDATES" "$CAPTURE/candidates" || true; '
            'rm -rf "$WORK"; record_cleanup_failure "$?"',
        )
    )

    result = subprocess.run(
        ["sh", str(restore)],
        env={
            **_preflight_restore_env(bin_dir, tmp_path),
            "BACKUP_RESTORE_MAX_CANDIDATES": "3",
            "CAPTURE": str(capture),
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=8,
    )

    assert result.returncode != 0
    assert capture.joinpath("candidates").read_text().splitlines() == [
        "20260716_000000",
        "20260715_000000",
        "20260714_000000",
    ]
    completions = [
        line for line in capture.joinpath("cat-order").read_text().splitlines()
        if line.endswith("/postgres.complete")
    ]
    assert completions == [
        "s3/atlas-backups/20260716_000000/postgres.complete",
        "s3/atlas-backups/20260715_000000/postgres.complete",
        "s3/atlas-backups/20260714_000000/postgres.complete",
    ]


@pytest.mark.parametrize("value", ["01", "86401"])
def test_entrypoint_rejects_noncanonical_or_excessive_timeout(value: str) -> None:
    result = subprocess.run(
        ["sh", str(REPO / "services/backup/init/scripts/entrypoint.sh"), "/bin/true"],
        env={"PATH": "/usr/bin:/bin", "BACKUP_SOURCE": "container", "BACKUP_COMMAND_TIMEOUT_SECONDS": value},
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 64


def test_restore_requires_external_manifest_key_and_deployment_identity() -> None:
    restore = REPO / "services/backup/init/scripts/restore-postgres.sh"
    result = subprocess.run(
        ["sh", str(restore)],
        env={
            "PATH": "/usr/bin:/bin",
            "BACKUP_RESTORE_MAINTENANCE_MODE": "confirmed",
            "SUPABASE_DB_USER": "postgres",
            "SUPABASE_DB_PASSWORD": "secret",
            "SUPABASE_DB_NAME": "postgres",
            "MINIO_ROOT_USER": "minio",
            "MINIO_ROOT_PASSWORD": "secret",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 64
    assert "BACKUP_MANIFEST_HMAC_KEY" in result.stderr


def test_disabled_backup_runner_fails_before_bootstrap() -> None:
    entrypoint = REPO / "services/backup/init/scripts/entrypoint.sh"
    result = subprocess.run(
        ["sh", str(entrypoint), "/bin/true"],
        env={"PATH": "/usr/bin:/bin", "BACKUP_SOURCE": "disabled"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "BACKUP_SOURCE=container" in result.stderr

    script = entrypoint.read_text(encoding="utf-8")
    assert script.index('BACKUP_SOURCE:-disabled') < script.index("command -v openssl")


def test_enabled_backup_runner_executes_requested_script(tmp_path) -> None:
    entrypoint = REPO / "services/backup/init/scripts/entrypoint.sh"
    fake_mc = tmp_path / "mc"
    fake_mc.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'mc version RELEASE.2025-08-13T08-35-41Z'\n",
        encoding="utf-8",
    )
    fake_mc.chmod(0o755)
    fake_timeout = tmp_path / "timeout"
    fake_timeout.write_text('#!/bin/sh\nshift 5\nexec "$@"\n', encoding="utf-8")
    fake_timeout.chmod(0o755)
    fake_sha = tmp_path / "sha256sum"
    fake_sha.write_text(
        '#!/bin/sh\nprintf "%s  %s\\n" 14c8c9616cfce4636add161304353244e8de383b2e2752c0e9dad01d4c27c12c "$1"\n',
        encoding="utf-8",
    )
    fake_sha.chmod(0o755)
    fake_uname = tmp_path / "uname"
    fake_uname.write_text("#!/bin/sh\nprintf '%s\\n' aarch64\n", encoding="utf-8")
    fake_uname.chmod(0o755)
    command = tmp_path / "command.sh"
    command.write_text("exit 0\n", encoding="utf-8")

    result = subprocess.run(
        ["sh", str(entrypoint), str(command)],
        env={"PATH": f"{tmp_path}:/usr/bin:/bin", "BACKUP_SOURCE": "container"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_restore_commands_use_manifest_owned_deadline(tmp_path) -> None:
    restore = REPO / "services/backup/init/scripts/restore-postgres.sh"
    trace = tmp_path / "trace"
    _write_fake_publication(tmp_path)
    _write_fake_setsid(tmp_path)

    timeout = tmp_path / "timeout"
    timeout.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$TRACE"\nshift 5\nexec "$@"\n',
        encoding="utf-8",
    )
    timeout.chmod(0o755)

    mc = tmp_path / "mc"
    mc.write_text(
        """#!/bin/sh
case "$1" in
  alias) exit 0 ;;
  ls) printf '20260714_000000/postgres.complete\n' ;;
  cat) cat "$FIXTURE/$(basename "$2")" ;;
esac
""",
        encoding="utf-8",
    )
    mc.chmod(0o755)

    pg_restore = tmp_path / "pg_restore"
    pg_restore.write_text(
        '#!/bin/sh\nprintf "pg_restore %s\\n" "$*" >> "$TRACE"\ncase "$*" in *--list*) printf "261; 1259 1 TABLE public state postgres\\n";; esac\nexit 0\n',
        encoding="utf-8",
    )
    pg_restore.chmod(0o755)

    sha256sum = tmp_path / "sha256sum"
    sha256sum.write_text(
        "#!/bin/sh\nprintf '%064d  %s\\n' 0 \"$1\"\n", encoding="utf-8"
    )
    sha256sum.chmod(0o755)
    openssl = tmp_path / "openssl"
    openssl.write_text(
        "#!/bin/sh\nprintf 'HMAC-SHA2-256(%s)= %064d\\n' \"$5\" 0\n",
        encoding="utf-8",
    )
    openssl.chmod(0o755)

    psql = tmp_path / "psql"
    psql.write_text(
        """#!/bin/sh
printf "psql %s\\n" "$*" >> "$TRACE"
stdin="$(cat)"
printf '%s\\n' "$stdin" >> "$TRACE"
case "$stdin:$*" in
  *pg_sleep*) exec sleep 60 ;;
  *server_version_num*) printf '170010\\n' ;;
  *SELECT\\ count\\(\\*\\)*lock_app=*) printf '1\\n' ;;
  *lock_app=*) printf 'locked\\n' ;;
  *pg_replication_slots*) printf '0\\n' ;;
  *datlocprovider*) printf 'c\\n' ;;
  *encode\\(convert_to*) printf '7075626c\\t7374617465\\n' ;;
  *target_db=*|*atlas_restore_*) printf '1\\n' ;;
esac
exit 0
""",
        encoding="utf-8",
    )
    psql.chmod(0o755)

    result = subprocess.run(
        ["sh", str(restore)],
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "TRACE": str(trace),
            "FIXTURE": str(tmp_path),
            "BACKUP_COMMAND_TIMEOUT_SECONDS": "17",
            "SUPABASE_DB_USER": "postgres",
            "SUPABASE_DB_PASSWORD": "secret",
            "SUPABASE_DB_NAME": "postgres",
            "MINIO_ROOT_USER": "minio",
            "MINIO_ROOT_PASSWORD": "secret",
            "BACKUP_MANIFEST_HMAC_KEY": "a" * 64,
            "BACKUP_DEPLOYMENT_ID": "atlas-test-deployment",
            "BACKUP_RESTORE_MAINTENANCE_MODE": "confirmed",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    calls = trace.read_text(encoding="utf-8")
    assert "mc alias set" not in calls
    restore_source = restore.read_text(encoding="utf-8")
    assert (
        'backup_s3_stream_command "$TIMEOUT_SECONDS" mc ls --recursive'
        in restore_source
    )
    assert (
        'backup_s3_stream_command "$TIMEOUT_SECONDS" mc cat "$object"'
        in restore_source
    )
    assert "17 head -c" in calls
    assert "17 pg_restore --list" in calls
    assert "17 sha256sum" in calls
    assert "17 awk -F=" in calls
    assert "17 openssl dgst" in calls
    assert "28800 sh" in calls
    assert "-k 111 28800 sh" in calls
    restore_call = next(
        line for line in calls.splitlines() if "pg_restore" in line and "--list" not in line
    )
    assert "--exit-on-error" in restore_call
    assert "--clean" not in restore_call
    assert "atlas_restore_" in restore_call

    assert "pg_advisory_lock" in calls
    create_index = calls.index("CREATE DATABASE")
    restore_index = calls.index(restore_call)
    validate_index = calls.index("NOT EXISTS (SELECT 1 FROM pg_index")
    cutover_index = calls.index("RENAME TO")
    assert create_index < restore_index < validate_index < cutover_index


def test_backup_timeout_must_be_a_positive_integer() -> None:
    restore = REPO / "services/backup/init/scripts/restore-postgres.sh"
    result = subprocess.run(
        ["sh", str(restore)],
        env={
            "PATH": "/usr/bin:/bin",
            "BACKUP_COMMAND_TIMEOUT_SECONDS": "0",
            "BACKUP_MANIFEST_HMAC_KEY": "a" * 64,
            "BACKUP_DEPLOYMENT_ID": "atlas-test-deployment",
            "SUPABASE_DB_USER": "postgres",
            "SUPABASE_DB_PASSWORD": "secret",
            "SUPABASE_DB_NAME": "postgres",
            "MINIO_ROOT_USER": "minio",
            "MINIO_ROOT_PASSWORD": "secret",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 64
    assert "positive integer" in result.stderr


def test_restore_timeout_rejects_values_too_large_for_lock_deadline() -> None:
    restore = REPO / "services/backup/init/scripts/restore-postgres.sh"
    result = subprocess.run(
        ["sh", str(restore)],
        env={
            "PATH": "/usr/bin:/bin",
            "BACKUP_COMMAND_TIMEOUT_SECONDS": "999999999999999999999",
            "BACKUP_RESTORE_MAINTENANCE_MODE": "confirmed",
            "BACKUP_MANIFEST_HMAC_KEY": "a" * 64,
            "BACKUP_DEPLOYMENT_ID": "atlas-test-deployment",
            "SUPABASE_DB_USER": "postgres",
            "SUPABASE_DB_PASSWORD": "secret",
            "SUPABASE_DB_NAME": "postgres",
            "MINIO_ROOT_USER": "minio",
            "MINIO_ROOT_PASSWORD": "secret",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 64
    assert "at most 86400" in result.stderr


@pytest.mark.parametrize(
    "timestamp",
    [
        "00000229_120000",
        "20230229_120000",
        "20260230_120000",
        "20261301_120000",
        "20260101_240000",
        "20260101_126000",
    ],
)
def test_restore_rejects_impossible_calendar_timestamps(
    tmp_path: Path, timestamp: str
) -> None:
    timeout = tmp_path / "timeout"
    timeout.write_text('#!/bin/sh\nshift 5\nexec "$@"\n', encoding="utf-8")
    timeout.chmod(0o755)
    mc = tmp_path / "mc"
    mc.write_text(
        "#!/bin/sh\ncase \"$1\" in alias) exit 0;; ls) exit 0;; *) exit 99;; esac\n",
        encoding="utf-8",
    )
    mc.chmod(0o755)

    result = subprocess.run(
        ["sh", str(REPO / "services/backup/init/scripts/restore-postgres.sh")],
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "BACKUP_TIMESTAMP": timestamp,
            "BACKUP_RESTORE_MAINTENANCE_MODE": "confirmed",
            "BACKUP_MANIFEST_HMAC_KEY": "a" * 64,
            "BACKUP_DEPLOYMENT_ID": "atlas-test-deployment",
            "SUPABASE_DB_USER": "postgres",
            "SUPABASE_DB_PASSWORD": "secret",
            "SUPABASE_DB_NAME": "postgres",
            "MINIO_ROOT_USER": "minio",
            "MINIO_ROOT_PASSWORD": "secret",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 64
    assert "invalid backup timestamp" in result.stderr


def test_restore_rejects_oversized_manifest_before_parsing(tmp_path: Path) -> None:
    timeout = tmp_path / "timeout"
    timeout.write_text('#!/bin/sh\nshift 5\nexec "$@"\n', encoding="utf-8")
    timeout.chmod(0o755)
    oversized = tmp_path / "oversized"
    oversized.write_bytes(b"x" * 5000)
    (tmp_path / "postgres.complete").write_text("\n".join([
        "completion_format=1", "backup_timestamp=20260829_120000", "backup_id=" + "1" * 32,
        "manifest_sha256=" + "0" * 64, "manifest_bytes=5000", "dump_bytes=1",
        "tables_bytes=1", "objects_bytes=1", "hmac_sha256=" + "0" * 64, "",
    ]))
    mc = tmp_path / "mc"
    mc.write_text(
        """#!/bin/sh
case "$1" in
  alias|ls) exit 0 ;;
  cat) cat "$FIXTURE/$(basename "$2")" ;;
esac
""",
        encoding="utf-8",
    )
    mc.chmod(0o755)

    result = subprocess.run(
        ["sh", str(REPO / "services/backup/init/scripts/restore-postgres.sh")],
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "FIXTURE": str(tmp_path),
            "BACKUP_TIMESTAMP": "20260829_120000",
            "BACKUP_RESTORE_MAINTENANCE_MODE": "confirmed",
            "BACKUP_MANIFEST_HMAC_KEY": "a" * 64,
            "BACKUP_DEPLOYMENT_ID": "atlas-test-deployment",
            "SUPABASE_DB_USER": "postgres",
            "SUPABASE_DB_PASSWORD": "secret",
            "SUPABASE_DB_NAME": "postgres",
            "MINIO_ROOT_USER": "minio",
            "MINIO_ROOT_PASSWORD": "secret",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "incomplete or unauthenticated" in result.stderr


def test_latest_skips_replay_and_incomplete_prefix_and_real_openssl_rejects_wrong_key(tmp_path: Path) -> None:
    key_hex = "3" * 64
    old = _write_real_publication(tmp_path, "20260827_120000", key_hex)
    replay = tmp_path / "20260829_120000"
    shutil.copytree(old, replay)
    (tmp_path / "20260828_120000").mkdir()  # interrupted: no completion marker
    trace = tmp_path / "trace"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "timeout").write_text('#!/bin/sh\nshift 5\nexec "$@"\n')
    (bin_dir / "mc").write_text("""#!/bin/sh
case "$1" in
  alias) exit 0 ;;
  ls) printf '20260829_120000/postgres.complete\n20260827_120000/postgres.complete\n' ;;
  cat) rel=${2#s3/atlas-backups/}; cat "$FIXTURE/$rel" ;;
esac
""")
    (bin_dir / "pg_restore").write_text('#!/bin/sh\nprintf "%s\\n" "$*" >>"$TRACE"\nexit 99\n')
    openssl_path = shutil.which("openssl")
    assert openssl_path
    (bin_dir / "openssl").write_text(f'#!/bin/sh\nprintf "openssl invoked\\n" >>"$TRACE"\nexec "{openssl_path}" "$@"\n')
    _write_fake_setsid(bin_dir)
    for file in bin_dir.iterdir():
        file.chmod(0o755)
    base_env = {
        **os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}", "FIXTURE": str(tmp_path), "TRACE": str(trace),
        "BACKUP_RESTORE_MAINTENANCE_MODE": "confirmed", "BACKUP_MANIFEST_HMAC_KEY": key_hex,
        "BACKUP_DEPLOYMENT_ID": "atlas-test-deployment", "SUPABASE_DB_USER": "postgres", "SUPABASE_DB_PASSWORD": "secret",
        "SUPABASE_DB_NAME": "postgres", "MINIO_ROOT_USER": "minio", "MINIO_ROOT_PASSWORD": "secret",
    }
    result = subprocess.run(["sh", str(REPO / "services/backup/init/scripts/restore-postgres.sh")], env=base_env, text=True, capture_output=True, check=False)
    assert result.returncode != 0
    assert "using completed backup 20260827_120000" in result.stdout
    assert trace.read_text().count("openssl invoked") == 2
    trace.write_text("")
    wrong = subprocess.run(
        ["sh", str(REPO / "services/backup/init/scripts/restore-postgres.sh")],
        env={**base_env, "BACKUP_TIMESTAMP": "20260827_120000", "BACKUP_MANIFEST_HMAC_KEY": "4" * 64},
        text=True, capture_output=True, check=False,
    )
    assert wrong.returncode != 0
    assert "pg_restore" not in trace.read_text()


def test_backup_writes_hmac_manifest_and_snapshot_owned_object_inventory(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "trace"
    timeout = tmp_path / "timeout"
    timeout.write_text('#!/bin/sh\nshift 5\nexec "$@"\n', encoding="utf-8")
    timeout.chmod(0o755)
    pg_dump = tmp_path / "pg_dump"
    pg_dump.write_text(
        '#!/bin/sh\nprintf "pg_dump %s\\n" "$*" >>"$TRACE"\nwhile [ "$#" -gt 0 ]; do [ "$1" = -f ] && { shift; printf dump >"$1"; exit; }; shift; done\n',
        encoding="utf-8",
    )
    pg_dump.chmod(0o755)
    psql = tmp_path / "psql"
    psql.write_text(
        """#!/bin/sh
stdin="$(cat)"
printf 'psql %s\\n%s\\n' "$*" "$stdin" >>"$TRACE"
case "$stdin" in
  *pg_try_advisory_lock*) printf 'locked\n'; exec sleep 60 ;;
  *pg_locks*) printf '1\n' ;;
  *pg_export_snapshot*) printf '00000003-00000001-1\\n'; exec sleep 60 ;;
  *SET*TRANSACTION*SNAPSHOT*) printf '7075626c\\t7374617465\\n' ;;
esac
case "$*" in
  *current_database*) printf '61746c6173\\n' ;;
  *server_version_num*) printf '170010\\n' ;;
esac
""",
        encoding="utf-8",
    )
    psql.chmod(0o755)
    pg_restore = tmp_path / "pg_restore"
    pg_restore.write_text(
        """#!/bin/sh
cat <<'EOF'
;
; Archive created at 2026-08-29 12:00:00 UTC
261; 1259 1 TABLE public state postgres
1255; 0 2 FUNCTION public state_value() postgres
261; 1259 3 VIEW public state_view postgres
EOF
""",
        encoding="utf-8",
    )
    pg_restore.chmod(0o755)
    sha256sum = tmp_path / "sha256sum"
    sha256sum.write_text("#!/bin/sh\nprintf '%064d  %s\\n' 0 \"$1\"\n", encoding="utf-8")
    sha256sum.chmod(0o755)
    openssl = tmp_path / "openssl"
    openssl.write_text(
        "#!/bin/sh\nprintf 'HMAC-SHA2-256(%s)= %064d\\n' \"$5\" 1\n",
        encoding="utf-8",
    )
    openssl.chmod(0o755)
    mc = tmp_path / "mc"
    mc.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >>"$TRACE"\nexit 0\n', encoding="utf-8"
    )
    mc.chmod(0o755)
    work = tmp_path / "work"
    _stage_backup_script_siblings(tmp_path)
    backup = tmp_path / "backup-all.sh"
    backup.write_text(
        (REPO / "services/backup/init/scripts/backup-all.sh")
        .read_text(encoding="utf-8")
        .replace('WORK="/tmp/atlas-backup-${backup_id}"', f"WORK={work}")
        .replace("for d in /volumes/*", f"for d in {tmp_path}/no-volumes/*"),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["sh", str(backup)],
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "TRACE": str(trace),
            "SUPABASE_DB_USER": "postgres",
            "SUPABASE_DB_PASSWORD": "secret",
            "SUPABASE_DB_NAME": "atlas",
            "MINIO_ROOT_USER": "minio",
            "MINIO_ROOT_PASSWORD": "secret",
            "BACKUP_MANIFEST_HMAC_KEY": "a" * 64,
            "BACKUP_DEPLOYMENT_ID": "atlas-test-deployment",
            "BACKUP_TIMESTAMP": "20260829_120000",
            "BACKUP_DATABASES": "false",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    manifest = (work / "postgres.manifest").read_text(encoding="utf-8")
    assert "format_version=3" in manifest
    assert "backup_timestamp=20260829_120000" in manifest
    assert "backup_id=" in manifest
    assert "deployment_id_hex=61746c61732d746573742d6465706c6f796d656e74" in manifest
    assert "database_name_hex=61746c6173" in manifest
    assert "dump_sha256=" in manifest
    assert "tables_sha256=" in manifest
    assert "table_count=1" in manifest
    assert "objects_sha256=" in manifest
    assert "object_count=3" in manifest
    assert "dump_bytes=" in manifest
    assert "tables_bytes=" in manifest
    assert "objects_bytes=" in manifest
    assert "completion_bytes=" in manifest
    assert "hmac_sha256=" in manifest
    assert (work / "postgres.tables").read_text() == "7075626c\t7374617465\n"
    objects = (work / "postgres.objects").read_text(encoding="utf-8")
    assert "TABLE public state" in objects
    assert "FUNCTION public state_value()" in objects
    assert "VIEW public state_view" in objects
    calls = trace.read_text()
    assert "--snapshot=00000003-00000001-1" in calls
    assert "SET TRANSACTION SNAPSHOT" in calls
    recursive_index = calls.index("cp --recursive")
    complete_index = calls.index("postgres.complete")
    assert recursive_index < complete_index


def test_backup_refuses_destination_prefix_reuse_before_database_access(tmp_path: Path) -> None:
    (tmp_path / "timeout").write_text('#!/bin/sh\nshift 5\nexec "$@"\n')
    (tmp_path / "mc").write_text('#!/bin/sh\ncase "$1" in alias|mb) exit 0;; ls) printf "existing-object\\n";; esac\n')
    (tmp_path / "psql").write_text(
        '''#!/bin/sh
stdin="$(cat)"
printf '%s\n' "$stdin" >>"$TRACE"
case "$stdin" in
  *pg_try_advisory_lock*) printf 'locked\n'; exec sleep 60 ;;
  *pg_locks*) printf '1\n' ;;
esac
exit 0
'''
    )
    for name in ("timeout", "mc", "psql"):
        (tmp_path / name).chmod(0o755)
    trace = tmp_path / "trace"
    result = subprocess.run(
        ["sh", str(REPO / "services/backup/init/scripts/backup-all.sh")],
        env={
            **os.environ, "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}", "TRACE": str(trace),
            "SUPABASE_DB_USER": "postgres", "SUPABASE_DB_PASSWORD": "secret", "SUPABASE_DB_NAME": "postgres",
            "MINIO_ROOT_USER": "minio", "MINIO_ROOT_PASSWORD": "secret", "BACKUP_MANIFEST_HMAC_KEY": "5" * 64,
            "BACKUP_DEPLOYMENT_ID": "atlas-test-deployment", "BACKUP_TIMESTAMP": "20260829_120000",
        }, text=True, capture_output=True, check=False,
    )
    assert result.returncode != 0
    assert "refusing to reuse existing destination prefix" in result.stderr
    assert "pg_try_advisory_lock" in trace.read_text()
    assert "pg_export_snapshot" not in trace.read_text()


def test_backup_observes_busy_status_after_lock_process_exits(tmp_path: Path) -> None:
    trace, bin_dir = _fake_s3_boundary(tmp_path)
    (bin_dir / "psql").write_text(
        "#!/bin/sh\nprintf 'busy\\n'\nexit 75\n", encoding="utf-8"
    )
    real_sed = shutil.which("sed")
    assert real_sed
    marker = tmp_path / "first-lock-read"
    (bin_dir / "sed").write_text(
        "#!/bin/sh\n"
        "case \"${1:-} ${2:-} ${3:-}\" in\n"
        "  '-n 1p /tmp/atlas-backup-'*'/publication-lock.status')\n"
        "    if [ ! -e \"$SED_MARKER\" ]; then : >\"$SED_MARKER\"; sleep 0.2; exit 0; fi;;\n"
        "esac\n"
        f'exec "{real_sed}" "$@"\n',
        encoding="utf-8",
    )
    for executable in (bin_dir / "psql", bin_dir / "sed"):
        executable.chmod(0o755)

    result = subprocess.run(
        ["sh", str(REPO / "services/backup/init/scripts/backup-all.sh")],
        env={
            **_backup_s3_env(bin_dir, trace),
            "BACKUP_S3_MODE": "external",
            "BACKUP_S3_ENDPOINT": "https://s3.example.test",
            "BACKUP_S3_ACCESS_KEY": "AK:ID@",
            "BACKUP_S3_SECRET_KEY": 'se/cr et+$"\\value',
            "BACKUP_S3_SESSION_TOKEN": "tok:@/+=",
            "SED_MARKER": str(marker),
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    assert marker.exists()
    assert result.returncode == 75, result.stderr
    assert "another backup publication is already in progress" in result.stderr


@pytest.mark.parametrize("kind", ("container", "volume"))
@pytest.mark.parametrize(
    "launch_failure",
    (
        subprocess.TimeoutExpired(("docker", "create"), 5),
        OSError("daemon transport lost"),
        KeyboardInterrupt(),
    ),
)
def test_database_runner_reconciles_late_owned_create(
    monkeypatch: pytest.MonkeyPatch, kind: str, launch_failure: BaseException,
) -> None:
    module = _database_orchestrator_module()
    runner = module.CommandRunner(token="a" * 32, timeout=5, scope="scope")
    role = "late-create"
    name = runner.unique_name(role)
    state = {"inspections": 0, "removed": False}
    removals: list[list[str]] = []
    ticks = iter((0.0, 2.0, 6.0))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    def run(command, **_kwargs):
        if command[:3] == ["docker", "volume", "create"]:
            raise launch_failure
        if command[:3] == ["docker", kind, "inspect"]:
            state["inspections"] += 1
            if state["inspections"] >= 2 and not state["removed"]:
                labels = {
                    module.OWNER_LABEL: runner.token,
                    module.SCOPE_LABEL: runner.scope,
                    module.ROLE_LABEL: role,
                }
                record = {"Name": f"/{name}" if kind == "container" else name}
                record.update(
                    {"Config": {"Labels": labels}}
                    if kind == "container" else {"Labels": labels}
                )
                return subprocess.CompletedProcess(command, 0, json.dumps([record]), "")
            return subprocess.CompletedProcess(command, 1, "", "not found")
        if command[:3] in (["docker", "ps", "-a"], ["docker", "volume", "ls"]):
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] in (["docker", "rm", "-f"], ["docker", "volume", "rm"]):
            state["removed"] = True
            removals.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    runner.run = run
    with pytest.raises(type(launch_failure)):
        try:
            if kind == "volume":
                runner.create_volume(role)
            else:
                runner.register_container(name)
                raise launch_failure
        finally:
            runner.cleanup()
    assert len(removals) == 1


@pytest.mark.parametrize("kind", ("container", "volume"))
def test_database_runner_retries_malformed_inspect_without_losing_authority(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    module = _database_orchestrator_module()
    runner = module.CommandRunner(token="b" * 32, timeout=5, scope="scope")
    name = runner.unique_name("malformed")
    getattr(runner, f"{kind}s").add(name)
    getattr(runner, f"{kind}_create_timeouts")[name] = 5
    record = {
        "Name": f"/{name}" if kind == "container" else name,
        **(
            {"Config": {"Labels": {module.OWNER_LABEL: runner.token,
                                      module.SCOPE_LABEL: runner.scope}}}
            if kind == "container"
            else {"Labels": {module.OWNER_LABEL: runner.token,
                              module.SCOPE_LABEL: runner.scope}}
        ),
    }
    inspections = iter(("not-json", json.dumps([record]), "[]"))
    removals: list[list[str]] = []
    ticks = iter((0.0, 2.0))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    def run(command, **_kwargs):
        if command[:3] == ["docker", kind, "inspect"]:
            payload = next(inspections)
            return subprocess.CompletedProcess(
                command, 1 if payload == "[]" else 0, payload, "not found"
            )
        if command[:3] in (["docker", "ps", "-a"], ["docker", "volume", "ls"]):
            return subprocess.CompletedProcess(command, 0, "", "")
        removals.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    runner.run = run
    runner.cleanup()
    assert len(removals) == 1


@pytest.mark.parametrize("kind", ("container", "volume"))
def test_database_runner_never_removes_foreign_exact_name(kind: str) -> None:
    module = _database_orchestrator_module()
    runner = module.CommandRunner(token="c" * 32, timeout=5, scope="scope")
    name = runner.unique_name("foreign")
    getattr(runner, f"{kind}s").add(name)
    getattr(runner, f"{kind}_create_timeouts")[name] = 5
    removals: list[list[str]] = []

    def run(command, **_kwargs):
        if command[:3] == ["docker", kind, "inspect"]:
            labels = {module.OWNER_LABEL: "foreign", module.SCOPE_LABEL: "scope"}
            record = {"Name": f"/{name}" if kind == "container" else name}
            record.update(
                {"Config": {"Labels": labels}}
                if kind == "container" else {"Labels": labels}
            )
            return subprocess.CompletedProcess(command, 0, json.dumps([record]), "")
        removals.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    runner.run = run
    with pytest.raises(module.ContractError, match="unowned"):
        runner.cleanup()
    assert removals == []
    assert name in getattr(runner, f"{kind}s")


def test_database_runner_cleanup_sleep_interrupt_preserves_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _database_orchestrator_module()
    runner = module.CommandRunner(token="d" * 32, timeout=5, scope="scope")
    name = runner.unique_name("sleep-interrupt")
    runner.register_container(name)
    primary = ValueError("primary")
    ticks = iter((0.0, 1.0, 6.0))
    sleeps = iter((KeyboardInterrupt("second interrupt"), None))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))

    def sleep(_seconds):
        outcome = next(sleeps)
        if outcome is not None:
            raise outcome

    def run(command, **_kwargs):
        if command[:3] == ["docker", "container", "inspect"]:
            return subprocess.CompletedProcess(command, 1, "", "not found")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.time, "sleep", sleep)
    runner.run = run
    with pytest.raises(ValueError) as caught:
        try:
            raise primary
        finally:
            runner.cleanup()
    assert caught.value is primary


def test_database_runner_defers_first_cleanup_interrupt_until_reconciled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _database_orchestrator_module()
    runner = module.CommandRunner(token="e" * 32, timeout=5, scope="scope")
    name = runner.unique_name("inspect-interrupt")
    runner.register_container(name)
    record = {
        "Name": f"/{name}",
        "Config": {
            "Labels": {
                module.OWNER_LABEL: runner.token,
                module.SCOPE_LABEL: runner.scope,
            }
        },
    }
    inspections = iter((KeyboardInterrupt("operator interrupt"), record))
    removals: list[str] = []
    ticks = iter((0.0, 1.0))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    def inspect(_kind: str, _name: str):
        outcome = next(inspections)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(runner, "_inspect_json", inspect)
    monkeypatch.setattr(
        runner,
        "_remove_visible_resource",
        lambda _kind, removed_name, _record: removals.append(removed_name),
    )
    with pytest.raises(KeyboardInterrupt, match="operator interrupt"):
        runner.remove_container(name)
    assert removals == [name]
    assert name not in runner.containers


@pytest.mark.parametrize("kind", ("container", "volume"))
def test_database_runner_preserves_interrupt_before_foreign_collision(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    module = _database_orchestrator_module()
    runner = module.CommandRunner(token="0" * 32, timeout=5, scope="scope")
    name = runner.unique_name("interrupt-foreign")
    getattr(runner, f"{kind}s").add(name)
    getattr(runner, f"{kind}_create_timeouts")[name] = 5
    labels = {module.OWNER_LABEL: "foreign", module.SCOPE_LABEL: runner.scope}
    foreign = {"Name": f"/{name}" if kind == "container" else name}
    foreign.update(
        {"Config": {"Labels": labels}}
        if kind == "container"
        else {"Labels": labels}
    )
    inspections = iter((module.SignalInterruption("received signal 15"), foreign))
    ticks = iter((0.0, 1.0))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    def inspect(_kind: str, _name: str):
        outcome = next(inspections)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(runner, "_inspect_json", inspect)
    with pytest.raises(module.SignalInterruption, match="signal 15") as caught:
        runner._remove_owned_resource(kind, name)

    assert "foreign resource" in "\n".join(caught.value.__notes__)
    assert name in getattr(runner, f"{kind}s")


def test_database_runner_defers_deadline_interrupt_until_late_owned_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _database_orchestrator_module()
    runner = module.CommandRunner(token="9" * 32, timeout=5, scope="scope")
    name = runner.unique_name("deadline-interrupt")
    runner.register_container(name)
    record = {
        "Name": f"/{name}",
        "Config": {
            "Labels": {
                module.OWNER_LABEL: runner.token,
                module.SCOPE_LABEL: runner.scope,
            }
        },
    }
    inspections = iter((None, record))
    ticks = iter((0.0, KeyboardInterrupt("deadline interrupt"), 1.0))
    removals: list[str] = []

    def monotonic() -> float:
        outcome = next(ticks)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(module.time, "monotonic", monotonic)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runner, "_inspect_json", lambda *_args: next(inspections))
    monkeypatch.setattr(
        runner,
        "_remove_visible_resource",
        lambda _kind, removed_name, _record: removals.append(removed_name),
    )

    with pytest.raises(KeyboardInterrupt, match="deadline interrupt"):
        runner.remove_container(name)

    assert removals == [name]
    assert name not in runner.containers


def test_database_runner_backs_off_failed_retries_and_stops_at_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _database_orchestrator_module()
    runner = module.CommandRunner(token="8" * 32, timeout=5, scope="scope")
    name = runner.unique_name("bounded-backoff")
    runner.register_container(name)
    attempts: list[str] = []
    sleeps: list[float] = []
    ticks = iter((0.0, 1.0, 6.0))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(module.time, "sleep", lambda seconds: sleeps.append(seconds))

    def fail_once(*_args):
        attempts.append("inspect")
        raise OSError("daemon unavailable")

    monkeypatch.setattr(runner, "_remove_owned_resource_once", fail_once)

    with pytest.raises(OSError, match="daemon unavailable"):
        runner.remove_container(name)

    assert attempts == ["inspect", "inspect"]
    assert sleeps == [0.2, 0.2]
    assert name in runner.containers


def test_database_runner_defers_entry_deadline_interrupt_before_owned_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _database_orchestrator_module()
    runner = module.CommandRunner(token="7" * 32, timeout=5, scope="scope")
    name = runner.unique_name("entry-deadline-interrupt")
    runner.register_container(name)
    record = {
        "Name": f"/{name}",
        "Config": {
            "Labels": {
                module.OWNER_LABEL: runner.token,
                module.SCOPE_LABEL: runner.scope,
            }
        },
    }
    ticks = iter((KeyboardInterrupt("entry interrupt"), 0.0))
    removals: list[str] = []
    sleep_calls = 0

    def monotonic() -> float:
        outcome = next(ticks)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(module.time, "monotonic", monotonic)
    def sleep(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 1:
            raise SystemExit("secondary sleep interrupt")

    monkeypatch.setattr(module.time, "sleep", sleep)
    monkeypatch.setattr(runner, "_inspect_json", lambda *_args: record)
    monkeypatch.setattr(
        runner,
        "_remove_visible_resource",
        lambda _kind, removed_name, _record: removals.append(removed_name),
    )

    with pytest.raises(KeyboardInterrupt, match="entry interrupt"):
        runner.remove_container(name)

    assert removals == [name]
    assert name not in runner.containers


def test_database_runner_defers_exit_signal_before_foreign_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _database_orchestrator_module()
    runner = module.CommandRunner(token="6" * 32, timeout=5, scope="scope")
    name = runner.unique_name("exit-signal-foreign")
    runner.register_container(name)

    class ExitSignalDeferral:
        interruption = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.interruption = KeyboardInterrupt("exit signal")

    monkeypatch.setattr(module, "_RecoverySignalDeferral", ExitSignalDeferral)
    monkeypatch.setattr(module.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(
        runner,
        "_remove_owned_resource_once",
        lambda *_args: (_ for _ in ()).throw(
            module._OwnershipMismatch("foreign collision")
        ),
    )

    with pytest.raises(KeyboardInterrupt, match="exit signal") as caught:
        runner.remove_container(name)

    assert "foreign collision" in "\n".join(caught.value.__notes__)
    assert name in runner.containers


def test_failed_compose_job_uses_effective_timeout_and_reconciles_late_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _database_orchestrator_module()
    runner = module.CommandRunner(token="f" * 32, timeout=5, scope="scope")
    coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.runner = runner
    name = runner.unique_name("compose-late")
    runner.register_container(name, timeout=900)
    record = {
        "Name": f"/{name}",
        "Config": {
            "Labels": {
                module.OWNER_LABEL: runner.token,
                module.SCOPE_LABEL: runner.scope,
            }
        },
    }
    outcomes = iter((None, record, None))
    ticks = iter((0.0, 6.0))
    removals: list[str] = []
    observed_timeouts: list[int | None] = []
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runner, "_inspect_json", lambda *_args: next(outcomes))
    def remove_visible(_kind: str, removed_name: str, _record: dict) -> None:
        observed_timeouts.append(runner.container_create_timeouts.get(removed_name))
        removals.append(removed_name)

    monkeypatch.setattr(runner, "_remove_visible_resource", remove_visible)

    primary = ValueError("compose launch failed")
    with pytest.raises(ValueError) as caught:
        try:
            raise primary
        finally:
            coordinator._finish_compose_job(name, preserve_primary=True)

    assert caught.value is primary
    assert observed_timeouts == [900]
    assert removals == [name]


def test_owned_helper_status_125_reconciles_late_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owned = live_integration.OwnedDocker("helper-status-125")
    name = f"{owned.prefix}-offline-helper"
    record = {
        "Name": f"/{name}",
        "Config": {"Labels": {live_integration.OWNER_LABEL: owned.token}},
    }
    outcomes = iter((None, record, None, None))
    removals: list[str] = []
    ticks = iter((0.0, 2.0, 3.0, 121.0))

    def run(*args, **_kwargs):
        if args[1] == "run":
            return subprocess.CompletedProcess(args, 125, "", "daemon reply lost")
        removals.append(args[-1])
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(owned, "_inspect_owned", lambda *_args: next(outcomes))
    monkeypatch.setattr(live_integration.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(live_integration.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(live_integration, "_run", run)

    result = owned.run_helper(
        "offline-helper", ["--network", "none", "neo4j:5.26.27"], check=False
    )
    assert result.returncode == 125
    owned.cleanup()
    assert removals == [name]


def test_owned_helper_status_125_preserves_late_foreign_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owned = live_integration.OwnedDocker("helper-status-125-foreign")
    name = f"{owned.prefix}-offline-helper"
    foreign = {
        "Name": f"/{name}",
        "Config": {"Labels": {live_integration.OWNER_LABEL: "foreign"}},
    }
    removals: list[str] = []
    outcomes = iter((None, foreign))
    ticks = iter((0.0, 2.0))

    def run(*args, **_kwargs):
        if args[1] == "run":
            return subprocess.CompletedProcess(args, 125, "", "name collision")
        removals.append(args[-1])
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(owned, "_inspect_owned", lambda *_args: next(outcomes))
    monkeypatch.setattr(live_integration.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(live_integration.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(live_integration, "_run", run)

    owned.run_helper(
        "offline-helper", ["--network", "none", "neo4j:5.26.27"], check=False
    )
    with pytest.raises(live_integration._OwnershipMismatch, match="foreign"):
        owned.cleanup()
    assert removals == []


def test_owned_helper_preserves_interrupt_before_late_foreign_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owned = live_integration.OwnedDocker("helper-interrupt-foreign")
    name = f"{owned.prefix}-offline-helper"
    owned.containers.append(name)
    owned.uncertain.add(("container", name))
    foreign = {
        "Name": f"/{name}",
        "Config": {"Labels": {live_integration.OWNER_LABEL: "foreign"}},
    }
    outcomes = iter((KeyboardInterrupt("operator interrupt"), foreign))
    ticks = iter((0.0, 2.0))
    def inspect(*_args):
        outcome = next(outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(owned, "_inspect_owned", inspect)
    monkeypatch.setattr(live_integration.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(live_integration.time, "sleep", lambda _seconds: None)

    with pytest.raises(KeyboardInterrupt, match="operator interrupt") as caught:
        owned.cleanup()

    assert "foreign resource" in "\n".join(caught.value.__notes__)
    assert name in owned.containers


def test_owned_helper_prioritizes_late_interrupt_across_cleanup_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owned = live_integration.OwnedDocker("helper-multi-resource-interrupt")
    owned.containers.extend(("interrupt-second", "foreign-first"))

    def remove(_kind: str, name: str) -> None:
        if name == "foreign-first":
            raise live_integration._OwnershipMismatch("foreign collision")
        raise KeyboardInterrupt("operator interrupt")

    monkeypatch.setattr(owned, "_remove_owned", remove)

    with pytest.raises(KeyboardInterrupt, match="operator interrupt") as caught:
        owned.cleanup()

    notes = "\n".join(caught.value.__notes__)
    assert "foreign-first" in notes
    assert "interrupt-second" in notes


def test_owned_helper_reconciles_certain_resource_after_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owned = live_integration.OwnedDocker("helper-certain-interrupt")
    name = f"{owned.prefix}-certain"
    owned.containers.append(name)
    attempts = iter((KeyboardInterrupt("operator interrupt"), None))
    attempted: list[str] = []
    ticks = iter((0.0, 1.0, 121.0))

    def remove(_kind: str, removed_name: str) -> None:
        attempted.append(removed_name)
        outcome = next(attempts)
        if outcome is not None:
            raise outcome

    monkeypatch.setattr(owned, "_remove_owned_once", remove)
    monkeypatch.setattr(live_integration.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(live_integration.time, "sleep", lambda _seconds: None)

    with pytest.raises(KeyboardInterrupt, match="operator interrupt"):
        owned.cleanup()

    assert attempted == [name, name]
