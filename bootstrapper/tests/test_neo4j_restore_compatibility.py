"""Neo4j snapshots from the previous pinned release stay restorable (#1312).

New backups always record the exact pinned release. Both restore gates accept
an explicit list of releases whose dumps load into the pinned image, and
reject every other release or a mismatched image/version pair.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

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
