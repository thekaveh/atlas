from __future__ import annotations

import io
import json
import re
from pathlib import Path
import sys
import tokenize

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised by the Python 3.10 test environment
    import tomli as tomllib

import yaml
import pytest


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
    assert "transformers>=5.5.0,<6" in parakeet_gpu
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
    assert "httpx2==2.6.0" in dev_requirements
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
    action_ref = re.compile(r"^\s*uses:\s*[^#\s]+@([^#\s]+)", re.MULTILINE)

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

    assert 'default: "apache/airflow:3.3.1"' in manifest
    assert "apache/airflow:3.3.1" in compose
    assert "ARG BASE_IMAGE=apache/airflow:3.3.1" in dockerfile
    assert "--constraint" not in dockerfile
    assert "setuptools>=83.0.0" in requirements
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
        "botocore<1.43.57",
        "importlib-metadata<9",
        "protobuf<6.34",
        "websockets<17",
    } <= set(requirements.splitlines())
    assert "python -m pip check" in dockerfile
    assert "rm -f /usr/bin/docker" in dockerfile
    assert "/home/airflow/.local/bin/uv" in dockerfile
    assert "/home/airflow/.local/bin/uvx" in dockerfile


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

    assert "ARG BASE_IMAGE=apache/airflow:3.3.1" in dockerfile
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

    assert "cryptography>=49.0.0,<50" in _text(
        "services/jupyterhub/build/requirements.txt"
    )
    assert "\ncryptography==49.0.0\n" in (
        "\n" + _text("services/jupyterhub/build/requirements-locked.txt")
    )

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
    lock = _text("services/jupyterhub/build/requirements-locked.txt")
    dockerfile = _text("services/jupyterhub/build/Dockerfile")

    assert "pyarrow==23.0.1" in requirements
    assert "torch==2.13.0" in requirements
    assert "nltk>=3.10.3" in requirements
    assert "\nnltk==3.10.3\n" in f"\n{lock}"
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


def test_services_lint_runs_backend_redis_lua_contracts_against_pinned_redis() -> None:
    workflow = yaml.safe_load(_text(".github/workflows/services-lint.yml"))
    steps = workflow["jobs"]["lint"]["steps"]
    by_name = {step.get("name"): step for step in steps}
    pull = by_name["Pull exact backup integration images"]["run"]
    backend = by_name["Backend unit tests"]["run"]

    assert "docker pull redis:7.2.14-alpine" in pull
    assert "docker run" in backend and "redis:7.2.14-alpine" in backend
    assert "ATLAS_TEST_REDIS_URL" in backend
    assert "docker exec" in backend and "redis-cli ping" in backend
    assert "trap" in backend and "docker rm -f" in backend


def test_services_lint_runs_pinned_otel_loki_grafana_smokes() -> None:
    workflow = yaml.safe_load(_text(".github/workflows/services-lint.yml"))
    steps = workflow["jobs"]["lint"]["steps"]
    by_name = {step.get("name"): step for step in steps}
    pull = by_name["Pull exact backup integration images"]["run"]
    unit_test = by_name[
        "Run unit tests (loader, validator, assembler, hooks, CLI)"
    ]

    for image in (
        "otel/opentelemetry-collector-contrib:0.154.0",
        "grafana/loki:3.7.0",
        "grafana/grafana:11.4.3",
    ):
        assert f"docker pull {image}" in pull
    assert unit_test["env"]["ATLAS_RUN_DOCKER_OTEL_SMOKE"] == "1"


def test_dependabot_covers_all_active_small_service_manifests() -> None:
    dependabot = _text(".github/dependabot.yml")

    for directory in (
        '"/services/asset-worker/app"',
        '"/services/asset-baker/app"',
        '"/services/mcp-servers/runtime"',
    ):
        assert directory in dependabot
    assert "package-ecosystem: npm" in dependabot
    assert '"/services/n8n/init/config"' in dependabot


def test_dependabot_covers_every_production_npm_lock() -> None:
    config = yaml.safe_load(_text(".github/dependabot.yml"))
    npm_directories: set[str] = set()
    for update in config["updates"]:
        if update["package-ecosystem"] != "npm":
            continue
        npm_directories.update(update.get("directories", []))
        if directory := update.get("directory"):
            npm_directories.add(directory)

    locks = {
        f"/{path.parent.relative_to(ROOT).as_posix()}"
        for path in (ROOT / "services").rglob("package-lock.json")
        if "node_modules" not in path.parts
    }
    assert locks == npm_directories


def test_required_workflow_runs_for_every_pull_request_path() -> None:
    workflow = _text(".github/workflows/services-lint.yml")
    pull_request_block = workflow.split("pull_request:", 1)[1].split("push:", 1)[0]

    assert not re.search(r"^\s+paths:", pull_request_block, re.MULTILINE)


def test_build_validation_is_not_opt_in() -> None:
    workflow = _text(".github/workflows/services-lint.yml")

    assert "ENABLE_BUILD_VALIDATION" not in workflow
    assert "Build-validation (Dockerfile + requirements.txt installability)" in workflow


def test_pinned_mlflow_gateway_guard_has_a_required_real_image_smoke() -> None:
    workflow = _text(".github/workflows/services-lint.yml")
    smoke = _text("scripts/smoke_mlflow_gateway_guard.py")

    assert "python scripts/smoke_mlflow_gateway_guard.py" in workflow
    assert '"services/mlflow|build/Dockerfile"' in workflow
    assert '--image "$image_tag"' in workflow
    assert "_MLFLOW_STATIC_PREFIX" in smoke
    assert "/api/3.0/mlflow/gateway/secrets/create" in smoke
    assert "/ajax-api/2.0/mlflow/gateway-proxy" in smoke
    assert "/api/2.0/mlflow/registered-models/create" in smoke
    assert "/api/2.0/mlflow/registered-models/get" in smoke
    assert "/api/2.0/mlflow-artifacts/artifacts/" in smoke


def test_large_service_runtime_graphs_use_compiled_constraints() -> None:
    workflow = _text(".github/workflows/services-lint.yml")
    assert "python -m scripts.check_runtime_locks" in workflow

    surfaces = (
        ("services/backend/app/Dockerfile", "services/backend/app/app/requirements-locked.txt"),
        ("services/airflow/build/Dockerfile", "services/airflow/build/requirements-locked.txt"),
        ("services/jupyterhub/build/Dockerfile", "services/jupyterhub/build/requirements-locked.txt"),
        ("services/mlflow/build/Dockerfile", "services/mlflow/build/requirements-locked.txt"),
        (
            "services/parakeet/provider/gpu/Dockerfile",
            "services/parakeet/provider/gpu/requirements-locked.txt",
        ),
        (
            "services/asset-baker/app/Dockerfile",
            "services/asset-baker/app/requirements-locked.txt",
        ),
        (
            "services/asset-worker/app/Dockerfile",
            "services/asset-worker/app/requirements-locked.txt",
        ),
        (
            "services/docling/provider/gpu/Dockerfile",
            "services/docling/provider/gpu/requirements-locked.txt",
        ),
        (
            "services/docling/provider/adapter/Dockerfile",
            "services/docling/provider/adapter/requirements-locked.txt",
        ),
        (
            "services/mcp-servers/build/Dockerfile",
            "services/mcp-servers/runtime/requirements-locked.txt",
        ),
    )
    for dockerfile_path, lock_path in surfaces:
        dockerfile = _text(dockerfile_path)
        lock = _text(lock_path)
        assert "requirements-locked.txt" in dockerfile, dockerfile_path
        assert "-c " in dockerfile, dockerfile_path
        assert lock and all(
            not line or line.startswith("#") or "==" in line
            for line in lock.splitlines()
        ), lock_path


def test_notebook_hygiene_is_part_of_a_required_ci_job() -> None:
    workflow = _text(".github/workflows/services-lint.yml")

    assert workflow.count("python -m scripts.notebook_reproducibility") == 1


def test_runtime_lock_vulnerability_audit_is_required() -> None:
    workflow = _text(".github/workflows/services-lint.yml")
    audit = _text("scripts/audit_runtime_locks.py")

    assert "pip-audit==2.10.0" in workflow
    assert "python -m scripts.audit_runtime_locks" in workflow
    assert "PYSEC-2026-2447" in audit
    assert "PYSEC-2026-3046" in audit


def test_tracked_shell_scripts_have_a_pinned_shellcheck_gate() -> None:
    workflow = _text(".github/workflows/services-lint.yml")

    assert "shellcheck-v0.11.0.linux.x86_64.tar.xz" in workflow
    assert "8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198" in workflow
    assert "sha256sum -c -" in workflow
    assert "git ls-files -z '*.sh' | xargs -0 shellcheck -x" in workflow


def test_jupyter_does_not_ship_unused_label_studio_sdk() -> None:
    requirements = _text("services/jupyterhub/build/requirements.txt")
    lock = _text("services/jupyterhub/build/requirements-locked.txt")

    assert "label-studio-sdk" not in requirements
    assert "label-studio-sdk" not in lock
    assert "datamodel-code-generator" not in lock


def test_n8n_comfyui_nodes_override_sharp_to_patched_release() -> None:
    package = json.loads(_text("services/n8n/init/config/package.json"))

    assert package["overrides"]["sharp"] == "0.35.3"
    assert '"node_modules/sharp"' in _text("services/n8n/init/config/package-lock.json")
    assert '"version": "0.35.3"' in _text(
        "services/n8n/init/config/package-lock.json"
    )


def test_asset_worker_toolchain_uses_an_audited_npm_lock() -> None:
    package = json.loads(_text("services/asset-worker/app/package.json"))
    dockerfile = _text("services/asset-worker/app/Dockerfile")
    helper = _text("scripts/gltf-transform-postprocess.sh")

    assert package["dependencies"]["@gltf-transform/cli"] == "4.4.1"
    assert package["overrides"]["sharp"] == "0.35.3"
    assert "package-lock.json" in dockerfile
    assert "npm ci --omit=dev" in dockerfile
    assert "npm install -g" not in dockerfile
    assert "package-lock.json" in helper
    assert "npm ci --omit=dev" in helper
    assert "npx --yes" not in helper


def test_required_ci_exercises_tier_a_runtime_constraints() -> None:
    workflow = _text(".github/workflows/services-lint.yml")

    for constraint in (
        "services/mcp-servers/runtime/requirements-test-locked.txt",
        "services/asset-worker/app/requirements-test-locked.txt",
    ):
        assert f"-c {constraint}" in workflow

    assert "python -m scripts.check_test_locks" in workflow
    assert "pytest httpx2" not in workflow

    for venv_var in (
        "BACKEND_TEST_VENV",
        "MCP_TEST_VENV",
        "ASSET_TEST_VENV",
    ):
        assert f'uv venv --python 3.12 "${venv_var}"' in workflow

    assert "name: Run bootstrapper tests on Python 3.10" in workflow
    assert "uv run --python 3.10 --isolated --locked --group dev" in workflow


def test_external_contract_ledger_matches_executable_pyg_pin() -> None:
    requirements = _text("services/jupyterhub/build/requirements.txt")
    ledger = _text("docs/maintenance/external-contract-ledger.md")

    directives = [
        line.strip()
        for line in requirements.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert "torch_geometric==2.7.0" in directives
    assert "torch-geometric `2.7.0`" in ledger
    # The compiled accelerator and its CDN are gone from both surfaces; the
    # requirements comments still explain the removal, so check directives.
    assert not any("pyg_lib" in line for line in directives)
    assert not any("data.pyg.org" in line for line in directives)
    assert "pyg_lib==0.8.0" not in ledger


def test_all_production_env_writers_use_shared_atomic_primitive() -> None:
    atomic_writers = (
        "bootstrapper/start.py",
        "bootstrapper/core/config_parser.py",
        "bootstrapper/core/port_manager.py",
        "bootstrapper/services/source_validator.py",
        "bootstrapper/services/migrations/migration_v1.py",
        "bootstrapper/services/migrations/migration_v2.py",
        "bootstrapper/services/migrations/migration_v3.py",
        "bootstrapper/scripts/reorg_user_env.py",
        "bootstrapper/services/service_config.py",
        "bootstrapper/utils/source_override_manager.py",
        "bootstrapper/utils/key_generator.py",
        "bootstrapper/utils/supabase_keys.py",
    )
    for relative in atomic_writers:
        content = _text(relative)
        assert (
            "atomic_write_text(" in content
            or "create_private_backup(" in content
        ), relative

    forbidden_by_module = {
        "bootstrapper/start.py": (
            'Path(str(env_file_path) + ".tmp")',
            "env_file_path.write_text(",
        ),
        "bootstrapper/core/config_parser.py": ("shutil.copy2(",),
        "bootstrapper/core/port_manager.py": ("tempfile.mkstemp(",),
        "bootstrapper/services/source_validator.py": (
            "with open(env_file, 'w'",
            "pass  # silent",
        ),
        "bootstrapper/services/migrations/migration_v1.py": (
            "backup_path.touch(",
            "tmp.write_text(",
            "env_path.write_text(",
        ),
        "bootstrapper/services/migrations/migration_v2.py": (
            "backup.touch(",
            "tmp.write_text(",
            "env_path.write_text(",
        ),
        "bootstrapper/services/migrations/migration_v3.py": (
            "backup.touch(",
            "tmp.write_text(",
            "env_path.write_text(",
        ),
        "bootstrapper/scripts/reorg_user_env.py": ("ENV_PATH.write_text(",),
        "bootstrapper/utils/key_generator.py": ("shutil.copy2(",),
    }
    for relative, forbidden_fragments in forbidden_by_module.items():
        content = _text(relative)
        for fragment in forbidden_fragments:
            assert fragment not in content, f"{relative}: {fragment}"


def test_ray_runtime_and_clients_move_in_lockstep() -> None:
    for relative in (
        "services/backend/app/app/requirements.txt",
        "services/jupyterhub/build/requirements.txt",
    ):
        assert "ray[client]==2.56.0" in _text(relative), relative

    for relative in (
        "services/backend/app/app/requirements-locked.txt",
        "services/backend/app/app/requirements-test-locked.txt",
        "services/jupyterhub/build/requirements-locked.txt",
    ):
        assert "ray==2.56.0" in _text(relative), relative
        assert "ray==2.56.1" not in _text(relative), relative

    manifest = _text("services/ray/service.yml")
    compose = _text("services/ray/compose.yml")
    service_config = _text("bootstrapper/services/service_config.py")
    assert "rayproject/ray:2.56.0" in manifest
    assert "rayproject/ray:2.56.0-gpu" in manifest
    assert "rayproject/ray:2.56.0" in compose
    assert "rayproject/ray:2.56.0" in service_config


def test_ragas_advisory_surface_remains_unreachable() -> None:
    forbidden = (
        "MultiModalFaithfulness",
        "MultiModalRelevance",
        "DiskCache",
        "diskcache",
    )
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
        if token in _python_without_comments(path.read_text(encoding="utf-8"))
    }
    assert offenders == {}


def test_ragas_reachability_scan_ignores_comments_but_not_executable_tokens() -> None:
    assert "diskcache" not in _python_without_comments(
        "# diskcache evidence\nvalue = 1\n"
    )
    assert "diskcache" in _python_without_comments("import diskcache\n")
    assert "diskcache" in _python_without_comments(
        'importlib.import_module("diskcache")\n'
    )


def test_ragas_advisory_surface_remains_unreachable_from_notebook_code() -> None:
    notebook_dir = ROOT / "services/jupyterhub/build/notebooks"
    offenders = {
        path.relative_to(ROOT).as_posix(): sorted(findings)
        for path in sorted(notebook_dir.rglob("*.ipynb"))
        if (
            findings := _ragas_notebook_findings(
                json.loads(path.read_text(encoding="utf-8"))
            )
        )
    }

    assert offenders == {}


@pytest.mark.parametrize(
    ("code", "expected"),
    (
        (
            "from ragas.metrics.collections import MultiModalFaithfulness",
            "multimodal faithfulness",
        ),
        (
            "from ragas.metrics.collections.multimodalfaithfulness import util",
            "multimodal faithfulness",
        ),
        (
            "from ragas.metrics.collections.multi_modal_faithfulness import util",
            "multimodal faithfulness",
        ),
        (
            "from ragas.metrics import multimodal_faithness",
            "multimodal faithfulness",
        ),
        (
            "from ragas.metrics.collections import MultiModalRelevance",
            "multimodal relevance",
        ),
        (
            "from ragas.metrics.collections.multi_modal_relevance import util",
            "multimodal relevance",
        ),
        ("from ragas.cache import DiskCacheBackend", "disk cache"),
        ("import diskcache; cache = diskcache.Cache('/tmp/x')", "disk cache"),
        (
            "llm_factory('model', client=client, cache=backend)",
            "cache argument or assignment",
        ),
    ),
)
def test_ragas_notebook_tripwire_rejects_vulnerable_code_cells(
    code: str, expected: str
) -> None:
    notebook = {
        "cells": [
            {"cell_type": "markdown", "source": f"Documented only: {code}"},
            {"cell_type": "code", "source": [code]},
        ]
    }

    assert expected in _ragas_notebook_findings(notebook)


def test_ragas_notebook_tripwire_ignores_markdown_only_mentions() -> None:
    notebook = {
        "cells": [
            {
                "cell_type": "markdown",
                "source": "Do not import MultiModalFaithfulness or DiskCacheBackend.",
            }
        ]
    }

    assert _ragas_notebook_findings(notebook) == set()


def test_backend_does_not_ship_unused_direct_groq_clients() -> None:
    requirements = _text("services/backend/app/app/requirements.txt")
    assert "langchain-groq" not in requirements
    assert "\ngroq" not in requirements


def test_jupyterhub_external_build_artifacts_are_digest_verified() -> None:
    dockerfile = _text("services/jupyterhub/build/Dockerfile")
    assert dockerfile.count("sha256sum -c -") >= 1
    assert "python -m spacy download" not in dockerfile
    assert "nltk.download(" not in dockerfile
    _assert_jupyterhub_nlp_asset_integrity(dockerfile)
    assert "ENV PYTHONPATH=/home/jovyan\n" in dockerfile
    assert "${PYTHONPATH}" not in dockerfile
    assert "COURSIER_SHA256=" in dockerfile


def test_tei_default_model_revision_is_immutable() -> None:
    manifest = yaml.safe_load(_text("services/tei-reranker/service.yml"))
    revision = next(
        item["default"]
        for item in manifest["env"]
        if item["name"] == "TEI_RERANKER_REVISION"
    )
    assert re.fullmatch(r"[0-9a-f]{40}", revision)
    compose = _text("services/tei-reranker/compose.yml")
    assert f"${{TEI_RERANKER_REVISION:-{revision}}}" in compose
    assert "TEI_RERANKER_REVISION:-main" not in compose


def test_parakeet_mlx_lock_is_committed() -> None:
    lock = ROOT / "services/parakeet/provider/mlx/requirements-locked.txt"
    assert lock.is_file()
    assert "parakeet-mlx==0.5.2" in lock.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "relative",
    (
        "services/stt-provider/README.md",
        "services/parakeet/provider/README.md",
        "services/parakeet/provider/mlx/README.md",
    ),
)
def test_parakeet_mlx_quickstarts_use_locked_python_and_host_gateway_auth(
    relative: str,
) -> None:
    guide = _text(relative)
    required = (
        "requirements-locked.txt",
        "python3.12 -m venv",
        "PARAKEET_API_TOKEN",
        "PARAKEET_LOCALHOST_BIND_HOST=0.0.0.0",
        "PARAKEET_LOCALHOST_PORT=63042",
        "python -m mlx.api_server",
    )
    assert all(fragment in guide for fragment in required), relative
    assert "python -m uvicorn mlx.api_server:app" not in guide, relative


@pytest.mark.parametrize(
    "relative",
    (
        "services/stt-provider/README.md",
        "services/parakeet/provider/README.md",
        "services/parakeet/provider/mlx/README.md",
    ),
)
def test_parakeet_guides_do_not_publish_unsupported_performance_claims(
    relative: str,
) -> None:
    unsupported = ("300×", "3380×", "SOTA-quality", "fastest option", "starts instantly")
    assert not any(claim in _text(relative) for claim in unsupported), relative


def test_parakeet_mlx_guide_uses_the_real_entrypoint_variables() -> None:
    mlx_guide = _text("services/parakeet/provider/mlx/README.md")

    assert "100-300x real-time" not in mlx_guide
    assert "3-hour podcast" not in mlx_guide
    assert " and PORT (63042)" not in mlx_guide


def test_default_speaches_quickstart_and_open_webui_wiring_are_accurate() -> None:
    provider_guide = _text("services/parakeet/provider/README.md")
    stt_guide = _text("services/stt-provider/README.md")
    from services.topology import get_topology

    speaches_port = get_topology(ROOT / "services").port_defaults["SPEACHES_PORT"]
    assert f"http://localhost:{speaches_port}/health" in provider_guide
    assert f"http://localhost:{speaches_port}/health" in stt_guide
    assert "first transcription request pulls" not in provider_guide
    assert "AUDIO_STT_OPENAI_API_KEY" in provider_guide


def test_parakeet_localhost_docs_match_the_host_gateway_runtime() -> None:
    parakeet_manifest = _text("services/parakeet/service.yml")
    assert "host.docker.internal:${PARAKEET_LOCALHOST_PORT:-63042}" in parakeet_manifest
