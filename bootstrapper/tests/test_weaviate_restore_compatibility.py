"""Weaviate snapshots from the previous pinned release stay restorable (#1286).

New backups always record the exact pinned release. The restore gate accepts an
explicit list of releases whose native backups load into the pinned image, and
rejects every other release or a mismatched image/version pair (exercised
through restore-databases.sh in test_database_volume_backup_contracts.py).
"""
from __future__ import annotations

from pathlib import Path
import os
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


def _weaviate_status_capture(tmp_path: Path, mode: str):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wget = bin_dir / "wget"
    wget.write_text(_STATUS_WGET, encoding="utf-8")
    wget.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    log = tmp_path / "wget.log"
    snapshot_id = f"atlas-20260101_000000-{'b' * 32}"
    version = re.search(r'^EXPECTED_WEAVIATE_VERSION="([^"]+)"', SNAPSHOTS.read_text(), re.M)[1]
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
