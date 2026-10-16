from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path

import pytest

from tests.test_docling_guide_examples import probe
from tests.test_three_surface_build import env_reference_config


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "services/docling/provider/shared/pipeline_config.py"


def _load_pipeline_module():
    spec = importlib.util.spec_from_file_location("docling_pipeline_config", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_pipeline_settings_honor_request_and_environment_contracts():
    pipeline = _load_pipeline_module()

    settings = pipeline.resolve_pipeline_settings(
        use_ocr="always",
        table_mode="fast",
        device="mps",
        enable_formulas="true",
        enable_code_blocks="false",
    )

    assert settings.do_ocr is True
    assert settings.force_full_page_ocr is True
    assert settings.table_mode == "fast"
    assert settings.device == "mps"
    assert settings.do_formula_enrichment is True
    assert settings.do_code_enrichment is False


def test_pipeline_settings_reject_unsupported_values():
    pipeline = _load_pipeline_module()

    for field, kwargs in (
        ("use_ocr", {"use_ocr": "sometimes"}),
        ("table_mode", {"table_mode": "turbo"}),
        ("device", {"device": "quantum"}),
    ):
        try:
            pipeline.resolve_pipeline_settings(**kwargs)
        except ValueError as exc:
            assert field in str(exc)
        else:
            raise AssertionError(f"unsupported {field} value was accepted")


def test_chunk_defaults_honor_environment_and_reject_invalid_pairs():
    pipeline = _load_pipeline_module()

    assert pipeline.resolve_chunk_defaults(
        {
            "DOCLING_CHUNK_SIZE": "900",
            "DOCLING_CHUNK_OVERLAP": "120",
        }
    ) == (900, 120)

    for values in (
        {"DOCLING_CHUNK_SIZE": "0", "DOCLING_CHUNK_OVERLAP": "0"},
        {"DOCLING_CHUNK_SIZE": "100", "DOCLING_CHUNK_OVERLAP": "100"},
        {"DOCLING_CHUNK_SIZE": "invalid", "DOCLING_CHUNK_OVERLAP": "10"},
    ):
        try:
            pipeline.resolve_chunk_defaults(values)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid chunk defaults were accepted: {values}")


def test_chunk_settings_reject_overlap_that_consumes_the_chunk():
    pipeline = _load_pipeline_module()

    assert pipeline.validate_chunk_settings(512, 50) == (512, 50)
    for size, overlap in (
        (0, 0),
        (100, -1),
        (100, 51),
        (100, 100),
        (100, 101),
    ):
        try:
            pipeline.validate_chunk_settings(size, overlap)
        except ValueError:
            pass
        else:
            raise AssertionError(
                f"invalid request chunk settings were accepted: {(size, overlap)}"
            )


def test_both_chunkers_bound_overlap_and_total_chunk_count():
    for relative, name in (
        ("services/docling/provider/shared/utils.py", "docling_shared_utils"),
        ("services/docling/provider/localhost/utils.py", "docling_local_utils"),
    ):
        utils = _load_module(ROOT / relative, name)
        chunks = utils.chunk_text("x" * 10_000, 512, 511)
        assert len(chunks) == 20

        utils.MAX_CHUNKS = 5
        try:
            utils.chunk_text("abcdef", 1, 0)
        except utils.ChunkLimitError:
            pass
        else:
            raise AssertionError(f"{relative} accepted more than MAX_CHUNKS")


def test_both_docling_apis_use_manifest_owned_chunk_defaults():
    for relative in (
        "services/docling/provider/shared/api_server.py",
        "services/docling/provider/localhost/server.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "resolve_chunk_defaults" in source
        assert "Form(default=_CHUNK_DEFAULTS.size" in source
        assert "Form(default=_CHUNK_DEFAULTS.overlap" in source
        assert "validate_chunk_settings(chunk_size, chunk_overlap)" in source
        assert "status_code=422" in source
        assert "except ChunkLimitError" in source
        assert "status_code=413" in source
        assert "chunk_size: int = Form(default=512)" not in source
        assert "chunk_overlap: int = Form(default=50)" not in source


def test_gpu_torch_requirements_match_base_image_patch():
    dockerfile = (ROOT / "services/docling/provider/gpu/Dockerfile").read_text()
    requirements = (ROOT / "services/docling/provider/gpu/requirements.txt").read_text()

    assert "pytorch/pytorch:2.13.0-" in dockerfile
    assert "torch==2.13.0" in requirements
    assert "torchvision==0.28.0" in requirements


def test_converter_readiness_is_nonblocking_and_truthful(monkeypatch):
    pipeline = _load_pipeline_module()
    settings = pipeline.resolve_pipeline_settings()

    async def run():
        monkeypatch.setattr(pipeline, "build_converter", lambda _settings: object())
        assert await pipeline.converter_status(settings) == "starting"
        await asyncio.sleep(0.01)
        assert await pipeline.converter_status(settings) == "healthy"

    asyncio.run(run())


def test_provider_health_uses_converter_status():
    for relative in (
        "services/docling/provider/gpu/processor.py",
        "services/docling/provider/localhost/processor.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "async def processor_status" in source
        assert "await converter_status(settings)" in source
        assert 'return "unavailable"' in source
    api_source = (
        ROOT / "services/docling/provider/shared/api_server.py"
    ).read_text(encoding="utf-8")
    assert 'models_loaded=["DocumentConverter"] if ready else []' in api_source
    assert 'models_loaded=["DocLayNet", "TableFormer"]' not in api_source


def test_both_docling_processors_separate_conversion_from_rendering():
    for relative in (
        "services/docling/provider/gpu/processor.py",
        "services/docling/provider/localhost/processor.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "def convert_document_once(" in source
        assert "def render_conversion(" in source
        assert "result = await asyncio.to_thread(" in source
        assert "convert_document_once," in source
        assert "return render_conversion(" in source


def test_both_docling_apis_install_boundary_and_deadline_expensive_routes():
    for relative in (
        "services/docling/provider/shared/api_server.py",
        "services/docling/provider/localhost/server.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "load_boundary_settings(" in source
        assert '"/v1/document/convert"' in source
        assert '"/internal/lightrag/bundle"' in source
        assert "install_provider_boundary(app," in source
        assert "run_with_deadline(" in source
        assert "fatal_timeout_response(" in source
        assert "CORSMiddleware" not in source


def test_docling_gpu_image_copies_bundle_and_boundary_modules():
    dockerfile = (ROOT / "services/docling/provider/gpu/Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "COPY provider_boundary.py /app/" in dockerfile
    assert "COPY lightrag_bundle.py /app/" in dockerfile


def test_converter_construction_is_single_flight_across_threads(monkeypatch):
    pipeline = _load_pipeline_module()
    settings = pipeline.resolve_pipeline_settings()
    calls = 0

    def construct(_settings):
        nonlocal calls
        calls += 1
        return object()

    monkeypatch.setattr(pipeline, "_construct_converter", construct)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(
            executor.map(pipeline.build_converter, [settings, settings])
        )

    assert first is second
    assert calls == 1


def test_docling_docs_keep_source_specific_launch_and_route_contracts(
    tmp_path, env_reference_config,
):
    aggregate = (ROOT / "services/doc-processor/README.md").read_text()
    quick_start = aggregate.split("## 2. Quick Start", 1)[1].split(
        "## 3. Test the API", 1
    )[0]
    localhost_flow = quick_start.split(
        "### 2.2. Localhost Users (CPU or Native GPU)", 1
    )[1].split(
        "### 2.3. Disable Document Processor", 1
    )[0]

    for context in ("container", "aggregate-native"):
        for variant in ("plain", "missing-token"):
            observed, expected = probe(
                ("repo", context, variant), tmp_path / context / variant,
                env_reference_config,
            )
            assert observed == expected
    assert "container-GPU provider only" in aggregate
    assert "localhost provider does not advertise that route" in aggregate
    assert "Step 3: Start doc processor server on host (Terminal 2, repository root)" in localhost_flow
    assert localhost_flow.index("cd ../../../..") < localhost_flow.index("./start.sh")
    assert localhost_flow.index("./start.sh") < localhost_flow.index("uv run server.py")


def test_docling_localhost_docs_start_atlas_before_each_provider_launch():
    localhost = (
        ROOT / "services/docling/provider/localhost/README.md"
    ).read_text()
    quick_start = localhost.split("## 2. Configuration", 1)[0]
    integration = localhost.split("## 6. Integration with Atlas", 1)[1].split(
        "## 7. Performance", 1
    )[0]
    assert quick_start.index("./start.sh") < quick_start.index("uv run server.py")
    assert quick_start.index("cd ../../../..") < quick_start.index("./start.sh")
    assert "Terminal 3, opened at the repository root" in quick_start
    assert "if still in the provider directory" not in quick_start
    for method in integration.split("### 6.")[1:]:
        assert "./start.sh" in method
        assert "uv run server.py" in method
        assert method.index("./start.sh") < method.index("uv run server.py")


def test_docling_localhost_docs_list_runtime_contract_variables():
    localhost = (
        ROOT / "services/docling/provider/localhost/README.md"
    ).read_text()
    for variable in (
        "DOCLING_API_TOKEN",
        "DOCLING_AUTH_MODE",
        "DOCLING_LOCALHOST_BIND_HOST",
        "DOCLING_MAX_FILE_SIZE",
        "DOCLING_UPLOAD_TIMEOUT_SECONDS",
        "DOCLING_INFERENCE_TIMEOUT_SECONDS",
    ):
        assert variable in localhost


def _stub_converter_construction(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace

    class JSONBackend:
        def __init__(self, document):
            self.document = document

        def convert(self):
            if isinstance(self.document, BaseException):
                raise self.document
            return self.document

    class SimplePipeline:
        pass

    class PDFOptions:
        def __init__(self):
            self.ocr_options = SimpleNamespace()
            self.table_structure_options = SimpleNamespace()

    class Converter:
        def __init__(self, *, format_options):
            default = SimpleNamespace(backend=JSONBackend, pipeline_cls=SimplePipeline)
            self.format_options = {"json": default, **format_options}

    modules = {
        "docling.datamodel.base_models": dict(InputFormat=SimpleNamespace(PDF="pdf", JSON_DOCLING="json")),
        "docling.datamodel.pipeline_options": dict(AcceleratorDevice=str, AcceleratorOptions=SimpleNamespace,
            PdfPipelineOptions=PDFOptions, TableFormerMode=str),
        "docling.document_converter": dict(DocumentConverter=Converter, PdfFormatOption=SimpleNamespace,
            FormatOption=SimpleNamespace),
        "docling.backend.json.docling_json_backend": dict(DoclingJSONBackend=JSONBackend),
        "docling.pipeline.simple_pipeline": dict(SimplePipeline=SimplePipeline),
    }
    for name, attributes in modules.items():
        module = ModuleType(name)
        vars(module).update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
    pipeline = _load_pipeline_module()
    settings = pipeline.resolve_pipeline_settings(use_ocr="always", table_mode="fast", device="mps")
    return pipeline, pipeline.build_converter(settings), settings


@pytest.mark.parametrize("case", [
    (collection, uri)
    for collection in ("pictures", "tables", "key_value_items", "form_items", "field_regions", "field_items", "pages")
    for uri in (Path("relative-image.png"), Path("/owned-test/no-such-private-image.png"),
                Path(r"C:\owned-test\image.png"), Path(r"\\owned-test\share\image.png"),
                "file", "ftp", "unknown")
])
def test_registered_json_backend_clears_all_filesystem_image_holders(monkeypatch, caplog, case):
    from types import SimpleNamespace

    collection, uri = case
    reference = uri if isinstance(uri, Path) else SimpleNamespace(scheme=uri)
    holder = SimpleNamespace(image=SimpleNamespace(uri=reference))
    values = {1: holder} if collection == "pages" else [holder]
    document = SimpleNamespace(**{collection: values}, texts=[SimpleNamespace(text="preserved text")])
    pipeline, converter, _settings = _stub_converter_construction(monkeypatch)
    result = converter.format_options["json"].backend(document).convert()
    assert holder.image is None
    assert result is document and result.texts[0].text == "preserved text"
    assert "count=1" in caplog.text
    assert "no-such-private-image" not in caplog.text and "relative-image.png" not in caplog.text
    converter.format_options["json"].backend(document).convert()
    assert caplog.text.count("count=1") == 1


@pytest.mark.parametrize("scheme", ["data", "http", "https"])
def test_registered_json_backend_keeps_allowed_image_references(monkeypatch, caplog, scheme):
    from types import SimpleNamespace

    image = SimpleNamespace(uri=SimpleNamespace(scheme=scheme))
    picture = SimpleNamespace(image=image)
    page = SimpleNamespace(image=image)
    document = SimpleNamespace(pictures=[picture], pages={1: page}, metadata="preserved")
    _pipeline, converter, _settings = _stub_converter_construction(monkeypatch)
    assert converter.format_options["json"].backend(document).convert() is document
    assert picture.image is image and page.image is image
    assert document.metadata == "preserved" and not caplog.records


def test_registered_json_backend_preserves_parse_error_and_pdf_cache(monkeypatch):
    pipeline, converter, settings = _stub_converter_construction(monkeypatch)
    assert pipeline.build_converter(settings) is converter
    options = converter.format_options["pdf"].pipeline_options
    assert options.do_ocr and options.ocr_options.force_full_page_ocr
    assert options.table_structure_options.mode == "fast"
    assert options.accelerator_options.device == "mps"
    failure = ValueError("owned parse failure")
    with pytest.raises(ValueError) as caught:
        converter.format_options["json"].backend(failure).convert()
    assert caught.value is failure
