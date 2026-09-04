"""Opt-in exact-image restore drills using only uniquely owned Docker resources."""

from __future__ import annotations

import json
import hashlib
import importlib.util
import os
from pathlib import Path
import secrets
import signal
import shutil
import subprocess
import sys
import threading
import time
import types
import uuid

import pytest

from tests.seed_harness import (
    begin_reconciliation_after_interruption,
    cleanup_deadline_expired,
    defer_cleanup_interruption,
    establish_cleanup_deadline,
    raise_deferred_cleanup_error,
    raise_deferred_or_collision,
    sleep_for_cleanup,
)


REPO = Path(__file__).resolve().parents[2]
NEO4J_IMAGE, WEAVIATE_IMAGE = (
    "neo4j:5.26.27", "cr.weaviate.io/semitechnologies/weaviate:1.38.13",
)
IMAGES = (NEO4J_IMAGE, WEAVIATE_IMAGE)
OWNER_LABEL, SCOPE_LABEL, ROLE_LABEL = (
    "com.atlas.database-restore-token",
    "com.atlas.database-restore-scope",
    "com.atlas.database-restore-role",
)
ORCHESTRATOR = REPO / "services/backup/database_orchestrator.py"
AMBIGUOUS_CREATE_FAILURES = (
    pytest.param(
        subprocess.TimeoutExpired(("docker", "network", "create"), 20),
        id="timeout",
    ),
    pytest.param(KeyboardInterrupt(), id="interrupt"),
)


class _OwnershipMismatch(BaseException):
    """The exact Docker name exists but is not owned by this test run."""


def _add_exception_note(exc: BaseException, note: str) -> None:
    if hasattr(exc, "add_note"):
        exc.add_note(note)
        return
    notes = getattr(exc, "__notes__", None)
    if notes is None:
        notes = []
        exc.__notes__ = notes
    notes.append(note)


def _preferred_cleanup_failure(
    failures: list[tuple[str, str, BaseException]],
) -> BaseException:
    return next(
        (
            exc
            for _kind, _name, exc in failures
            if isinstance(exc, (KeyboardInterrupt, SystemExit))
        ),
        failures[0][2],
    )


def _run(
    *args: str,
    check: bool = True,
    timeout: int = 120,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(args), text=True, capture_output=True, check=False, timeout=timeout,
        input=input_text,
    )
    if check and result.returncode != 0:
        pytest.fail(
            f"command failed ({result.returncode}): {' '.join(args)}\n{result.stderr}"
        )
    return result


def _wait_exec(container: str, *args: str, timeout: int = 90) -> str:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        result = _run("docker", "exec", container, *args, check=False, timeout=15)
        if result.returncode == 0:
            return result.stdout
        last = result.stderr
        time.sleep(1)
    pytest.fail(f"{container} did not become ready: {last}")


def _wait_compose_exec(service: str, *args: str, timeout: int = 90) -> str:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        result = _run(
            "docker", "compose", "exec", "-T", service, *args,
            check=False, timeout=15,
        )
        if result.returncode == 0:
            return result.stdout
        last = result.stderr
        time.sleep(1)
    pytest.fail(f"Compose service {service} did not become ready: {last}")


@pytest.fixture
def exact_docker() -> None:
    if os.environ.get("ATLAS_DATABASE_BACKUP_LIVE_INTEGRATION") != "1":
        pytest.skip("set ATLAS_DATABASE_BACKUP_LIVE_INTEGRATION=1 for exact-image drills")
    if shutil.which("docker") is None:
        pytest.fail("live database integration was requested but docker CLI is missing")
    try:
        daemon = _run("docker", "info", check=False, timeout=20)
    except (subprocess.SubprocessError, OSError) as exc:
        pytest.fail(f"Docker daemon probe failed: {type(exc).__name__}: {exc}")
    if daemon.returncode != 0:
        pytest.fail("live database integration was requested but Docker daemon is unavailable")
    for image in IMAGES:
        try:
            probe = _run(
                "docker", "image", "inspect", image, check=False, timeout=20
            )
        except (subprocess.SubprocessError, OSError) as exc:
            pytest.fail(
                f"Docker image probe failed for {image}: {type(exc).__name__}: {exc}"
            )
        if probe.returncode != 0:
            pytest.fail(f"required exact image is not cached: {image}")


class OwnedDocker:
    def __init__(self, role: str):
        self.token = secrets.token_hex(16)
        self.prefix = f"atlas-it-{role}-{self.token}"
        self.containers: list[str] = []
        self.volumes: list[str] = []
        self.networks: list[str] = []
        self.uncertain: set[tuple[str, str]] = set()

    def volume(
        self, role: str, *, name: str | None = None, scope: str = "direct"
    ) -> str:
        name = name or f"{self.prefix}-{role}"
        self.volumes.append(name)
        try:
            _run(
                "docker", "volume", "create",
                "--label", f"{OWNER_LABEL}={self.token}",
                "--label", f"{SCOPE_LABEL}={scope}",
                "--label", f"{ROLE_LABEL}={role}", name,
            )
            record = self._inspect_owned("volume", name)
            assert record is not None
            labels = record.get("Labels") or {}
            assert (
                record.get("Name") == name
                and labels.get(OWNER_LABEL) == self.token
                and labels.get(SCOPE_LABEL) == scope
                and labels.get(ROLE_LABEL) == role
            )
        except BaseException:
            self.uncertain.add(("volume", name))
            self.cleanup()
            raise
        return name

    def network(self, role: str = "network") -> str:
        name = f"{self.prefix}-{role}"
        self.networks.append(name)
        try:
            _run(
                "docker", "network", "create",
                "--label", f"{OWNER_LABEL}={self.token}", name,
            )
        except BaseException:
            self.uncertain.add(("network", name))
            self.cleanup()
            raise
        return name

    def container(self, role: str) -> str:
        name = f"{self.prefix}-{role}"
        self.containers.append(name)
        return name

    def run_helper(
        self, role: str, args: list[str], *, check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        name = self.container(role)
        self.uncertain.add(("container", name))
        result = _run(
            "docker", "run", "--pull=never", "--rm", "--name", name,
            "--label", f"{OWNER_LABEL}={self.token}",
            "--label", f"{SCOPE_LABEL}=direct",
            "--label", f"{ROLE_LABEL}={role}",
            *args, check=check,
        )
        if result.returncode != 125:
            self.uncertain.discard(("container", name))
        return result

    def cleanup(self) -> None:
        primary = sys.exc_info()[1]
        failures: list[tuple[str, str, BaseException]] = []
        resources = (
            *(("container", name) for name in reversed(self.containers)),
            *(("network", name) for name in reversed(self.networks)),
            *(("volume", name) for name in reversed(self.volumes)),
        )
        for kind, name in resources:
            try:
                self._remove_owned(kind, name)
            except BaseException as exc:
                failures.append((kind, name, exc))
        if not failures:
            return
        detail = "; ".join(
            f"{kind} {name}: {type(exc).__name__}: {exc}"
            for kind, name, exc in failures
        )
        note = f"Owned Docker cleanup could not be proven: {detail}"
        if primary is not None:
            _add_exception_note(primary, note)
            return
        cleanup_error = _preferred_cleanup_failure(failures)
        _add_exception_note(cleanup_error, note)
        raise cleanup_error

    def _remove_owned(self, kind: str, name: str) -> None:
        primary = sys.exc_info()[1]
        deferred_error = primary
        settle_until, deferred_error = establish_cleanup_deadline(
            120 if (kind, name) in self.uncertain else None, deferred_error
        )
        while True:
            try:
                self._remove_owned_once(kind, name)
                last_failure = None
            except _OwnershipMismatch as exc:
                raise_deferred_or_collision(primary, deferred_error, exc)
            except (Exception, KeyboardInterrupt, SystemExit) as exc:
                deferred_error = defer_cleanup_interruption(deferred_error, exc)
                settle_until, deferred_error = begin_reconciliation_after_interruption(
                    settle_until,
                    120,
                    deferred_error,
                    [(f"remove {kind} {name}", exc)],
                )
                if settle_until is None:
                    raise
                last_failure = exc
            expired, deferred_error = cleanup_deadline_expired(
                settle_until, deferred_error
            )
            if expired:
                if last_failure is not None:
                    raise_deferred_cleanup_error(primary, deferred_error)
                    raise last_failure
                self.uncertain.discard((kind, name))
                raise_deferred_cleanup_error(primary, deferred_error)
                return
            deferred_error = sleep_for_cleanup(0.1, deferred_error)

    def _remove_owned_once(self, kind: str, name: str) -> None:
        record = self._inspect_owned(kind, name)
        if record is not None:
            self._remove_visible_owned(kind, name, record)
            record = self._inspect_owned(kind, name)
        assert record is None

    def _remove_visible_owned(self, kind: str, name: str, record: dict) -> None:
        actual = record.get("Name", "").lstrip("/")
        labels = record.get("Labels") or record.get("Config", {}).get("Labels") or {}
        if actual != name or labels.get(OWNER_LABEL) != self.token:
            raise _OwnershipMismatch(
                f"refusing to remove foreign {kind} at exact name {name}"
            )
        if kind == "container":
            _run("docker", kind, "rm", "-f", name, timeout=30)
        else:
            _run("docker", kind, "rm", name, timeout=30)

    def _inspect_owned(self, kind: str, name: str) -> dict | None:
        inspected = _run("docker", kind, "inspect", name, check=False, timeout=15)
        if inspected.returncode != 0:
            if kind == "container":
                listed = _run(
                    "docker", "ps", "-a", "--format", "{{.Names}}",
                    "--filter", f"name=^/{name}$", check=False, timeout=15,
                )
            elif kind == "volume":
                listed = _run(
                    "docker", "volume", "ls", "-q", "--filter", f"name={name}",
                    check=False, timeout=15,
                )
            else:
                listed = _run(
                    "docker", "network", "ls", "--format", "{{.Name}}",
                    "--filter", f"name=^{name}$", check=False, timeout=15,
                )
            assert listed.returncode == 0
            assert name not in listed.stdout.splitlines()
            return None
        records = json.loads(inspected.stdout)
        assert len(records) == 1 and isinstance(records[0], dict)
        return records[0]


def _start_neo4j(
    owned: OwnedDocker, name: str, network: str, volume: str, *, auth: str = "none"
) -> None:
    _run(
        "docker", "run", "--pull=never", "-d", "--name", name,
        "--label", f"{OWNER_LABEL}={owned.token}", "--network", network,
        "-e", f"NEO4J_AUTH={auth}", "-e", "NEO4J_ACCEPT_LICENSE_AGREEMENT=yes",
        "-v", f"{volume}:/data", NEO4J_IMAGE,
    )
    if auth == "none":
        command = ("cypher-shell", "-d", "system", "SHOW DATABASES")
    else:
        username, password = auth.split("/", 1)
        command = (
            "cypher-shell", "-u", username, "-p", password,
            "-d", "system", "SHOW DATABASES",
        )
    _wait_exec(name, *command)


def test_exact_neo4j_full_dump_load_and_second_load_failure_isolated(
    exact_docker: None,
) -> None:
    owned = OwnedDocker("neo")
    network = owned.network()
    source = owned.volume("source")
    snapshots = owned.volume("snapshots")
    restored = owned.volume("restored")
    failed = owned.volume("failed")
    source_name = owned.container("source")
    restored_name = owned.container("restored")
    scripts = REPO / "services/neo4j/build/scripts"
    timestamp = "20260830_010203"
    try:
        _start_neo4j(owned, source_name, network, source)
        _run(
            "docker", "exec", source_name, "cypher-shell", "-d", "neo4j",
            "CREATE (:AtlasDrill {generation:'source-generation'})",
        )
        _run("docker", "stop", "--time", "20", source_name, timeout=30)
        _run("docker", "rm", source_name)

        owned.run_helper(
            "offline-backup", ["--network", "none",
            "-e", f"BACKUP_TIMESTAMP={timestamp}",
            "-e", "BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS=60",
            "-v", f"{source}:/data", "-v", f"{snapshots}:/snapshot",
            "-v", f"{scripts}:/scripts:ro", "--entrypoint", "bash", NEO4J_IMAGE,
            "/scripts/offline-backup.sh"],
        )

        injected = owned.run_helper(
            "offline-restore-failed", ["--network", "none",
            "-e", "BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS=60",
            "-e", "ATLAS_NEO4J_RESTORE_TEST_FAIL_AFTER_SYSTEM_LOAD=confirmed",
            "-v", f"{failed}:/data", "-v", f"{snapshots}:/snapshot",
            "-v", f"{scripts}:/scripts:ro", "--entrypoint", "bash", NEO4J_IMAGE,
            "/scripts/offline-restore.sh", f"/snapshot/{timestamp}"], check=False,
        )
        assert injected.returncode == 79

        owned.run_helper(
            "offline-restore", ["--network", "none",
            "-e", "BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS=60",
            "-v", f"{restored}:/data", "-v", f"{snapshots}:/snapshot",
            "-v", f"{scripts}:/scripts:ro", "--entrypoint", "bash", NEO4J_IMAGE,
            "/scripts/offline-restore.sh", f"/snapshot/{timestamp}"],
        )
        _start_neo4j(owned, restored_name, network, restored)
        query = _run(
            "docker", "exec", restored_name, "cypher-shell", "-d", "neo4j",
            "MATCH (n:AtlasDrill) RETURN n.generation",
        )
        assert "source-generation" in query.stdout

        _start_neo4j(owned, source_name, network, source)
        unchanged = _run(
            "docker", "exec", source_name, "cypher-shell", "-d", "neo4j",
            "MATCH (n:AtlasDrill) RETURN n.generation",
        )
        assert "source-generation" in unchanged.stdout
    finally:
        owned.cleanup()


def _weaviate_json(container: str, path: str, *, body: dict | None = None) -> dict:
    command = ["docker", "exec", container, "wget", "-qO-", "--timeout=10"]
    if body is not None:
        command += [
            "--header=Content-Type: application/json",
            f"--post-data={json.dumps(body, separators=(',', ':'))}",
        ]
    command.append(f"http://127.0.0.1:8080{path}")
    return json.loads(_run(*command, timeout=20).stdout)


def _weaviate_mutation(
    container: str, method: str, path: str, *, body: dict | None = None
) -> None:
    """Issue an exact HTTP mutation with the pinned image's BusyBox nc."""
    payload = "" if body is None else json.dumps(body, separators=(",", ":"))
    request = (
        f"{method} {path} HTTP/1.1\r\n"
        "Host: 127.0.0.1:8080\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(payload.encode('utf-8'))}\r\n"
        "Connection: close\r\n\r\n"
        f"{payload}"
    )
    response = _run(
        "docker", "exec", "-i", container, "nc", "-w", "10", "127.0.0.1", "8080",
        timeout=20, input_text=request,
    )
    status_line = response.stdout.splitlines()[0] if response.stdout else ""
    expected = {"PUT": {200, 204}, "DELETE": {204}}
    try:
        status = int(status_line.split()[1])
    except (IndexError, ValueError) as exc:
        pytest.fail(f"Weaviate returned an invalid HTTP status line: {status_line!r}: {exc}")
    assert status in expected[method], response.stdout[:1000]


def _start_weaviate(
    owned: OwnedDocker, name: str, network: str, data: str, backups: str
) -> None:
    _run(
        "docker", "run", "--pull=never", "-d", "--name", name,
        "--hostname", "weaviate", "--network", network,
        "--label", f"{OWNER_LABEL}={owned.token}",
        "-e", "AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED=true",
        "-e", "PERSISTENCE_DATA_PATH=/var/lib/weaviate",
        "-e", "CLUSTER_HOSTNAME=weaviate", "-e", "ENABLE_MODULES=backup-filesystem",
        "-e", "DEFAULT_VECTORIZER_MODULE=none",
        "-v", f"{data}:/var/lib/weaviate", "-v", f"{backups}:/backups",
        "-e", "BACKUP_FILESYSTEM_PATH=/backups", WEAVIATE_IMAGE,
    )
    _wait_exec(
        name, "wget", "-qO-", "--timeout=10",
        "http://127.0.0.1:8080/v1/.well-known/ready",
    )


def _wait_weaviate_operation(container: str, path: str, initial: dict) -> None:
    status = initial.get("status")
    deadline = time.monotonic() + 90
    while status in {"STARTED", "TRANSFERRING", "TRANSFERRED", "FINALIZING"}:
        if time.monotonic() >= deadline:
            pytest.fail(f"Weaviate operation timed out in status {status}")
        time.sleep(1)
        status = _weaviate_json(container, path).get("status")
    assert status == "SUCCESS"


def _orchestrator_module():
    spec = importlib.util.spec_from_file_location("atlas_live_database_orchestrator", ORCHESTRATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_exact_weaviate_concurrent_backup_restores_into_empty_volume(
    exact_docker: None,
) -> None:
    owned = OwnedDocker("weaviate")
    network = owned.network("source-network")
    restore_network = owned.network("restore-network")
    source = owned.volume("source")
    backups = owned.volume("backups")
    restored = owned.volume("restored")
    source_name = owned.container("source")
    restored_name = owned.container("restored")
    snapshot_id = f"atlas-live-{owned.token}"
    try:
        _start_weaviate(owned, source_name, network, source, backups)
        _weaviate_json(
            source_name, "/v1/schema",
            body={
                "class": "AtlasDrill", "vectorizer": "none",
                "properties": [{"name": "generation", "dataType": ["text"]}],
            },
        )
        seed_id = str(uuid.uuid4())
        _weaviate_json(
            source_name, "/v1/objects",
            body={
                "class": "AtlasDrill", "id": seed_id,
                "properties": {"generation": "seed"},
            },
        )
        initial_state = {seed_id: "seed"}
        bulk_ids: list[str] = []
        # Keep the native backup pending long enough to prove that every
        # mutation type begins inside its online snapshot boundary.
        for batch_number in range(40):
            objects = []
            for item_number in range(30):
                object_id = str(uuid.uuid4())
                generation = secrets.token_hex(2048)
                bulk_ids.append(object_id)
                initial_state[object_id] = generation
                objects.append({
                    "class": "AtlasDrill", "id": object_id,
                    "properties": {"generation": generation},
                })
            inserted = _weaviate_json(
                source_name, "/v1/batch/objects", body={"objects": objects}
            )
            assert isinstance(inserted, list) and len(inserted) == len(objects)
            assert all(not record.get("result", {}).get("errors") for record in inserted)
        started = _weaviate_json(
            source_name, "/v1/backups/filesystem", body={"id": snapshot_id}
        )
        pending_statuses = {"STARTED", "TRANSFERRING", "TRANSFERRED", "FINALIZING"}
        assert started.get("status") in pending_statuses
        writer_errors: list[Exception] = []
        mutations: list[tuple[str, str, str | None]] = []
        mutation_statuses: dict[str, str] = {}

        def assert_backup_pending(operation: str) -> None:
            status = _weaviate_json(
                source_name, f"/v1/backups/filesystem/{snapshot_id}"
            ).get("status")
            assert status in pending_statuses
            mutation_statuses[operation] = status

        def write_during_backup() -> None:
            try:
                for number in range(6):
                    object_id = str(uuid.uuid4())
                    if number == 0:
                        assert_backup_pending("create")
                    _weaviate_json(
                        source_name, "/v1/objects",
                        body={
                            "class": "AtlasDrill", "id": object_id,
                            "properties": {"generation": f"concurrent-{number}"},
                        },
                    )
                    mutations.append(("put", object_id, f"concurrent-{number}"))
                    if number == 0:
                        assert_backup_pending("update")
                        _weaviate_mutation(
                            source_name, "PUT", f"/v1/objects/AtlasDrill/{seed_id}",
                            body={
                                "class": "AtlasDrill", "id": seed_id,
                                "properties": {"generation": "seed-updated"},
                            },
                        )
                        mutations.append(("put", seed_id, "seed-updated"))
                        assert_backup_pending("delete")
                        _weaviate_mutation(
                            source_name, "DELETE",
                            f"/v1/objects/AtlasDrill/{bulk_ids[0]}",
                        )
                        mutations.append(("delete", bulk_ids[0], None))
            except Exception as exc:  # surfaced in the owning test thread
                writer_errors.append(exc)

        writer = threading.Thread(target=write_during_backup)
        writer.start()
        _wait_weaviate_operation(
            source_name, f"/v1/backups/filesystem/{snapshot_id}", started
        )
        writer.join(timeout=30)
        assert not writer.is_alive()
        assert not writer_errors
        assert set(mutation_statuses) == {"create", "update", "delete"}
        source_count = len(
            _weaviate_json(source_name, "/v1/objects?limit=2000")["objects"]
        )
        assert source_count == len(initial_state) + 6 - 1

        _start_weaviate(owned, restored_name, restore_network, restored, backups)
        restored_started = _weaviate_json(
            restored_name,
            f"/v1/backups/filesystem/{snapshot_id}/restore",
            body={},
        )
        _wait_weaviate_operation(
            restored_name,
            f"/v1/backups/filesystem/{snapshot_id}/restore",
            restored_started,
        )
        restored_objects = _weaviate_json(
            restored_name, "/v1/objects?limit=2000"
        )["objects"]
        restored_count = len(restored_objects)
        restored_ids = [record["id"] for record in restored_objects]
        assert len(restored_ids) == len(set(restored_ids))
        assert all(
            isinstance(record.get("properties", {}).get("generation"), str)
            for record in restored_objects
        )
        restored_state = {
            record["id"]: record["properties"]["generation"]
            for record in restored_objects
        }
        valid_state = dict(initial_state)
        state_matches_serialized_prefix = restored_state == valid_state
        for operation, object_id, generation in mutations:
            if operation == "delete":
                valid_state.pop(object_id)
            else:
                assert generation is not None
                valid_state[object_id] = generation
            state_matches_serialized_prefix |= restored_state == valid_state
        assert state_matches_serialized_prefix
        assert seed_id in restored_state  # committed before native backup began
        assert restored_count <= source_count
        assert _weaviate_json(restored_name, "/v1/meta")["version"] == "1.38.13"
    finally:
        owned.cleanup()


def test_exact_docker_signal_cleanup_precedes_boundary_unlock(
    exact_docker: None, tmp_path: Path
) -> None:
    """A real Docker child cannot survive signal cleanup or outlive the lock."""
    owned = OwnedDocker("signal-docker")
    scope = "exact-signal-drill"
    name = f"atlas-db-signal-docker-{owned.token}"
    owned.containers.append(name)
    lock_path = tmp_path / "signal-docker.lock"
    cleaned = tmp_path / "signal-docker.cleaned"
    harness = tmp_path / "signal_docker_harness.py"
    harness.write_text(
        r'''
import importlib.util
from pathlib import Path
import signal
import sys

module_path, token, scope, name, lock_path, cleaned_path, image = sys.argv[1:]
spec = importlib.util.spec_from_file_location("atlas_signal_docker_orchestrator", module_path)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
for handled in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
    signal.signal(handled, module._signal_as_exception)
lock = module.OwnedFileLock(Path(lock_path), token=token)
lock.acquire()
coordinator = object.__new__(module.DatabaseCoordinator)
coordinator.poison_reason = None
coordinator.runner = module.CommandRunner(token=token, timeout=30, scope=scope)
coordinator.runner.register_container(name)
real_release = lock.release
def verified_release():
    coordinator.runner.assert_no_owned_containers()
    Path(cleaned_path).write_text("container-absent-before-release")
    real_release()
lock.release = verified_release
exit_code = 0
try:
    coordinator.runner.run([
        "docker", "run", "--pull=never", "--name", name,
        "--label", f"{module.OWNER_LABEL}={token}",
        "--label", f"{module.SCOPE_LABEL}={scope}",
        "--label", f"{module.ROLE_LABEL}=signal-docker",
        "--entrypoint", "sh", image, "-c",
        "trap '' TERM INT; while :; do sleep 1; done",
    ], timeout=30)
except module.SignalInterruption:
    exit_code = 130
finally:
    module.finalize_boundary_lock(lock, coordinator, retained=set())
raise SystemExit(exit_code)
''',
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [
            sys.executable, str(harness), str(ORCHESTRATOR), owned.token, scope,
            name, str(lock_path), str(cleaned), WEAVIATE_IMAGE,
        ],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 20
        record = None
        while time.monotonic() < deadline and process.poll() is None:
            record = owned._inspect_owned("container", name)
            if record and record.get("State", {}).get("Running") is True:
                break
            time.sleep(0.1)
        assert record and record.get("State", {}).get("Running") is True
        labels = record.get("Config", {}).get("Labels") or {}
        assert labels.get(SCOPE_LABEL) == scope and labels.get(ROLE_LABEL) == "signal-docker"
        os.kill(process.pid, signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 130, (stdout, stderr)
        assert cleaned.read_text(encoding="utf-8") == "container-absent-before-release"
        assert owned._inspect_owned("container", name) is None
        assert not lock_path.exists()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        owned.cleanup()


def test_exact_production_coordinator_cutover_uses_only_token_bound_test_volumes(
    exact_docker: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _orchestrator_module()
    owned = OwnedDocker("coordinator")
    token = owned.token
    scope = hashlib.sha256(str(REPO.resolve()).encode()).hexdigest()[:24]
    network = owned.network("seed-network")
    neo_live_name = f"atlas-it-{token}-neo-live"
    weav_live_name = f"atlas-it-{token}-weaviate-live"
    neo_live = owned.volume("test-neo-live", name=neo_live_name, scope=scope)
    weav_live = owned.volume("test-weaviate-live", name=weav_live_name, scope=scope)
    weav_backups = owned.volume("seed-weaviate-backups")
    auth = "neo4j/coordinator-pass"
    compose = tmp_path / "compose.yml"
    compose.write_text(
        f"""
services:
  neo4j-graph-db:
    image: {NEO4J_IMAGE}
    environment:
      NEO4J_AUTH: {auth}
      NEO4J_ACCEPT_LICENSE_AGREEMENT: "yes"
    volumes:
      - {neo_live}:/data
    healthcheck:
      test: ["CMD", "cypher-shell", "-u", "neo4j", "-p", "coordinator-pass", "-d", "system", "SHOW DATABASES"]
      interval: 2s
      timeout: 5s
      retries: 45
  weaviate:
    image: {WEAVIATE_IMAGE}
    hostname: weaviate
    environment:
      AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED: "true"
      PERSISTENCE_DATA_PATH: /var/lib/weaviate
      CLUSTER_HOSTNAME: weaviate
      ENABLE_MODULES: backup-filesystem
      BACKUP_FILESYSTEM_PATH: /backups
      DEFAULT_VECTORIZER_MODULE: none
    volumes:
      - {weav_live}:/var/lib/weaviate
      - {weav_backups}:/backups
    healthcheck:
      test: ["CMD", "wget", "-qO-", "--timeout=5", "http://127.0.0.1:8080/v1/.well-known/ready"]
      interval: 2s
      timeout: 5s
      retries: 45
volumes:
  {neo_live}:
    external: true
  {weav_live}:
    external: true
  {weav_backups}:
    external: true
""".lstrip(),
        encoding="utf-8",
    )
    for key, value in {
        "ATLAS_DATABASE_BACKUP_LIVE_INTEGRATION": "1",
        "ATLAS_DATABASE_BACKUP_TEST_TOKEN": token,
        "ATLAS_NEO4J_LIVE_VOLUME": neo_live,
        "ATLAS_WEAVIATE_LIVE_VOLUME": weav_live,
        "NEO4J_GRAPH_DB_SOURCE": "container",
        "WEAVIATE_SOURCE": "container",
        "WEAVIATE_ENABLE_MODULES": "backup-filesystem",
        "GRAPH_DB_AUTH": auth,
        "COMPOSE_FILE": str(compose),
        "COMPOSE_PROJECT_NAME": f"atlas_it_{token}",
        "BACKUP_LOCAL_ROLLBACK_RETENTION_COUNT": "2",
    }.items():
        monkeypatch.setenv(key, value)

    coordinator = module.DatabaseCoordinator(REPO, token=token, timeout=90)
    neo_stage = coordinator.runner.create_volume("neo4j-stage")
    weav_stage = coordinator.runner.create_volume("weaviate-stage")
    coordinator.stage.update({"neo4j": neo_stage, "weaviate": weav_stage})

    def seed_neo(volume: str, generation: str) -> None:
        name = owned.container(f"neo-{generation}")
        _start_neo4j(owned, name, network, volume, auth=auth)
        _run(
            "docker", "exec", name, "cypher-shell", "-u", "neo4j", "-p",
            "coordinator-pass", "-d", "neo4j",
            f"CREATE (:AtlasCoordinator {{generation:'{generation}'}})",
        )
        _run("docker", "stop", "--time", "20", name, timeout=30)
        _run("docker", "rm", name)

    def seed_weaviate(volume: str, generation: str) -> None:
        name = owned.container(f"weav-{generation}")
        _start_weaviate(owned, name, network, volume, weav_backups)
        _weaviate_json(
            name, "/v1/schema",
            body={
                "class": "AtlasCoordinator", "vectorizer": "none",
                "properties": [{"name": "generation", "dataType": ["text"]}],
            },
        )
        _weaviate_json(
            name, "/v1/objects",
            body={
                "class": "AtlasCoordinator", "id": str(uuid.uuid4()),
                "properties": {"generation": generation},
            },
        )
        _run("docker", "stop", "--time", "20", name, timeout=30)
        _run("docker", "rm", name)

    try:
        seed_neo(neo_live, "old")
        seed_neo(neo_stage, "new")
        seed_weaviate(weav_live, "old")
        seed_weaviate(weav_stage, "new")
        _run(
            "docker", "compose", "up", "-d", "--wait", "--wait-timeout", "90",
            timeout=120,
        )
        _wait_compose_exec(
            "neo4j-graph-db", "cypher-shell", "-u", "neo4j", "-p",
            "coordinator-pass", "-d", "system", "SHOW DATABASES",
        )
        _wait_compose_exec(
            "weaviate", "wget", "-qO-", "--timeout=5",
            "http://127.0.0.1:8080/v1/.well-known/ready",
        )
        retained = coordinator.cutover({})
        assert len(retained) == 2
        neo = _run(
            "docker", "compose", "exec", "-T", "neo4j-graph-db",
            "cypher-shell", "-u", "neo4j", "-p", "coordinator-pass", "-d", "neo4j",
            "MATCH (n:AtlasCoordinator) RETURN n.generation",
        )
        assert "new" in neo.stdout and "old" not in neo.stdout
        weaviate = _run(
            "docker", "compose", "exec", "-T", "weaviate", "wget", "-qO-",
            "http://127.0.0.1:8080/v1/objects?class=AtlasCoordinator&limit=10",
        )
        assert '"generation":"new"' in weaviate.stdout
        assert '"generation":"old"' not in weaviate.stdout
    finally:
        _run("docker", "compose", "down", "--timeout", "20", check=False, timeout=60)
        coordinator.runner.cleanup()
        owned.cleanup()

def _signal_module():
    spec = importlib.util.spec_from_file_location(
        "atlas_database_orchestrator_signal_deferral", ORCHESTRATOR
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _signal_coordinator(module):
    coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.poison_reason = None
    coordinator.boundary_state = "cutover-mutated"
    return coordinator


def _record_later_signal(module, monkeypatch) -> None:
    original_exit = module._RecoverySignalDeferral.__exit__

    def exit_with_later_signal(self, exc_type, exc, traceback):
        result = original_exit(self, exc_type, exc, traceback)
        self._record(signal.SIGTERM, None)
        return result

    monkeypatch.setattr(
        module._RecoverySignalDeferral, "__exit__", exit_with_later_signal
    )


def test_body_interruption_precedes_signal_recorded_during_deferral_exit(
    monkeypatch,
) -> None:
    module = _signal_module(); coordinator = _signal_coordinator(module)
    coordinator._recover_after_live_mutation_once = lambda _enabled: (_ for _ in ()).throw(
        KeyboardInterrupt()
    )
    _record_later_signal(module, monkeypatch)

    with pytest.raises(KeyboardInterrupt):
        coordinator._recover_after_live_mutation([])


def test_deferred_signal_precedes_later_body_interruption() -> None:
    module = _signal_module(); coordinator = _signal_coordinator(module)

    def interrupt_after_signal(_enabled):
        os.kill(os.getpid(), signal.SIGTERM)
        raise KeyboardInterrupt()

    coordinator._recover_after_live_mutation_once = interrupt_after_signal
    with pytest.raises(module.SignalInterruption, match=f"signal {signal.SIGTERM}"):
        coordinator._recover_after_live_mutation([])


def test_later_exit_signal_surfaces_and_poisons_body_failure(
    monkeypatch, capsys,
) -> None:
    module = _signal_module(); coordinator = _signal_coordinator(module)
    coordinator._recover_after_live_mutation_once = lambda _enabled: (_ for _ in ()).throw(
        module.ContractError("rollback copy failed")
    )
    _record_later_signal(module, monkeypatch)

    with pytest.raises(module.SignalInterruption, match=f"signal {signal.SIGTERM}"):
        coordinator._recover_after_live_mutation([])

    assert coordinator.poison_reason and "deferred" in coordinator.poison_reason
    assert "rollback copy failed" in capsys.readouterr().err


def test_post_unmask_signal_cannot_replace_body_failure(monkeypatch, capsys) -> None:
    module = _signal_module(); coordinator = _signal_coordinator(module)
    coordinator._recover_after_live_mutation_once = lambda _enabled: (_ for _ in ()).throw(
        module.ContractError("rollback copy failed before unmask")
    )
    original_exit = module._RecoverySignalDeferral.__exit__

    def exit_then_signal(self, exc_type, exc, traceback):
        original_exit(self, exc_type, exc, traceback)
        raise module.SignalInterruption("received signal 15 after unmask")

    monkeypatch.setattr(module._RecoverySignalDeferral, "__exit__", exit_then_signal)
    with pytest.raises(module.SignalInterruption, match="signal 15 after unmask"):
        coordinator._recover_after_live_mutation([])

    assert coordinator.poison_reason and "deferred" in coordinator.poison_reason
    assert "rollback copy failed before unmask" in capsys.readouterr().err


def test_recovery_entry_interruption_retries_before_propagation() -> None:
    module = _signal_module(); coordinator = _signal_coordinator(module)
    attempts = 0

    def recover(_enabled):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            coordinator._mark_poison("recovery failed while cancellation was deferred")
            raise module.SignalInterruption("received signal 15 at recovery entry")
        coordinator.boundary_state = "recovery-proven"

    coordinator._recover_after_live_mutation = recover
    with pytest.raises(module.SignalInterruption, match="signal 15 at recovery entry"):
        coordinator._recover_cutover_failure([], module.ContractError("cutover failed"))

    assert attempts == 2
    assert coordinator.boundary_state == "recovery-proven"
    assert coordinator.poison_reason is None


def test_signal_at_cutover_mutation_transition_runs_compensation() -> None:
    module = _signal_module(); coordinator = _signal_coordinator(module)
    coordinator.plan = module.SourcePlan(True, False)
    coordinator.neo_live = "neo-live"; coordinator.weaviate_live = "weaviate-live"
    coordinator.stage = {"neo4j": "neo-stage"}
    coordinator.was_running = {}; coordinator.initial_states = {}; coordinator.rollback = {}
    coordinator.cutover_started = False; coordinator.boundary_state = "pre-cutover"
    coordinator.timeout = 5; coordinator._bounded_count = lambda *_args: 1
    coordinator._validate_neo4j_data_volume = lambda *_args: None
    running = {"neo4j-graph-db": True}
    coordinator._service_state = lambda service: module.DatabaseServiceState(
        True, running[service], running[service]
    )
    roles: list[str] = []
    coordinator._copy_volume = lambda _source, _target, role: roles.append(role)
    coordinator._verify_volume_copy = lambda *_args: None
    coordinator.runner = types.SimpleNamespace(
        create_volume=lambda role: role,
        assert_no_owned_containers=lambda: None,
        remove_volume=lambda _name: None,
        prune_retained_rollbacks=lambda *_args, **_kwargs: None,
    )
    coordinator.compose = lambda *args, **_kwargs: running.__setitem__(
        args[-1], args[0] == "up"
    )
    coordinator._begin_cutover_mutation = lambda: (_ for _ in ()).throw(
        module.SignalInterruption("received signal 15 at mutation transition")
    )

    with pytest.raises(module.SignalInterruption, match="mutation transition"):
        coordinator.cutover({})

    assert "neo4j-rollback-restore" in roles
    assert running["neo4j-graph-db"] is True
    assert coordinator.boundary_state == "recovery-proven"
    assert coordinator.poison_reason is None
