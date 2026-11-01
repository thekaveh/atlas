"""Weaviate snapshots from the previous pinned release stay restorable (#1286).

New backups always record the exact pinned release. The restore gate accepts an
explicit list of releases whose native backups load into the pinned image, and
rejects every other release or a mismatched image/version pair (exercised
through restore-databases.sh in test_database_volume_backup_contracts.py).
"""
from __future__ import annotations

from pathlib import Path
import os
import secrets
import shutil
import subprocess
import re
import uuid

import pytest
import yaml

from tests.test_database_backup_repair_contracts import _shell_function

from tests.test_database_backup_live_integration import (
    PREVIOUS_WEAVIATE_IMAGE,
    WEAVIATE_IMAGE,
    OwnedDocker,
    _start_weaviate,
    _wait_weaviate_operation,
    _weaviate_json,
    exact_docker,  # noqa: F401 - pytest fixture used by the live drill
)


REPO = Path(__file__).resolve().parents[2]
SNAPSHOTS = REPO / "services/backup/init/scripts/database-snapshots.sh"
RESTORE_DATABASES = REPO / "services/backup/init/scripts/restore-databases.sh"
ORCHESTRATOR = REPO / "services/backup/database_orchestrator.py"
PINNED = WEAVIATE_IMAGE.rpartition(":")[2]
PREVIOUS = PREVIOUS_WEAVIATE_IMAGE.rpartition(":")[2]


def _shell_value(name: str) -> str:
    text = SNAPSHOTS.read_text(encoding="utf-8")
    matches = re.findall(rf'^{name}="([^"]*)"$', text, re.M)
    assert len(matches) == 1, name
    return matches[0]


def test_backup_restore_and_runtime_pin_one_weaviate_release() -> None:
    manifest = yaml.safe_load((REPO / "services/weaviate/service.yml").read_text())
    (image,) = [
        row["default"] for row in manifest["images"] if row["var"] == "WEAVIATE_IMAGE"
    ]
    assert image == WEAVIATE_IMAGE
    assert f'WEAVIATE_IMAGE = "{WEAVIATE_IMAGE}"' in ORCHESTRATOR.read_text(
        encoding="utf-8"
    )
    assert (
        _shell_value("EXPECTED_WEAVIATE_IMAGE"),
        _shell_value("EXPECTED_WEAVIATE_VERSION"),
    ) == (WEAVIATE_IMAGE, PINNED)
    assert _shell_value("RESTORABLE_WEAVIATE_VERSIONS").split() == [PREVIOUS, PINNED]


def test_previous_release_backup_restores_into_the_pinned_image(
    exact_docker: None,
) -> None:
    """A native backup the previous release took serves on the pin, vectors intact."""
    owned = OwnedDocker("weaviate-upgrade")
    network = owned.network("source-network")
    restore_network = owned.network("restore-network")
    source = owned.volume("source")
    backups = owned.volume("backups")
    restored = owned.volume("restored")
    source_name = owned.container("source")
    restored_name = owned.container("restored")
    snapshot_id = f"atlas-upgrade-{owned.token}"
    object_id = str(uuid.uuid4())
    vector = [0.1, 0.2, 0.3, 0.4]
    try:
        _start_weaviate(
            owned, source_name, network, source, backups, image=PREVIOUS_WEAVIATE_IMAGE
        )
        assert _weaviate_json(source_name, "/v1/meta")["version"] == PREVIOUS
        _weaviate_json(
            source_name, "/v1/schema",
            body={
                "class": "AtlasUpgradeDrill", "vectorizer": "none",
                "properties": [{"name": "generation", "dataType": ["text"]}],
            },
        )
        _weaviate_json(
            source_name, "/v1/objects",
            body={
                "class": "AtlasUpgradeDrill", "id": object_id, "vector": vector,
                "properties": {"generation": "previous-release"},
            },
        )
        started = _weaviate_json(
            source_name, "/v1/backups/filesystem", body={"id": snapshot_id}
        )
        _wait_weaviate_operation(
            source_name, f"/v1/backups/filesystem/{snapshot_id}", started
        )

        _start_weaviate(owned, restored_name, restore_network, restored, backups)
        restore_path = f"/v1/backups/filesystem/{snapshot_id}/restore"
        restoring = _weaviate_json(restored_name, restore_path, body={})
        _wait_weaviate_operation(restored_name, restore_path, restoring)

        assert _weaviate_json(restored_name, "/v1/meta")["version"] == PINNED
        restored_object = _weaviate_json(
            restored_name, f"/v1/objects/AtlasUpgradeDrill/{object_id}?include=vector"
        )
        assert restored_object["properties"] == {"generation": "previous-release"}
        assert restored_object["vector"] == pytest.approx(vector)
        nearest = _weaviate_json(
            restored_name, "/v1/graphql",
            body={"query": (
                "{ Get { AtlasUpgradeDrill(nearVector: {vector: [0.1, 0.2, 0.3, 0.4]}, "
                "limit: 1) { generation } } }"
            )},
        )
        assert nearest["data"]["Get"]["AtlasUpgradeDrill"] == [
            {"generation": "previous-release"}
        ]
    finally:
        owned.cleanup()

_STATUS_WGET = r"""#!/bin/sh
out=/dev/null method=GET
while [ "$#" -gt 1 ]; do
  case "$1" in -O) out=$2; shift ;; --method=*) method=${1#--method=} ;; --post-data=*) method=POST ;; esac
  shift
done
printf '%s %s\n' "$method" "$1" >>"$WGET_LOG"
case "$method:$1" in
  DELETE:*) : >"$WGET_LOG.cancelled"; printf '{}' >"$out" ;;
  GET:*/v1/meta) printf '{"version":"%s"}' "$WEAVIATE_VERSION" >"$out" ;;
  POST:*)
    mkdir -p "$DATABASE_WEAVIATE_SNAPSHOT_ROOT/$FIXTURE_SNAPSHOT_ID"
    printf 'owned snapshot' >"$DATABASE_WEAVIATE_SNAPSHOT_ROOT/$FIXTURE_SNAPSHOT_ID/backup.json"
    printf '{"status":"STARTED"}' >"$out"
    ;;
  *)
    if [ -e "$WGET_LOG.cancelled" ]; then printf '{"status":"CANCELED"}' >"$out"; exit 0; fi
    case "$STATUS_RESPONSE" in
      transport-success) printf '{"status":"SUCCESS"}' >"$out"; exit 4 ;;
      truncated-success) printf '{"status":"SUCCESS"' >"$out"; exit 4 ;;
      empty-error) : >"$out"; exit 4 ;;
      over-limit)
        { printf '{"status":"SUCCESS","padding":"'; head -c 65536 /dev/zero | tr '\000' x; printf '"}'; } >"$out"
        ;;
      failed) printf '{"status":"FAILED"}' >"$out" ;;
      *) printf '{"status":"SUCCESS"}' >"$out" ;;
    esac
    ;;
esac
"""


def _weaviate_status_capture(tmp_path: Path, mode: str, version: str | None = None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wget = bin_dir / "wget"
    wget.write_text(_STATUS_WGET, encoding="utf-8")
    wget.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    log = tmp_path / "wget.log"
    snapshot_id = f"atlas-20260101_000000-{'b' * 32}"
    version = version or re.search(r'^EXPECTED_WEAVIATE_VERSION="([^"]+)"', SNAPSHOTS.read_text(), re.M)[1]
    script = (
        f"set -eu\n. '{SNAPSHOTS}'\nrun_bounded() {{ \"$@\"; }}\n"
        f"capture_database_snapshots '{work}' 20260101_000000 {'b' * 32}\n"
    )
    result = subprocess.run(
        ["sh", "-c", script], text=True, capture_output=True, check=False, timeout=30,
        env={"PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
             "WGET_LOG": str(log), "WEAVIATE_VERSION": version,
             "STATUS_RESPONSE": mode, "FIXTURE_SNAPSHOT_ID": snapshot_id,
             "BACKUP_MANIFEST_HMAC_KEY": "5" * 64, "BACKUP_DEPLOYMENT_ID": "atlas-test",
             "BACKUP_NEO4J_SOURCE": "disabled", "BACKUP_WEAVIATE_SOURCE": "container",
             "DATABASE_WEAVIATE_SNAPSHOT_ROOT": str(tmp_path / "weaviate"),
             "BACKUP_COMMAND_TIMEOUT_SECONDS": "5", "BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS": "5"},
    )
    return result, work, log.read_text().splitlines(), snapshot_id


@pytest.mark.parametrize("mode, error", [
    ("transport-success", None), ("truncated-success", None),
    ("over-limit", "response exceeded the 64 KiB contract"),
    ("empty-error", None), ("failed", "Weaviate backup failed"),
])
def test_weaviate_capture_rejects_invalid_status_response(tmp_path: Path, mode: str, error: str | None):
    result, work, calls, snapshot_id = _weaviate_status_capture(tmp_path, mode)
    assert result.returncode == 1, result.stdout + result.stderr
    _assert_no_database_publication(work)
    assert "captured Neo4j" not in result.stdout
    url = f"http://weaviate:8080/v1/backups/filesystem/{snapshot_id}"
    assert calls[-2:] == [f"DELETE {url}", f"GET {url}"]
    assert sum(call.startswith("DELETE ") for call in calls) == 1
    assert "did not settle" not in result.stderr
    if error is None:
        # A wget transport failure fails the capture before any body diagnostic.
        assert "backup: Weaviate" not in result.stderr, result.stderr
    else:
        assert error in result.stderr


def _assert_no_database_publication(work: Path):
    assert not (work / "databases.manifest").exists()
    assert not (work / "databases.complete").exists()
    assert not (work / "weaviate.snapshot.tar.gz").exists()


def test_weaviate_capture_preserves_successful_status_response(tmp_path: Path):
    result, work, calls, snapshot_id = _weaviate_status_capture(tmp_path, "success")
    assert result.returncode == 0, result.stderr
    assert (work / "databases.complete").exists()
    assert "weaviate_state=complete\n" in (work / "databases.manifest").read_text()
    assert (work / "weaviate.snapshot.tar.gz").exists()
    assert "captured Neo4j (offline) and Weaviate (online) snapshots" in result.stdout
    assert calls == ["GET http://weaviate:8080/v1/meta",
                     "POST http://weaviate:8080/v1/backups/filesystem",
                     f"GET http://weaviate:8080/v1/backups/filesystem/{snapshot_id}"]


@pytest.mark.parametrize("method", ["POST", "GET", "DELETE"])
def test_weaviate_request_preserves_transport_error_in_condition(tmp_path: Path, method: str):
    response = tmp_path / "response"
    harness = (
        "set -eu\nrun_bounded() { if [ \"$1\" = wget ]; then "
        "printf '{\"status\":\"SUCCESS\"}' >\"$4\"; return 4; fi; \"$@\"; }\n"
        + _shell_function(SNAPSHOTS, "weaviate_request")
        + f"if weaviate_request {method} http://weaviate.test/status '{response}' '{{}}'; then exit 0; else exit $?; fi\n"
    )
    result = subprocess.run(["sh", "-c", harness], text=True, capture_output=True, check=False, timeout=5)
    assert result.returncode == 4, result.stderr
    assert response.read_text() == '{"status":"SUCCESS"}'


def test_weaviate_capture_refuses_an_unpinned_server_before_any_backup(tmp_path: Path):
    """The previous release stays restorable, but a new capture needs the exact pin."""
    result, work, calls, _snapshot_id = _weaviate_status_capture(tmp_path, "success", PREVIOUS)
    assert result.returncode == 78, result.stderr
    assert f"got Weaviate {PREVIOUS}" in result.stderr
    _assert_no_database_publication(work)
    assert calls == ["GET http://weaviate:8080/v1/meta"]


@pytest.mark.parametrize("pair", [
    (WEAVIATE_IMAGE, PINNED, 0), (PREVIOUS_WEAVIATE_IMAGE, PREVIOUS, 0),
    (PREVIOUS_WEAVIATE_IMAGE, PINNED, 1), (WEAVIATE_IMAGE, PREVIOUS, 1),
    ("cr.weaviate.io/semitechnologies/weaviate:1.38.12", PINNED, 1),
])
def test_signed_snapshot_gate_needs_an_agreeing_restorable_weaviate_pair(pair):
    """restore-databases.sh admits a signed set through this predicate (#1286)."""
    image, version, status = pair
    result = subprocess.run(
        ["sh", "-c", '. "$1"; weaviate_snapshot_is_restorable "$2" "$3"', "sh", str(SNAPSHOTS), image, version],
        text=True, capture_output=True, check=False, timeout=10,
    )
    assert result.returncode == status, result.stderr


# --- restore-databases.sh refuses unsafe requests before any S3 access -------
def _restore_prepare(tmp_path: Path, overrides: dict[str, str], mode: str = "prepare"):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    mc = bin_dir / "mc"
    mc.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >>"{tmp_path / "mc.log"}"\nexit 22\n', encoding="utf-8")
    mc.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin", "BACKUP_TIMESTAMP": "20260830_010203",
        "BACKUP_MANIFEST_HMAC_KEY": "6" * 64, "BACKUP_DEPLOYMENT_ID": "atlas-test",
        "BACKUP_S3_MODE": "external", "BACKUP_S3_ENDPOINT": "https://s3.example.test",
        "BACKUP_S3_ACCESS_KEY": "access", "BACKUP_S3_SECRET_KEY": "secret", **overrides,
    }
    return subprocess.run(
        ["sh", str(RESTORE_DATABASES), mode], env=env,
        text=True, capture_output=True, check=False, timeout=30,
    )


@pytest.mark.parametrize("case", [
    ({"BACKUP_RESTORE_TOKEN": "../x"}, "prepare", "invalid ownership token"),
    ({"BACKUP_RESTORE_TOKEN": "e" * 31}, "prepare", "invalid ownership token"),
    ({"BACKUP_RESTORE_TOKEN": "e" * 33}, "prepare", "invalid ownership token"),
    ({"BACKUP_RESTORE_TOKEN": "E" * 32}, "prepare", "invalid ownership token"),
    ({}, "apply", "only prepare mode is supported"),
    ({"BACKUP_NEO4J_SOURCE": "localhost"}, "prepare", "localhost source is unsupported"),
    ({"BACKUP_NEO4J_SOURCE": "external"}, "prepare", "invalid Neo4j source"),
    ({"BACKUP_WEAVIATE_SOURCE": "CONTAINER"}, "prepare", "invalid Weaviate source"),
], ids=["path-token", "short-token", "long-token", "uppercase-token", "apply-mode",
        "localhost-source", "neo4j-source", "weaviate-source"])
def test_restore_prepare_rejects_unsafe_requests_before_s3(tmp_path: Path, case):
    overrides, mode, message = case
    result = _restore_prepare(tmp_path, {"BACKUP_RESTORE_TOKEN": "e" * 32, **overrides}, mode)
    assert result.returncode == 64, result.stderr
    assert message in result.stderr
    assert not (tmp_path / "mc.log").exists()


def test_restore_prepare_refuses_a_symlinked_restore_root(tmp_path: Path):
    token = secrets.token_hex(16)
    root = Path(f"/tmp/atlas-database-restore-test-{token}")
    target = tmp_path / "target"
    target.mkdir()
    root.symlink_to(target)
    try:
        result = _restore_prepare(tmp_path, {"BACKUP_RESTORE_TOKEN": token, "DATABASE_RESTORE_ROOT": str(root)})
        assert result.returncode == 64, result.stderr
        assert "restore root must not be a symlink" in result.stderr
        assert not list(target.iterdir())
        assert not (tmp_path / "mc.log").exists()
    finally:
        root.unlink()


def test_restore_prepare_refuses_an_existing_private_stage(tmp_path: Path):
    token = secrets.token_hex(16)
    root = Path(f"/tmp/atlas-database-restore-test-{token}")
    stage = root / f"restore-{token}"
    stage.mkdir(parents=True)
    (stage / "keep").write_text("earlier stage\n", encoding="utf-8")
    try:
        result = _restore_prepare(tmp_path, {"BACKUP_RESTORE_TOKEN": token, "DATABASE_RESTORE_ROOT": str(root)})
        assert result.returncode == 73, result.stderr
        assert "private restore stage already exists" in result.stderr
        assert (stage / "keep").read_text(encoding="utf-8") == "earlier stage\n"
        assert not Path(f"/tmp/atlas-database-restore-{token}").exists()
        assert not (tmp_path / "mc.log").exists()
    finally:
        shutil.rmtree(root)
