"""Consumer object-storage contract (#404).

A consumer declares object stores in its manifest ``storage:`` block; Atlas
compiles each to the existing #409 ``MINIO_EXTRA_CONSUMERS`` provisioning
grammar, validates names/collisions, exports stable per-store endpoint and
credential-reference fields (consumed by #345), and generates a minio-init
overlay so the consumer writes no compose override.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from core.consumer_manifest import (
    ConsumerManifestError,
    compile_storage_exports,
    compile_storage_provisioning,
    load_consumer_config,
    render_minio_storage_overlay,
    storage_credential_tokens,
)


def _write_root(root: Path) -> None:
    (root / ".env.example").write_text("PROJECT_NAME=atlas\n", encoding="utf-8")


def _write_manifest(root: Path, name: str, storage_yaml: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    manifest = d / "atlas.consumer.yml"
    manifest.write_text(
        textwrap.dedent(f"name: {name}\n") + textwrap.dedent(storage_yaml),
        encoding="utf-8",
    )
    return manifest


# ── provisioning ────────────────────────────────────────────────────

def test_storage_compiles_to_extra_consumers_provisioning(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "daydreams",
        """
        storage:
          buckets:
            - name: artifacts
              bucket: daydreams-artifacts
        """,
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])

    assert len(config.storage) == 1
    store = config.storage[0]
    assert store.key == "DAYDREAMS_ARTIFACTS"
    assert store.consumer_id == "daydreams-artifacts"
    assert store.bucket == "daydreams-artifacts"

    prov = compile_storage_provisioning(config.storage)
    assert prov["MINIO_BUCKET_DAYDREAMS_ARTIFACTS"] == "daydreams-artifacts"
    assert prov["MINIO_EXTRA_CONSUMERS"] == (
        "daydreams-artifacts:MINIO_BUCKET_DAYDREAMS_ARTIFACTS:"
        "MINIO_DAYDREAMS_ARTIFACTS_ACCESS_KEY:MINIO_DAYDREAMS_ARTIFACTS_SECRET_KEY"
    )
    # Provisioning is carried by the overlay, NOT persisted to .env — so
    # removing the store later cannot orphan a MINIO_EXTRA_CONSUMERS entry.
    assert "MINIO_EXTRA_CONSUMERS" not in config.env_overrides
    assert "MINIO_BUCKET_DAYDREAMS_ARTIFACTS" not in config.env_overrides


def test_bucket_name_defaults_to_consumer_and_store(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "rag-showcase",
        """
        storage:
          buckets:
            - name: corpus
        """,
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert config.storage[0].bucket == "rag-showcase-corpus"
    prov = compile_storage_provisioning(config.storage)
    assert prov["MINIO_BUCKET_RAG_SHOWCASE_CORPUS"] == "rag-showcase-corpus"


def test_extra_buckets_share_the_scoped_account(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path,
        "daydreams",
        """
        storage:
          buckets:
            - name: media
              bucket: daydreams-media
              extra_buckets: [daydreams-thumbs, daydreams-exports]
        """,
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    prov = compile_storage_provisioning(config.storage)
    assert prov["MINIO_BUCKET_DAYDREAMS_MEDIA_EXTRA_0"] == "daydreams-thumbs"
    assert prov["MINIO_BUCKET_DAYDREAMS_MEDIA_EXTRA_1"] == "daydreams-exports"
    assert prov["MINIO_EXTRA_CONSUMERS"].endswith(
        ":MINIO_BUCKET_DAYDREAMS_MEDIA_EXTRA_0,MINIO_BUCKET_DAYDREAMS_MEDIA_EXTRA_1"
    )


def test_multiple_consumers_accumulate_extra_consumers(tmp_path: Path) -> None:
    _write_root(tmp_path)
    one = _write_manifest(
        tmp_path, "alpha",
        "storage:\n  buckets:\n    - {name: store, bucket: alpha-store}\n",
    )
    two = _write_manifest(
        tmp_path, "beta",
        "storage:\n  buckets:\n    - {name: store, bucket: beta-store}\n",
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(one), str(two)])
    entries = compile_storage_provisioning(config.storage)["MINIO_EXTRA_CONSUMERS"].split()
    assert len(entries) == 2
    assert any(e.startswith("alpha-store:") for e in entries)
    assert any(e.startswith("beta-store:") for e in entries)


# ── exports (#345 fields) ───────────────────────────────────────────

def test_exports_expose_distinct_internal_and_public_endpoints(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path, "daydreams",
        "storage:\n  buckets:\n    - {name: artifacts, bucket: daydreams-artifacts}\n",
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    exports = compile_storage_exports(
        config.storage,
        minio_endpoint="http://minio:9000",
        minio_public_endpoint="http://localhost:63020",
        minio_region="us-east-1",
    )
    p = "ATLAS_STORE_DAYDREAMS_ARTIFACTS"
    assert exports[f"{p}_BUCKET"] == "daydreams-artifacts"
    assert exports[f"{p}_INTERNAL_ENDPOINT"] == "http://minio:9000"
    assert exports[f"{p}_PUBLIC_ENDPOINT"] == "http://localhost:63020"
    assert exports[f"{p}_REGION"] == "us-east-1"
    # secret references (var NAMES) — never raw secret values
    assert exports[f"{p}_ACCESS_KEY_VAR"] == "MINIO_DAYDREAMS_ARTIFACTS_ACCESS_KEY"
    assert exports[f"{p}_SECRET_KEY_VAR"] == "MINIO_DAYDREAMS_ARTIFACTS_SECRET_KEY"
    # distinct internal vs public fields
    assert exports[f"{p}_INTERNAL_ENDPOINT"] != exports[f"{p}_PUBLIC_ENDPOINT"]


def test_exports_track_base_port_change(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path, "daydreams",
        "storage:\n  buckets:\n    - {name: artifacts, bucket: daydreams-artifacts}\n",
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    kw = dict(minio_endpoint="http://minio:9000", minio_region="us-east-1")
    a = compile_storage_exports(config.storage, minio_public_endpoint="http://localhost:63020", **kw)
    b = compile_storage_exports(config.storage, minio_public_endpoint="http://localhost:64020", **kw)
    key = "ATLAS_STORE_DAYDREAMS_ARTIFACTS_PUBLIC_ENDPOINT"
    assert a[key] != b[key]
    assert b[key] == "http://localhost:64020"


def test_credential_tokens_for_keygen_backfill(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path, "daydreams",
        "storage:\n  buckets:\n    - {name: a, bucket: dd-a}\n    - {name: b, bucket: dd-b}\n",
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert storage_credential_tokens(config.storage) == [
        "DAYDREAMS_A",
        "DAYDREAMS_B",
    ]


# ── validation ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "bucket, match",
    [
        ("Daydreams-Artifacts", "lowercase"),      # uppercase
        ("dd_artifacts", "lowercase"),             # underscore
        ("ab", "3-63"),                            # too short
        ("a" * 64, "3-63"),                        # too long
        ("dd..artifacts", r"\.\."),                # double dot
        ("10.0.0.1", "IP-formatted"),              # ip-like
    ],
)
def test_invalid_bucket_names_rejected(tmp_path: Path, bucket: str, match: str) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path, "c",
        f"storage:\n  buckets:\n    - {{name: s, bucket: {bucket}}}\n",
    )
    with pytest.raises(ConsumerManifestError, match=match):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_bucket_collision_with_builtin_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path, "c",
        "storage:\n  buckets:\n    - {name: s, bucket: backend}\n",
    )
    with pytest.raises(ConsumerManifestError, match="built-in"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_cross_store_bucket_collision_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    one = _write_manifest(
        tmp_path, "alpha",
        "storage:\n  buckets:\n    - {name: s, bucket: shared-bucket}\n",
    )
    two = _write_manifest(
        tmp_path, "beta",
        "storage:\n  buckets:\n    - {name: s, bucket: shared-bucket}\n",
    )
    with pytest.raises(ConsumerManifestError, match="multiple stores"):
        load_consumer_config(tmp_path, explicit_paths=[str(one), str(two)])


def test_duplicate_store_name_within_consumer_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path, "c",
        "storage:\n  buckets:\n    - {name: s, bucket: c-1}\n    - {name: s, bucket: c-2}\n",
    )
    with pytest.raises(ConsumerManifestError, match="duplicate storage bucket name"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_empty_buckets_list_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(tmp_path, "c", "storage:\n  buckets: []\n")
    with pytest.raises(ConsumerManifestError, match="non-empty list"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


# ── generated overlay ───────────────────────────────────────────────

def test_storage_overlay_wires_minio_init_without_consumer_override(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(
        tmp_path, "daydreams",
        "storage:\n  buckets:\n    - {name: artifacts, bucket: daydreams-artifacts}\n",
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert config.storage_overlay is not None
    content = config.storage_overlay.content
    assert "minio-init:" in content
    # bucket name is LITERAL (source of truth is the manifest, not .env)
    assert 'MINIO_BUCKET_DAYDREAMS_ARTIFACTS: "daydreams-artifacts"' in content
    # MINIO_EXTRA_CONSUMERS merges with any operator/_user value from .env
    assert "${MINIO_EXTRA_CONSUMERS:-} daydreams-artifacts:MINIO_BUCKET_DAYDREAMS_ARTIFACTS" in content
    # credential comes from .env (persisted, blank-only)
    assert "MINIO_DAYDREAMS_ARTIFACTS_ACCESS_KEY: ${MINIO_DAYDREAMS_ARTIFACTS_ACCESS_KEY:-}" in content
    assert config.storage_overlay.path.name == "consumer-storage.compose.yml"


def test_render_overlay_is_valid_yaml_and_merges_extra_consumers() -> None:
    import yaml

    from core.consumer_manifest import StorageStore

    store = StorageStore(
        consumer="daydreams", name="artifacts", key="DAYDREAMS_ARTIFACTS",
        consumer_id="daydreams-artifacts", bucket="daydreams-artifacts",
    )
    env = yaml.safe_load(render_minio_storage_overlay([store]))[
        "services"]["minio-init"]["environment"]
    # literal bucket, credential reference, and operator-merging extra consumers
    assert env["MINIO_BUCKET_DAYDREAMS_ARTIFACTS"] == "daydreams-artifacts"
    assert env["MINIO_DAYDREAMS_ARTIFACTS_ACCESS_KEY"] == "${MINIO_DAYDREAMS_ARTIFACTS_ACCESS_KEY:-}"
    assert env["MINIO_EXTRA_CONSUMERS"].startswith("${MINIO_EXTRA_CONSUMERS:-} ")


def test_no_storage_block_is_inert(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_manifest(tmp_path, "plain", "env:\n  values:\n    FOO: bar\n")
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert config.storage == ()
    assert config.storage_overlay is None
    assert "MINIO_EXTRA_CONSUMERS" not in config.env_overrides


# ── runtime wiring (docker_manager + KeyGenerator) ──────────────────

def test_docker_manager_includes_storage_overlay_when_present(tmp_path: Path) -> None:
    from core.docker_manager import DockerManager
    from core.consumer_manifest import MINIO_STORAGE_OVERLAY_PATH

    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / ".env").write_text("PROJECT_NAME=atlas\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("PROJECT_NAME=atlas\n", encoding="utf-8")

    manager = DockerManager(str(tmp_path))
    # No overlay yet → default auto-discovery (empty -f list).
    assert manager._compose_file_args() == []

    overlay = tmp_path / MINIO_STORAGE_OVERLAY_PATH
    overlay.parent.mkdir(parents=True, exist_ok=True)
    overlay.write_text("services: {minio-init: {environment: {}}}\n", encoding="utf-8")

    args = manager._compose_file_args()
    assert "-f" in args
    assert "docker-compose.yml" in args
    assert str(MINIO_STORAGE_OVERLAY_PATH) in args


def test_extra_minio_consumer_keys_are_blank_only(tmp_path: Path) -> None:
    from utils.key_generator import KeyGenerator

    env = tmp_path / ".env"
    env.write_text(
        "MINIO_DAYDREAMS_ARTIFACTS_ACCESS_KEY=\n"
        "MINIO_DAYDREAMS_ARTIFACTS_SECRET_KEY=preset-secret\n",
        encoding="utf-8",
    )
    gen = KeyGenerator(str(tmp_path))
    gen.env_file_path = env
    gen.generate_and_update_extra_minio_consumer_keys(["DAYDREAMS_ARTIFACTS"])

    values = {}
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            values[k] = v
    # blank access key was generated; preset secret was preserved (blank-only).
    assert values["MINIO_DAYDREAMS_ARTIFACTS_ACCESS_KEY"]
    assert values["MINIO_DAYDREAMS_ARTIFACTS_SECRET_KEY"] == "preset-secret"


def test_finalize_consumer_storage_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_finalize_consumer_storage generates creds, merges resolved exports, and
    writes the minio-init overlay — the single chokepoint both flows call."""
    import start as start_module
    from core.consumer_manifest import MINIO_STORAGE_OVERLAY_PATH

    (tmp_path / ".env.example").write_text("PROJECT_NAME=atlas\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "PROJECT_NAME=atlas\n"
        "MINIO_PORT=63020\n"
        "MINIO_REGION=us-east-1\n"
        "MINIO_ENDPOINT=http://minio:9000\n"
        "MINIO_PUBLIC_ENDPOINT=http://localhost:63020\n",
        encoding="utf-8",
    )
    manifest = _write_manifest(
        tmp_path, "daydreams",
        "storage:\n  buckets:\n    - {name: artifacts, bucket: daydreams-artifacts}\n",
    )
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))

    starter = start_module.AtlasStarter()
    starter.root_dir = tmp_path
    starter.config_parser.root_dir = tmp_path
    starter.config_parser.env_file_path = tmp_path / ".env"
    starter.config_parser.env_example_path = tmp_path / ".env.example"
    starter.key_generator.env_file_path = tmp_path / ".env"

    assert starter._finalize_consumer_storage() is True

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    # scoped credentials generated (blank-only)
    assert "MINIO_DAYDREAMS_ARTIFACTS_ACCESS_KEY=" in env_text
    assert "\nMINIO_DAYDREAMS_ARTIFACTS_ACCESS_KEY=\n" not in env_text  # not blank
    # resolved exports merged
    assert "ATLAS_STORE_DAYDREAMS_ARTIFACTS_PUBLIC_ENDPOINT=http://localhost:63020" in env_text
    assert "ATLAS_STORE_DAYDREAMS_ARTIFACTS_INTERNAL_ENDPOINT=http://minio:9000" in env_text
    assert "ATLAS_STORE_DAYDREAMS_ARTIFACTS_ACCESS_KEY_VAR=MINIO_DAYDREAMS_ARTIFACTS_ACCESS_KEY" in env_text
    # provisioning is NOT persisted to .env (lives only in the overlay)
    assert "MINIO_EXTRA_CONSUMERS=daydreams" not in env_text
    assert "MINIO_BUCKET_DAYDREAMS_ARTIFACTS=" not in env_text
    # overlay written for docker_manager to include
    overlay = tmp_path / MINIO_STORAGE_OVERLAY_PATH
    assert overlay.is_file()
    assert "minio-init" in overlay.read_text(encoding="utf-8")


def _starter_at(tmp_path: Path):
    import start as start_module

    starter = start_module.AtlasStarter()
    starter.root_dir = tmp_path
    starter.config_parser.root_dir = tmp_path
    starter.config_parser.env_file_path = tmp_path / ".env"
    starter.config_parser.env_example_path = tmp_path / ".env.example"
    starter.key_generator.env_file_path = tmp_path / ".env"
    return starter


def test_removing_storage_leaves_no_dangling_provisioning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: after a store is removed, a warm restart must not leave a
    MINIO_EXTRA_CONSUMERS entry in .env that would crash minio-init."""
    from core.consumer_manifest import MINIO_STORAGE_OVERLAY_PATH

    (tmp_path / ".env.example").write_text("PROJECT_NAME=atlas\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "PROJECT_NAME=atlas\nMINIO_SOURCE=container\nMINIO_PORT=63020\n"
        "MINIO_REGION=us-east-1\nMINIO_ENDPOINT=http://minio:9000\n"
        "MINIO_PUBLIC_ENDPOINT=http://localhost:63020\n",
        encoding="utf-8",
    )
    manifest = _write_manifest(
        tmp_path, "daydreams",
        "storage:\n  buckets:\n    - {name: artifacts, bucket: daydreams-artifacts}\n",
    )
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))

    # Run 1: storage declared → overlay written, exports emitted.
    assert _starter_at(tmp_path)._finalize_consumer_storage() is True
    overlay = tmp_path / MINIO_STORAGE_OVERLAY_PATH
    assert overlay.is_file()
    assert "ATLAS_STORE_DAYDREAMS_ARTIFACTS_BUCKET" in (tmp_path / ".env").read_text()
    # provisioning was never written to .env in the first place
    assert "MINIO_EXTRA_CONSUMERS=daydreams" not in (tmp_path / ".env").read_text()

    # Run 2 (warm restart): storage removed from the manifest.
    manifest.write_text("name: daydreams\n", encoding="utf-8")
    assert _starter_at(tmp_path)._finalize_consumer_storage() is True

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert not overlay.exists()                       # overlay removed
    assert "MINIO_EXTRA_CONSUMERS=daydreams" not in env_text  # no dangling entry
    assert "ATLAS_STORE_DAYDREAMS_ARTIFACTS" not in env_text   # stale exports cleared


def test_storage_skipped_when_minio_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A storage declaration against disabled MinIO is skipped (no overlay, no
    unusable empty-endpoint export) rather than silently provisioned."""
    from core.consumer_manifest import MINIO_STORAGE_OVERLAY_PATH

    (tmp_path / ".env.example").write_text("PROJECT_NAME=atlas\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "PROJECT_NAME=atlas\nMINIO_SOURCE=disabled\n"
        "MINIO_ENDPOINT=\nMINIO_PUBLIC_ENDPOINT=\n",
        encoding="utf-8",
    )
    manifest = _write_manifest(
        tmp_path, "daydreams",
        "storage:\n  buckets:\n    - {name: artifacts, bucket: daydreams-artifacts}\n",
    )
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", str(manifest))

    assert _starter_at(tmp_path)._finalize_consumer_storage() is True
    assert not (tmp_path / MINIO_STORAGE_OVERLAY_PATH).exists()
    assert "ATLAS_STORE_DAYDREAMS_ARTIFACTS" not in (tmp_path / ".env").read_text()
