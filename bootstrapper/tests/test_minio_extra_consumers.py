from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from tests.three_surface_test_utils import surface_text

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
MINIO_MANIFEST = REPO_ROOT / "services" / "minio" / "service.yml"
MINIO_COMPOSE = REPO_ROOT / "services" / "minio" / "compose.yml"
MINIO_INIT = REPO_ROOT / "services" / "minio" / "init" / "scripts" / "init-minio.sh"
MINIO_README = REPO_ROOT / "services" / "minio" / "README.md"
REUSING_ATLAS = REPO_ROOT / "docs" / "deployment" / "reusing-atlas.md"
SUBMODULE_USAGE = REPO_ROOT / "docs" / "deployment" / "submodule-usage.md"
ENV_EXAMPLE = REPO_ROOT / ".env.example"


BUILT_IN_CONSUMER_ENV = {
    "MINIO_ROOT_USER": "minioadmin",
    "MINIO_ROOT_PASSWORD": "minio-root-password",
    "MINIO_BUCKET_COMFYUI": "comfyui",
    "MINIO_COMFYUI_ACCESS_KEY": "comfyui-ak",
    "MINIO_COMFYUI_SECRET_KEY": "comfyui-sk",
    "MINIO_BUCKET_BACKEND": "backend",
    "MINIO_BACKEND_ACCESS_KEY": "backend-ak",
    "MINIO_BACKEND_SECRET_KEY": "backend-sk",
    "MINIO_BUCKET_N8N": "n8n",
    "MINIO_N8N_ACCESS_KEY": "n8n-ak",
    "MINIO_N8N_SECRET_KEY": "n8n-sk",
    "MINIO_BUCKET_JUPYTER": "jupyter",
    "MINIO_JUPYTER_ACCESS_KEY": "jupyter-ak",
    "MINIO_JUPYTER_SECRET_KEY": "jupyter-sk",
    "MINIO_BUCKET_SPARK_HISTORY": "spark-history",
    "MINIO_SPARK_ACCESS_KEY": "spark-ak",
    "MINIO_SPARK_SECRET_KEY": "spark-sk",
    "MINIO_BUCKET_DOCLING": "docling",
    "MINIO_DOCLING_ACCESS_KEY": "docling-ak",
    "MINIO_DOCLING_SECRET_KEY": "docling-sk",
    "MINIO_BUCKET_LANGFUSE": "langfuse",
    "MINIO_LANGFUSE_ACCESS_KEY": "langfuse-ak",
    "MINIO_LANGFUSE_SECRET_KEY": "langfuse-sk",
    "MINIO_BUCKET_MLFLOW": "mlflow",
    "MINIO_MLFLOW_ACCESS_KEY": "mlflow-ak",
    "MINIO_MLFLOW_SECRET_KEY": "mlflow-sk",
    "MINIO_BUCKET_LABEL_STUDIO": "label-studio",
    "MINIO_LABEL_STUDIO_ACCESS_KEY": "label-studio-ak",
    "MINIO_LABEL_STUDIO_SECRET_KEY": "label-studio-sk",
    "MINIO_BUCKET_ICEBERG_LAKEHOUSE": "lakehouse",
    "MINIO_BUCKET_ICEBERG_JARS": "jars",
    "MINIO_BUCKET_ICEBERG_CHECKPOINTS": "checkpoints",
    "MINIO_BUCKET_ICEBERG_LANDING": "landing",
    "MINIO_ICEBERG_ACCESS_KEY": "iceberg-ak",
    "MINIO_ICEBERG_SECRET_KEY": "iceberg-sk",
    "MINIO_BUCKET_ASSET_INPUTS": "raw-assets",
    "MINIO_ASSET_INGEST_ACCESS_KEY": "asset-ingest-ak",
    "MINIO_ASSET_INGEST_SECRET_KEY": "asset-ingest-sk",
    "ASSET_WORKER_MINIO_BUCKET": "asset-worker",
    "MINIO_ASSET_WORKER_ACCESS_KEY": "asset-worker-ak",
    "MINIO_ASSET_WORKER_SECRET_KEY": "asset-worker-sk",
    "ASSET_BAKER_MINIO_BUCKET": "asset-baker",
    "MINIO_ASSET_BAKER_ACCESS_KEY": "asset-baker-ak",
    "MINIO_ASSET_BAKER_SECRET_KEY": "asset-baker-sk",
}


def test_asset_processors_use_scoped_minio_accounts() -> None:
    manifest = yaml.safe_load(MINIO_MANIFEST.read_text(encoding="utf-8"))
    env_vars = {entry["name"]: entry for entry in manifest["env"]}
    assert env_vars["MINIO_BUCKET_ASSET_INPUTS"]["default"] == "raw-assets"
    assert env_vars["MINIO_ASSET_INGEST_ACCESS_KEY"]["secret"] is True
    assert env_vars["MINIO_ASSET_INGEST_SECRET_KEY"]["secret"] is True

    compose = yaml.safe_load(MINIO_COMPOSE.read_text(encoding="utf-8"))
    init_env = compose["services"]["minio-init"]["environment"]
    init_script = MINIO_INIT.read_text(encoding="utf-8")
    assert (
        "asset-ingest:MINIO_BUCKET_ASSET_INPUTS:MINIO_ASSET_INGEST_ACCESS_KEY:"
        "MINIO_ASSET_INGEST_SECRET_KEY"
    ) in init_script
    for service in ("ASSET_WORKER", "ASSET_BAKER"):
        assert env_vars[f"MINIO_{service}_ACCESS_KEY"]["secret"] is True
        assert env_vars[f"MINIO_{service}_SECRET_KEY"]["secret"] is True
        assert init_env[f"{service}_MINIO_BUCKET"] == f"${{{service}_MINIO_BUCKET}}"
        assert init_env[f"MINIO_{service}_ACCESS_KEY"] == f"${{MINIO_{service}_ACCESS_KEY}}"
        assert (
            f"{service}_MINIO_BUCKET:MINIO_{service}_ACCESS_KEY:"
            f"MINIO_{service}_SECRET_KEY::MINIO_BUCKET_ASSET_INPUTS"
        ) in init_script


def test_spark_consumer_uses_scoped_lakehouse_account() -> None:
    manifest = yaml.safe_load(MINIO_MANIFEST.read_text(encoding="utf-8"))
    env_vars = {entry["name"]: entry for entry in manifest["env"]}
    compose = yaml.safe_load(MINIO_COMPOSE.read_text(encoding="utf-8"))
    init_env = compose["services"]["minio-init"]["environment"]
    init_script = MINIO_INIT.read_text(encoding="utf-8")

    assert env_vars["MINIO_BUCKET_SPARK_HISTORY"]["default"] == "spark-history"
    assert env_vars["MINIO_SPARK_ACCESS_KEY"]["secret"] is True
    assert env_vars["MINIO_SPARK_SECRET_KEY"]["secret"] is True
    assert init_env["MINIO_SPARK_ACCESS_KEY"] == "${MINIO_SPARK_ACCESS_KEY}"
    assert (
        "spark:MINIO_BUCKET_SPARK_HISTORY:MINIO_SPARK_ACCESS_KEY:"
        "MINIO_SPARK_SECRET_KEY:MINIO_BUCKET_ICEBERG_LAKEHOUSE,"
        "MINIO_BUCKET_ICEBERG_JARS,MINIO_BUCKET_ICEBERG_CHECKPOINTS,"
        "MINIO_BUCKET_ICEBERG_LANDING"
    ) in init_script


def test_asset_processor_input_policy_is_read_only() -> None:
    script = MINIO_INIT.read_text(encoding="utf-8")
    assert 'read_only_statements=' in script
    assert '"Action": ["s3:GetObject"]' in script
    assert '"Action": ["s3:ListBucket"]' in script
    assert 'writable_buckets="$bucket"' in script


def test_asset_processor_compose_never_falls_back_to_minio_root() -> None:
    for service in ("asset-worker", "asset-baker"):
        compose_text = (REPO_ROOT / "services" / service / "compose.yml").read_text(
            encoding="utf-8"
        )
        token = service.upper().replace("-", "_")
        assert "MINIO_ROOT_USER" not in compose_text
        assert "MINIO_ROOT_PASSWORD" not in compose_text
        assert f"${{MINIO_{token}_ACCESS_KEY}}" in compose_text
        assert f"${{MINIO_{token}_SECRET_KEY}}" in compose_text
        assert "service_completed_successfully" in compose_text


def test_renamed_asset_input_bucket_reaches_both_processor_allowlists() -> None:
    env = os.environ.copy()
    env.update(
        {
            "MINIO_BUCKET_ASSET_INPUTS": "incoming-assets",
            "ASSET_WORKER_ALLOWED_INPUT_BUCKETS": "",
            "ASSET_BAKER_ALLOWED_INPUT_BUCKETS": "",
        }
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(ENV_EXAMPLE),
            "-f",
            str(REPO_ROOT / "docker-compose.yml"),
            "config",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    for service in ("asset-worker", "asset-baker"):
        token = service.upper().replace("-", "_")
        assert rendered["services"][service]["environment"][
            f"{token}_ALLOWED_INPUT_BUCKETS"
        ] == "incoming-assets"


def test_minio_extra_consumers_are_declared_in_manifest_compose_and_env_example() -> None:
    manifest = yaml.safe_load(MINIO_MANIFEST.read_text(encoding="utf-8"))
    env_vars = {entry["name"]: entry for entry in manifest["env"]}

    assert env_vars["MINIO_EXTRA_CONSUMERS"]["default"] == ""
    assert "CONSUMER:BUCKET_VAR:ACCESS_VAR:SECRET_VAR" in env_vars[
        "MINIO_EXTRA_CONSUMERS"
    ]["description"]

    compose = yaml.safe_load(MINIO_COMPOSE.read_text(encoding="utf-8"))
    minio_init_env = compose["services"]["minio-init"]["environment"]
    assert minio_init_env["MINIO_EXTRA_CONSUMERS"] == "${MINIO_EXTRA_CONSUMERS:-}"

    assert "MINIO_EXTRA_CONSUMERS=" in ENV_EXAMPLE.read_text(encoding="utf-8")


def test_minio_init_provisions_parent_owned_extra_consumer_bucket(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    command_log = tmp_path / "mc.log"
    asset_policy = tmp_path / "asset-worker-policy.json"
    ingest_policy = tmp_path / "asset-ingest-policy.json"
    stub = bin_dir / "mc"
    stub.write_text(
        f"""#!/bin/sh
printf '%s\\n' "$*" >> {command_log}
if [ "$1 $2 $3 $4 $5" = "admin policy create local asset-worker-policy" ]; then
  cp "$6" {asset_policy}
fi
if [ "$1 $2 $3 $4 $5" = "admin policy create local asset-ingest-policy" ]; then
  cp "$6" {ingest_policy}
fi
if [ "$1 $2 $3 $4" = "admin policy info local" ]; then
  exit 1
fi
if [ "$1 $2 $3 $4" = "admin user svcacct info" ]; then
  exit 1
fi
exit 0
""",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    env = os.environ.copy()
    env.update(BUILT_IN_CONSUMER_ENV)
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "MINIO_EXTRA_CONSUMERS": (
                "daydreams:MINIO_BUCKET_DAYDREAMS:"
                "MINIO_DAYDREAMS_ACCESS_KEY:MINIO_DAYDREAMS_SECRET_KEY"
            ),
            "MINIO_BUCKET_DAYDREAMS": "daydreams-artifacts",
            "MINIO_DAYDREAMS_ACCESS_KEY": "daydreams-ak",
            "MINIO_DAYDREAMS_SECRET_KEY": "daydreams-sk",
            "ASSET_WORKER_MINIO_BUCKET": "custom-worker-output",
        }
    )

    result = subprocess.run(
        ["/bin/sh", str(MINIO_INIT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    log = command_log.read_text(encoding="utf-8")
    assert "mb --ignore-existing local/daydreams-artifacts" in log
    assert "admin policy create local daydreams-policy" in log
    assert "admin user svcacct add local minioadmin --access-key daydreams-ak" in log
    assert "--secret-key daydreams-sk" in log
    assert "mb --ignore-existing local/custom-worker-output" in log
    policy = json.loads(asset_policy.read_text(encoding="utf-8"))
    output_statement = next(
        statement
        for statement in policy["Statement"]
        if "s3:PutObject" in statement["Action"]
    )
    assert output_statement["Resource"] == [
        "arn:aws:s3:::custom-worker-output/*"
    ]
    input_statement = next(
        statement
        for statement in policy["Statement"]
        if statement["Action"] == ["s3:GetObject"]
    )
    assert input_statement["Resource"] == ["arn:aws:s3:::raw-assets/*"]
    ingest = json.loads(ingest_policy.read_text(encoding="utf-8"))
    assert any(
        "s3:PutObject" in statement["Action"]
        and statement["Resource"] == ["arn:aws:s3:::raw-assets/*"]
        for statement in ingest["Statement"]
    )


def test_docs_cover_parent_owned_minio_extra_consumers() -> None:
    canonical_docs = "\n".join(
        [
            MINIO_README.read_text(encoding="utf-8"),
            REUSING_ATLAS.read_text(encoding="utf-8"),
            SUBMODULE_USAGE.read_text(encoding="utf-8"),
        ]
    )
    for expected in (
        "MINIO_EXTRA_CONSUMERS",
        "daydreams:MINIO_BUCKET_DAYDREAMS:MINIO_DAYDREAMS_ACCESS_KEY:MINIO_DAYDREAMS_SECRET_KEY",
        "services/_user/<name>/compose.yml",
        "parent-owned",
    ):
        assert expected in canonical_docs

    for text in (
        surface_text("docs/development.md", "site"),
        surface_text("docs/development.md", "wiki"),
    ):
        assert "MINIO_EXTRA_CONSUMERS" in text
        assert "daydreams:MINIO_BUCKET_DAYDREAMS" in text
