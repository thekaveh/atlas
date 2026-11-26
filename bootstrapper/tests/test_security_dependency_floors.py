from __future__ import annotations

import io
import re
from pathlib import Path
import tokenize
import tomllib

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _assert_jupyterhub_nlp_asset_integrity(dockerfile: str) -> None:
    assert "--no-deps --require-hashes -r /tmp/nlp-model-requirements.txt" in dockerfile
    assert "install_nlp_assets.py install" in dockerfile
    assert "install_nlp_assets.py verify" in dockerfile
    assert "ENV NLTK_DATA=/home/jovyan/nltk_data" in dockerfile
    for retired in (
        "SPACY_MODEL_SHA256=",
        "NLTK_DATA_COMMIT=",
        "VADER_LEXICON_SHA256=",
    ):
        assert retired not in dockerfile


def _locked_version(relative: str, package: str) -> str:
    lock = tomllib.loads(_text(relative))
    return next(row["version"] for row in lock["package"] if row["name"] == package)


def _python_without_comments(source: str) -> str:
    return " ".join(
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type != tokenize.COMMENT
    )


_RAGAS_NOTEBOOK_PATTERNS = {
    "multimodal faithfulness": re.compile(
        r"multi_?modal_?faith(?:fulness|ness)", re.IGNORECASE
    ),
    "multimodal relevance": re.compile(
        r"multi_?modal_?relevance", re.IGNORECASE
    ),
    "disk cache": re.compile(r"diskcache", re.IGNORECASE),
    "cache argument or assignment": re.compile(r"\bcache\s*="),
}


def _notebook_code_cells(notebook: dict) -> str:
    sources: list[str] = []
    for cell in notebook.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        source = cell.get("source", "")
        sources.append("".join(source) if isinstance(source, list) else source)
    return "\n".join(sources)


def _ragas_notebook_findings(notebook: dict) -> set[str]:
    code = _notebook_code_cells(notebook)
    return {
        label
        for label, pattern in _RAGAS_NOTEBOOK_PATTERNS.items()
        if pattern.search(code)
    }


def test_python_multipart_security_floor_is_consistent() -> None:
    requirements = {
        "services/asset-worker/app/requirements.txt": "python-multipart==0.0.32",
        "services/asset-baker/app/requirements.txt": "python-multipart==0.0.32",
        "services/backend/app/app/requirements.txt": "python-multipart>=0.0.31",
        "services/docling/provider/gpu/requirements.txt": "python-multipart==0.0.32",
        "services/parakeet/provider/gpu/requirements.txt": "python-multipart>=0.0.31",
        "services/parakeet/provider/mlx/requirements.txt": "python-multipart>=0.0.31",
    }
    for relative, requirement in requirements.items():
        assert requirement in _text(relative), relative

    pyproject = tomllib.loads(
        _text("services/docling/provider/localhost/pyproject.toml")
    )
    assert "python-multipart>=0.0.31" in pyproject["project"]["dependencies"]
    assert (
        _locked_version("services/docling/provider/localhost/uv.lock", "python-multipart")
        == "0.0.32"
    )


def test_small_api_frameworks_resolve_patched_security_baselines() -> None:
    for relative in (
        "services/asset-worker/app/requirements.txt",
        "services/asset-baker/app/requirements.txt",
    ):
        requirements = _text(relative)
        assert "fastapi==0.139.0" in requirements, relative
        assert "uvicorn[standard]==0.51.0" in requirements, relative

    parakeet_gpu = _text("services/parakeet/provider/gpu/requirements.txt")
    assert "nemo_toolkit[asr]>=3.0.0,<4.0" in parakeet_gpu
    assert "transformers>=5.10.0,<6" in parakeet_gpu
    assert "onnx>=1.22.0" in parakeet_gpu
    assert "protobuf>=5.29.6,<5.30" in parakeet_gpu


def test_backend_ci_installs_the_owned_test_contract() -> None:
    requirements = _text("services/backend/app/app/requirements.txt")
    dev_requirements = _text("services/backend/app/app/requirements-dev.txt")
    workflow = _text(".github/workflows/services-lint.yml")
    dockerfile = _text("services/backend/app/Dockerfile")

    assert "pytest" not in requirements
    assert "httpx2" not in requirements
    assert "pytest==9.1.1" in dev_requirements
    assert "pytest-asyncio==1.4.0" in dev_requirements
    assert "httpx2==2.12.0" in dev_requirements
    assert "requirements-test-locked.txt" in workflow
    assert "requirements-dev.txt" not in dockerfile
    assert "pytest-asyncio omitted intentionally" not in workflow


def test_backend_fal_client_supports_cancellable_async_transport() -> None:
    requirements = _text("services/backend/app/app/requirements.txt")

    assert "fal-client>=1.0.0" in requirements


def test_github_actions_are_commit_pinned() -> None:
    workflow_dir = ROOT / ".github" / "workflows"
    workflows = sorted(
        [*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")]
    )
    # `(?:-\s+)?` also matches list-item steps (`- uses: actions/checkout@...`).
    action_ref = re.compile(r"^\s*(?:-\s+)?uses:\s*[^#\s]+@([^#\s]+)", re.MULTILINE)

    for workflow in workflows:
        for ref in action_ref.findall(workflow.read_text(encoding="utf-8")):
            assert re.fullmatch(r"[0-9a-f]{40}", ref), (
                f"{workflow.relative_to(ROOT)} uses mutable action ref {ref!r}"
            )


def test_airflow_uses_supported_core_and_unfrozen_provider_security_fixes() -> None:
    manifest = _text("services/airflow/service.yml")
    compose = _text("services/airflow/compose.yml")
    dockerfile = _text("services/airflow/build/Dockerfile")
    requirements = _text("services/airflow/build/requirements.txt")

    assert 'default: "apache/airflow:3.3.2"' in manifest
    assert "apache/airflow:3.3.2" in compose
    assert "ARG BASE_IMAGE=apache/airflow:3.3.2" in dockerfile
    assert "--constraint" not in dockerfile
    # #1329: the base's own tornado 6.5.8 and virtualenv 21.7.9 carry fixed
    # advisories, so the overlay raises them.
    assert "tornado>=6.6" in requirements
    # #782: the 6.x spark provider swapped its dep to pyspark-client, whose
    # overlapping module tree overwrites the pinned pyspark==4.1.2 files and
    # breaks `import pyspark` (SparkExecutorInfo ImportError → image build
    # fails). The cap and the classic-pyspark pin move only with the whole
    # Spark 4.1 family (cluster image + iceberg-spark-runtime-4.1).
    assert "apache-airflow-providers-apache-spark==5.6.0" in requirements
    assert "pyspark[connect]==4.1.2" in requirements


def test_airflow_overlay_remains_compatible_with_the_upstream_image() -> None:
    dockerfile = _text("services/airflow/build/Dockerfile")
    requirements = _text("services/airflow/build/requirements.txt")

    assert {
        "botocore<1.43.76",
        "filelock>=4.2.1,<5",
        "importlib-metadata<9",
        "protobuf<6.34",
        "websockets<17",
    } <= set(requirements.splitlines())
    # The overlay installs onto the base with the lock as a constraint. A lock
    # pinning another core release than the base, or a botocore outside the
    # base's aiobotocore range (3.9.1 on 3.3.2: >=1.43.66,<1.43.76), downgrades
    # the base and fails the Dockerfile's `pip check`; a pins-only 3.3.1 ->
    # 3.3.2 bump that kept botocore 1.43.56 would have done exactly that.
    lock = set(_text("services/airflow/build/requirements-locked.txt").splitlines())
    base = re.search(r"^ARG BASE_IMAGE=apache/airflow:(\S+)$", dockerfile, re.M).group(1)
    assert (
        f"apache-airflow=={base}" in lock,
        f"apache-airflow-core=={base}" in lock,
        "botocore==1.43.75" in lock,
    ) == (True, True, True)
    assert "python -m pip check" in dockerfile
    assert "rm -f /usr/bin/docker" in dockerfile
    assert "/home/airflow/.local/bin/uv" in dockerfile
    assert "/home/airflow/.local/bin/uvx" in dockerfile


@pytest.mark.parametrize(("package", "floor"), (
    ("certifi", "2026.10.19"),
    ("authlib", "1.9.1"),
    ("dnspython", "2.9.2"),
    ("gitpython", "3.2.1"),
    ("msgpack", "1.2.3"),
    ("setuptools", "84.1.0"),
    ("virtualenv", "21.14.13"),
    ("opentelemetry-api", "1.45.2"),
    ("opentelemetry-exporter-prometheus", "0.66b1"),
))
def test_airflow_security_floors_survive_overlay_refresh(package, floor) -> None:
    declarations = _text("services/airflow/build/requirements.txt").splitlines()
    matching = _requirements_named(declarations, package)
    assert len(matching) == 1, f"Missing durable {package} floor in Airflow inputs"
    assert str(matching[0].specifier) == f">={floor}"
    assert matching[0].marker is None


@pytest.mark.parametrize(("package", "version"), (
    ("certifi", "2026.10.19"),
    ("authlib", "1.9.1"),
    ("dnspython", "2.9.2"),
    ("gitpython", "3.2.1"),
    ("msgpack", "1.2.3"),
    ("setuptools", "84.1.0"),
    ("virtualenv", "21.14.13"),
    ("packaging", "26.3"),
    ("opentelemetry-api", "1.45.2"),
    ("opentelemetry-sdk", "1.45.2"),
    ("opentelemetry-proto", "1.45.2"),
    ("opentelemetry-exporter-otlp", "1.45.2"),
    ("opentelemetry-exporter-otlp-proto-common", "1.45.2"),
    ("opentelemetry-exporter-otlp-proto-grpc", "1.45.2"),
    ("opentelemetry-exporter-otlp-proto-http", "1.45.2"),
    ("opentelemetry-semantic-conventions", "0.66b2"),
    ("opentelemetry-exporter-otlp-common", "0.66b2"),
    ("opentelemetry-exporter-http-transport", "0.66b2"),
    ("opentelemetry-exporter-prometheus", "0.66b2"),
    ("prometheus-client", "0.26.0"),
))
def test_airflow_lock_contains_the_compatible_security_patch_family(package, version) -> None:
    lines = _text("services/airflow/build/requirements-locked.txt").splitlines()
    assert f"{package}=={version}" in lines


def test_lakehouse_images_share_hadoops_minio_compatible_aws_sdk_bundle() -> None:
    version = "ARG AWS_SDK_BUNDLE_VERSION=2.29.52"
    checksum = (
        "ARG AWS_SDK_BUNDLE_SHA512="
        "a909eb82364c6272cd4ff67aaff4123e4e5de9205d21c27d1f286e7748780a7b4"
        "c2b570c41d184f0600fabe482b1dc3f28d9c60ebbd4b8795666e45ecfb4802a"
    )
    for relative in (
        "services/airflow/build/Dockerfile",
        "services/spark/build/Dockerfile",
        "services/zeppelin/build/Dockerfile",
    ):
        dockerfile = _text(relative)
        assert version in dockerfile, relative
        assert checksum in dockerfile, relative
        assert "2.54.7" not in dockerfile, relative
        assert "2.41.30" not in dockerfile, relative


def test_security_evidence_matches_airflow_and_hydra_runtime_contracts() -> None:
    ledger = _text("docs/maintenance/external-contract-ledger.md")
    changelog = _text("docs/CHANGELOG.md")

    assert "Linux/Python 3.13 provider graph" in ledger
    assert "2026-11-27 re-review" in changelog
    assert "2026-09-01 re-review" not in changelog
    for dependency in (
        "botocore 1.43.56",
        "importlib-metadata 8.9.0",
        "protobuf 6.33.6",
        "websockets 16.1.1",
        "pyyaml-ft 8.0.0",
    ):
        assert dependency in changelog


def test_spark_image_applies_available_base_os_security_updates() -> None:
    dockerfile = _text("services/spark/build/Dockerfile")

    assert "apt-get update" in dockerfile
    assert "apt-get upgrade -y" in dockerfile
    assert "rm -rf /var/lib/apt/lists/*" in dockerfile


def test_airflow_build_validation_uses_runtime_core_release() -> None:
    workflow = _text(".github/workflows/services-lint.yml")
    dockerfile = _text("services/airflow/build/Dockerfile")
    contributor_docs = _text("docs/CONTRIBUTING-services.md")
    dependabot = _text(".github/dependabot.yml")

    assert "ARG BASE_IMAGE=apache/airflow:3.3.2" in dockerfile
    assert '"services/airflow/build|Dockerfile"' in workflow
    assert "--build-arg BASE_IMAGE=apache/airflow" not in workflow
    assert "--build-arg BASE_IMAGE=apache/airflow:3.2.2" not in workflow
    for context in ("services/asset-worker/app", "services/asset-baker/app"):
        assert context in workflow
        assert context in contributor_docs
    for provider in (
        "amazon",
        "postgres",
        "redis",
        "common-sql",
        "neo4j",
        "openai",
        "fab",
        "weaviate",
    ):
        assert f'dependency-name: "apache-airflow-providers-{provider}"' not in dependabot


def test_image_http_and_parser_security_floors() -> None:
    backend = _text("services/backend/app/app/requirements.txt")
    assert "Pillow>=13.0.0" in backend
    assert "PyJWT>=2.15.1" in backend
    assert "python-jose" not in backend

    pillow_manifests = {
        "bootstrapper/pyproject.toml": "pillow>=13.0.0",
        "services/backend/app/app/requirements.txt": "Pillow>=13.0.0",
        "services/docling/provider/gpu/requirements.txt": "Pillow>=13.0.0",
        "services/docling/provider/localhost/pyproject.toml": "pillow>=13.0.0",
        "services/jupyterhub/build/requirements.txt": "Pillow>=13.0.0",
    }
    for relative, requirement in pillow_manifests.items():
        assert requirement in _text(relative), relative

    request_manifests = {
        "bootstrapper/pyproject.toml": "requests>=2.33.0",
        "services/backend/app/app/requirements.txt": "requests>=2.33.0",
        "services/jupyterhub/build/requirements.txt": "requests>=2.33.0",
        "services/mcp-servers/runtime/requirements.txt": "requests==2.34.2",
    }
    for relative, requirement in request_manifests.items():
        assert requirement in _text(relative), relative

    assert "cryptography>=50.0.0,<51" in _text(
        "services/jupyterhub/build/requirements.txt"
    )
    assert "\ncryptography==50.0.1\n" in (
        "\n" + _text("services/jupyterhub/build/requirements-locked.txt")
    )

    assert _locked_version("bootstrapper/uv.lock", "requests") == "2.34.2"
    assert _locked_version("bootstrapper/uv.lock", "click") == "8.4.2"
    assert (
        _locked_version("services/docling/provider/localhost/uv.lock", "pillow")
        == "13.0.0"
    )
    # CVE-2026-85999 / CVE-2026-86000 fixed in 2.9.0 (#1110). soupsieve 4.1.3
    # fixes CVE-2026-126190; the provider requires Python 3.11.5, so the lock has
    # no 3.11.0-3.11.4 fork and holds 4.1.3 alone.
    soupsieve_lock = tomllib.loads(_text("services/docling/provider/localhost/uv.lock"))
    assert sorted(
        row["version"] for row in soupsieve_lock["package"]
        if row["name"] == "soupsieve"
    ) == ["4.1.3"]
    assert (
        _locked_version(
            "services/docling/provider/localhost/uv.lock", "pydantic-settings"
        )
        == "2.14.2"
    )


def test_docling_localhost_and_jupyterhub_take_the_2026_10_01_fixes() -> None:
    """Floors for the advisories that turned the runtime-lock audit red."""
    docling_project = _text("services/docling/provider/localhost/pyproject.toml")
    docling_lock = "services/docling/provider/localhost/uv.lock"
    # transformers: PYSEC-2026-3929 (fixed in 5.10.0) and CVE-2026-80047;
    # accelerate: PYSEC-2026-3804 (fixed in 1.15.0). One version per package,
    # so the darwin fork that held transformers at 5.8.1 is gone.
    assert '"transformers>=5.10.0"' in docling_project
    assert '"accelerate>=1.15.0"' in docling_project
    assert _locked_version(docling_lock, "transformers") == "5.18.0"
    assert _locked_version(docling_lock, "accelerate") == "1.15.0"

    # tornado 6.5.9: GHSA-3hv7-mjh2-fv65, GHSA-c2m8-h5v5-343r, GHSA-chx6-46f5-w4vp;
    # 6.6: GHSA-pcg2-g6cj-q92c, GHSA-pw9p-v7r6-gx4p, GHSA-r4j5-9xpp-3985;
    # 6.6.1: CVE-2026-126347.
    assert "\ntornado>=6.6.1\n" in _text("services/jupyterhub/build/requirements.txt")
    assert "\ntornado==6.6.1\n" in _text(
        "services/jupyterhub/build/requirements-locked.txt"
    )


def test_docling_providers_share_one_pin_and_the_gpu_lock_is_patched() -> None:
    """#999: both providers pin one Docling; the GPU lock is past the fixes."""
    localhost = tomllib.loads(
        _text("services/docling/provider/localhost/pyproject.toml")
    )["project"]["dependencies"]
    gpu_requirements = _text("services/docling/provider/gpu/requirements.txt")
    gpu_lock = dict(
        line.split("==", 1)
        for line in _text(
            "services/docling/provider/gpu/requirements-locked.txt"
        ).splitlines()
    )
    pins = [d for d in localhost if d.startswith("docling==")]

    # One pin, moved together in both providers, their locks and the ledger.
    assert len(pins) == 1 and f"\n{pins[0]}\n" in f"\n{gpu_requirements}"
    version = pins[0].removeprefix("docling==")
    assert (
        _locked_version("services/docling/provider/localhost/uv.lock", "docling"),
        gpu_lock["docling"],
    ) == (version, version)
    ledger = _text("docs/maintenance/external-contract-ledger.md")
    assert f"`{pins[0]}`" in ledger
    # accelerate: PYSEC-2026-3804 (fixed in 1.15.0), floored in the GPU image
    # as in the localhost provider; transformers: PYSEC-2026-3929 (5.10.0).
    assert "\naccelerate>=1.15.0\n" in gpu_requirements
    assert (
        Version(gpu_lock["accelerate"]) >= Version("1.15.0")
        and Version(gpu_lock["transformers"]) >= Version("5.10.0")
    )


def _requirements_named(declarations, package):
    requirements = (Requirement(line) for line in declarations
                    if line.strip() and not line.lstrip().startswith("#"))
    return [item for item in requirements if item.name.lower() == package]


def _uv_versions_for_environment(relative, package, environment):
    packages = tomllib.loads(_text(relative))["package"]
    return [item["version"] for item in packages if item["name"] == package
            and (not item.get("resolution-markers") or any(
                Requirement(f"{package}; {marker}").marker.evaluate(environment)
                for marker in item["resolution-markers"]))]


@pytest.mark.parametrize("mode", ("gpu", "localhost"))
@pytest.mark.parametrize(("package", "floor"), (
    ("certifi", "2026.10.19"),
    ("setuptools", "84.1.0"),
    ("fsspec", "2026.6.0"),
))
def test_docling_security_floors_survive_fresh_resolution(mode, package, floor) -> None:
    base = f"services/docling/provider/{mode}"
    if mode == "localhost":
        project = tomllib.loads(_text(f"{base}/pyproject.toml"))["project"]
        requirements = project["dependencies"]
        version = _locked_version(f"{base}/uv.lock", package)
    else:
        requirements = _text(f"{base}/requirements.txt").splitlines()
        version = dict(line.split("==", 1) for line in
                       _text(f"{base}/requirements-locked.txt").splitlines())[package]
    matching = _requirements_named(requirements, package)
    assert len(matching) == 1, f"Missing durable {package} floor in {mode} inputs"
    requirement = matching[0]
    assert str(requirement.specifier) == f">={floor}"
    assert Version(version) >= Version(floor)


def _docling_lock(mode):
    if mode == "localhost":
        return {p["name"]: p["version"] for p in tomllib.loads(
            _text("services/docling/provider/localhost/uv.lock"))["package"]}
    return dict(line.split("==", 1) for line in
                _text("services/docling/provider/gpu/requirements-locked.txt").splitlines())


@pytest.mark.parametrize("mode", ("gpu", "localhost"))
@pytest.mark.parametrize("package,floor", {
    "docling": "2.142.0", "docling-core": "2.104.0",
    "docling-ibm-models": "4.0.4", "docling-parse": "7.22.4",
    "doclang": "0.8.0", "pillow": "13.0.0",
}.items())
def test_docling_patch_family_floor(mode, package, floor) -> None:
    assert Version(_docling_lock(mode)[package]) >= Version(floor)


@pytest.mark.parametrize("mode", ("gpu", "localhost"))
def test_docling_qualification_choice_tracks_the_core_lock(mode) -> None:
    # Tripwire: a docling-core bump must rerun test_document_metadata.py in each
    # provider environment before its docstring and --expected-core choice move.
    pinned = re.findall(
        r'--expected-core(?: |", required=True, choices=\(")([^"\s]+)(?:\n|",\)\))',
        _text("services/docling/provider/tests/test_document_metadata.py"))
    assert pinned == [_docling_lock(mode)["docling-core"]] * 2, pinned


@pytest.mark.parametrize("mode", ("gpu", "localhost"))
def test_docling_slim_and_gpu_transformers_floor(mode) -> None:
    lock = _docling_lock(mode)
    assert lock["docling-slim"] == lock["docling"]
    assert "\ntransformers>=5.10.0\n" in _text("services/docling/provider/gpu/requirements.txt")
    assert Version(lock["transformers"]).major == 5


def test_docling_filelock_floor_covers_supported_python() -> None:
    base = "services/docling/provider/localhost"
    project = tomllib.loads(_text(f"{base}/pyproject.toml"))["project"]
    assert project["requires-python"] == ">=3.11.5"
    matching = _requirements_named(project["dependencies"], "filelock")
    assert len(matching) == 1, "Missing filelock security floor"
    requirement = matching[0]
    assert requirement.marker is None
    assert all(Version(python_version) in SpecifierSet(project["requires-python"])
               for python_version in ("3.11.5", "3.12", "3.13"))
    assert str(requirement.specifier) == ">=4.2.1"
    gpu = _text("services/docling/provider/gpu/requirements.txt")
    assert "\nfilelock>=4.2.1\n" in gpu


@pytest.mark.parametrize("python_version", ("3.11", "3.12", "3.13"))
def test_docling_filelock_lock_covers_supported_python(python_version) -> None:
    environment = {"python_version": python_version,
                   "python_full_version": f"{python_version}.0"}
    selected = _uv_versions_for_environment(
        "services/docling/provider/localhost/uv.lock", "filelock", environment,
    )
    assert len(selected) == 1
    assert Version(selected[0]) >= Version("4.2.1")


@pytest.mark.parametrize(("python_version", "platform"), (
    ("3.13", "darwin"),
    ("3.11", "darwin"),
    ("3.12", "darwin"),
    ("3.11", "linux"),
    ("3.12", "linux"),
    ("3.12", "win32"),
))
def test_docling_native_scipy_floor_is_platform_scoped(python_version, platform) -> None:
    base = "services/docling/provider/localhost"
    project = tomllib.loads(_text(f"{base}/pyproject.toml"))["project"]
    assert project["requires-python"] == ">=3.11.5"
    matching = _requirements_named(project["dependencies"], "scipy")
    assert len(matching) == 1, "Missing native SciPy compatibility floor"
    requirement = matching[0]
    environment = {"python_version": python_version,
                   "python_full_version": f"{python_version}.0", "sys_platform": platform}
    assert str(requirement.specifier) == ">=1.16.0"
    assert requirement.marker is not None
    # The floor itself still applies on darwin only. The lock resolves SciPy 1.16.0
    # on every platform, so the selection below is the same for each one.
    assert requirement.marker.evaluate(environment) == (platform == "darwin")
    selected = _uv_versions_for_environment(f"{base}/uv.lock", "scipy", environment)
    assert selected == ["1.16.0"]


def test_jupyter_binary_ml_stack_uses_supported_security_baseline() -> None:
    requirements = _text("services/jupyterhub/build/requirements.txt")
    lock = _text("services/jupyterhub/build/requirements-locked.txt")
    dockerfile = _text("services/jupyterhub/build/Dockerfile")

    assert "pyarrow==23.0.1" in requirements
    assert "torch==2.13.0" in requirements
    assert "nltk>=3.10.4" in requirements
    assert "\nnltk==3.10.4\n" in f"\n{lock}"
    assert "torchvision==0.28.0" in requirements
    assert "torchaudio" not in requirements
    # The image resolves entirely from PyPI: `pyg_lib`'s only publisher
    # (data.pyg.org) went unresolvable on 2026-09-02, so neither the wheel
    # index nor the compiled accelerator may reappear.
    assert "torch-2.13.0+cpu.html" not in requirements
    assert "--find-links" not in requirements
    assert "torch_geometric==2.7.0" in requirements
    assert "torch-spline-conv" not in requirements
    assert "torch==2.13.0 torchvision==0.28.0" in dockerfile
    assert "torchaudio" not in dockerfile
    assert "--index-url https://download.pytorch.org/whl/cpu" in dockerfile
    changelog = _text("docs/CHANGELOG.md")
    assert "PyTorch 2.13" in changelog
    assert "PyG 0.8" in changelog


def test_dependabot_torch_coordination_matches_current_compiled_family() -> None:
    dependabot = _text(".github/dependabot.yml")

    # No wheel-index URL is pinned anywhere now; the ignore list still has to
    # name every member of the family so none can be auto-bumped alone.
    assert "torch-2.13.0+cpu.html" not in dependabot
    assert "torch-2.11.0+cpu.html" not in dependabot
    for package in (
        "torch",
        "torchvision",
        "torch-scatter",
        "torch-sparse",
        "torch-cluster",
        "pyg_lib",
        "torch_geometric",
    ):
        assert f'dependency-name: "{package}"' in dependabot
    for absent_package in ("torchao", "torch-spline-conv"):
        assert f'dependency-name: "{absent_package}"' not in dependabot


def test_n8n_does_not_publish_retired_noop_environment_knobs() -> None:
    retired = {
        "N8N_AUTH_ENABLED",
        "N8N_ALLOW_CONNECTIONS_FROM",
        "N8N_COMMUNITY_PACKAGES_ENABLED",
        "N8N_COMMUNITY_PACKAGES_ALLOW_TOOL_USAGE",
    }
    surfaces = {
        "manifest": _text("services/n8n/service.yml"),
        "compose": _text("services/n8n/compose.yml"),
        "generated env": _text(".env.example"),
    }

    for surface, content in surfaces.items():
        for name in retired:
            assert name not in content, f"{name} remains on the n8n {surface} surface"


def test_mcp_framework_contracts_run_with_runtime_dependencies_in_ci() -> None:
    workflow = _text(".github/workflows/services-lint.yml")

    assert "services/mcp-servers/runtime/requirements-test.txt" in workflow
    assert "bootstrapper/tests/test_mcp_servers_framework.py" in workflow
