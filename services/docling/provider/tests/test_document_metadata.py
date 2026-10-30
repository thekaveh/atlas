"""Mandatory real-model renderer and deployment-import regressions.

Run separately in each already installed provider model environment (no pytest):
    <python> -B services/docling/provider/tests/test_document_metadata.py \
        --expected-core 2.104.0

A missing or different core version fails. The 2.104 model-only closure qualifies
pinned model/renderer behavior, not the full GPU installation or inference.
"""

import argparse
from contextlib import contextmanager
import importlib.abc
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


PROVIDER = Path(__file__).resolve().parents[1]
FORMULA = "E=mc^2"
FORMATS = ("markdown", "html", "json", "doctags")
MODULE_NAMES = (
    "models", "utils", "pipeline_config", "document_metadata", "shared",
    "shared.pipeline_config", "shared.document_metadata",
)
INFERENCE_MODULES = ("torch", "scipy", "docling.document_converter", "transformers", "accelerate")


class NoConverter(importlib.abc.MetaPathFinder):
    """Exclude only converter import while loading the renderer under test."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "docling.document_converter":
            raise ImportError("Renderer test excludes converter import")


def assert_no_inference():
    loaded = [name for name in sys.modules if any(
        name == root or name.startswith(root + ".") for root in INFERENCE_MODULES
    )]
    if loaded:
        raise AssertionError(f"Inference modules loaded: {loaded}")


def load_gpu_support_modules():
    for name in ("models", "utils"):
        spec = importlib.util.spec_from_file_location(name, PROVIDER / "shared" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module


@contextmanager
def processor_source(mode, layout="direct", directory=None):
    """Load real source and restore import state, including direct-load fallbacks."""
    original_path = sys.path[:]
    original_modules = {name: sys.modules.pop(name) for name in MODULE_NAMES if name in sys.modules}
    mode_dir = PROVIDER / mode
    if layout == "native":
        paths = [str(mode_dir), str(PROVIDER)]
    elif layout == "flat":
        mode_dir = directory
        paths = [str(directory)]
    else:
        paths = [str(mode_dir)]
        if mode == "gpu":
            # GPU source has flat models/utils in its image; preload those real
            # files to exercise processor.py's shared-directory fallback here.
            load_gpu_support_modules()
    sys.path[:] = paths + [p for p in original_path if not p.startswith(str(PROVIDER))]
    guard = NoConverter()
    try:
        spec = importlib.util.spec_from_file_location("formula_test_processor", mode_dir / "processor.py")
        processor = importlib.util.module_from_spec(spec)
        sys.meta_path.insert(0, guard)
        try:
            spec.loader.exec_module(processor)
        finally:
            sys.meta_path.remove(guard)
        if processor.DocumentConverter is not None:
            raise AssertionError("Renderer import constructed or exposed a converter")
        assert_no_inference()
        yield processor
    finally:
        sys.path[:] = original_path
        for name in MODULE_NAMES:
            sys.modules.pop(name, None)
        sys.modules.update(original_modules)


def fixture(formulas):
    from docling_core.types.doc import (
        BoundingBox, DocItemLabel, DoclingDocument, ProvenanceItem, Size, TableData,
    )
    document = DoclingDocument(name="formula fixture")
    for page in (1, 2):
        document.add_page(page_no=page, size=Size(width=612, height=792))
    provenance = ProvenanceItem(
        page_no=1, bbox=BoundingBox(l=10, t=20, r=100, b=40), charspan=(0, len(FORMULA)),
    )
    document.add_text(label=DocItemLabel.TEXT, text="Ordinary text with $math-like$ prose", prov=provenance)
    for _ in range(formulas):
        document.add_formula(text=FORMULA, prov=provenance)
    document.add_table(data=TableData(num_rows=0, num_cols=0, table_cells=[]), prov=provenance)
    document.add_picture(prov=provenance)
    return document


def serialized_content(document, output_format):
    if output_format == "json":
        return json.dumps(document.export_to_dict(), indent=2)
    exporter = {
        "markdown": document.export_to_markdown,
        "html": document.export_to_html,
        "doctags": document.export_to_doctags,
    }[output_format]
    return exporter()


def expected_chunks(content):
    return [
        {"text": content[start:start + 41], "metadata": {
            "chunk_index": index, "chunk_type": "text",
            "page_number": None, "section_title": None,
        }}
        for index, start in enumerate(range(0, len(content), 34))
    ]


def render(processor, document, input_path, request):
    output_format, chunking = request
    with patch.object(processor.time, "time", return_value=100.0):
        return processor.render_conversion(
            SimpleNamespace(document=document), file_path=str(input_path),
            output_format=output_format, enable_chunking=chunking,
            chunk_size=41, chunk_overlap=7, started_at=98.5,
        ).model_dump()


class DocumentMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="docling-formula-test-")
        self.addCleanup(self.temporary.cleanup)
        self.input_path = Path(self.temporary.name) / "fixture.pdf"
        self.input_path.write_bytes(b"real test input\x00\n")
        self.expected_metadata = {
            "pages": 2, "tables": 1, "images": 1, "processing_time": 1.5,
            "source_format": "pdf", "file_size": 17,
        }

    def assert_formula_content(self, content, output_format, formulas):
        if output_format == "json":
            items = json.loads(content)["texts"]
            actual = [item["text"] for item in items if item["label"] == "formula"]
            self.assertEqual(actual, [FORMULA] * formulas)
        else:
            marker = {
                "markdown": "$$E=mc^2$$",
                "html": '<annotation encoding="TeX">E=mc^2</annotation>',
                "doctags": "<formula>",
            }[output_format]
            self.assertEqual(content.count(marker), formulas)
            if output_format == "doctags":
                self.assertEqual(content.count(FORMULA), formulas)

    def assert_response(self, response, document, case):
        formulas, output_format, chunking = case
        content = serialized_content(document, output_format)
        self.assertEqual(response["content"], content)
        self.assertEqual(response["format"], output_format)
        self.assert_formula_content(content, output_format, formulas)
        self.assertEqual(response["chunks"], expected_chunks(content) if chunking else None)
        self.assertEqual(response["metadata"], {**self.expected_metadata, "formulas": formulas})

    def test_renderer_matrix(self):
        """48 rows: both providers, 0/1/3 formulas, four exports, chunks on/off."""
        responses = {}
        for mode in ("gpu", "localhost"):
            with processor_source(mode) as processor:
                for formulas in (0, 1, 3):
                    document = fixture(formulas)
                    for output_format in FORMATS:
                        for chunking in (False, True):
                            with self.subTest(mode=mode, formulas=formulas, format=output_format, chunks=chunking):
                                response = render(processor, document, self.input_path, (output_format, chunking))
                                self.assert_response(response, document, (formulas, output_format, chunking))
                                key = (formulas, output_format, chunking)
                                if mode == "gpu":
                                    responses[key] = response
                                else:
                                    self.assertEqual(response, responses[key])

    def test_empty_document_page_fallback(self):
        from docling_core.types.doc import DoclingDocument
        document = DoclingDocument(name="empty")
        self.expected_metadata.update(pages=1, tables=0, images=0)
        for mode in ("gpu", "localhost"):
            with processor_source(mode) as processor:
                for output_format in FORMATS:
                    with self.subTest(mode=mode, format=output_format):
                        response = render(processor, document, self.input_path, (output_format, True))
                        self.assert_response(response, document, (0, output_format, True))

    def test_storage_policy(self):
        from docling_core.types.doc import ContentLayer, FormulaItem
        document = fixture(1)
        formula = next(item for item in document.texts if isinstance(item, FormulaItem))
        formula.prov.append(formula.prov[0].model_copy())
        document.body.children.append(formula.get_ref())
        document.add_formula(text=FORMULA, content_layer=ContentLayer.FURNITURE)
        document.add_formula(text=FORMULA, content_layer=ContentLayer.INVISIBLE)
        for mode in ("gpu", "localhost"):
            with processor_source(mode) as processor:
                with self.subTest(mode=mode):
                    response = render(processor, document, self.input_path, ("json", False))
                    self.assertEqual(response["metadata"]["formulas"], 3)
                    self.assertEqual(response["content"], serialized_content(document, "json"))

    def test_serialized_reload(self):
        from docling_core.types.doc import DoclingDocument, FormulaItem
        document = DoclingDocument.model_validate_json(fixture(3).model_dump_json())
        self.assertEqual(sum(isinstance(item, FormulaItem) for item in document.texts), 3)
        for mode in ("gpu", "localhost"):
            with processor_source(mode) as processor:
                for output_format in FORMATS:
                    with self.subTest(mode=mode, format=output_format):
                        response = render(processor, document, self.input_path, (output_format, True))
                        self.assert_response(response, document, (3, output_format, True))

    def test_packaging_imports_in_fresh_processes(self):
        for layout, mode in (("native", "localhost"), ("direct", "localhost"), ("direct", "gpu"), ("flat", "gpu")):
            with self.subTest(layout=layout, mode=mode):
                command = [sys.executable, "-B", str(Path(__file__).resolve()),
                           "--expected-core", EXPECTED_CORE, "--packaging-child", layout, "--mode", mode]
                environment = {key: value for key, value in os.environ.items() if key not in ("PYTHONPATH", "PYTHONHOME")}
                result = subprocess.run(command, cwd=self.temporary.name, env=environment,
                                        text=True, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                print(result.stdout.strip(), flush=True)

    @classmethod
    def tearDownClass(cls):
        assert_no_inference()


def packaging_probe(layout, mode):
    with tempfile.TemporaryDirectory(prefix="docling-formula-flat-") as temporary:
        directory = Path(temporary)
        if layout == "flat":
            for source in (PROVIDER / "shared").glob("*.py"):
                shutil.copy2(source, directory / source.name)
            for name in ("bounded_upload.py", "provider_boundary.py", "lightrag_bundle.py"):
                shutil.copy2(PROVIDER / name, directory / name)
            shutil.copy2(PROVIDER / "gpu" / "processor.py", directory / "processor.py")
        with processor_source(mode, layout, directory) as processor:
            helper = sys.modules[processor.count_formulas.__module__]
            expected = (directory if layout == "flat" else PROVIDER / "shared").resolve()
            if Path(helper.__file__).resolve() != expected / "document_metadata.py":
                raise AssertionError(f"Wrong helper origin: {helper.__file__}")
            if "docling_core" in sys.modules:
                raise AssertionError("Shared helper eagerly imported document models")
            from docling_core.types.doc import DoclingDocument
            document = DoclingDocument(name="packaging")
            document.add_formula(text=FORMULA)
            if processor.count_formulas(document) != 1:
                raise AssertionError("Packaged helper did not count the real formula")
            assert_no_inference()
            print(json.dumps({"layout": layout, "mode": mode, "core": EXPECTED_CORE,
                              "helper_origin": str(Path(helper.__file__).resolve())}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-core", required=True, choices=("2.104.0",))
    parser.add_argument("--packaging-child", choices=("native", "direct", "flat"))
    parser.add_argument("--mode", choices=("gpu", "localhost"))
    arguments = parser.parse_args()
    EXPECTED_CORE = arguments.expected_core
    actual_core = importlib.metadata.version("docling-core")
    if actual_core != EXPECTED_CORE:
        raise SystemExit(f"Expected docling-core {EXPECTED_CORE}; installed {actual_core}")
    print(f"ACTUAL_CORE={actual_core}; GPU pin=2.104.0; localhost pin=2.104.0", flush=True)
    if arguments.packaging_child:
        packaging_probe(arguments.packaging_child, arguments.mode)
    else:
        unittest.main(argv=[sys.argv[0]], verbosity=2)
