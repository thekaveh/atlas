"""Neo4j snapshots from the previous pinned release stay restorable (#1312).

New backups always record the exact pinned release. Both restore gates accept
an explicit list of releases whose dumps load into the pinned image, and
reject every other release or a mismatched image/version pair.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import types

import pytest
import yaml

from tests.test_database_backup_live_integration import (
    NEO4J_IMAGE,
    PREVIOUS_NEO4J_IMAGE,
    OwnedDocker,
    _run,
    _start_neo4j,
    exact_docker,  # noqa: F401 - pytest fixture used by the live drill
)


REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "services/neo4j/build/scripts"
OFFLINE_BACKUP = SCRIPTS / "offline-backup.sh"
OFFLINE_RESTORE = SCRIPTS / "offline-restore.sh"
SNAPSHOTS = REPO / "services/backup/init/scripts/database-snapshots.sh"
ORCHESTRATOR = REPO / "services/backup/database_orchestrator.py"
PINNED = NEO4J_IMAGE.removeprefix("neo4j:")
PREVIOUS = PREVIOUS_NEO4J_IMAGE.removeprefix("neo4j:")


def _shell_value(path: Path, name: str) -> str:
    matches = re.findall(rf'^{name}="([^"]*)"$', path.read_text(encoding="utf-8"), re.M)
    assert len(matches) == 1, (path, name)
    return matches[0]


def test_backup_restore_and_staging_all_pin_the_manifest_image() -> None:
    manifest = yaml.safe_load((REPO / "services/neo4j/service.yml").read_text())
    (image,) = [row["default"] for row in manifest["images"] if row["var"] == "NEO4J_GRAPH_DB_IMAGE"]
    assert image == NEO4J_IMAGE
    assert f'NEO4J_IMAGE = "{NEO4J_IMAGE}"' in ORCHESTRATOR.read_text(encoding="utf-8")
    pins = {
        (_shell_value(script, "EXPECTED_NEO4J_IMAGE"), _shell_value(script, "EXPECTED_NEO4J_VERSION"))
        for script in (OFFLINE_BACKUP, SNAPSHOTS)
    }
    assert pins == {(NEO4J_IMAGE, PINNED)}
    assert _shell_value(OFFLINE_RESTORE, "EXPECTED_NEO4J_VERSION") == PINNED


def test_restore_gates_share_one_release_list_ending_at_the_pin() -> None:
    releases = _shell_value(OFFLINE_RESTORE, "RESTORABLE_NEO4J_VERSIONS")
    assert releases == _shell_value(SNAPSHOTS, "RESTORABLE_NEO4J_VERSIONS")
    assert releases.split() == [PREVIOUS, PINNED]
    assert "RESTORABLE_NEO4J_VERSIONS" not in OFFLINE_BACKUP.read_text(encoding="utf-8")


def _offline_restore(
    tmp_path: Path, image: str, version: str, runtime: str = PINNED
) -> tuple[subprocess.CompletedProcess[str], str]:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    metadata = ["snapshot_state=complete", f"neo4j_image={image}", f"neo4j_version={version}"]
    for database in ("system", "neo4j"):
        payload = f"{database}-dump".encode()
        (snapshot / f"{database}.dump").write_bytes(payload)
        digest = subprocess.run(
            ["sha256sum", str(snapshot / f"{database}.dump")],
            text=True, capture_output=True, check=True,
        ).stdout.split()[0]
        metadata += [f"{database}_sha256={digest}", f"{database}_bytes={len(payload)}"]
    (snapshot / "snapshot.metadata").write_text("\n".join(metadata) + "\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    admin_log = tmp_path / "neo4j-admin.log"
    (fake_bin / "timeout").write_text('#!/bin/sh\nshift 5\nexec "$@"\n', encoding="utf-8")
    (fake_bin / "neo4j").write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    (fake_bin / "neo4j-admin").write_text(
        f'#!/bin/sh\n[ "$1" = --version ] && {{ echo {runtime}; exit 0; }}\n'
        f'echo "$*" >>"{admin_log}"\n',
        encoding="utf-8",
    )
    for executable in fake_bin.iterdir():
        executable.chmod(0o755)
    result = subprocess.run(
        ["bash", str(OFFLINE_RESTORE), str(snapshot)],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
            "NEO4J_REPORT_ROOT": str(tmp_path / "reports"),
            "BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS": "5",
        },
        text=True, capture_output=True, check=False, timeout=30,
    )
    return result, admin_log.read_text(encoding="utf-8") if admin_log.exists() else ""


@pytest.mark.parametrize("version", [PREVIOUS, PINNED])
def test_offline_restore_loads_snapshots_from_each_restorable_release(
    tmp_path: Path, version: str
) -> None:
    result, admin = _offline_restore(tmp_path, f"neo4j:{version}", version)

    assert result.returncode == 0, result.stderr
    for step in ("database load --info system", "database load neo4j", "database check neo4j"):
        assert step in admin


@pytest.mark.parametrize(
    ("image", "version"),
    [
        ("neo4j:5.26.29", "5.26.29"),
        ("neo4j:5.26.3", "5.26.3"),
        (f"neo4j:{PREVIOUS}", PINNED),
        (f"neo4j:{PINNED}-enterprise", PINNED),
    ],
)
def test_offline_restore_rejects_other_releases_before_any_load(
    tmp_path: Path, image: str, version: str
) -> None:
    result, admin = _offline_restore(tmp_path, image, version)

    assert result.returncode == 65
    assert "is not a restorable release" in result.stderr
    assert admin == ""


def test_offline_restore_still_requires_the_exact_pinned_runtime(tmp_path: Path) -> None:
    result, admin = _offline_restore(tmp_path, f"neo4j:{PREVIOUS}", PREVIOUS, runtime=PREVIOUS)

    assert result.returncode == 78
    assert "exact version mismatch" in result.stderr
    assert admin == ""


@pytest.mark.parametrize("runtime", [f"{PINNED}0", f"Neo4j {PINNED}"])
def test_offline_scripts_need_the_exact_runtime_not_a_substring(tmp_path: Path, runtime: str) -> None:
    """Both offline scripts refuse a runtime whose version merely contains the
    pin (#1388); this replaced a grep for one glob spelling."""
    restored, admin = _offline_restore(tmp_path, f"neo4j:{PINNED}", PINNED, runtime=runtime)
    fake_bin = tmp_path / "bin"
    backed_up = subprocess.run(
        ["bash", str(OFFLINE_BACKUP)],
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
             "BACKUP_TIMESTAMP": "20260101_000000", "NEO4J_SNAPSHOT_ROOT": str(tmp_path / "out"),
             "BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS": "5"},
        text=True, capture_output=True, check=False, timeout=30,
    )

    for result in (restored, backed_up):
        assert result.returncode == 78, result.stderr
        assert "exact version mismatch" in result.stderr
    assert admin == "" and not (tmp_path / "out").exists()


def test_previous_release_dump_restores_into_the_pinned_image(
    exact_docker: None, tmp_path: Path
) -> None:
    """A dump taken by the previous release's own offline backup serves on the pin."""
    backup = OFFLINE_BACKUP.read_text(encoding="utf-8")
    pins = {
        f'EXPECTED_NEO4J_IMAGE="{NEO4J_IMAGE}"\n': f'EXPECTED_NEO4J_IMAGE="{PREVIOUS_NEO4J_IMAGE}"\n',
        f'EXPECTED_NEO4J_VERSION="{PINNED}"\n': f'EXPECTED_NEO4J_VERSION="{PREVIOUS}"\n',
    }
    previous_scripts = tmp_path / "previous-scripts"
    previous_scripts.mkdir()
    for current, previous in pins.items():
        assert backup.count(current) == 1
        backup = backup.replace(current, previous)
    (previous_scripts / "offline-backup.sh").write_text(backup, encoding="utf-8")
    previous_scripts.chmod(0o755)

    owned = OwnedDocker("neo-upgrade")
    network = owned.network()
    source = owned.volume("source")
    snapshots = owned.volume("snapshots")
    restored = owned.volume("restored")
    source_name = owned.container("source")
    restored_name = owned.container("restored")
    timestamp = "20260930_010203"
    try:
        _start_neo4j(owned, source_name, network, source, image=PREVIOUS_NEO4J_IMAGE)
        _run(
            "docker", "exec", source_name, "cypher-shell", "-d", "neo4j",
            "CREATE (:AtlasDrill {generation:'previous-release'})",
        )
        _run("docker", "stop", "--time", "20", source_name, timeout=30)
        _run("docker", "rm", source_name)
        owned.run_helper(
            "offline-backup-previous", ["--network", "none",
            "-e", f"BACKUP_TIMESTAMP={timestamp}",
            "-e", "BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS=60",
            "-v", f"{source}:/data", "-v", f"{snapshots}:/snapshot",
            "-v", f"{previous_scripts}:/scripts:ro", "--entrypoint", "bash",
            PREVIOUS_NEO4J_IMAGE, "/scripts/offline-backup.sh"],
        )
        owned.run_helper(
            "offline-restore-pinned", ["--network", "none",
            "-e", "BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS=60",
            "-v", f"{restored}:/data", "-v", f"{snapshots}:/snapshot",
            "-v", f"{SCRIPTS}:/scripts:ro", "--entrypoint", "bash", NEO4J_IMAGE,
            "/scripts/offline-restore.sh", f"/snapshot/{timestamp}"],
        )
        _start_neo4j(owned, restored_name, network, restored)
        query = _run(
            "docker", "exec", restored_name, "cypher-shell", "-d", "neo4j",
            "MATCH (n:AtlasDrill) RETURN n.generation",
        )
        assert "previous-release" in query.stdout
    finally:
        owned.cleanup()


def _orchestrator():
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "atlas_database_orchestrator_boundary", ORCHESTRATOR
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_boundary_lock_location_ignores_per_session_tmpdir(tmp_path, monkeypatch) -> None:
    """A cron backup (no TMPDIR) and a terminal restore (TMPDIR=/var/folders/…)
    must contend for the same lock file."""
    module = _orchestrator()
    monkeypatch.delenv("ATLAS_DATABASE_LOCK_DIR", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path / "session-a"))
    monkeypatch.setattr(module.pwd, "getpwuid", lambda _uid: types.SimpleNamespace(pw_dir=str(tmp_path)))
    first = module.database_lock_directory()
    monkeypatch.setenv("TMPDIR", str(tmp_path / "session-b"))
    assert module.database_lock_directory() == first
    assert first == tmp_path / ".atlas-database-locks"
    monkeypatch.setenv("ATLAS_DATABASE_LOCK_DIR", str(tmp_path))
    assert module.database_lock_directory() == tmp_path


def test_retention_counts_read_the_env_file_like_other_settings(tmp_path, monkeypatch) -> None:
    """README §3 lists the retention counts as .env settings; they used to
    be read from the process environment only, so `.env` was ignored."""
    module = _orchestrator()
    (tmp_path / ".env").write_text(
        "BACKUP_LOCAL_SNAPSHOT_RETENTION_COUNT=10\n", encoding="utf-8"
    )
    monkeypatch.delenv("BACKUP_LOCAL_SNAPSHOT_RETENTION_COUNT", raising=False)
    coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.env_values = module._env_file_values(tmp_path)
    assert coordinator._bounded_count("BACKUP_LOCAL_SNAPSHOT_RETENTION_COUNT", "3", 100) == 10


def test_env_parser_keeps_hash_inside_quoted_values(tmp_path) -> None:
    """GRAPH_DB_AUTH="neo4j/pa#ss" reached the orchestrator as neo4j/pa, so
    every Neo4j restore's credential check failed."""
    module = _orchestrator()
    (tmp_path / ".env").write_text(
        'GRAPH_DB_AUTH="neo4j/pa#ss"\n'
        "SINGLE='a#b'\n"
        "PLAIN=value # comment\n"
        "TIGHT=a#b\n",
        encoding="utf-8",
    )
    values = module._env_file_values(tmp_path)
    assert values["GRAPH_DB_AUTH"] == "neo4j/pa#ss"
    assert values["SINGLE"] == "a#b"
    assert values["PLAIN"] == "value"
    assert values["TIGHT"] == "a#b"


def test_unwritable_lock_directory_names_the_override(tmp_path, monkeypatch) -> None:
    module = _orchestrator()
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv("ATLAS_DATABASE_LOCK_DIR", str(blocker / "locks"))
    with pytest.raises(module.ContractError, match="ATLAS_DATABASE_LOCK_DIR"):
        module.database_lock_directory()


def test_auto_restore_never_overwrites_a_populated_database(tmp_path: Path) -> None:
    """Loading the newest backup on every boot rolled the live graph back,
    discarding every write since the backup."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    called = tmp_path / "loaded"
    fake_admin = bin_dir / "neo4j-admin"
    fake_admin.write_text(f'#!/bin/sh\ntouch "{called}"\ncat >/dev/null\n', encoding="utf-8")
    fake_admin.chmod(0o755)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "backup_20260101.dump").write_bytes(b"dump")
    database = tmp_path / "databases" / "neo4j"
    database.mkdir(parents=True)
    (database / "neostore").write_bytes(b"live")

    result = subprocess.run(
        ["bash", str(SCRIPTS / "auto_restore.sh")],
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "NEO4J_SNAPSHOT_DIR": str(snapshot),
            "NEO4J_DATABASE_DIR": str(database),
        },
        text=True, capture_output=True, check=False, timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "skipping automatic restore" in result.stdout
    assert not called.exists(), "a populated database was overwritten"


def test_auto_restore_retries_after_a_failed_load(tmp_path: Path) -> None:
    """A failed load leaves partial files; the incomplete-restore marker must
    keep the populated-database skip from booting that partial store."""
    database = tmp_path / "databases" / "neo4j"
    database.mkdir(parents=True)
    (database / "partial").write_bytes(b"half")
    marker = tmp_path / ".atlas-restore-incomplete"
    marker.touch()

    result = subprocess.run(
        ["bash", str(SCRIPTS / "auto_restore.sh")],
        env={
            **os.environ,
            "NEO4J_SNAPSHOT_DIR": str(tmp_path / "snapshot"),
            "NEO4J_DATABASE_DIR": str(database),
            "NEO4J_RESTORE_MARKER": str(marker),
        },
        text=True, capture_output=True, check=False, timeout=30,
    )

    assert "skipping automatic restore" not in result.stdout


def test_auto_restore_refuses_a_partial_store_with_no_dump_left(tmp_path: Path) -> None:
    database = tmp_path / "databases" / "neo4j"
    database.mkdir(parents=True)
    (database / "partial").write_bytes(b"half")
    marker = tmp_path / ".atlas-restore-incomplete"
    marker.touch()
    (tmp_path / "snapshot").mkdir()

    result = subprocess.run(
        ["bash", str(SCRIPTS / "auto_restore.sh")],
        env={
            **os.environ,
            "NEO4J_SNAPSHOT_DIR": str(tmp_path / "snapshot"),
            "NEO4J_DATABASE_DIR": str(database),
            "NEO4J_RESTORE_MARKER": str(marker),
        },
        text=True, capture_output=True, check=False, timeout=30,
    )

    assert result.returncode == 1
    assert "unfinished restore" in result.stderr


def test_manual_restore_marks_an_unfinished_load() -> None:
    script = (SCRIPTS / "restore.sh").read_text(encoding="utf-8")
    load = script.index("neo4j-admin database load")
    assert script.index('touch "${RESTORE_MARKER}"') < load
    assert 'rm -f "${RESTORE_MARKER}"' in script[load:]


def _legacy_marker_commands(fake_bin):
    """Delegate normal filesystem operations and inject only marker failures."""
    for command, argument in (("mkdir", '"$2" = "$MARKER_PARENT"'),
                              ("touch", '"$1" = "$NEO4J_RESTORE_MARKER"'),
                              ("rm", '"$2" = "$NEO4J_RESTORE_MARKER"')):
        actual = shutil.which(command)
        assert actual
        (fake_bin / command).write_text(
            f'#!/bin/sh\nif [ "$FAIL_MARKER_STEP" = {command} ] && [ {argument} ]; then\n'
            f'  echo "synthetic {command} failure" >&2; exit 73\nfi\n'
            f'exec "{actual}" "$@"\n',
            encoding="utf-8",
        )


@pytest.fixture(params=["auto_restore.sh", "restore.sh"])
def legacy_restore(tmp_path, request):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    dump = snapshot / "backup_20261028.dump"
    dump.write_bytes(b"synthetic dump")
    database = tmp_path / "databases" / "neo4j"
    database.mkdir(parents=True)
    marker = tmp_path / "markers" / ".atlas-restore-incomplete"
    admin_log = tmp_path / "admin.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _legacy_marker_commands(fake_bin)
    # The image uses GNU find's -printf, unavailable on a macOS test host.
    (fake_bin / "find").write_text(
        '#!/bin/sh\nprintf "1 %s\\n" "$SYNTHETIC_DUMP"\n', encoding="utf-8"
    )
    (fake_bin / "neo4j").write_text('#!/bin/sh\nexit 3\n', encoding="utf-8")
    (fake_bin / "neo4j-admin").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$SYNTHETIC_ADMIN_LOG"\n'
        'cat >/dev/null\nprintf partial > "$NEO4J_DATABASE_DIR/neostore"\n'
        '[ "$FAIL_LOAD" != 1 ]\n', encoding="utf-8",
    )
    for executable in fake_bin.iterdir():
        executable.chmod(0o755)
    source = SCRIPTS / request.param
    if request.param == "restore.sh":
        # The legacy manual command fixes /snapshot; rebase only that path.
        body = source.read_text(encoding="utf-8")
        assert body.count("SNAPSHOT_DIR=/snapshot") == 1
        source = tmp_path / "restore.sh"
        source.write_text(body.replace("SNAPSHOT_DIR=/snapshot", f'SNAPSHOT_DIR="{snapshot}"'), encoding="utf-8")
    env = {
        "PATH": f"{fake_bin}{os.pathsep}{os.defpath}",
        "NEO4J_SNAPSHOT_DIR": str(snapshot),
        "NEO4J_DATABASE_DIR": str(database),
        "NEO4J_RESTORE_MARKER": str(marker),
        "MARKER_PARENT": str(marker.parent),
        "SYNTHETIC_DUMP": str(dump),
        "SYNTHETIC_ADMIN_LOG": str(admin_log),
        "FAIL_MARKER_STEP": "",
        "FAIL_LOAD": "0",
    }

    def run(**overrides):
        return subprocess.run(
            ["bash", str(source)], env={**env, **overrides}, cwd=tmp_path,
            text=True, capture_output=True, check=False, timeout=10,
        )

    return types.SimpleNamespace(run=run, marker=marker, database=database,
                                 admin_log=admin_log, automatic=request.param == "auto_restore.sh")


@pytest.mark.parametrize("failure", ["mkdir", "touch"])
def test_legacy_restore_refuses_load_without_recovery_marker(legacy_restore, failure):
    case = legacy_restore
    for _ in range(2):
        result = case.run(FAIL_MARKER_STEP=failure, FAIL_LOAD="1")
        assert result.returncode != 0
        assert "restored successfully" not in result.stdout
        assert not case.admin_log.exists(), "destructive load ran without a recovery marker"
        assert not any(case.database.iterdir())


def test_legacy_restore_refuses_success_until_marker_is_cleared(legacy_restore):
    case = legacy_restore
    for _ in range(2):
        result = case.run(FAIL_MARKER_STEP="rm")
        assert result.returncode != 0
        assert "restored successfully" not in result.stdout
        assert case.marker.is_file()
        assert "skipping automatic restore" not in result.stdout
    assert len(case.admin_log.read_text().splitlines()) == 2


def test_legacy_restore_success_and_failed_load_retry(legacy_restore):
    case = legacy_restore
    failed = case.run(FAIL_LOAD="1")
    assert failed.returncode != 0
    assert case.marker.is_file() and any(case.database.iterdir())
    retried = case.run()
    assert retried.returncode == 0, retried.stderr
    assert "restored successfully" in retried.stdout
    assert not case.marker.exists()
    assert len(case.admin_log.read_text().splitlines()) == 2
    if case.automatic:
        _assert_populated_restore_skips(case)


def _assert_populated_restore_skips(case):
    populated = case.run()
    assert populated.returncode == 0
    assert "skipping automatic restore" in populated.stdout
    assert len(case.admin_log.read_text().splitlines()) == 2


def _offline_backup_signal_sender():
    return '''
pid = int(Path(os.environ["OWNED_PID_FILE"]).read_text())
assert os.getppid() == pid, (os.getppid(), pid)
marker = Path(os.environ["SIGNAL_MARKER"])
if os.environ["FIXTURE_PHASE"] == phase and not marker.exists():
    marker.write_text("owned signal sent")
    os.kill(pid, int(os.environ["FIXTURE_SIGNAL"]))
'''


def _offline_backup_recorders(fake_bin):
    import sys

    python = f"#!{sys.executable}\n"
    sender = _offline_backup_signal_sender()
    scripts = {
        "timeout": '#!/bin/sh\nshift 5\nexec "$@"\n',
        "neo4j": "#!/bin/sh\nexit 3\n",
        "date": python + 'import os\nfrom pathlib import Path\nphase = "date"\n'
        + sender + '\nprint("2026-10-29T12:00:00Z")\n',
        "sha256sum": python + 'import hashlib,sys\nfrom pathlib import Path\n'
        + 'print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest()+"  "+sys.argv[1])\n',
        "neo4j-admin": python + '''import os,sys
from pathlib import Path
args = sys.argv[1:]
if args == ["--version"]:
''' + "    print(" + repr(PINNED) + ")\n" + '''    sys.exit(0)
assert args[:2] == ["database", "dump"], args
if os.environ["FIXTURE_MODE"] == "dump-error":
    sys.exit(42)
target = next(arg.split("=", 1)[1] for arg in args if arg.startswith("--to-path="))
(Path(target) / (args[2] + ".dump")).write_bytes(b"owned synthetic archive")
phase = "dump"
''' + sender,
    }
    for name, body in scripts.items():
        path = fake_bin / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o700)


@pytest.fixture
def offline_backup_signal(tmp_path):
    import signal

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _offline_backup_recorders(fake_bin)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    pid_file = tmp_path / "pid"
    marker = tmp_path / "signal"

    def run(mode="ordinary", phase="none", name="TERM"):
        env = {
            "PATH": f"{fake_bin}{os.pathsep}{os.defpath}", "HOME": str(tmp_path),
            "FIXTURE_MODE": mode, "FIXTURE_PHASE": phase,
            "FIXTURE_SIGNAL": str(getattr(signal, "SIG" + name)),
            "OWNED_PID_FILE": str(pid_file), "SIGNAL_MARKER": str(marker),
            "NEO4J_SNAPSHOT_ROOT": str(snapshot), "BACKUP_TIMESTAMP": "20261029_120000",
            "BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS": "120",
        }
        process = subprocess.Popen(
            ["bash", str(OFFLINE_BACKUP)], env=env, cwd=tmp_path,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
        )
        pid_file.write_text(str(process.pid), encoding="utf-8")
        try:
            stdout, stderr = process.communicate(timeout=10)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
        return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)

    return types.SimpleNamespace(run=run, snapshot=snapshot, marker=marker)


@pytest.mark.parametrize("name", ["HUP", "INT", "TERM"])
@pytest.mark.parametrize("phase", ["date", "dump"])
def test_offline_backup_signals_fail_and_remove_unpublished_stage(offline_backup_signal, name, phase):
    case = offline_backup_signal
    result = case.run(mode="signal", phase=phase, name=name)
    assert case.marker.read_text() == "owned signal sent"
    assert result.returncode == {"HUP": 129, "INT": 130, "TERM": 143}[name]
    assert not list(case.snapshot.iterdir())
    assert "neo4j backup: complete" not in result.stdout


@pytest.mark.parametrize("mode", ["ordinary", "dump-error"])
def test_offline_backup_keeps_success_and_failure_publication_contract(offline_backup_signal, mode):
    case = offline_backup_signal
    result = case.run(mode=mode)
    assert not case.marker.exists()
    assert not list(case.snapshot.glob(".atlas-*"))
    assert result.returncode == (0 if mode == "ordinary" else 42), result.stderr
    if mode == "dump-error":
        assert not list(case.snapshot.iterdir())
        assert "neo4j backup: complete" not in result.stdout
        return
    _assert_offline_backup_publication(case, result)


def _assert_offline_backup_publication(case, result):
    import hashlib

    published = case.snapshot / "20261029_120000"
    assert sorted(path.name for path in published.iterdir()) == ["neo4j.dump", "snapshot.metadata", "system.dump"]
    metadata = dict(line.split("=", 1) for line in (published / "snapshot.metadata").read_text().splitlines())
    for database in ["system", "neo4j"]:
        payload = (published / (database + ".dump")).read_bytes()
        assert payload == b"owned synthetic archive"
        assert metadata[database + "_sha256"] == hashlib.sha256(payload).hexdigest()
        assert metadata[database + "_bytes"] == str(len(payload))
    assert metadata["snapshot_state"] == "complete" and metadata["neo4j_version"] == PINNED
    assert result.stdout.splitlines() == [
        f"neo4j backup: dumping offline system database with neo4j:{PINNED}",
        f"neo4j backup: dumping offline neo4j database with neo4j:{PINNED}",
        f"neo4j backup: complete -> {published}",
    ]
