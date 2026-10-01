"""Weaviate snapshots from the previous pinned release stay restorable (#1286).

New backups always record the exact pinned release. The restore gate accepts an
explicit list of releases whose native backups load into the pinned image, and
rejects every other release or a mismatched image/version pair (exercised
through restore-databases.sh in test_database_volume_backup_contracts.py).
"""
from __future__ import annotations

from pathlib import Path
import re
import uuid

import pytest
import yaml

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
