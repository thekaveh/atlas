"""Docling's LightRAG bundle is deterministic, complete, and path-safe."""

from __future__ import annotations

import importlib.util
import io
import sys
import types
import zipfile
from pathlib import Path, PurePosixPath

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "services" / "docling" / "provider" / "lightrag_bundle.py"


def _load_bundle_module(monkeypatch):
    doc_module = types.ModuleType("docling_core.types.doc")

    class ImageRefMode:
        REFERENCED = "referenced"

    doc_module.ImageRefMode = ImageRefMode
    types_module = types.ModuleType("docling_core.types")
    core_module = types.ModuleType("docling_core")
    monkeypatch.setitem(sys.modules, "docling_core", core_module)
    monkeypatch.setitem(sys.modules, "docling_core.types", types_module)
    monkeypatch.setitem(sys.modules, "docling_core.types.doc", doc_module)

    spec = importlib.util.spec_from_file_location("docling_lightrag_bundle", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeDocument:
    def __init__(self, *, make_symlink: bool = False):
        self.json_calls = 0
        self.markdown_calls = 0
        self.make_symlink = make_symlink

    def save_as_json(self, filename, *, artifacts_dir, image_mode):
        self.json_calls += 1
        assert image_mode == "referenced"
        Path(filename).write_text('{"schema_name":"DoclingDocument"}', encoding="utf-8")
        asset_dir = Path(artifacts_dir)
        asset_dir.mkdir(parents=True, exist_ok=True)
        (asset_dir / "json-image.png").write_bytes(b"json-png")

    def save_as_markdown(self, filename, *, artifacts_dir, image_mode):
        self.markdown_calls += 1
        assert image_mode == "referenced"
        Path(filename).write_text("# Converted\n\n![figure](assets/figure.png)\n", encoding="utf-8")
        asset_dir = Path(artifacts_dir)
        asset_dir.mkdir(parents=True, exist_ok=True)
        (asset_dir / "figure.png").write_bytes(b"png")
        if self.make_symlink:
            outside = asset_dir.parent / "outside.txt"
            outside.write_text("private", encoding="utf-8")
            try:
                (asset_dir / "escape.txt").symlink_to(outside)
            except OSError:
                pass


class FakeResult:
    def __init__(self, document):
        self.document = document


def test_bundle_exports_json_markdown_and_assets_once(monkeypatch):
    bundle = _load_bundle_module(monkeypatch)
    document = FakeDocument()

    payload = bundle.build_lightrag_bundle(
        FakeResult(document), upload_name="Quarterly Report.pdf"
    )

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        assert names == [
            "Quarterly_Report.json",
            "Quarterly_Report.md",
            "assets/figure.png",
            "assets/json-image.png",
        ]
        assert archive.read("Quarterly_Report.json").startswith(b'{"schema_name"')
        assert archive.read("Quarterly_Report.md").startswith(b"# Converted")
        assert archive.read("assets/figure.png") == b"png"
        assert archive.read("assets/json-image.png") == b"json-png"
    assert document.json_calls == 1
    assert document.markdown_calls == 1


@pytest.mark.parametrize(
    "upload_name",
    [
        "../../secret.pdf",
        "/absolute/private.pdf",
        "..\\..\\windows-secret.docx",
        "nul\x00name.pdf",
        "....pdf",
        "   .pdf",
    ],
)
def test_bundle_canonicalizes_untrusted_upload_names(monkeypatch, upload_name):
    bundle = _load_bundle_module(monkeypatch)

    payload = bundle.build_lightrag_bundle(
        FakeResult(FakeDocument()), upload_name=upload_name
    )

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
    assert len(names) == len(set(names))
    for name in names:
        path = PurePosixPath(name)
        assert not path.is_absolute()
        assert ".." not in path.parts
        assert "\x00" not in name


def test_bundle_never_follows_symlinked_artifacts(monkeypatch):
    bundle = _load_bundle_module(monkeypatch)

    payload = bundle.build_lightrag_bundle(
        FakeResult(FakeDocument(make_symlink=True)), upload_name="safe.pdf"
    )

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
    assert "assets/escape.txt" not in names
    assert all("outside" not in name for name in names)


def test_bundle_rejects_a_result_without_a_docling_document(monkeypatch):
    bundle = _load_bundle_module(monkeypatch)

    with pytest.raises(ValueError, match="document"):
        bundle.build_lightrag_bundle(object(), upload_name="safe.pdf")
