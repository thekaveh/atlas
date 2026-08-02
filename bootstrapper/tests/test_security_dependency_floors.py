from __future__ import annotations

import tomllib
import json
import re
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


def test_backend_fal_client_supports_cancellable_async_transport() -> None:
    requirements = _text("services/backend/app/app/requirements.txt")

    assert "fal-client>=1.0.0" in requirements


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
    assert "apache-airflow-providers-apache-spark==5.6.0" in requirements
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
    assert "torch==2.13.0" in requirements
    assert "torchvision==0.28.0" in requirements
    assert "torchaudio" not in requirements
    assert "nltk>=3.10.0" in requirements
    assert "torch-2.13.0+cpu.html" in requirements
    assert "pyg_lib==0.8.0" in requirements
    assert "torch-spline-conv" not in requirements
    assert "torch==2.13.0 torchvision==0.28.0" in dockerfile
    assert "torchaudio" not in dockerfile
    assert "--index-url https://download.pytorch.org/whl/cpu" in dockerfile


def test_dependabot_torch_coordination_matches_current_compiled_family() -> None:
    dependabot = _text(".github/dependabot.yml")

    assert "torch-2.13.0+cpu.html" in dependabot
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

    assert "services/mcp-servers/runtime/requirements.txt" in workflow
    assert "bootstrapper/tests/test_mcp_servers_framework.py" in workflow


def test_dependabot_covers_all_active_small_service_manifests() -> None:
    dependabot = _text(".github/dependabot.yml")

    for directory in (
        '"/services/asset-worker/app"',
        '"/services/asset-baker/app"',
        '"/services/mcp-servers/runtime"',
    ):
        assert directory in dependabot
    assert "package-ecosystem: npm" in dependabot
    assert 'directory: "/services/n8n/init/config"' in dependabot


def test_required_workflow_runs_for_every_pull_request_path() -> None:
    workflow = _text(".github/workflows/services-lint.yml")
    pull_request_block = workflow.split("pull_request:", 1)[1].split("push:", 1)[0]

    assert not re.search(r"^\s+paths:", pull_request_block, re.MULTILINE)


def test_build_validation_is_not_opt_in() -> None:
    workflow = _text(".github/workflows/services-lint.yml")

    assert "ENABLE_BUILD_VALIDATION" not in workflow
    assert "Build-validation (Dockerfile + requirements.txt installability)" in workflow


def test_large_service_runtime_graphs_use_compiled_constraints() -> None:
    workflow = _text(".github/workflows/services-lint.yml")
    assert "python -m scripts.check_runtime_locks" in workflow

    surfaces = (
        ("services/backend/app/Dockerfile", "services/backend/app/app/requirements.lock"),
        ("services/airflow/build/Dockerfile", "services/airflow/build/requirements.lock"),
        ("services/jupyterhub/build/Dockerfile", "services/jupyterhub/build/requirements.lock"),
        (
            "services/parakeet/provider/gpu/Dockerfile",
            "services/parakeet/provider/gpu/requirements.lock",
        ),
    )
    for dockerfile_path, lock_path in surfaces:
        dockerfile = _text(dockerfile_path)
        lock = _text(lock_path)
        assert "requirements.lock" in dockerfile, dockerfile_path
        assert "-c " in dockerfile, dockerfile_path
        assert lock and all(
            not line or line.startswith("#") or "==" in line
            for line in lock.splitlines()
        ), lock_path


def test_notebook_hygiene_is_part_of_a_required_ci_job() -> None:
    workflow = _text(".github/workflows/services-lint.yml")

    assert workflow.count("python -m scripts.notebook_reproducibility") >= 2


def test_n8n_comfyui_nodes_override_sharp_to_patched_release() -> None:
    package = json.loads(_text("services/n8n/init/config/package.json"))

    assert package["overrides"]["sharp"] == "0.35.3"
    assert '"node_modules/sharp"' in _text("services/n8n/init/config/package-lock.json")
    assert '"version": "0.35.3"' in _text(
        "services/n8n/init/config/package-lock.json"
    )


def test_external_contract_ledger_matches_executable_pyg_lib_pin() -> None:
    requirements = _text("services/jupyterhub/build/requirements.txt")
    ledger = _text("docs/maintenance/external-contract-ledger.md")

    assert "pyg_lib==0.8.0" in requirements
    assert "pyg_lib==0.8.0" in ledger
    assert "pyg_lib==0.6.0" not in ledger


def test_all_secret_writers_use_shared_atomic_primitive() -> None:
    for relative in (
        "bootstrapper/services/service_config.py",
        "bootstrapper/utils/source_override_manager.py",
        "bootstrapper/utils/key_generator.py",
        "bootstrapper/utils/supabase_keys.py",
    ):
        content = _text(relative)
        assert "atomic_write_text(" in content, relative
        assert "os.replace(tmp_path" not in content, relative


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
