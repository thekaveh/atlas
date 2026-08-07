"""
Tests for C3: bootstrapper/utils/comfyui_manifest_generator.py.

All tests are pure unit tests — NO network, NO DB, NO running containers.
Synthetic catalog entries are passed so ``assemble_wizard_catalog()``
(live HF/civitai scrape) is never called.

Test matrix (4 tests per brief):
  1. Generator writes valid manifest YAML + correct TSV columns + '' for null sha256.
  2. When COMFYUI_SOURCE=disabled the generator skips without writing files.
  3. download_models.sh does NOT reference public.comfyui_models (grep guard).
  4. Manifest round-trips: active set matches COMFYUI_USER_MODELS + sidecar selection.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

# Add bootstrapper/ to sys.path so utils.* imports resolve.
import sys
_BOOTSTRAPPER = Path(__file__).resolve().parent.parent
if str(_BOOTSTRAPPER) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAPPER))

from utils.comfyui_custom_nodes import ComfyUICustomNode
from utils.comfyui_library import ComfyUIModelFile, ComfyUILibraryEntry
from utils.comfyui_manifest_generator import ComfyUIManifestGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry(
    name: str,
    *,
    category: str = "checkpoint",
    url: str = "https://huggingface.co/example/model.safetensors",
    sha256: str | None = None,
    essential: bool = False,
    filename: str | None = None,
    requires_custom_node: tuple[str, ...] = (),
) -> ComfyUILibraryEntry:
    return ComfyUILibraryEntry(
        name=name,
        family="TestFamily",
        category=category,
        size_gb=1.0,
        url=url,
        sha256=sha256,
        target_dir=category + "s",
        min_vram_gb=None,
        cpu_supported=True,
        requires_custom_node=requires_custom_node,
        popularity=0,
        source="curated",
        pulled=False,
        essential=essential,
        notes=None,
        filename=filename,
    )


def _bundle_entry(name: str = "Krea2Bundle") -> ComfyUILibraryEntry:
    return ComfyUILibraryEntry(
        name=name,
        family="Krea 2",
        category="diffusion_models",
        size_gb=17.5,
        url="https://huggingface.co/example/krea/resolve/main/krea.safetensors",
        sha256=None,
        target_dir="diffusion_models",
        min_vram_gb=16,
        cpu_supported=False,
        requires_custom_node=(),
        popularity=0,
        source="curated",
        pulled=False,
        precision="bf16",
        variant="mps-safe",
        files=(
            ComfyUIModelFile(
                role="diffusion",
                category="diffusion_models",
                url="https://huggingface.co/example/krea/resolve/main/krea.safetensors",
                filename="krea.safetensors",
                sha256="a" * 64,
            ),
            ComfyUIModelFile(
                role="text_encoder",
                category="text_encoders",
                url="https://huggingface.co/example/krea/resolve/main/t5xxl.safetensors",
                filename="t5xxl.safetensors",
                sha256="b" * 64,
            ),
            ComfyUIModelFile(
                role="vae",
                category="vae",
                url="https://huggingface.co/example/krea/resolve/main/vae.safetensors",
                filename="vae.safetensors",
                sha256="c" * 64,
            ),
        ),
    )


def _schema_path() -> Path:
    here = Path(__file__).resolve().parent
    return here.parent / "schemas" / "comfyui-manifest.schema.json"


def _validate_manifest(data: dict) -> None:
    # jsonschema is a hard runtime dependency (bootstrapper/pyproject.toml);
    # import unconditionally so a missing install fails loudly rather than
    # silently skipping schema enforcement.
    import jsonschema
    schema = json.loads(_schema_path().read_text(encoding="utf-8"))
    jsonschema.validate(instance=data, schema=schema)


def _custom_nodes_manifest(path: Path) -> Path:
    path.write_text(
        "\n".join([
            "custom_nodes:",
            "  - name: ComfyUI-GGUF",
            "    repo: https://github.com/city96/ComfyUI-GGUF.git",
            "    ref: 6ea2651e7df66d7585f6ffee804b20e92fb38b8a",
            "    install_requirements: true",
            "  - name: ComfyUI_IPAdapter_plus",
            "    repo: https://github.com/cubiq/ComfyUI_IPAdapter_plus.git",
            "    ref: a0f451a5113cf9becb0847b92884cb10cbdec0ef",
            "    install_requirements: true",
            "",
        ]),
        encoding="utf-8",
    )
    return path


# Full 40-char hex SHAs (valid refs). Shared with test_comfyui_custom_nodes.py.
_NODE_SHA_GGUF = "6ea2651e7df66d7585f6ffee804b20e92fb38b8a"
_NODE_SHA_IPADAPTER = "a0f451a5113cf9becb0847b92884cb10cbdec0ef"
_NODE_SHA_KREA = "1111111111111111111111111111111111111111"
_NODE_SHA_SHADOW = "2222222222222222222222222222222222222222"


def _write_nodes_yaml(path: Path, nodes: list[dict]) -> Path:
    """Flexible custom-nodes YAML writer (Atlas- or consumer-shaped).

    Mirrors the ``_write_nodes`` helper in test_comfyui_custom_nodes.py so both
    files stay in sync on the wire format. Each node dict needs name/repo/ref;
    install_requirements and mps_unsafe default to false when omitted.
    """
    lines = ["custom_nodes:"]
    for n in nodes:
        lines.append(f"  - name: {n['name']}")
        lines.append(f"    repo: {n['repo']}")
        lines.append(f"    ref: {n['ref']}")
        if n.get("install_requirements"):
            lines.append("    install_requirements: true")
        if n.get("mps_unsafe"):
            lines.append("    mps_unsafe: true")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Test 1: generator writes valid YAML + correct TSV for an active set
# ---------------------------------------------------------------------------

class TestGeneratorWritesFiles:
    """C3 T1 — files are written with correct content when comfyui is enabled."""

    def test_yaml_manifest_valid(self, tmp_path, monkeypatch):
        """YAML manifest validates against comfyui-manifest.schema.json."""
        catalog = [
            _entry("ModelA", sha256="abc123"),
            _entry("ModelB", category="vae"),
        ]
        env = {"COMFYUI_SOURCE": "container", "COMFYUI_USER_MODELS": "ModelA,ModelB"}

        # Patch active_comfyui_models to use our synthetic catalog.
        import utils.comfyui_resolver as resolver
        monkeypatch.setattr(
            resolver,
            "active_comfyui_models",
            lambda e, **kw: [m for m in catalog if m.name in {"ModelA", "ModelB"}],
        )

        gen = ComfyUIManifestGenerator(env)
        assert gen.write(tmp_path) is True

        yaml_path = tmp_path / "selected-models.yaml"
        assert yaml_path.exists(), "selected-models.yaml not written"
        data = yaml.safe_load(yaml_path.read_text())
        _validate_manifest(data)
        assert len(data["models"]) == 2
        names = {m["name"] for m in data["models"]}
        assert names == {"ModelA", "ModelB"}

    def test_tsv_columns_and_null_sha256(self, tmp_path, monkeypatch):
        """TSV has 6 tab-separated columns; null sha256 → empty string."""
        catalog = [
            _entry("ModelA", filename="model-a.safetensors", sha256="deadbeef"),
            _entry("ModelB", filename="model-b.safetensors", sha256=None),
        ]
        env = {"COMFYUI_SOURCE": "container", "COMFYUI_USER_MODELS": "ModelA,ModelB"}

        import utils.comfyui_resolver as resolver
        monkeypatch.setattr(
            resolver,
            "active_comfyui_models",
            lambda e, **kw: catalog,
        )

        gen = ComfyUIManifestGenerator(env)
        gen.write(tmp_path)

        tsv_path = tmp_path / "active-models.tsv"
        assert tsv_path.exists(), "active-models.tsv not written"
        lines = [l for l in tsv_path.read_text().splitlines() if l]
        assert len(lines) == 2, f"Expected 2 rows, got: {lines}"

        # Each row must have exactly 6 tab-separated columns.
        for line in lines:
            cols = line.split("\t")
            assert len(cols) == 6, f"Expected 6 columns, got {len(cols)}: {line!r}"

        # name / type / filename / download_url / sha256 / target_dir
        row_a = dict(zip(["name","type","filename","download_url","sha256","target_dir"],
                         lines[0].split("\t")))
        row_b = dict(zip(["name","type","filename","download_url","sha256","target_dir"],
                         lines[1].split("\t")))

        assert row_a["name"] == "ModelA"
        assert row_a["sha256"] == "deadbeef"
        assert row_a["target_dir"] == "checkpoints"
        assert row_b["name"] == "ModelB"
        assert row_b["sha256"] == "", "null sha256 must be empty string in TSV"

    def test_tsv_url_matches_entry(self, tmp_path, monkeypatch):
        """TSV download_url column matches the entry's URL."""
        url = "https://huggingface.co/some/model.safetensors"
        catalog = [_entry("ModelC", url=url)]
        env = {"COMFYUI_SOURCE": "container", "COMFYUI_USER_MODELS": "ModelC"}

        import utils.comfyui_resolver as resolver
        monkeypatch.setattr(
            resolver, "active_comfyui_models", lambda e, **kw: catalog
        )

        ComfyUIManifestGenerator(env).write(tmp_path)
        tsv = (tmp_path / "active-models.tsv").read_text().strip()
        cols = tsv.split("\t")
        assert cols[3] == url
        assert cols[5] == "checkpoints"

    @pytest.mark.parametrize(
        "field,value",
        [
            ("name", "Bad\tName"),
            ("name", "Bad\nName"),
            ("filename", "../escape.safetensors"),
            ("filename", "nested/escape.safetensors"),
            ("filename", "nested\\escape.safetensors"),
            ("download_url", "https://example.test/model\nother"),
            ("sha256", "abc\tdef"),
        ],
    )
    def test_tsv_rejects_unsafe_fields(self, tmp_path, monkeypatch, field, value):
        """TSV fields must not shift columns or write outside the model dir."""
        kwargs = {}
        name = "SafeName"
        sha256 = "deadbeef"
        url = "https://huggingface.co/example/model.safetensors"
        if field == "name":
            name = value
        elif field == "filename":
            kwargs["filename"] = value
        elif field == "download_url":
            url = value
        elif field == "sha256":
            sha256 = value
        catalog = [_entry(name, url=url, sha256=sha256, **kwargs)]
        env = {"COMFYUI_SOURCE": "container", "COMFYUI_USER_MODELS": name}

        import utils.comfyui_resolver as resolver
        monkeypatch.setattr(resolver, "active_comfyui_models", lambda e, **kw: catalog)

        with pytest.raises(ValueError):
            ComfyUIManifestGenerator(env).write(tmp_path)

    def test_empty_catalog_produces_empty_tsv(self, tmp_path, monkeypatch):
        """Empty active set → empty TSV (zero bytes → download_models.sh exits 0)."""
        env = {"COMFYUI_SOURCE": "container", "COMFYUI_USER_MODELS": ""}

        import utils.comfyui_resolver as resolver
        monkeypatch.setattr(resolver, "active_comfyui_models", lambda e, **kw: [])

        ComfyUIManifestGenerator(env).write(tmp_path)
        tsv_path = tmp_path / "active-models.tsv"
        assert tsv_path.exists()
        assert tsv_path.stat().st_size == 0, "Empty active set must produce empty TSV"

    def test_required_custom_nodes_write_allowlisted_install_tsv(self, tmp_path, monkeypatch):
        """Selected models' requires_custom_node values produce a pinned install plan."""
        catalog = [
            _entry(
                "FluxGGUF",
                requires_custom_node=("ComfyUI-GGUF", "UnknownNode"),
            ),
            _entry(
                "IPAdapter",
                requires_custom_node=("ComfyUI_IPAdapter_plus",),
            ),
        ]
        env = {
            "COMFYUI_SOURCE": "container",
            "COMFYUI_USER_MODELS": "FluxGGUF,IPAdapter",
            "COMFYUI_CUSTOM_NODES_FILE": str(_custom_nodes_manifest(tmp_path / "custom-nodes.yaml")),
        }

        import utils.comfyui_resolver as resolver
        monkeypatch.setattr(resolver, "active_comfyui_models", lambda e, **kw: catalog)
        # Isolate from the repo's real Atlas allowlist so the merged set is exactly
        # this test's file (env-mode otherwise prepends services/comfyui/custom-nodes.yaml).
        monkeypatch.setattr("utils.comfyui_custom_nodes._host_repo_custom_nodes", lambda: None)

        ComfyUIManifestGenerator(env).write(tmp_path)

        custom_nodes_tsv = tmp_path / "active-custom-nodes.tsv"
        assert custom_nodes_tsv.exists(), "active-custom-nodes.tsv not written"
        lines = custom_nodes_tsv.read_text(encoding="utf-8").splitlines()
        assert lines == [
            "ComfyUI-GGUF\thttps://github.com/city96/ComfyUI-GGUF.git\t6ea2651e7df66d7585f6ffee804b20e92fb38b8a\ttrue",
            "ComfyUI_IPAdapter_plus\thttps://github.com/cubiq/ComfyUI_IPAdapter_plus.git\ta0f451a5113cf9becb0847b92884cb10cbdec0ef\ttrue",
        ]

    def test_no_required_custom_nodes_writes_empty_install_tsv(self, tmp_path, monkeypatch):
        """No node requirements still creates an empty TSV for deterministic init behavior."""
        catalog = [_entry("PlainSDXL")]
        env = {"COMFYUI_SOURCE": "container", "COMFYUI_USER_MODELS": "PlainSDXL"}

        import utils.comfyui_resolver as resolver
        monkeypatch.setattr(resolver, "active_comfyui_models", lambda e, **kw: catalog)

        ComfyUIManifestGenerator(env).write(tmp_path)

        custom_nodes_tsv = tmp_path / "active-custom-nodes.tsv"
        assert custom_nodes_tsv.exists()
        assert custom_nodes_tsv.stat().st_size == 0

    def test_bundle_manifest_expands_to_file_rows_with_target_dirs(self, tmp_path, monkeypatch):
        """One selected logical bundle must expand into one manifest/TSV row per file."""
        catalog = [_bundle_entry()]
        env = {"COMFYUI_SOURCE": "container", "COMFYUI_USER_MODELS": "Krea2Bundle"}

        import utils.comfyui_resolver as resolver
        monkeypatch.setattr(resolver, "active_comfyui_models", lambda e, **kw: catalog)

        ComfyUIManifestGenerator(env).write(tmp_path)

        data = yaml.safe_load((tmp_path / "selected-models.yaml").read_text())
        _validate_manifest(data)
        rows = data["models"]
        assert [row["bundle_file_role"] for row in rows] == [
            "diffusion",
            "text_encoder",
            "vae",
        ]
        assert {row["bundle_id"] for row in rows} == {"Krea2Bundle"}
        assert [row["target_dir"] for row in rows] == [
            "diffusion_models",
            "text_encoders",
            "vae",
        ]
        assert [row["precision"] for row in rows] == ["bf16", "bf16", "bf16"]
        assert [row["variant"] for row in rows] == [
            "mps-safe",
            "mps-safe",
            "mps-safe",
        ]

        tsv_lines = (tmp_path / "active-models.tsv").read_text().splitlines()
        assert len(tsv_lines) == 3
        first_cols = tsv_lines[0].split("\t")
        assert first_cols == [
            "Krea2Bundle",
            "diffusion_models",
            "krea.safetensors",
            "https://huggingface.co/example/krea/resolve/main/krea.safetensors",
            "a" * 64,
            "diffusion_models",
        ]

    def test_mesh_model_can_route_file_to_checkpoint_dir(self, tmp_path, monkeypatch):
        """A logical mesh model can send a loader-specific weight to checkpoints."""
        catalog = [
            ComfyUILibraryEntry(
                name="Hunyuan3DSynthetic",
                family="Hunyuan3D",
                category="mesh_model",
                size_gb=4.0,
                url="https://huggingface.co/example/hunyuan/resolve/main/model.safetensors",
                sha256=None,
                target_dir="mesh_models",
                min_vram_gb=12,
                cpu_supported=False,
                requires_custom_node=("ComfyUI-3D-Pack",),
                popularity=0,
                source="curated",
                pulled=False,
                precision="bf16",
                files=(
                    ComfyUIModelFile(
                        role="checkpoint",
                        category="checkpoint",
                        target_dir="checkpoints",
                        url="https://huggingface.co/example/hunyuan/resolve/main/model.safetensors",
                        filename="hunyuan3d.safetensors",
                    ),
                ),
            )
        ]
        env = {
            "COMFYUI_SOURCE": "container",
            "COMFYUI_USER_MODELS": "Hunyuan3DSynthetic",
        }

        import utils.comfyui_resolver as resolver
        monkeypatch.setattr(resolver, "active_comfyui_models", lambda e, **kw: catalog)

        ComfyUIManifestGenerator(env).write(tmp_path)

        row = yaml.safe_load((tmp_path / "selected-models.yaml").read_text())["models"][0]
        assert row["type"] == "checkpoint"
        assert row["bundle_id"] == "Hunyuan3DSynthetic"
        assert row["target_dir"] == "checkpoints"
        assert (tmp_path / "active-models.tsv").read_text().split("\t")[-1].strip() == "checkpoints"


# ---------------------------------------------------------------------------
# Test 2: generator skips when COMFYUI_SOURCE=disabled
# ---------------------------------------------------------------------------

class TestGeneratorSkipsWhenDisabled:
    """C3 T2 — no files written when ComfyUI is disabled."""

    def test_disabled_returns_true_no_files(self, tmp_path):
        env = {"COMFYUI_SOURCE": "disabled"}
        gen = ComfyUIManifestGenerator(env)
        assert gen.is_enabled() is False
        result = gen.write(tmp_path)
        assert result is True
        assert not (tmp_path / "selected-models.yaml").exists()
        assert not (tmp_path / "active-models.tsv").exists()

    def test_missing_source_key_treated_as_disabled(self, tmp_path):
        """Missing COMFYUI_SOURCE defaults to disabled."""
        env: dict[str, str] = {}
        gen = ComfyUIManifestGenerator(env)
        assert gen.is_enabled() is False
        result = gen.write(tmp_path)
        assert result is True
        assert not (tmp_path / "selected-models.yaml").exists()


# ---------------------------------------------------------------------------
# #905: consumer custom_nodes union semantics through _write_custom_nodes_tsv
# ---------------------------------------------------------------------------

class TestConsumerCustomNodesUnion:
    """Integration coverage for the #905 union semantics as they surface through
    ``_write_custom_nodes_tsv`` (the generator's ``active-custom-nodes.tsv``
    output).

    The resolver-level unit tests (``active_custom_nodes`` model-gating vs.
    ``from_consumer`` unconditional) live in ``test_comfyui_custom_nodes.py``;
    these tests verify the generator wires that union correctly into the
    shell-consumable install TSV that ``provision_custom_nodes.sh`` reads.

    Union contract (``utils.comfyui_custom_nodes``):
      • Atlas-shipped nodes (``_host_repo_custom_nodes``) load first with
        ``from_consumer=False`` → model-gated (active only when an entry's
        ``requires_custom_node`` names them).
      • Consumer-declared nodes (``COMFYUI_CUSTOM_NODES_FILE``, os.pathsep
        -joined) append with ``from_consumer=True`` → active unconditionally
        (a model need not reference them; e.g. comfyui-krea2edit is a workflow
        node no catalog model declares).
      • Duplicate names resolve first-wins (Atlas wins because it loads first).
    """

    def test_atlas_and_consumer_union_atlas_first_no_dupes(self, tmp_path, monkeypatch):
        """Atlas allowlist + consumer file → rows from BOTH, Atlas-ordered first,
        and the duplicate name collapses to a single Atlas row."""
        atlas_file = _write_nodes_yaml(
            tmp_path / "atlas-nodes.yaml",
            [
                {
                    "name": "ComfyUI-GGUF",
                    "repo": "https://github.com/city96/ComfyUI-GGUF.git",
                    "ref": _NODE_SHA_GGUF,
                    "install_requirements": True,
                },
                {
                    "name": "ComfyUI_IPAdapter_plus",
                    "repo": "https://github.com/cubiq/ComfyUI_IPAdapter_plus.git",
                    "ref": _NODE_SHA_IPADAPTER,
                    "install_requirements": True,
                },
            ],
        )
        consumer_file = _write_nodes_yaml(
            tmp_path / "consumer-nodes.yaml",
            [
                # Same name as an Atlas node → Atlas first-wins; consumer ref shadowed.
                {
                    "name": "ComfyUI_IPAdapter_plus",
                    "repo": "https://github.com/cubiq/ComfyUI_IPAdapter_plus.git",
                    "ref": _NODE_SHA_SHADOW,
                    "install_requirements": True,
                },
                # Consumer-only node (no model requires it; active via from_consumer).
                {
                    "name": "comfyui-krea2edit",
                    "repo": "https://github.com/krea-ai/comfyui-krea2edit.git",
                    "ref": _NODE_SHA_KREA,
                },
            ],
        )

        # Isolate from the real repo allowlist so the test does not depend on
        # services/comfyui/custom-nodes.yaml contents at runtime.
        import utils.comfyui_custom_nodes as ccn
        monkeypatch.setattr(ccn, "_host_repo_custom_nodes", lambda: atlas_file)

        catalog = [
            _entry(
                "FluxGGUF",
                requires_custom_node=("ComfyUI-GGUF", "ComfyUI_IPAdapter_plus"),
            ),
        ]
        env = {
            "COMFYUI_SOURCE": "container",
            "COMFYUI_USER_MODELS": "FluxGGUF",
            "COMFYUI_CUSTOM_NODES_FILE": str(consumer_file),
        }

        import utils.comfyui_resolver as resolver
        monkeypatch.setattr(resolver, "active_comfyui_models", lambda e, **kw: catalog)

        ComfyUIManifestGenerator(env).write(tmp_path)

        lines = (tmp_path / "active-custom-nodes.tsv").read_text(encoding="utf-8").splitlines()
        names = [line.split("\t")[0] for line in lines]
        # Atlas-ordered first (GGUF, IPAdapter), then the consumer-only node;
        # the duplicate IPAdapter name appears exactly once.
        assert names == ["ComfyUI-GGUF", "ComfyUI_IPAdapter_plus", "comfyui-krea2edit"]
        # First-wins: TSV carries the Atlas IPAdapter ref, not the consumer shadow ref.
        ipadapter_row = next(
            line for line in lines if line.startswith("ComfyUI_IPAdapter_plus\t")
        )
        assert _NODE_SHA_IPADAPTER in ipadapter_row
        assert _NODE_SHA_SHADOW not in ipadapter_row

    def test_consumer_only_node_written_without_model_requirement(
        self, tmp_path, monkeypatch
    ):
        """#905 fix: a from_consumer node NOT in any active model's
        ``requires_custom_node`` is still written to the install TSV.

        Pre-fix this was dropped by the model-gating predicate; the
        ``or node.from_consumer`` clause in ``active_custom_nodes`` keeps it.
        """
        atlas_file = _write_nodes_yaml(
            tmp_path / "atlas-nodes.yaml",
            [
                {
                    "name": "ComfyUI-GGUF",
                    "repo": "https://github.com/city96/ComfyUI-GGUF.git",
                    "ref": _NODE_SHA_GGUF,
                    "install_requirements": True,
                },
            ],
        )
        consumer_file = _write_nodes_yaml(
            tmp_path / "consumer-nodes.yaml",
            [
                {
                    "name": "comfyui-krea2edit",
                    "repo": "https://github.com/krea-ai/comfyui-krea2edit.git",
                    "ref": _NODE_SHA_KREA,
                },
            ],
        )

        import utils.comfyui_custom_nodes as ccn
        monkeypatch.setattr(ccn, "_host_repo_custom_nodes", lambda: atlas_file)

        # Active model requires NO custom nodes — the consumer node must still appear.
        catalog = [_entry("PlainSDXL")]
        env = {
            "COMFYUI_SOURCE": "container",
            "COMFYUI_USER_MODELS": "PlainSDXL",
            "COMFYUI_CUSTOM_NODES_FILE": str(consumer_file),
        }

        import utils.comfyui_resolver as resolver
        monkeypatch.setattr(resolver, "active_comfyui_models", lambda e, **kw: catalog)

        ComfyUIManifestGenerator(env).write(tmp_path)

        lines = (tmp_path / "active-custom-nodes.tsv").read_text(encoding="utf-8").splitlines()
        names = [line.split("\t")[0] for line in lines]
        assert "comfyui-krea2edit" in names, (
            "Consumer-declared node was dropped — #905 regression: "
            "from_consumer nodes must be active unconditionally."
        )
        # The Atlas node (model-gated, not required by PlainSDXL) is correctly absent,
        # proving the unconditional clause only lifts consumer nodes, not Atlas ones.
        assert "ComfyUI-GGUF" not in names

    @pytest.mark.parametrize(
        "field,value",
        [
            ("name", "Bad\tName"),
            ("name", "Bad\nName"),
            ("name", "Bad\rName"),
            ("repo", "https://github.com/x/y.git\tinjected"),
            ("repo", "https://github.com/x/y.git\ninjected"),
            ("ref", "abc\tdef"),
            ("ref", "abc\ndef"),
        ],
    )
    def test_custom_node_tsv_rejects_unsafe_fields(
        self, tmp_path, monkeypatch, field, value
    ):
        """#905 preserves the TSV field-escaping contract: a custom-node field
        containing a tab / newline / carriage-return must raise rather than
        shift TSV columns (mirrors ``test_tsv_rejects_unsafe_fields`` for the
        custom-node install plan)."""
        base: dict = {
            "name": "SafeNode",
            "repo": "https://github.com/foo/bar.git",
            "ref": "a" * 40,
            "install_requirements": False,
        }
        base[field] = value
        bad_node = ComfyUICustomNode(**base)

        # Bypass the loader (which would reject some of these) so we exercise
        # the TSV-write guard in ``_safe_custom_node_field`` directly.
        import utils.comfyui_custom_nodes as ccn
        monkeypatch.setattr(
            ccn, "active_custom_nodes", lambda entries, env=None: [bad_node]
        )

        catalog = [_entry("AnyModel", filename="any.safetensors")]
        import utils.comfyui_resolver as resolver
        monkeypatch.setattr(resolver, "active_comfyui_models", lambda e, **kw: catalog)

        env = {"COMFYUI_SOURCE": "container", "COMFYUI_USER_MODELS": "AnyModel"}
        with pytest.raises(ValueError):
            ComfyUIManifestGenerator(env).write(tmp_path)


# ---------------------------------------------------------------------------
# Test 3: download_models.sh does NOT reference public.comfyui_models
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_download_models_sh_no_public_comfyui_models():
    """download_models.sh must not query public.comfyui_models (C3 regression guard)."""
    text = (REPO_ROOT / "services/comfyui/init/scripts/download_models.sh").read_text()
    assert "public.comfyui_models" not in text, (
        "download_models.sh still references public.comfyui_models — "
        "model list must come from $MANIFEST_TSV (bootstrapper-generated TSV), not the DB."
    )


def test_download_models_sh_no_psql_call():
    """download_models.sh must not invoke psql as a command (no DB dependency after C3).

    Comments mentioning 'psql' (historical context) are acceptable; executable
    psql invocations are not.  We check that no non-comment line contains the
    word psql.
    """
    text = (REPO_ROOT / "services/comfyui/init/scripts/download_models.sh").read_text()
    code_lines = [
        line for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    for line in code_lines:
        assert "psql" not in line, (
            f"download_models.sh has a psql call on a non-comment line: {line!r} — "
            "it should read $MANIFEST_TSV instead."
        )


def test_download_models_sh_reads_manifest_tsv():
    """download_models.sh must reference MANIFEST_TSV (the bootstrapper-generated file)."""
    text = (REPO_ROOT / "services/comfyui/init/scripts/download_models.sh").read_text()
    assert "MANIFEST_TSV" in text, (
        "download_models.sh does not reference MANIFEST_TSV — "
        "it should read the bootstrapper manifest, not the DB."
    )


def test_download_models_sh_reads_target_dir_column():
    """download_models.sh must honor the explicit target_dir TSV column, and
    split rows with `cut` so an empty sha column does not shift target_dir into
    sha (whitespace-IFS `read` collapses the two adjacent tabs)."""
    text = (REPO_ROOT / "services/comfyui/init/scripts/download_models.sh").read_text()
    assert "cut -f5" in text and "cut -f6" in text
    # The old whitespace-IFS 6-variable read silently mis-parsed no-sha rows.
    assert "read -r name category filename url sha target_dir" not in text
    assert 'dir="$target_dir"' in text
    assert "unsafe target_dir" in text


def test_download_models_sh_parse_preserves_empty_sha_column():
    """Regression guard for the cache-defeat bug: a no-checksum row (empty sha
    column) must parse with sha empty and target_dir intact. The prior
    whitespace-IFS `read` collapsed the two adjacent tabs, put the target_dir
    value into sha, and re-downloaded cached multi-GB models on every restart."""
    import subprocess

    row = "flux\tcheckpoints\tflux.safetensors\thttps://example/flux\t\tcheckpoints\n"
    harness = (
        "IFS= read -r _row\n"
        'printf "%s|%s\\n" '
        '"$(printf "%s\\n" "$_row" | cut -f5)" '
        '"$(printf "%s\\n" "$_row" | cut -f6)"\n'
    )
    result = subprocess.run(
        ["sh", "-c", harness], input=row, capture_output=True, text=True
    )
    # sha empty, target_dir preserved.
    assert result.stdout.strip() == "|checkpoints"


def test_download_models_sh_keeps_model_downloader_db_free():
    """comfyui-init remains the model downloader; custom nodes use AI-Dock provisioning."""
    text = (REPO_ROOT / "services/comfyui/init/scripts/download_models.sh").read_text()
    assert "CUSTOM_NODES_TSV" not in text
    assert "install_custom_node" not in text


def test_comfyui_provisioning_installs_active_custom_nodes():
    """The AI-Dock provisioning hook must clone pins and install requirements in-runtime."""
    text = (REPO_ROOT / "services/comfyui/provisioning/provision_custom_nodes.sh").read_text()
    assert "CUSTOM_NODES_TSV" in text
    assert "install_custom_node" in text
    assert "git clone" in text
    assert "checkout --detach" in text
    assert "install_requirements" in text
    assert "COMFYUI_VENV_PIP" in text
    assert "unsafe custom node name" in text


def test_comfyui_init_compose_no_pg_env():
    """comfyui-init in compose.yml must not inject PGHOST/PGPASSWORD (no DB)."""
    text = (REPO_ROOT / "services/comfyui/compose.yml").read_text()
    # Extract only the comfyui-init block (roughly between comfyui-init: and comfyui:).
    start = text.find("  comfyui-init:")
    end = text.find("  comfyui:", start)
    block = text[start:end]
    assert "PGHOST" not in block, "comfyui-init compose block still has PGHOST"
    assert "PGPASSWORD" not in block, "comfyui-init compose block still has PGPASSWORD"


def test_comfyui_init_compose_has_manifest_mount():
    """comfyui-init in compose.yml must bind-mount volumes/comfyui."""
    text = (REPO_ROOT / "services/comfyui/compose.yml").read_text()
    start = text.find("  comfyui-init:")
    end = text.find("  comfyui:", start)
    block = text[start:end]
    assert "volumes/comfyui" in block, (
        "comfyui-init compose block missing volumes/comfyui bind-mount"
    )
    assert "COMFYUI_MANIFEST_TSV" in block, (
        "comfyui-init compose block missing COMFYUI_MANIFEST_TSV env var"
    )


def test_comfyui_compose_mounts_custom_node_provisioning_hook():
    """comfyui runtime must run the custom-node installer inside AI-Dock's env."""
    text = (REPO_ROOT / "services/comfyui/compose.yml").read_text()
    start = text.find("  comfyui:")
    end = text.find("\n\nvolumes:", start)
    block = text[start:end]
    assert "COMFYUI_CUSTOM_NODES_TSV" in block
    assert "COMFYUI_CUSTOM_NODES_PATH" in block
    assert "provision_custom_nodes.sh:/opt/ai-dock/bin/provisioning.sh:ro" in block
    assert "../../volumes/comfyui:/comfyui-manifest:ro" in block


def test_comfyui_service_exposes_pinned_core_ref():
    """The ai-dock runtime must update ComfyUI to a pinned upstream tag."""
    manifest = yaml.safe_load((REPO_ROOT / "services/comfyui/service.yml").read_text())
    env = {entry["name"]: entry for entry in manifest["env"]}
    assert env["COMFYUI_AUTO_UPDATE"]["default"] is True
    assert env["COMFYUI_REF"]["default"] == "v0.27.0"

    compose = (REPO_ROOT / "services/comfyui/compose.yml").read_text()
    start = compose.find("  comfyui:")
    end = compose.find("\n\nvolumes:", start)
    block = compose[start:end]
    assert "COMFYUI_REF=${COMFYUI_REF:-v0.27.0}" in block


# ---------------------------------------------------------------------------
# Test 4: manifest round-trips through the resolver
# ---------------------------------------------------------------------------

class TestManifestRoundTrip:
    """C3 T4 — active set matches COMFYUI_USER_MODELS + sidecar selection."""

    def test_user_models_csv_selects_subset(self, tmp_path, monkeypatch):
        """Only the named models appear in the manifest."""
        catalog = [
            _entry("Alpha"),
            _entry("Beta"),
            _entry("Gamma"),
        ]
        env = {
            "COMFYUI_SOURCE": "container",
            "COMFYUI_USER_MODELS": "Alpha,Gamma",
        }

        import utils.comfyui_resolver as resolver
        monkeypatch.setattr(
            resolver,
            "active_comfyui_models",
            lambda e, **kw: [m for m in catalog if m.name in {"Alpha", "Gamma"}],
        )

        ComfyUIManifestGenerator(env).write(tmp_path)
        data = yaml.safe_load((tmp_path / "selected-models.yaml").read_text())
        names = {m["name"] for m in data["models"]}
        assert names == {"Alpha", "Gamma"}, f"Unexpected names: {names}"

    def test_tsv_rows_match_yaml_rows(self, tmp_path, monkeypatch):
        """TSV row count equals YAML model count."""
        catalog = [
            _entry("M1", filename="m1.safetensors"),
            _entry("M2", filename="m2.safetensors"),
            _entry("M3", filename="m3.safetensors"),
        ]
        env = {"COMFYUI_SOURCE": "container", "COMFYUI_USER_MODELS": "M1,M2,M3"}

        import utils.comfyui_resolver as resolver
        monkeypatch.setattr(
            resolver, "active_comfyui_models", lambda e, **kw: catalog
        )

        ComfyUIManifestGenerator(env).write(tmp_path)
        yaml_rows = yaml.safe_load(
            (tmp_path / "selected-models.yaml").read_text()
        )["models"]
        tsv_lines = [
            l for l in (tmp_path / "active-models.tsv").read_text().splitlines() if l
        ]
        assert len(yaml_rows) == len(tsv_lines), (
            f"YAML has {len(yaml_rows)} rows but TSV has {len(tsv_lines)} rows"
        )

    def test_tsv_names_match_yaml_names(self, tmp_path, monkeypatch):
        """TSV name column matches YAML name field for every row."""
        catalog = [
            _entry("X1", filename="x1.safetensors"),
            _entry("X2", filename="x2.safetensors"),
        ]
        env = {"COMFYUI_SOURCE": "container", "COMFYUI_USER_MODELS": "X1,X2"}

        import utils.comfyui_resolver as resolver
        monkeypatch.setattr(
            resolver, "active_comfyui_models", lambda e, **kw: catalog
        )

        ComfyUIManifestGenerator(env).write(tmp_path)
        yaml_names = {
            m["name"]
            for m in yaml.safe_load(
                (tmp_path / "selected-models.yaml").read_text()
            )["models"]
        }
        tsv_names = {
            l.split("\t")[0]
            for l in (tmp_path / "active-models.tsv").read_text().splitlines()
            if l
        }
        assert yaml_names == tsv_names
