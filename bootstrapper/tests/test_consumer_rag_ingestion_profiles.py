"""Consumer RAG ingestion profile contract (#413).

A consumer declares a versioned ``rag_ingestion_profiles`` block describing a
repeatable ingestion lifecycle. Atlas validates + normalizes each profile
(unique names, corpus path safety, parser/chunker/target schema, no colliding
Weaviate collections), hashes it into a stable ``revision`` (the idempotency
input), and compiles a single JSON profiles file the backend reads plus a compose
overlay bind-mounting it into the backend. Artifacts regenerate every start.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from core.consumer_manifest import (
    ConsumerManifestError,
    compile_rag_ingestion_profiles_file,
    load_consumer_config,
    render_rag_ingestion_overlay,
)


def _write_root(root: Path) -> None:
    (root / ".env.example").write_text("PROJECT_NAME=atlas\n", encoding="utf-8")


def _write_consumer(root: Path, name: str, body: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    manifest = d / "atlas.consumer.yml"
    manifest.write_text(f"name: {name}\n" + textwrap.dedent(body), encoding="utf-8")
    return manifest


_FULL = """
    rag_ingestion_profiles:
      version: 1
      profiles:
        - name: showcase-default
          corpus:
            source: mount
            path: corpus/raw
          parser_order: [docling, tika, plain_text]
          chunker: {strategy: recursive, chunk_size: 700, overlap: 120}
          vector_targets:
            - {backend: weaviate, collection_prefix: RagShowcase, on_unavailable: fail}
          graph_targets:
            - {backend: lightrag, mode: upload_documents, wait_for_extraction: true, timeout_seconds: 3600, on_unavailable: skip}
"""


# ── happy path ──────────────────────────────────────────────────────

def test_single_profile_parsed(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(tmp_path, "rag-showcase", _FULL)
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert len(config.rag_ingestion_profiles) == 1
    p = config.rag_ingestion_profiles[0]
    assert p.name == "showcase-default"
    assert p.consumer == "rag-showcase"
    assert p.corpus.source == "mount" and p.corpus.path == "corpus/raw"
    assert p.parser_order == ("docling", "tika", "plain_text")
    assert p.chunker.chunk_size == 700 and p.chunker.overlap == 120
    assert p.vector_targets[0].collection_prefix == "RagShowcase"
    assert p.graph_targets[0].timeout_seconds == 3600
    assert len(p.revision) == 16  # stable content hash


def test_profiles_file_and_overlay_generated(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(tmp_path, "rag-showcase", _FULL)
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])

    assert config.rag_ingestion_file is not None
    assert config.rag_ingestion_file.path == tmp_path / "volumes/backend/rag-ingestion-profiles.json"
    doc = json.loads(config.rag_ingestion_file.content)
    assert doc["version"] == 1
    entry = doc["profiles"][0]
    assert entry["name"] == "showcase-default"
    assert entry["consumer"] == "rag-showcase"
    assert entry["revision"] == config.rag_ingestion_profiles[0].revision
    assert entry["corpus"] == {"source": "mount", "path": "corpus/raw"}

    assert config.rag_ingestion_overlay is not None
    overlay = config.rag_ingestion_overlay.content
    assert "backend:" in overlay
    assert "RAG_INGESTION_PROFILES_FILE: /atlas-consumer-config/rag-ingestion-profiles.json" in overlay
    assert "./volumes/backend/rag-ingestion-profiles.json:/atlas-consumer-config/rag-ingestion-profiles.json:ro" in overlay


def test_plain_text_auto_appended_as_fallback(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "c",
        """
        rag_ingestion_profiles:
          version: 1
          profiles:
            - name: p
              corpus: {source: mount, path: raw}
              parser_order: [docling]
              vector_targets:
                - {backend: weaviate, collection_prefix: P, on_unavailable: skip}
        """,
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert config.rag_ingestion_profiles[0].parser_order == ("docling", "plain_text")


def test_default_chunker_when_omitted(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "c",
        """
        rag_ingestion_profiles:
          version: 1
          profiles:
            - name: p
              corpus: {source: mount, path: raw}
              vector_targets:
                - {backend: weaviate, collection_prefix: P, on_unavailable: skip}
        """,
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    ch = config.rag_ingestion_profiles[0].chunker
    assert ch.strategy == "recursive" and ch.chunk_size == 700 and ch.overlap == 120


def test_generated_output_is_byte_stable(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(tmp_path, "rag-showcase", _FULL)
    a = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    b = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert compile_rag_ingestion_profiles_file(a.rag_ingestion_profiles) == \
        compile_rag_ingestion_profiles_file(b.rag_ingestion_profiles)
    assert render_rag_ingestion_overlay(a.rag_ingestion_profiles) == \
        render_rag_ingestion_overlay(b.rag_ingestion_profiles)


def test_no_profiles_yields_no_artifacts(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(tmp_path, "plain", "env:\n  values:\n    X: \"1\"\n")
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert config.rag_ingestion_profiles == ()
    assert config.rag_ingestion_file is None
    assert config.rag_ingestion_overlay is None


def test_minio_corpus_source(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "c",
        """
        rag_ingestion_profiles:
          version: 1
          profiles:
            - name: p
              corpus: {source: minio, bucket: corpora, prefix: showcase/}
              vector_targets:
                - {backend: weaviate, collection_prefix: P, on_unavailable: skip}
        """,
    )
    config = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    corpus = config.rag_ingestion_profiles[0].corpus
    assert corpus.source == "minio" and corpus.bucket == "corpora" and corpus.prefix == "showcase/"


# ── collisions / ownership ──────────────────────────────────────────

def test_duplicate_name_within_consumer_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "c",
        """
        rag_ingestion_profiles:
          version: 1
          profiles:
            - name: dup
              corpus: {source: mount, path: a}
              vector_targets: [{backend: weaviate, collection_prefix: A, on_unavailable: skip}]
            - name: dup
              corpus: {source: mount, path: b}
              vector_targets: [{backend: weaviate, collection_prefix: B, on_unavailable: skip}]
        """,
    )
    with pytest.raises(ConsumerManifestError, match="duplicate rag_ingestion_profiles name 'dup'"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_duplicate_name_across_consumers_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    a = _write_consumer(
        tmp_path, "alpha",
        "rag_ingestion_profiles:\n  version: 1\n  profiles:\n    - name: shared\n      corpus: {source: mount, path: a}\n      vector_targets: [{backend: weaviate, collection_prefix: A, on_unavailable: skip}]\n",
    )
    b = _write_consumer(
        tmp_path, "beta",
        "rag_ingestion_profiles:\n  version: 1\n  profiles:\n    - name: shared\n      corpus: {source: mount, path: b}\n      vector_targets: [{backend: weaviate, collection_prefix: B, on_unavailable: skip}]\n",
    )
    with pytest.raises(ConsumerManifestError, match="declared by multiple consumers"):
        load_consumer_config(tmp_path, explicit_paths=[str(a), str(b)])


def test_duplicate_weaviate_collection_rejected(tmp_path: Path) -> None:
    # Two profiles whose {prefix}_{name} collapse to the same class must be rejected.
    _write_root(tmp_path)
    a = _write_consumer(
        tmp_path, "alpha",
        "rag_ingestion_profiles:\n  version: 1\n  profiles:\n    - name: p\n      corpus: {source: mount, path: a}\n      vector_targets: [{backend: weaviate, collection_prefix: Shared, on_unavailable: skip}]\n",
    )
    b = _write_consumer(
        tmp_path, "beta",
        "rag_ingestion_profiles:\n  version: 1\n  profiles:\n    - name: p2\n      corpus: {source: mount, path: b}\n      vector_targets: [{backend: weaviate, collection_prefix: Shared, on_unavailable: skip}]\n",
    )
    # Different names → different classes; make them collide by reusing prefix+name.
    c = _write_consumer(
        tmp_path, "gamma",
        "rag_ingestion_profiles:\n  version: 1\n  profiles:\n    - name: dupclass\n      corpus: {source: mount, path: c}\n      vector_targets: [{backend: weaviate, collection_prefix: X, on_unavailable: skip}, {backend: weaviate, collection_prefix: X, on_unavailable: skip}]\n",
    )
    with pytest.raises(ConsumerManifestError, match="Weaviate collection .* declared by two"):
        load_consumer_config(tmp_path, explicit_paths=[str(c)])


def test_spoofed_owner_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "honest",
        """
        rag_ingestion_profiles:
          version: 1
          profiles:
            - name: p
              owner: someone-else
              corpus: {source: mount, path: a}
              vector_targets: [{backend: weaviate, collection_prefix: A, on_unavailable: skip}]
        """,
    )
    with pytest.raises(ConsumerManifestError, match="cannot be spoofed"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


# ── schema / validation ─────────────────────────────────────────────

@pytest.mark.parametrize("version", ["2", "0", "missing"])
def test_version_must_be_one(tmp_path: Path, version: str) -> None:
    _write_root(tmp_path)
    vline = "" if version == "missing" else f"  version: {version}\n"
    manifest = _write_consumer(
        tmp_path, "c",
        "rag_ingestion_profiles:\n" + vline + "  profiles:\n    - name: p\n      corpus: {source: mount, path: a}\n      vector_targets: [{backend: weaviate, collection_prefix: A, on_unavailable: skip}]\n",
    )
    with pytest.raises(ConsumerManifestError, match="rag_ingestion_profiles.version must be 1"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_empty_profiles_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(tmp_path, "c", "rag_ingestion_profiles:\n  version: 1\n  profiles: []\n")
    with pytest.raises(ConsumerManifestError, match="non-empty list"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_unknown_profile_field_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "c",
        "rag_ingestion_profiles:\n  version: 1\n  profiles:\n    - name: p\n      corpuss: {source: mount, path: a}\n      vector_targets: [{backend: weaviate, collection_prefix: A, on_unavailable: skip}]\n",
    )
    with pytest.raises(ConsumerManifestError, match="unknown field"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


@pytest.mark.parametrize("bad", ["/abs/path", "../escape", "~/home"])
def test_mount_path_safety(tmp_path: Path, bad: str) -> None:
    # The security boundary: no arbitrary host paths (absolute / '..' / '~').
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "c",
        f"rag_ingestion_profiles:\n  version: 1\n  profiles:\n    - name: p\n      corpus: {{source: mount, path: \"{bad}\"}}\n      vector_targets: [{{backend: weaviate, collection_prefix: A, on_unavailable: skip}}]\n",
    )
    with pytest.raises(ConsumerManifestError, match="corpus.path"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_minio_requires_bucket_and_prefix(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "c",
        "rag_ingestion_profiles:\n  version: 1\n  profiles:\n    - name: p\n      corpus: {source: minio, bucket: onlybucket}\n      vector_targets: [{backend: weaviate, collection_prefix: A, on_unavailable: skip}]\n",
    )
    with pytest.raises(ConsumerManifestError, match="requires both bucket and prefix"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_bad_corpus_source_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "c",
        "rag_ingestion_profiles:\n  version: 1\n  profiles:\n    - name: p\n      corpus: {source: http, path: a}\n      vector_targets: [{backend: weaviate, collection_prefix: A, on_unavailable: skip}]\n",
    )
    with pytest.raises(ConsumerManifestError, match="corpus.source"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_bad_parser_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "c",
        "rag_ingestion_profiles:\n  version: 1\n  profiles:\n    - name: p\n      corpus: {source: mount, path: a}\n      parser_order: [magic]\n      vector_targets: [{backend: weaviate, collection_prefix: A, on_unavailable: skip}]\n",
    )
    with pytest.raises(ConsumerManifestError, match="invalid parser"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_overlap_must_be_less_than_chunk_size(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "c",
        "rag_ingestion_profiles:\n  version: 1\n  profiles:\n    - name: p\n      corpus: {source: mount, path: a}\n      chunker: {strategy: token, chunk_size: 100, overlap: 100}\n      vector_targets: [{backend: weaviate, collection_prefix: A, on_unavailable: skip}]\n",
    )
    with pytest.raises(ConsumerManifestError, match="overlap .* must be < "):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_bad_vector_backend_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "c",
        "rag_ingestion_profiles:\n  version: 1\n  profiles:\n    - name: p\n      corpus: {source: mount, path: a}\n      vector_targets: [{backend: pinecone, collection_prefix: A, on_unavailable: skip}]\n",
    )
    with pytest.raises(ConsumerManifestError, match="vector target backend"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_bad_on_unavailable_rejected(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "c",
        "rag_ingestion_profiles:\n  version: 1\n  profiles:\n    - name: p\n      corpus: {source: mount, path: a}\n      vector_targets: [{backend: weaviate, collection_prefix: A, on_unavailable: maybe}]\n",
    )
    with pytest.raises(ConsumerManifestError, match="on_unavailable"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_profile_requires_a_target(tmp_path: Path) -> None:
    _write_root(tmp_path)
    manifest = _write_consumer(
        tmp_path, "c",
        "rag_ingestion_profiles:\n  version: 1\n  profiles:\n    - name: p\n      corpus: {source: mount, path: a}\n",
    )
    with pytest.raises(ConsumerManifestError, match="at least one vector_target or graph_target"):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("chunk_size", 9000, "chunk_size .* must be <= 8192"),
        ("overlap", 3000, "overlap .* must be <= 2048"),
    ],
)
def test_chunker_bounds_match_backend_limits(tmp_path: Path, field: str, value: int, match: str) -> None:
    # Reject out-of-range chunker values at load so they can't crash the backend
    # chunk phase (whose ChunkRequest caps chunk_size at 8192, overlap at 2048).
    _write_root(tmp_path)
    chunker = {"strategy": "token", "chunk_size": 8192, "overlap": 100}
    chunker[field] = value
    manifest = _write_consumer(
        tmp_path, "c",
        f"rag_ingestion_profiles:\n  version: 1\n  profiles:\n    - name: p\n      corpus: {{source: mount, path: a}}\n      chunker: {chunker}\n      vector_targets: [{{backend: weaviate, collection_prefix: A, on_unavailable: skip}}]\n",
    )
    with pytest.raises(ConsumerManifestError, match=match):
        load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def test_revision_changes_when_profile_changes(tmp_path: Path) -> None:
    _write_root(tmp_path)
    m1 = _write_consumer(
        tmp_path, "c1",
        "rag_ingestion_profiles:\n  version: 1\n  profiles:\n    - name: p\n      corpus: {source: mount, path: a}\n      chunker: {strategy: token, chunk_size: 100, overlap: 10}\n      vector_targets: [{backend: weaviate, collection_prefix: A, on_unavailable: skip}]\n",
    )
    m2 = _write_consumer(
        tmp_path, "c2",
        "rag_ingestion_profiles:\n  version: 1\n  profiles:\n    - name: q\n      corpus: {source: mount, path: a}\n      chunker: {strategy: token, chunk_size: 200, overlap: 10}\n      vector_targets: [{backend: weaviate, collection_prefix: A, on_unavailable: skip}]\n",
    )
    r1 = load_consumer_config(tmp_path, explicit_paths=[str(m1)]).rag_ingestion_profiles[0].revision
    r2 = load_consumer_config(tmp_path, explicit_paths=[str(m2)]).rag_ingestion_profiles[0].revision
    assert r1 != r2  # chunk_size change flips the revision


def test_generated_registry_mounts_avoid_app_source_bind():
    """#533: both generated consumer registries must mount OUTSIDE /app.

    The backend binds ./app/app:/app (services/backend/compose.yml), and Docker
    Desktop/VirtioFS rejects creating a nested single-file mountpoint inside a
    host-directory bind ("mountpoint … is outside of rootfs"). The reserved
    /atlas-consumer-config/ directory is the internal Atlas container contract
    for generated consumer registries.
    """
    from core.consumer_manifest import (
        LIGHTRAG_QUERY_PROFILES_CONTAINER_PATH,
        RAG_INGESTION_CONTAINER_PATH,
    )

    for path in (RAG_INGESTION_CONTAINER_PATH, LIGHTRAG_QUERY_PROFILES_CONTAINER_PATH):
        assert not path.startswith("/app/"), (
            f"{path}: generated registries must not mount inside the /app "
            "source bind (Docker Desktop/VirtioFS rejects nested mountpoints)"
        )
        assert path.startswith("/atlas-consumer-config/"), (
            f"{path}: expected the reserved /atlas-consumer-config/ contract dir"
        )
