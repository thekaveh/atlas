from __future__ import annotations

import json
import re
import tomllib

import yaml
import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from tests.test_security_dependency_floors import (
    ROOT,
    _assert_jupyterhub_nlp_asset_integrity,
    _python_without_comments,
    _ragas_notebook_findings,
    _requirements_named,
    _text,
    _uv_versions_for_environment,
)


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

    assert package["overrides"]["sharp"] == "0.35.5"
    assert '"node_modules/sharp"' in _text("services/n8n/init/config/package-lock.json")
    # 0.35.5 is the release that carries the fix; <0.35.5 is the advisory range.
    assert '"version": "0.35.5"' in _text(
        "services/n8n/init/config/package-lock.json"
    )


def test_asset_worker_toolchain_uses_an_audited_npm_lock() -> None:
    package = json.loads(_text("services/asset-worker/app/package.json"))
    dockerfile = _text("services/asset-worker/app/Dockerfile")
    helper = _text("scripts/gltf-transform-postprocess.sh")

    assert package["dependencies"]["@gltf-transform/cli"] == "4.5.1"
    assert package["overrides"]["sharp"] == "0.35.5"
    # Clears GHSA-3g3m-2fjr-6pw4 (glob 10) and GHSA-g3hv-75wg-m249 (minimatch 9).
    assert package["overrides"]["glob"] == "14.0.3"
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

    assert "name: Run bootstrapper tests on Python 3.11" in workflow
    assert "uv run --python 3.11 --isolated --locked --group dev" in workflow


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
        "bootstrapper/services/migrations/migration_v4.py",
        "bootstrapper/services/migrations/migration_v5.py",
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


def _pins(relative: str, package: str) -> set[str]:
    pattern = rf"^{re.escape(package)}==(\S+)"
    return set(re.findall(pattern, _text(relative), re.MULTILINE))


def test_spark_connect_clients_move_in_lockstep_with_the_spark_image() -> None:
    images = yaml.safe_load(_text("services/spark/service.yml"))["images"]
    spark_image = {row["var"]: row["default"] for row in images}["SPARK_IMAGE"]
    match = re.match(r"apache/spark:(\d+(?:\.\d+)+)", spark_image)
    assert match, spark_image
    version = {match.group(1)}

    assert _pins("services/jupyterhub/build/requirements.txt", "pyspark-client") == version
    assert _pins("services/jupyterhub/build/requirements-locked.txt", "pyspark-client") == version
    assert _pins("services/airflow/build/requirements.txt", "pyspark[connect]") == version
    assert _pins("services/airflow/build/requirements-locked.txt", "pyspark") == version

    dependabot = yaml.safe_load(_text(".github/dependabot.yml"))
    ignored = {
        entry["dependency-name"]
        for update in dependabot["updates"]
        for entry in update.get("ignore", [])
    }
    assert {"pyspark", "pyspark-client"} <= ignored


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
        "PARAKEET_LOCALHOST_BIND_HOST=127.0.0.1",
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


def _bootstrapper_pillow_branches(environment):
    project = tomllib.loads(_text("bootstrapper/pyproject.toml"))["project"]
    assert project["requires-python"] == ">=3.11"
    requirements = [Requirement(item) for item in project["dependencies"]
                    if Requirement(item).name == "pillow"]
    assert len(requirements) == 1
    assert requirements[0].marker is None
    assert Version(environment["python_version"]) in SpecifierSet(project["requires-python"])
    return requirements


@pytest.mark.parametrize("python_version", ("3.11", "3.12", "3.13", "3.14", "3.15"))
@pytest.mark.parametrize("sys_platform", ("linux", "darwin", "win32", "emscripten"))
def test_bootstrapper_pillow_floor_covers_supported_python(python_version, sys_platform):
    environment = {"python_version": python_version,
                   "python_full_version": f"{python_version}.0",
                   "sys_platform": sys_platform}
    selected = _bootstrapper_pillow_branches(environment)
    assert len(selected) == 1
    expected = "13.0.0"
    bounds = ">=13.0.0,<14"
    assert selected[0].specifier == SpecifierSet(bounds)
    assert _uv_versions_for_environment("bootstrapper/uv.lock", "pillow", environment) == [expected]
    assert _exported_bootstrapper_pillow_versions(environment) == [SpecifierSet(f"=={expected}")]


def _exported_bootstrapper_pillow_versions(environment):
    lines = _text("bootstrapper/requirements-locked.txt").splitlines()
    requirements = [Requirement(line) for line in lines if line.startswith("pillow==")]
    return [item.specifier for item in requirements
            if item.marker is None or item.marker.evaluate(environment)]


@pytest.mark.parametrize(
    ("name", "fixed"),
    (
        ('fastmcp', '3.4.10'),
        ('brotli', '1.2.1'),
        ('pillow', '13.0.0'),
        ('setuptools', '84.1.0'),
        ('gitpython', '3.2.1'),
        ('mlflow', '3.17.2'),
        ('aiohttp', '3.14.4'),
        ('authlib', '1.9.1'),
        ('banks', '2.5.1'),
        ('dnspython', '2.9.2'),
        ('filelock', '4.2.1'),
        ('msgpack', '1.2.3'),
        ('opentelemetry-api', '1.45.2'),
        ('nltk', '3.10.4'),
    ),
)
def test_jupyter_repaired_dependencies_have_security_floors(name, fixed):
    requirements = [Requirement(line.split("#", 1)[0].strip())
                    for line in _text("services/jupyterhub/build/requirements.txt").splitlines()
                    if line.split("#", 1)[0].strip()]
    selected = [item for item in requirements if item.name.lower() == name]
    assert len(selected) == 1
    assert Version(fixed) in selected[0].specifier
    previous = {"fastmcp": "3.4.4", "brotli": "1.2.0", "pillow": "12.3.0",
                "setuptools": "84.0.0", "gitpython": "3.1.62", "mlflow": "3.16.0",
                "aiohttp": "3.14.3", "authlib": "1.8.0", "banks": "2.5.0",
                "dnspython": "2.8.0", "filelock": "3.32.6", "msgpack": "1.2.2",
                "opentelemetry-api": "1.44.0", "nltk": "3.10.3"}
    assert Version(previous[name]) not in selected[0].specifier


@pytest.mark.parametrize(
    ("name", "fixed"),
    (
        ('aiohttp', '3.14.4'),
        ('yarl', '1.25.1'),
        ('authlib', '1.9.1'),
        ('banks', '2.5.1'),
        ('brotli', '1.2.1'),
        ('dnspython', '2.9.2'),
        ('fastmcp', '3.4.10'),
        ('fastmcp-slim', '3.4.10'),
        ('filelock', '4.2.1'),
        ('gitpython', '3.2.1'),
        ('msgpack', '1.2.3'),
        ('opentelemetry-api', '1.45.2'),
        ('opentelemetry-sdk', '1.45.2'),
        ('opentelemetry-proto', '1.45.2'),
        ('opentelemetry-semantic-conventions', '0.66b2'),
        ('pillow', '13.0.0'),
        ('setuptools', '84.1.0'),
        ('mlflow', '3.17.2'),
        ('mlflow-skinny', '3.17.2'),
        ('mlflow-tracing', '3.17.2'),
        ('nltk', '3.10.4'),
    ),
)
def test_jupyter_repaired_lock_contains_the_compatible_fixed_family(name, fixed):
    lines = _text("services/jupyterhub/build/requirements-locked.txt").splitlines()
    locked = dict(line.split("==", 1) for line in lines if "==" in line)
    assert locked[name] == fixed


@pytest.mark.parametrize(("name", "fixed", "previous"), (
    ("pillow", "13.0.0", "12.3.0"),
    ("aiohttp", "3.14.4", "3.14.3"),
    ("setuptools", "84.1.0", "83.0.0"),
    ("filelock", "4.2.1", "3.32.2"),
    ("msgpack", "1.2.3", "1.2.1"),
    ("certifi", "2026.10.19", "2026.7.22"),
    ("transformers", "5.10.0", "5.5.0"),
))
def test_nemo_gpu_repaired_dependencies_reject_older_vulnerable_versions(name, fixed, previous):
    requirement, = _requirements_named(
        (line.split("#", 1)[0] for line in
         _text("services/parakeet/provider/gpu/requirements.txt").splitlines()), name,
    )
    assert (Version(fixed) in requirement.specifier
            and Version(previous) not in requirement.specifier)


@pytest.mark.parametrize(("name", "fixed"), (
    ("pillow", "13.0.0"), ("aiohttp", "3.14.4"), ("yarl", "1.25.1"),
    ("setuptools", "84.1.0"), ("filelock", "4.2.1"), ("msgpack", "1.2.3"),
))
def test_nemo_gpu_repaired_lock_keeps_the_fixed_leaf_family(name, fixed):
    assert dict(
        line.split("==", 1) for line in
        _text("services/parakeet/provider/gpu/requirements-locked.txt").splitlines()
        if "==" in line
    )[name] == fixed


# Images whose pip runs at build time. Each upgrades pip to 26.3 before the first
# pip use, because the base pip vendors a certifi that still trusts the distrusted
# emSign ECC Root CA - G3 (#1905).
PIP_BEFORE_USE_DOCKERFILES = (
    "services/mcp-servers/build/Dockerfile",
    "services/jupyterhub/build/Dockerfile",
    "services/airflow/build/Dockerfile",
    "services/litellm/init/Dockerfile",
    "services/open-webui/init/Dockerfile",
    "services/local-deep-researcher/build/Dockerfile",
    "services/parakeet/provider/gpu/Dockerfile",
    "services/docling/provider/gpu/Dockerfile",
    "services/docling/provider/adapter/Dockerfile",
    "services/asset-worker/app/Dockerfile",
    "services/mlflow/build/Dockerfile",
    "services/asset-baker/app/Dockerfile",
    "services/backend/app/Dockerfile",
)


@pytest.mark.parametrize("relative", PIP_BEFORE_USE_DOCKERFILES)
def test_pip_is_upgraded_to_26_3_before_its_first_use(relative: str) -> None:
    text = _text(relative)
    upgrade = re.search(r"^RUN (python -m )?pip install --no-cache-dir pip==26\.3\b", text, re.M)
    first_use = re.search(r"^RUN (python -m )?pip install", text, re.M)
    assert upgrade is not None, f"{relative} must upgrade pip to 26.3 (#1905)"
    assert first_use is not None
    assert upgrade.start() <= first_use.start(), f"{relative} uses pip before upgrading it"


def test_backend_disables_the_distrusted_emsign_g3_root_in_its_system_store() -> None:
    """Newer ca-certificates already ship the root disabled; the Dockerfile must
    end with the root disabled either way, then rebuild the bundle (#1905)."""
    text = _text("services/backend/app/Dockerfile")
    assert "mozilla/emSign_ECC_Root_CA_-_G3.crt" in text
    assert "sed -i 's|^mozilla/emSign_ECC_Root_CA_-_G3.crt$|!mozilla/emSign_ECC_Root_CA_-_G3.crt|'" in text
    assert "grep -q '^!mozilla/emSign_ECC_Root_CA_-_G3.crt$' /etc/ca-certificates.conf" in text
    assert "update-ca-certificates --fresh" in text


def test_backend_drops_rays_bundled_aiohttp_copy_but_keeps_its_own() -> None:
    """Ray bundles aiohttp 3.14.1 for its runtime-env agent, which the Backend never
    starts. The Backend build removes that copy, and fails if Ray moves it (#1948)."""
    text = _text("services/backend/app/Dockerfile")
    assert 'ray_agent/aiohttp-3.14.1.dist-info" && \\' in text
    assert 'rm -rf "$ray_agent/aiohttp" "$ray_agent/aiohttp-3.14.1.dist-info"' in text


def test_mlflow_lock_pins_the_scanner_fixed_base_packages() -> None:
    """pillow, aiohttp, certifi and pyasn1 are base-image packages the Final-image
    scan flags (#1948). The audited lock must control them, not the base."""
    requirements = _text("services/mlflow/build/requirements.txt")
    locked = _text("services/mlflow/build/requirements-locked.txt")
    for pin in ("pillow==13.0.0", "pyasn1==0.6.5", "aiohttp==3.14.4", "certifi==2026.10.19"):
        assert pin in requirements, pin
        assert pin in locked.splitlines(), pin
