"""Consistency and restore contracts for Neo4j and Weaviate backups."""

from __future__ import annotations

from pathlib import Path
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile

import pytest
import yaml

from tests import test_database_backup_live_integration as live_integration
from tests import test_postgres_restore_safety as restore_safety


REPO = Path(__file__).resolve().parents[2]
NEO4J_IMAGE = "neo4j:5.26.27"
WEAVIATE_IMAGE = "cr.weaviate.io/semitechnologies/weaviate:1.38.13"


@pytest.mark.parametrize(
    "create_failure", live_integration.AMBIGUOUS_CREATE_FAILURES
)
@pytest.mark.parametrize("kind", ("network", "volume"))
def test_owned_docker_reconciles_ambiguous_resource_create(
    monkeypatch: pytest.MonkeyPatch, kind: str, create_failure: BaseException
) -> None:
    owned = live_integration.OwnedDocker("ambiguous")
    prior = f"{owned.prefix}-prior-network"
    owned.networks.append(prior)
    cleanup_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        live_integration,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(create_failure),
    )
    monkeypatch.setattr(
        owned,
        "_remove_owned",
        lambda resource_kind, name: cleanup_calls.append((resource_kind, name)),
    )

    with pytest.raises(type(create_failure)):
        getattr(owned, kind)("candidate")

    name = f"{owned.prefix}-candidate"
    assert name in getattr(owned, f"{kind}s")
    expected = [(kind, name), ("network", prior)] if kind == "network" else [
        ("network", prior), (kind, name)
    ]
    assert cleanup_calls == expected


@pytest.mark.parametrize("kind", ("network", "volume"))
def test_owned_docker_reports_create_and_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    owned = live_integration.OwnedDocker("cleanup-failure")
    monkeypatch.setattr(
        live_integration,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(("docker", kind, "create"), 20)
        ),
    )
    monkeypatch.setattr(
        owned,
        "_remove_owned",
        lambda *_args: (_ for _ in ()).throw(PermissionError("cleanup denied")),
    )

    with pytest.raises(subprocess.TimeoutExpired) as raised:
        getattr(owned, kind)("candidate")
    assert "cleanup could not be proven" in "\n".join(raised.value.__notes__)


@pytest.mark.parametrize(
    "launch_failure", live_integration.AMBIGUOUS_CREATE_FAILURES
)
def test_owned_helper_reconciles_late_container_after_ambiguous_launch(
    monkeypatch: pytest.MonkeyPatch, launch_failure: BaseException,
) -> None:
    owned = live_integration.OwnedDocker("helper-late")
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
            raise launch_failure
        removals.append(args[-1])
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(owned, "_inspect_owned", lambda *_args: next(outcomes))
    monkeypatch.setattr(live_integration.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(live_integration.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(live_integration, "_run", run)

    with pytest.raises(type(launch_failure)):
        try:
            owned.run_helper(
                "offline-helper", ["--network", "none", NEO4J_IMAGE]
            )
        finally:
            owned.cleanup()

    assert removals == [name]


@pytest.mark.parametrize(
    "create_failure", live_integration.AMBIGUOUS_CREATE_FAILURES
)
@pytest.mark.parametrize(
    ("network_visible", "expected_removals"),
    ((False, 0), (True, 1)),
    ids=("before-create", "after-create"),
)
def test_restore_fixture_reconciles_ambiguous_network_create(
    monkeypatch: pytest.MonkeyPatch,
    create_failure: BaseException,
    network_visible: bool,
    expected_removals: int,
) -> None:
    cleanup_calls: list[str] = []
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(restore_safety.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(
        restore_safety.uuid,
        "uuid4",
        lambda: type("UUID", (), {"hex": "restorefixture0000"})(),
    )
    ticks = iter((0.0, 0.4, 0.8, 61.2))
    monkeypatch.setattr(restore_safety.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(restore_safety.time, "sleep", lambda _seconds: None)

    def run(*args, **_kwargs):
        nonlocal network_visible
        if args[1:3] == ("network", "create"):
            assert (
                f"{restore_safety.RESTORE_OWNER_LABEL}=restorefixture0000"
                in args
            )
            raise create_failure
        if args[1:3] == ("network", "inspect"):
            if network_visible:
                record = {
                    "Name": "atlas-restore-test-restorefixtu",
                    "Labels": {
                        restore_safety.RESTORE_OWNER_LABEL: "restorefixture0000"
                    },
                }
                return subprocess.CompletedProcess(args, 0, json.dumps([record]), "")
            return subprocess.CompletedProcess(args, 1, "", "not found")
        if args[1:3] == ("container", "inspect"):
            return subprocess.CompletedProcess(args, 1, "", "not found")
        if args[1:3] == ("network", "rm"):
            cleanup_calls.append(args[-1])
            network_visible = False
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(restore_safety, "_run", run)
    fixture = restore_safety.disposable_postgres.__wrapped__()
    with pytest.raises(type(create_failure)):
        next(fixture)
    assert cleanup_calls == ["atlas-restore-test-restorefixtu"] * expected_removals


def test_restore_fixture_reconciles_visibility_after_thirty_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "late-token"
    network = "late-network"
    container = "absent-container"
    inspections = 0
    visible = False
    removals: list[str] = []
    ticks = iter((0.0, 35.0, 61.0))
    monkeypatch.setattr(restore_safety.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(restore_safety.time, "sleep", lambda _seconds: None)

    def run(*args, **_kwargs):
        nonlocal inspections, visible
        if args[1:3] == ("container", "inspect"):
            return subprocess.CompletedProcess(args, 1, "", "not found")
        if args[1:3] == ("network", "inspect"):
            inspections += 1
            if inspections >= 3 and not removals:
                visible = True
            if visible:
                record = {
                    "Name": network,
                    "Labels": {restore_safety.RESTORE_OWNER_LABEL: token},
                }
                return subprocess.CompletedProcess(args, 0, json.dumps([record]), "")
            return subprocess.CompletedProcess(args, 1, "", "not found")
        if args[1:3] == ("network", "rm"):
            removals.append(args[-1])
            visible = False
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(restore_safety, "_run", run)
    restore_safety._cleanup_disposable_postgres(
        container, network, token, uncertain=True
    )
    assert removals == [network]


def test_owned_docker_retries_late_visibility_before_proving_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owned = live_integration.OwnedDocker("late-visible")
    name = f"{owned.prefix}-network"
    owned.networks.append(name)
    owned.uncertain.add(("network", name))
    record = {"Name": name, "Labels": {live_integration.OWNER_LABEL: owned.token}}
    records = iter((None, record, None, None))
    removals: list[str] = []
    ticks = iter((0.0, 2.0, 3.0, 121.2))
    monkeypatch.setattr(owned, "_inspect_owned", lambda *_args: next(records))
    monkeypatch.setattr(live_integration.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(live_integration.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        live_integration,
        "_run",
        lambda *_args, **_kwargs: removals.append(name)
        or subprocess.CompletedProcess([], 0, "", ""),
    )
    owned.cleanup()
    assert removals == [name]


@pytest.mark.parametrize(
    "transient_failure",
    (
        subprocess.TimeoutExpired(("docker", "network", "inspect"), 15),
        OSError("temporary daemon transport failure"),
        ValueError("malformed inspect payload"),
        KeyboardInterrupt(),
    ),
)
def test_owned_docker_retries_transient_inspection_failure_before_late_visibility(
    monkeypatch: pytest.MonkeyPatch, transient_failure: BaseException,
) -> None:
    owned = live_integration.OwnedDocker("inspect-retry")
    name = f"{owned.prefix}-network"
    owned.networks.append(name)
    owned.uncertain.add(("network", name))
    record = {"Name": name, "Labels": {live_integration.OWNER_LABEL: owned.token}}
    outcomes = iter((transient_failure, record, None, None))
    removals: list[str] = []
    ticks = iter((0.0, 2.0, 3.0, 121.2))

    def inspect(*_args):
        outcome = next(outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(owned, "_inspect_owned", inspect)
    monkeypatch.setattr(live_integration.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(live_integration.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        live_integration,
        "_run",
        lambda *_args, **_kwargs: removals.append(name)
        or subprocess.CompletedProcess([], 0, "", ""),
    )

    if isinstance(transient_failure, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            owned.cleanup()
    else:
        owned.cleanup()

    assert removals == [name]


def test_restore_fixture_preserves_primary_and_attempts_all_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[tuple[str, ...]] = []
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(restore_safety.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(
        restore_safety.uuid,
        "uuid4",
        lambda: type("UUID", (), {"hex": "cleanupfixture0000"})(),
    )
    ticks = iter((0.0, 0.4, 0.8, 61.2))
    monkeypatch.setattr(restore_safety.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(restore_safety.time, "sleep", lambda _seconds: None)

    def run(*args, **_kwargs):
        if args[1:3] == ("network", "create"):
            raise subprocess.TimeoutExpired(args, 20)
        if args[1:3] == ("network", "inspect"):
            record = {
                "Name": "atlas-restore-test-cleanupfixtu",
                "Labels": {
                    restore_safety.RESTORE_OWNER_LABEL: "cleanupfixture0000"
                },
            }
            return subprocess.CompletedProcess(args, 0, json.dumps([record]), "")
        if args[1:3] == ("container", "inspect"):
            record = {
                "Name": "/atlas-restore-pg-cleanupfixtu",
                "Config": {
                    "Labels": {
                        restore_safety.RESTORE_OWNER_LABEL: "cleanupfixture0000"
                    }
                },
            }
            return subprocess.CompletedProcess(args, 0, json.dumps([record]), "")
        if args[1:3] in (("network", "rm"), ("rm", "-f")):
            attempts.append(args[1:])
            raise PermissionError("cleanup denied")
        if args[1] == "ps":
            return subprocess.CompletedProcess(
                args, 0, "atlas-restore-pg-cleanupfixtu\n", ""
            )
        if args[1:3] == ("network", "ls"):
            return subprocess.CompletedProcess(
                args, 0, "atlas-restore-test-cleanupfixtu\n", ""
            )
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(restore_safety, "_run", run)
    fixture = restore_safety.disposable_postgres.__wrapped__()
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        next(fixture)
    assert any(call[:2] == ("rm", "-f") for call in attempts)
    assert any(call[:2] == ("network", "rm") for call in attempts)
    assert "cleanup could not be proven" in "\n".join(raised.value.__notes__)


def test_restore_fixture_never_removes_a_foreign_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    removals: list[tuple[str, ...]] = []
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(restore_safety.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(
        restore_safety.uuid,
        "uuid4",
        lambda: type("UUID", (), {"hex": "foreignfixture000"})(),
    )
    ticks = iter((0.0, 0.4, 0.8, 61.2))
    monkeypatch.setattr(restore_safety.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(restore_safety.time, "sleep", lambda _seconds: None)

    def run(*args, **_kwargs):
        if args[1:3] == ("network", "create"):
            return subprocess.CompletedProcess(args, 1, "", "already exists")
        if args[1:3] == ("network", "inspect"):
            record = {
                "Name": "atlas-restore-test-foreignfixtu",
                "Labels": {restore_safety.RESTORE_OWNER_LABEL: "someone-else"},
            }
            return subprocess.CompletedProcess(args, 0, json.dumps([record]), "")
        if args[1:3] == ("container", "inspect"):
            return subprocess.CompletedProcess(args, 1, "", "not found")
        if args[1:3] in (("network", "rm"), ("rm", "-f")):
            removals.append(args[1:])
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(restore_safety, "_run", run)
    fixture = restore_safety.disposable_postgres.__wrapped__()
    with pytest.raises(pytest.fail.Exception, match="network creation failed"):
        next(fixture)
    assert removals == []


class _RestoreHelperPostgres:
    container = "restore-helper-postgres"
    network = "restore-helper-network"

    @staticmethod
    def sql(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 0, "", "")


def _invoke_restore_helper(kind: str, tmp_path: Path) -> None:
    postgres = _RestoreHelperPostgres()
    tmp_path.joinpath("postgres.dump").write_bytes(b"dump")
    if kind == "dump":
        restore_safety._dump_database(postgres, tmp_path, "source")
    elif kind == "sidecar":
        restore_safety._write_restore_sidecar(
            postgres, tmp_path, "source", "target"
        )
    else:
        restore_safety._run_restore(postgres, tmp_path, "target")


@pytest.mark.parametrize("kind", ("dump", "sidecar", "restore"))
def test_restore_helper_containers_are_owner_labeled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str,
) -> None:
    token = "a" * 32
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        restore_safety.uuid,
        "uuid4",
        lambda: type("UUID", (), {"hex": token})(),
    )

    def run(*args, **_kwargs):
        calls.append(args)
        if args[1:3] == ("container", "inspect"):
            return subprocess.CompletedProcess(args, 1, "", "not found")
        if len(args) > 1 and args[1] == "ps":
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(restore_safety, "_run", run)

    _invoke_restore_helper(kind, tmp_path)

    launched = next(args for args in calls if args[1] == "run")
    label_index = launched.index("--label")
    assert launched[label_index + 1] == (
        f"{restore_safety.RESTORE_OWNER_LABEL}={token}"
    )


@pytest.mark.parametrize("kind", ("dump", "sidecar", "restore"))
def test_restore_helper_cleanup_preserves_foreign_name_collision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str,
) -> None:
    token = "b" * 32
    name = f"atlas-restore-client-{token[:12]}"
    removals: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        restore_safety.uuid,
        "uuid4",
        lambda: type("UUID", (), {"hex": token})(),
    )
    ticks = iter((0.0, 31.0))
    monkeypatch.setattr(restore_safety.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(restore_safety.time, "sleep", lambda _seconds: None)

    def run(*args, **_kwargs):
        if args[1] == "run":
            raise subprocess.CalledProcessError(125, args, stderr="name collision")
        if args[1:3] == ("container", "inspect"):
            record = {
                "Name": f"/{name}",
                "Config": {
                    "Labels": {restore_safety.RESTORE_OWNER_LABEL: "foreign-token"}
                },
            }
            return subprocess.CompletedProcess(args, 0, json.dumps([record]), "")
        if args[1:3] == ("rm", "-f"):
            removals.append(args[1:])
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(restore_safety, "_run", run)

    with pytest.raises(subprocess.CalledProcessError):
        _invoke_restore_helper(kind, tmp_path)

    assert removals == []


@pytest.mark.parametrize("kind", ("dump", "sidecar", "restore"))
def test_restore_helper_cleanup_preserves_primary_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str,
) -> None:
    token = "c" * 32
    monkeypatch.setattr(
        restore_safety.uuid,
        "uuid4",
        lambda: type("UUID", (), {"hex": token})(),
    )
    ticks = iter((0.0, 31.0))
    monkeypatch.setattr(restore_safety.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(restore_safety.time, "sleep", lambda _seconds: None)

    def run(*args, **_kwargs):
        if args[1] == "run":
            raise RuntimeError("primary helper failure")
        if args[1:3] == ("container", "inspect"):
            raise subprocess.TimeoutExpired(args, 10)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(restore_safety, "_run", run)

    with pytest.raises(RuntimeError, match="primary helper failure") as raised:
        _invoke_restore_helper(kind, tmp_path)

    assert "cleanup could not be proven" in "\n".join(raised.value.__notes__)


def test_generated_backup_client_wrappers_use_owned_cleanup(tmp_path: Path) -> None:
    bin_dir = restore_safety._write_backup_host_wrappers(
        _RestoreHelperPostgres(), tmp_path, tmp_path, inject_concurrent_ddl=False
    )
    for command in ("pg_dump", "pg_restore"):
        source = bin_dir.joinpath(command).read_text(encoding="utf-8")
        compile(source, str(bin_dir / command), "exec")
        assert f'"--label", "{restore_safety.RESTORE_OWNER_LABEL}=" + owner_token' in source
        assert '"container", "inspect"' in source
        assert "observed_token == owner_token" in source
        assert "restore helper cleanup sleep interrupted" in source


def _compose(service: str) -> dict:
    return yaml.safe_load(
        (REPO / f"services/{service}/compose.yml").read_text(encoding="utf-8")
    )


def _manifest(service: str) -> dict:
    return yaml.safe_load(
        (REPO / f"services/{service}/service.yml").read_text(encoding="utf-8")
    )


def test_backup_runner_never_mounts_live_database_volumes() -> None:
    compose = _compose("backup")
    mounts = compose["services"]["backup"]["volumes"]
    rendered = "\n".join(mounts)

    assert "/volumes/graph-db" not in rendered
    assert "/volumes/weaviate" not in rendered
    assert "neo4j-backups:/database-snapshots/neo4j" in rendered
    assert "weaviate-backups:/database-snapshots/weaviate" in rendered

    script = (REPO / "services/backup/init/scripts/backup-all.sh").read_text(
        encoding="utf-8"
    )
    assert "for d in /volumes/*" not in script
    assert "database-snapshots.sh" in script


def test_weaviate_exact_image_enables_native_filesystem_backups() -> None:
    manifest = _manifest("weaviate")
    images = {entry["var"]: entry["default"] for entry in manifest["images"]}
    env = {entry["name"]: entry for entry in manifest["env"]}
    assert images["WEAVIATE_IMAGE"] == WEAVIATE_IMAGE
    assert "backup-filesystem" in env["WEAVIATE_ENABLE_MODULES"]["default"].split(",")

    compose = _compose("weaviate")
    service = compose["services"]["weaviate"]
    assert service["environment"]["BACKUP_FILESYSTEM_PATH"] == (
        "/var/lib/weaviate/backups"
    )
    assert "weaviate-backups:/var/lib/weaviate/backups" in service["volumes"]


def test_neo4j_exact_image_uses_bounded_offline_dump_contract() -> None:
    manifest = _manifest("neo4j")
    images = {entry["var"]: entry["default"] for entry in manifest["images"]}
    assert images["NEO4J_GRAPH_DB_IMAGE"] == NEO4J_IMAGE

    script = (REPO / "services/neo4j/build/scripts/offline-backup.sh").read_text(
        encoding="utf-8"
    )
    assert "neo4j:5.26.27" in script
    assert "database is still online" in script
    assert "neo4j-admin database dump neo4j" in script
    assert "neo4j-admin database dump system" in script
    assert re.search(r"run_bounded neo4j-admin database dump", script)
    assert "timeout -s TERM -k 10" in script
    assert "snapshot_state=complete" in script
    assert "sha256sum" in script

    wrapper = (REPO / "services/backup/run-consistent-backup.sh").read_text(
        encoding="utf-8"
    )
    orchestrator = (REPO / "services/backup/database_orchestrator.py").read_text(
        encoding="utf-8"
    )
    assert "database_orchestrator.py" in wrapper
    assert 'self.compose("stop"' in orchestrator
    assert "neo4j-graph-db" in orchestrator
    assert "offline-backup.sh" in orchestrator
    assert 'self.compose("up"' in orchestrator
    assert "signal.SIGTERM" in orchestrator


def test_database_backup_and_restore_entrypoints_are_present() -> None:
    scripts = REPO / "services/backup/init/scripts"
    snapshot = scripts / "database-snapshots.sh"
    restore = scripts / "restore-databases.sh"
    orchestrator = REPO / "services/backup/run-consistent-backup.sh"
    restore_orchestrator = REPO / "services/backup/run-database-restore.sh"

    for path in (snapshot, restore, orchestrator, restore_orchestrator):
        assert path.is_file(), f"missing executable contract: {path}"

    snapshot_text = snapshot.read_text(encoding="utf-8")
    assert WEAVIATE_IMAGE in snapshot_text
    assert "/v1/backups/filesystem" in snapshot_text
    assert "SUCCESS" in snapshot_text
    assert "sha256sum" in snapshot_text
    assert "snapshot_state=complete" in snapshot_text

    restore_text = restore.read_text(encoding="utf-8")
    host_restore = (REPO / "services/backup/database_orchestrator.py").read_text(
        encoding="utf-8"
    )
    assert "/v1/backups/filesystem" in host_restore
    assert "/restore" in host_restore
    assert "database_sha256" in restore_text
    assert "hmac_sha256" in restore_text


def test_database_snapshot_contract_is_declared_by_backup_manifest() -> None:
    manifest = _manifest("backup")
    env = {entry["name"]: entry for entry in manifest["env"]}
    assert env["BACKUP_DATABASES"]["default"] is True
    assert env["BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS"]["default"] == 120
    assert env["BACKUP_MAX_DATABASE_ARCHIVE_BYTES"]["default"] == 53687091200

    capability = next(
        item for item in manifest["capabilities"]
        if item["name"] == "Consistent Neo4j and Weaviate backup/restore"
    )
    assert capability["status"] == "supported"
    assert capability["verification"] == "tested"


def _fake_docker(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    trace = tmp_path / "docker.trace"
    state = tmp_path / "neo4j.state"
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >>\"$TRACE\"\n"
        "case \"$*\" in\n"
        "  'compose ps --all -q neo4j-graph-db') printf '%064d\\n' 0 | tr 0 a ;;\n"
        "  'container inspect aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')\n"
        "    if [ \"$(cat \"$NEO4J_STATE\")\" = running ]; then running=true; status=running; else running=false; status=exited; fi\n"
        "    printf '[{\"Config\":{\"Labels\":{\"com.docker.compose.service\":\"neo4j-graph-db\"}},\"State\":{\"Running\":%s,\"Status\":\"%s\",\"Health\":{\"Status\":\"healthy\"}}}]\\n' \"$running\" \"$status\"\n"
        "    ;;\n"
        "  'compose ps --status running --services neo4j-graph-db')\n"
        "    [ \"$(cat \"$NEO4J_STATE\")\" = running ] && "
        "printf '%s\\n' neo4j-graph-db\n"
        "    ;;\n"
        "  'compose stop --timeout 5 neo4j-graph-db') printf '%s' stopped >\"$NEO4J_STATE\" ;;\n"
        "  'compose up --no-deps -d neo4j-graph-db') printf '%s' running >\"$NEO4J_STATE\" ;;\n"
        "  container\\ inspect\\ *) exit 1 ;;\n"
        "  ps\\ -aq\\ *) exit 0 ;;\n"
        "  *'backup /scripts/backup-all.sh') exit \"${BACKUP_FAKE_RC:-0}\" ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return trace, {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "TRACE": str(trace),
        "NEO4J_STATE": str(state),
        "TMPDIR": str(tmp_path),
        "BACKUP_TIMESTAMP": "20260830_010203",
        "BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS": "5",
        "BACKUP_RESTORE_GLOBAL_TIMEOUT_SECONDS": "10",
        "NEO4J_GRAPH_DB_SOURCE": "container",
        "WEAVIATE_SOURCE": "disabled",
        "WEAVIATE_ENABLE_MODULES": "backup-filesystem",
    }


def test_backup_orchestrator_preserves_an_initially_stopped_neo4j(tmp_path: Path) -> None:
    trace, env = _fake_docker(tmp_path)
    Path(env["NEO4J_STATE"]).write_text("stopped", encoding="utf-8")
    result = subprocess.run(
        ["sh", str(REPO / "services/backup/run-consistent-backup.sh")],
        env={**env, "NEO4J_INITIAL_STATE": "stopped"},
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    calls = trace.read_text(encoding="utf-8")
    assert "offline-backup.sh" in calls
    assert "compose stop" not in calls
    assert "compose up" not in calls


def test_backup_orchestrator_restarts_running_neo4j_after_failure(tmp_path: Path) -> None:
    trace, env = _fake_docker(tmp_path)
    Path(env["NEO4J_STATE"]).write_text("running", encoding="utf-8")
    result = subprocess.run(
        ["sh", str(REPO / "services/backup/run-consistent-backup.sh")],
        env={**env, "NEO4J_INITIAL_STATE": "running", "BACKUP_FAKE_RC": "9"},
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 64, result.stderr
    calls = trace.read_text(encoding="utf-8")
    assert "compose stop" in calls
    assert "offline-backup.sh" in calls
    assert calls.count("compose up") == 1


def test_weaviate_status_parser_rejects_duplicate_or_escaped_status(tmp_path: Path) -> None:
    response = tmp_path / "response.json"
    response.write_text(
        '{"status":"SUCCESS","nested":{"status":"FAILED"}}',
        encoding="utf-8",
    )
    script = REPO / "services/backup/init/scripts/database-snapshots.sh"
    duplicate = subprocess.run(
        ["sh", "-c", '. "$1"; weaviate_json_string "$2" status', "sh", str(script), str(response)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert duplicate.returncode != 0

    response.write_text('{"status":"SUCC\\"ESS"}', encoding="utf-8")
    escaped = subprocess.run(
        ["sh", "-c", '. "$1"; weaviate_json_string "$2" status', "sh", str(script), str(response)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert escaped.returncode != 0


def test_database_collector_archives_only_completed_native_snapshots(tmp_path: Path) -> None:
    timestamp = "20260830_010203"
    backup_id = "a" * 32
    neo4j_root = tmp_path / "neo4j"
    weaviate_root = tmp_path / "weaviate"
    work = tmp_path / "work"
    snapshot = neo4j_root / timestamp
    snapshot.mkdir(parents=True)
    weaviate_root.mkdir()
    work.mkdir()
    (snapshot / "neo4j.dump").write_bytes(b"neo4j-consistent")
    (snapshot / "system.dump").write_bytes(b"system-consistent")
    neo4j_sha = hashlib.sha256((snapshot / "neo4j.dump").read_bytes()).hexdigest()
    system_sha = hashlib.sha256((snapshot / "system.dump").read_bytes()).hexdigest()
    (snapshot / "snapshot.metadata").write_text(
        "snapshot_format=1\n"
        "snapshot_state=complete\n"
        f"backup_timestamp={timestamp}\n"
        f"neo4j_image={NEO4J_IMAGE}\n"
        "neo4j_version=5.26.27\n"
        "started_at=2026-08-30T01:02:01Z\n"
        "completed_at=2026-08-30T01:02:02Z\n"
        f"neo4j_sha256={neo4j_sha}\n"
        "neo4j_bytes=16\n"
        f"system_sha256={system_sha}\n"
        "system_bytes=17\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    wget = fake_bin / "wget"
    wget.write_text(
        "#!/bin/sh\n"
        "output=\nbody=\n"
        "while [ $# -gt 0 ]; do\n"
        "  case \"$1\" in -O) output=$2; shift 2;; --post-data=*) body=${1#*=}; shift;; --header=*) shift;; -q) shift;; *) url=$1; shift;; esac\n"
        "done\n"
        "case \"$url\" in\n"
        "  */v1/meta) printf '%s' '{\"version\":\"1.38.13\"}' >\"$output\";;\n"
        "  */v1/objects?limit=1) printf '%s' '{\"totalResults\":1}' >\"$output\";;\n"
        "  */v1/backups/filesystem)\n"
        "    id=$(printf '%s' \"$body\" | sed -n 's/.*\"id\":\"\\([^\"]*\\)\".*/\\1/p')\n"
        "    mkdir -p \"$WEAVIATE_FIXTURE_ROOT/$id\"\n"
        "    printf '%s' native-snapshot >\"$WEAVIATE_FIXTURE_ROOT/$id/data\"\n"
        "    printf '%s' '{\"status\":\"STARTED\"}' >\"$output\";;\n"
        "  */v1/backups/filesystem/*) printf '%s' '{\"status\":\"SUCCESS\"}' >\"$output\";;\n"
        "  *) exit 22;;\n"
        "esac\n",
        encoding="utf-8",
    )
    wget.chmod(0o755)
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "#!/bin/sh\nset -eu\n"
        ". \"$SNAPSHOT_SCRIPT\"\n"
        "run_bounded() { \"$@\"; }\n"
        f"capture_database_snapshots \"{work}\" {timestamp} {backup_id}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["sh", str(probe)],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
            "SNAPSHOT_SCRIPT": str(
                REPO / "services/backup/init/scripts/database-snapshots.sh"
            ),
            "DATABASE_NEO4J_SNAPSHOT_ROOT": str(neo4j_root),
            "DATABASE_WEAVIATE_SNAPSHOT_ROOT": str(weaviate_root),
            "WEAVIATE_FIXTURE_ROOT": str(weaviate_root),
            "BACKUP_MANIFEST_HMAC_KEY": "5" * 64,
            "BACKUP_DEPLOYMENT_ID": "atlas-test",
            "BACKUP_NEO4J_SOURCE": "container",
            "BACKUP_WEAVIATE_SOURCE": "container",
            "BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS": "5",
            "BACKUP_MAX_DATABASE_ARCHIVE_BYTES": "1048576",
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    manifest = (work / "databases.manifest").read_text(encoding="utf-8")
    payload, signature_line = manifest.rsplit("hmac_sha256=", 1)
    expected = hmac.new(bytes.fromhex("5" * 64), payload.encode(), hashlib.sha256)
    assert signature_line.strip() == expected.hexdigest()
    assert "neo4j_state=complete" in manifest
    assert "weaviate_state=complete" in manifest
    assert (work / "neo4j.snapshot.tar.gz").stat().st_size > 0
    assert (work / "weaviate.snapshot.tar.gz").stat().st_size > 0


def _signed_database_publication(root: Path, timestamp: str, key_hex: str) -> str:
    backup_id = "b" * 32
    artifact_dir = root / "atlas-backups" / timestamp / backup_id
    artifact_dir.mkdir(parents=True)
    fixture = root / "fixture"
    neo4j = fixture / "neo4j"
    weaviate_id = f"atlas-{timestamp}-{backup_id}"
    weaviate = fixture / weaviate_id
    neo4j.mkdir(parents=True)
    weaviate.mkdir(parents=True)
    (neo4j / "neo4j.dump").write_bytes(b"neo4j-restorable")
    (neo4j / "system.dump").write_bytes(b"system-restorable")
    neo4j_sha = hashlib.sha256((neo4j / "neo4j.dump").read_bytes()).hexdigest()
    system_sha = hashlib.sha256((neo4j / "system.dump").read_bytes()).hexdigest()
    (neo4j / "snapshot.metadata").write_text(
        "snapshot_state=complete\n"
        f"backup_timestamp={timestamp}\n"
        f"neo4j_image={NEO4J_IMAGE}\n"
        "neo4j_version=5.26.27\n"
        f"neo4j_sha256={neo4j_sha}\n"
        "neo4j_bytes=16\n"
        f"system_sha256={system_sha}\n"
        "system_bytes=17\n",
        encoding="utf-8",
    )
    (weaviate / "data").write_bytes(b"weaviate-restorable")
    neo_archive = artifact_dir / "neo4j.snapshot.tar.gz"
    weaviate_archive = artifact_dir / "weaviate.snapshot.tar.gz"
    with tarfile.open(neo_archive, "w:gz") as archive:
        for item in sorted(neo4j.iterdir()):
            archive.add(item, arcname=item.name)
    with tarfile.open(weaviate_archive, "w:gz") as archive:
        archive.add(weaviate, arcname=weaviate_id)
    key = bytes.fromhex(key_hex)
    payload = "\n".join([
        "format_version=1", "snapshot_state=complete",
        f"backup_timestamp={timestamp}", f"backup_id={backup_id}",
        "deployment_id_hex=61746c61732d74657374",
        f"neo4j_image={NEO4J_IMAGE}", "neo4j_version=5.26.27",
        "neo4j_state=complete", "neo4j_started_at=2026-08-30T01:02:01Z",
        "neo4j_completed_at=2026-08-30T01:02:02Z",
        f"neo4j_archive_sha256={hashlib.sha256(neo_archive.read_bytes()).hexdigest()}",
        f"neo4j_archive_bytes={neo_archive.stat().st_size}",
        f"weaviate_image={WEAVIATE_IMAGE}", "weaviate_version=1.38.13",
        "weaviate_state=complete", f"weaviate_snapshot_id={weaviate_id}",
        "weaviate_started_at=2026-08-30T01:02:02Z",
        "weaviate_completed_at=2026-08-30T01:02:03Z",
        f"weaviate_archive_sha256={hashlib.sha256(weaviate_archive.read_bytes()).hexdigest()}",
        f"weaviate_archive_bytes={weaviate_archive.stat().st_size}", "",
    ])
    manifest = payload + "hmac_sha256=" + hmac.new(
        key, payload.encode(), hashlib.sha256
    ).hexdigest() + "\n"
    (artifact_dir / "databases.manifest").write_text(manifest, encoding="utf-8")
    manifest_sha = hashlib.sha256(manifest.encode()).hexdigest()
    complete_payload = "\n".join([
        "completion_format=1", "snapshot_state=complete",
        f"backup_timestamp={timestamp}", f"backup_id={backup_id}",
        f"manifest_sha256={manifest_sha}",
        f"manifest_bytes={len(manifest.encode())}", "",
    ])
    complete = complete_payload + "hmac_sha256=" + hmac.new(
        key, complete_payload.encode(), hashlib.sha256
    ).hexdigest() + "\n"
    (artifact_dir.parent / "databases.complete").write_text(
        complete, encoding="utf-8"
    )
    return weaviate_id


def test_database_restore_authenticates_stages_and_invokes_native_restore(
    tmp_path: Path,
) -> None:
    timestamp = "20260830_010203"
    key_hex = "6" * 64
    s3_root = tmp_path / "s3"
    weaviate_id = _signed_database_publication(s3_root, timestamp, key_hex)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "timeout").write_text(
        '#!/bin/sh\nshift 5\nexec "$@"\n', encoding="utf-8"
    )
    (fake_bin / "mc").write_text(
        "#!/bin/sh\n"
        "case \"$1 $2\" in 'alias import') exit 0;; esac\n"
        "[ \"$1\" = cat ] || exit 22\n"
        "path=${2#s3/}\ncat \"$S3_FIXTURE_ROOT/$path\"\n",
        encoding="utf-8",
    )
    (fake_bin / "wget").write_text(
        "#!/bin/sh\n"
        "while [ $# -gt 0 ]; do case \"$1\" in -O) output=$2; shift 2;; *) shift;; esac; done\n"
        "printf '%s' '{\"status\":\"SUCCESS\"}' >\"$output\"\n",
        encoding="utf-8",
    )
    (fake_bin / "setsid").write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "os.setsid()\n"
        "os.execvp(sys.argv[1], sys.argv[1:])\n",
        encoding="utf-8",
    )
    for executable in fake_bin.iterdir():
        executable.chmod(0o755)
    common = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "S3_FIXTURE_ROOT": str(s3_root),
        "BACKUP_TIMESTAMP": timestamp,
        "BACKUP_MANIFEST_HMAC_KEY": key_hex,
        "BACKUP_DEPLOYMENT_ID": "atlas-test",
        "BACKUP_BUCKET": "atlas-backups",
        "BACKUP_S3_MODE": "external",
        "BACKUP_S3_ENDPOINT": "https://s3.example.test",
        "BACKUP_S3_ACCESS_KEY": "access",
        "BACKUP_S3_SECRET_KEY": "secret",
        "BACKUP_COMMAND_TIMEOUT_SECONDS": "5",
        "BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS": "5",
        "BACKUP_MAX_DATABASE_ARCHIVE_BYTES": "1048576",
    }
    script = REPO / "services/backup/init/scripts/restore-databases.sh"
    restore_token = secrets.token_hex(16)
    restore_root = Path(f"/tmp/atlas-database-restore-test-{restore_token}")
    restore_root.mkdir(mode=0o700)
    restore_owner = restore_root / ".restore-owner"
    restore_owner.write_text(restore_token, encoding="utf-8")
    try:
        prepared = subprocess.run(
            ["sh", str(script), "prepare"],
            env={
                **common,
                "BACKUP_RESTORE_TOKEN": restore_token,
                "DATABASE_RESTORE_ROOT": str(restore_root),
            },
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        assert prepared.returncode == 0, prepared.stderr
        stage = restore_root / f"restore-{restore_token}"
        assert (stage / "neo4j/neo4j.dump").is_file()
        assert (stage / f"weaviate/{weaviate_id}/data").is_file()
        control = (stage / "restore-set.complete").read_text(encoding="utf-8")
        assert "neo4j_state=complete" in control
        assert "weaviate_state=complete" in control
        assert f"weaviate_snapshot_id={weaviate_id}" in control
    finally:
        assert not restore_root.is_symlink()
        assert restore_owner.read_text(encoding="utf-8") == restore_token
        shutil.rmtree(restore_root)
