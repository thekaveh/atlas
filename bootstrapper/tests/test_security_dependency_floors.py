from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _locked_version(relative: str, package: str) -> str:
    lock = tomllib.loads(_text(relative))
    return next(row["version"] for row in lock["package"] if row["name"] == package)


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
    assert "nemo_toolkit[asr]>=2.7.3,<3.0" in parakeet_gpu
    assert "transformers>=4.57.0,<4.58" in parakeet_gpu
    assert "onnx>=1.22.0" in parakeet_gpu
    assert "protobuf>=5.29.6,<5.30" in parakeet_gpu


def test_backend_ci_installs_the_owned_test_contract() -> None:
    requirements = _text("services/backend/app/app/requirements.txt")
    dev_requirements = _text("services/backend/app/app/requirements-dev.txt")
    workflow = _text(".github/workflows/services-lint.yml")
    dockerfile = _text("services/backend/app/Dockerfile")

    assert "pytest" not in requirements
    assert "httpx2" not in requirements
    assert "pytest>=9.1.1" in dev_requirements
    assert "pytest-asyncio>=1.4.0" in dev_requirements
    assert "httpx2>=2.6.0" in dev_requirements
    assert '-r requirements.txt -r requirements-dev.txt' in workflow
    assert "requirements-dev.txt" not in dockerfile
    assert "pytest-asyncio omitted intentionally" not in workflow


def test_airflow_uses_supported_core_and_unfrozen_provider_security_fixes() -> None:
    manifest = _text("services/airflow/service.yml")
    compose = _text("services/airflow/compose.yml")
    dockerfile = _text("services/airflow/build/Dockerfile")
    requirements = _text("services/airflow/build/requirements.txt")

    assert 'default: "apache/airflow:3.3.0"' in manifest
    assert "apache/airflow:3.3.0" in compose
    assert "ARG BASE_IMAGE=apache/airflow:3.3.0" in dockerfile
    assert "--constraint" not in dockerfile
    assert "setuptools>=83.0.0" in requirements
    # #782: the 6.x spark provider swapped its dep to pyspark-client, whose
    # overlapping module tree overwrites the pinned pyspark==4.1.2 files and
    # breaks `import pyspark` (SparkExecutorInfo ImportError → image build
    # fails). The cap and the classic-pyspark pin move only with the whole
    # Spark 4.1 family (cluster image + iceberg-spark-runtime-4.1).
    assert "apache-airflow-providers-apache-spark>=5.4.0,<6" in requirements
    assert "pyspark[connect]==4.1.2" in requirements


def test_airflow_build_validation_uses_runtime_core_release() -> None:
    workflow = _text(".github/workflows/services-lint.yml")
    contributor_docs = _text("docs/CONTRIBUTING-services.md")
    dependabot = _text(".github/dependabot.yml")

    assert "--build-arg BASE_IMAGE=apache/airflow:3.3.0" in workflow
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
    assert "Pillow>=12.3.0" in backend
    assert "PyJWT>=2.13.0" in backend
    assert "python-jose" not in backend

    pillow_manifests = {
        "bootstrapper/pyproject.toml": "pillow>=12.3.0",
        "services/backend/app/app/requirements.txt": "Pillow>=12.3.0",
        "services/docling/provider/gpu/requirements.txt": "Pillow>=12.3.0",
        "services/docling/provider/localhost/pyproject.toml": "pillow>=12.3.0",
        "services/jupyterhub/build/requirements.txt": "Pillow>=12.3.0",
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

    assert _locked_version("bootstrapper/uv.lock", "requests") == "2.34.2"
    assert _locked_version("bootstrapper/uv.lock", "click") == "8.4.2"
    assert _locked_version("bootstrapper/uv.lock", "pillow") == "12.3.0"
    assert (
        _locked_version("services/docling/provider/localhost/uv.lock", "pillow")
        == "12.3.0"
    )
    assert (
        _locked_version("services/docling/provider/localhost/uv.lock", "soupsieve")
        == "2.8.4"
    )
    assert (
        _locked_version(
            "services/docling/provider/localhost/uv.lock", "pydantic-settings"
        )
        == "2.14.2"
    )


def test_jupyter_binary_ml_stack_uses_supported_security_baseline() -> None:
    requirements = _text("services/jupyterhub/build/requirements.txt")
    dockerfile = _text("services/jupyterhub/build/Dockerfile")

    assert "pyarrow==23.0.1" in requirements
    assert "torch==2.11.0" in requirements
    assert "torchvision==0.26.0" in requirements
    assert "torchaudio==2.11.0" in requirements
    assert "nltk>=3.10.0" in requirements
    assert "torch-2.11.0+cpu.html" in requirements
    # #776: 0.7.0 does not exist on the torch-2.11 index (only 0.6.0, and
    # only with x86_64 wheels) — an unsatisfiable pin is not a security floor,
    # it is a broken cold build. The platform marker is part of the contract.
    assert 'pyg_lib==0.6.0; platform_machine == "x86_64"' in requirements
    assert "torch-spline-conv" not in requirements
    assert "torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0" in dockerfile
    assert "--index-url https://download.pytorch.org/whl/cpu" in dockerfile


def test_dependabot_torch_coordination_matches_current_compiled_family() -> None:
    dependabot = _text(".github/dependabot.yml")

    assert "torch-2.11.0+cpu.html" in dependabot
    assert "torch-2.4.0+cpu.html" not in dependabot
    for package in (
        "torch",
        "torchvision",
        "torchaudio",
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


def test_ray_runtime_and_clients_move_in_lockstep() -> None:
    for relative in (
        "services/backend/app/app/requirements.txt",
        "services/jupyterhub/build/requirements.txt",
    ):
        assert "ray[client]>=2.56.0,<2.57" in _text(relative), relative

    manifest = _text("services/ray/service.yml")
    compose = _text("services/ray/compose.yml")
    service_config = _text("bootstrapper/services/service_config.py")
    assert "rayproject/ray:2.56.0" in manifest
    assert "rayproject/ray:2.56.0-gpu" in manifest
    assert "rayproject/ray:2.56.0" in compose
    assert "rayproject/ray:2.56.0" in service_config


def test_ragas_advisory_surface_remains_unreachable() -> None:
    forbidden = ("MultiModalFaithfulness", "MultiModalRelevance", "DiskCache", "diskcache")
    source_files = (
        path
        for source_root in (ROOT / "bootstrapper", ROOT / "scripts", ROOT / "services")
        for path in source_root.rglob("*.py")
        if not {"tests", ".venv", "site-packages"}.intersection(path.parts)
    )
    offenders = {
        path.relative_to(ROOT).as_posix(): token
        for path in source_files
        for token in forbidden
        if token in path.read_text(encoding="utf-8")
    }
    assert offenders == {}


def test_backend_does_not_ship_unused_direct_groq_clients() -> None:
    requirements = _text("services/backend/app/app/requirements.txt")
    assert "langchain-groq" not in requirements
    assert "\ngroq" not in requirements
