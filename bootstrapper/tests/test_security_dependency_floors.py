from __future__ import annotations

import json
import re
from pathlib import Path
import sys

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised by the Python 3.10 test environment
    import tomli as tomllib

import yaml


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
    assert "pytest==9.1.1" in dev_requirements
    assert "pytest-asyncio==1.4.0" in dev_requirements
    assert "httpx2==2.6.0" in dev_requirements
    assert "requirements-test-locked.txt" in workflow
    assert "requirements-dev.txt" not in dockerfile
    assert "pytest-asyncio omitted intentionally" not in workflow


def test_backend_fal_client_supports_cancellable_async_transport() -> None:
    requirements = _text("services/backend/app/app/requirements.txt")

    assert "fal-client>=1.0.0" in requirements


def test_user_facing_images_clear_current_security_advisories() -> None:
    open_webui = _text("services/open-webui/service.yml")
    n8n = _text("services/n8n/service.yml")

    assert 'default: "dyrnq/open-webui:v0.11.0"' in open_webui
    assert open_webui.count('default: "dyrnq/open-webui:v0.11.0"') == 1
    assert n8n.count('default: "n8nio/n8n:2.36.7"') == 2


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


def test_airflow_build_validation_uses_runtime_core_release() -> None:
    workflow = _text(".github/workflows/services-lint.yml")
    contributor_docs = _text("docs/CONTRIBUTING-services.md")
    dependabot = _text(".github/dependabot.yml")

    assert "--build-arg BASE_IMAGE=apache/airflow:3.3.1" in workflow
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
    changelog = _text("docs/CHANGELOG.md")
    assert "PyTorch 2.13" in changelog
    assert "PyG 0.8" in changelog


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

    assert "services/mcp-servers/runtime/requirements-test.txt" in workflow
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


def test_large_service_runtime_graphs_use_compiled_constraints() -> None:
    workflow = _text(".github/workflows/services-lint.yml")
    assert "python -m scripts.check_runtime_locks" in workflow

    surfaces = (
        ("services/backend/app/Dockerfile", "services/backend/app/app/requirements-locked.txt"),
        ("services/airflow/build/Dockerfile", "services/airflow/build/requirements-locked.txt"),
        ("services/jupyterhub/build/Dockerfile", "services/jupyterhub/build/requirements-locked.txt"),
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


def test_external_contract_ledger_matches_executable_pyg_lib_pin() -> None:
    requirements = _text("services/jupyterhub/build/requirements.txt")
    ledger = _text("docs/maintenance/external-contract-ledger.md")

    assert "pyg_lib==0.8.0" in requirements
    assert "pyg_lib==0.8.0" in ledger
    assert "pyg_lib==0.6.0" not in ledger


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


def test_jupyterhub_external_build_artifacts_are_digest_verified() -> None:
    dockerfile = _text("services/jupyterhub/build/Dockerfile")
    assert dockerfile.count("sha256sum -c -") >= 1
    assert "python -m spacy download" not in dockerfile
    assert "nltk.download(" not in dockerfile
    _assert_jupyterhub_nlp_asset_integrity(dockerfile)
    assert "ENV PYTHONPATH=/home/jovyan\n" in dockerfile
    assert "${PYTHONPATH}" not in dockerfile
    assert "COURSIER_SHA256=" in dockerfile
