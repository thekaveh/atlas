from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest
from pathlib import Path

from tests.three_surface_test_utils import surface_text

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
MINIO_MANIFEST = REPO_ROOT / "services" / "minio" / "service.yml"
MINIO_COMPOSE = REPO_ROOT / "services" / "minio" / "compose.yml"
MINIO_INIT = REPO_ROOT / "services" / "minio" / "init" / "scripts" / "init-minio.sh"
MINIO_README = REPO_ROOT / "services" / "minio" / "README.md"
REUSING_ATLAS = REPO_ROOT / "docs" / "operations" / "reusing-atlas.md"
SUBMODULE_USAGE = REPO_ROOT / "docs" / "operations" / "submodule-usage.md"
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
    "MINIO_JENKINS_ACCESS_KEY": "jenkins-ak",
    "MINIO_JENKINS_SECRET_KEY": "jenkins-sk",
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
    assert '"Action": ["s3:ListBucket", "s3:GetBucketLocation"]' in script  # still read-only
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


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI unavailable")
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


# Fake mc: logs every call and captures, at call time, each named policy and
# each service account's inline --policy file (init-minio.sh deletes it next).
_FAKE_MC = r"""#!/bin/sh
printf '%s\n' "$*" >>"$MC_LOG"
case "$1 $2 $3 $4" in
  "admin policy create local") cp "$6" "$MC_CAPTURE/named-$5.json" ;;
  "admin policy info local") exit 1 ;;
  "admin user svcacct info")
    # argv: admin user svcacct info local <account> [--json]
    if [ "$7" = "--json" ]; then
      printf '{"accessKey":"%s","description":"%s"}\n' "$6" "$(cat "$MC_CAPTURE/desc-$6" 2>/dev/null)"
      exit 0
    fi
    [ "${FAKE_SVCACCT_EXISTS:-}" = 1 ]; exit $? ;;
  "admin user svcacct ls")
    for account in ${FAKE_ACCOUNTS:-}; do printf '{"accessKey":"%s"}\n' "$account"; done
    exit 0 ;;
  "admin user svcacct rm") printf '%s\n' "$6" >> "$MC_CAPTURE/removed"; exit 0 ;;
  "admin user svcacct add"|"admin user svcacct edit")
    access=; policy=; secret=; prev=
    [ "$4" = edit ] && access=$6
    for arg in "$@"; do
      case "$prev" in --access-key) access=$arg ;; --policy) policy=$arg ;; --secret-key) secret=$arg ;; esac
      prev=$arg
    done
    [ -z "$policy" ] || cp "$policy" "$MC_CAPTURE/inline-$access.json"
    if [ -n "${FAKE_SVCACCT_FAIL:-}" ]; then
      echo "mc: <ERROR> Unable to add service account '$access'." >&2
      exit 1
    fi
    # Like the real mc, success echoes the new credentials on stdout.
    printf 'Access Key: %s\nSecret Key: %s\nExpiration: no-expiry\n' "$access" "$secret"
    ;;
esac
exit 0
"""


def _run_minio_init(tmp_path: Path, overrides: dict[str, str]) -> tuple[subprocess.CompletedProcess[str], str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "mc"
    stub.write_text(_FAKE_MC, encoding="utf-8")
    stub.chmod(0o755)
    capture = tmp_path / "capture"
    capture.mkdir(exist_ok=True)
    command_log = tmp_path / "mc.log"
    env = os.environ.copy()
    env.update(BUILT_IN_CONSUMER_ENV)
    env.update({"PATH": f"{bin_dir}{os.pathsep}{env['PATH']}", "MC_LOG": str(command_log),
                "MC_CAPTURE": str(capture), **overrides})
    result = subprocess.run(["/bin/sh", str(MINIO_INIT)], env=env, text=True, capture_output=True, check=False)
    return result, command_log.read_text(encoding="utf-8") if command_log.exists() else ""


_DAYDREAMS_ENV = {
    "MINIO_EXTRA_CONSUMERS": (
        "daydreams:MINIO_BUCKET_DAYDREAMS:"
        "MINIO_DAYDREAMS_ACCESS_KEY:MINIO_DAYDREAMS_SECRET_KEY"
    ),
    "MINIO_BUCKET_DAYDREAMS": "daydreams-artifacts",
    "MINIO_DAYDREAMS_ACCESS_KEY": "daydreams-ak",
    "MINIO_DAYDREAMS_SECRET_KEY": "daydreams-sk",
}


def _policy(tmp_path: Path, name: str) -> dict:
    return json.loads((tmp_path / "capture" / name).read_text(encoding="utf-8"))


def test_minio_init_provisions_parent_owned_extra_consumer_bucket(tmp_path: Path) -> None:
    result, log = _run_minio_init(tmp_path, {**_DAYDREAMS_ENV, "ASSET_WORKER_MINIO_BUCKET": "custom-worker-output"})

    assert result.returncode == 0, result.stderr + result.stdout
    assert "mb --ignore-existing local/daydreams-artifacts" in log
    assert "admin policy create local daydreams-policy" in log
    assert "admin user svcacct add local minioadmin --access-key daydreams-ak" in log
    assert "--secret-key daydreams-sk" in log
    assert "mb --ignore-existing local/custom-worker-output" in log
    policy = _policy(tmp_path, "named-asset-worker-policy.json")
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
    ingest = _policy(tmp_path, "named-asset-ingest-policy.json")
    assert any(
        "s3:PutObject" in statement["Action"]
        and statement["Resource"] == ["arn:aws:s3:::raw-assets/*"]
        for statement in ingest["Statement"]
    )


def _assert_scoped_inline_grants(tmp_path: Path, log: str, verb: str) -> None:
    """The named policy is never attached: each key's inline --policy is its grant.
    Without it a key inherits its parent's (MINIO_ROOT_USER) access."""
    svcacct = [line for line in log.splitlines() if line.startswith(f"admin user svcacct {verb} ")]
    accesses = [value for key, value in BUILT_IN_CONSUMER_ENV.items() if key.endswith("_ACCESS_KEY")]
    assert len(svcacct) == len(accesses) + 1
    assert all(" --policy /tmp/" in line for line in svcacct), svcacct
    for access in accesses:
        consumer = access.removesuffix("-ak")
        assert _policy(tmp_path, f"inline-{access}.json") == _policy(tmp_path, f"named-{consumer}-policy.json")
    daydreams = _policy(tmp_path, "inline-daydreams-ak.json")
    assert [statement["Resource"] for statement in daydreams["Statement"]] == [
        ["arn:aws:s3:::daydreams-artifacts/*"], ["arn:aws:s3:::daydreams-artifacts"],
    ]


@pytest.mark.parametrize("existing", [False, True], ids=["create", "refresh"])
def test_minio_service_accounts_carry_their_scoped_inline_policy(tmp_path: Path, existing: bool) -> None:
    result, log = _run_minio_init(tmp_path, {**_DAYDREAMS_ENV, "FAKE_SVCACCT_EXISTS": "1" if existing else ""})

    assert result.returncode == 0, result.stderr + result.stdout
    _assert_scoped_inline_grants(tmp_path, log, "edit" if existing else "add")
    if existing:
        assert "admin user svcacct edit local daydreams-ak --secret-key daydreams-sk --policy /tmp/daydreams-policy.json" in log
        assert "svcacct add " not in log
    # /tmp is shared with concurrent test runs, so check the cleanup statically.
    assert 'rm -f "$policy_file"' in MINIO_INIT.read_text(encoding="utf-8")


@pytest.mark.parametrize("case", [
    ("daydreams:MINIO_BUCKET_DAYDREAMS", {}, "invalid consumer entry"),
    ("daydreams:MINIO_BUCKET_DAYDREAMS:MINIO_DAYDREAMS_ACCESS_KEY:MINIO_DAYDREAMS_SECRET_KEY",
     {"MINIO_DAYDREAMS_SECRET_KEY": ""}, "(MINIO_BUCKET_DAYDREAMS/MINIO_DAYDREAMS_ACCESS_KEY/MINIO_DAYDREAMS_SECRET_KEY)"),
    ("daydreams:MINIO_BUCKET_DAYDREAMS:MINIO_DAYDREAMS_ACCESS_KEY:MINIO_DAYDREAMS_SECRET_KEY:MINIO_BUCKET_UNSET_RW",
     {}, "(MINIO_BUCKET_UNSET_RW)"),
    ("daydreams:MINIO_BUCKET_DAYDREAMS:MINIO_DAYDREAMS_ACCESS_KEY:MINIO_DAYDREAMS_SECRET_KEY::MINIO_BUCKET_UNSET_RO",
     {}, "(MINIO_BUCKET_UNSET_RO)"),
], ids=["short-entry", "missing-secret", "missing-writable-bucket", "missing-read-only-bucket"])
def test_minio_init_fails_closed_on_an_incomplete_consumer(tmp_path: Path, case) -> None:
    entry, overrides, message = case
    result, log = _run_minio_init(tmp_path, {**_DAYDREAMS_ENV, "MINIO_EXTRA_CONSUMERS": entry, **overrides})

    assert result.returncode == 1, result.stdout
    assert message in result.stderr
    assert "--access-key daydreams-ak" not in log
    assert "provisioning complete" not in result.stdout


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


def test_minio_root_password_is_never_an_mc_argument():
    """`mc alias set local URL USER PASSWORD` put the root password in
    /proc/*/cmdline on every start; the keys go on stdin."""
    import re
    from pathlib import Path

    script = (Path(__file__).resolve().parents[2] / "services/minio/init/scripts/init-minio.sh").read_text(encoding="utf-8")
    mc_lines = [line for line in script.splitlines() if re.search(r"\bmc\b", line) and not line.lstrip().startswith("#")]
    assert not [line for line in mc_lines if "MINIO_ROOT_PASSWORD" in line.split("| mc", 1)[-1]]


@pytest.mark.parametrize("existing", [False, True], ids=["create", "refresh"])
def test_minio_init_output_carries_no_service_account_secret(tmp_path: Path, existing: bool) -> None:
    """#1974: mc echoes each account's keys on stdout, and that output became
    the container log. Only account names may reach it."""
    result, log = _run_minio_init(tmp_path, {**_DAYDREAMS_ENV, "FAKE_SVCACCT_EXISTS": "1" if existing else ""})
    output = result.stdout + result.stderr
    secrets = [value for key, value in {**BUILT_IN_CONSUMER_ENV, **_DAYDREAMS_ENV}.items()
               if key.endswith("_SECRET_KEY")]

    assert (
        result.returncode,
        "Secret Key:" in output,
        [secret for secret in secrets if secret in output],
        f"admin user svcacct {'edit' if existing else 'add'} " in log,
    ) == (0, False, [], True)


def test_minio_init_still_reports_a_failed_service_account_write(tmp_path: Path) -> None:
    """#1974: silencing stdout must not hide a failure; mc's stderr and the
    nonzero exit still reach the operator."""
    result, _ = _run_minio_init(tmp_path, {**_DAYDREAMS_ENV, "FAKE_SVCACCT_FAIL": "1"})

    assert (result.returncode != 0, "Unable to add service account" in result.stderr) == (True, True)


def test_minio_init_revokes_only_superseded_atlas_managed_accounts(tmp_path):
    """#1385: an account Atlas created for a consumer whose key is no longer
    managed is revoked. A current Atlas account and an account an operator made
    by hand (no atlas-managed description) survive."""
    accounts = {
        "old-ak": "atlas-managed:backend",
        "daydreams-ak": "atlas-managed:daydreams",
        "handmade-ak": "",
    }
    result, _ = _run_minio_init(tmp_path, {
        **_DAYDREAMS_ENV, "FAKE_ACCOUNTS": " ".join(accounts),
        **{}})
    for access, description in accounts.items():
        (tmp_path / "capture" / f"desc-{access}").write_text(description, encoding="utf-8")
    result, _ = _run_minio_init(tmp_path, {
        **_DAYDREAMS_ENV, "FAKE_ACCOUNTS": " ".join(accounts)})

    removed = (tmp_path / "capture" / "removed").read_text(encoding="utf-8").split()
    assert (result.returncode, removed) == (0, ["old-ak"])


def test_jenkins_gets_an_account_that_can_write_only_the_jars_bucket(tmp_path):
    """#1385: Jenkins publishes JARs, so its key reaches the jars bucket only;
    the Iceberg key stays on the Iceberg consumer."""
    result, log = _run_minio_init(tmp_path, {**_DAYDREAMS_ENV})

    assert result.returncode == 0, result.stderr + result.stdout
    assert "svcacct add local minioadmin --access-key jenkins-ak" in log
    policy = _policy(tmp_path, "named-jenkins-policy.json")
    resources = [r for statement in policy["Statement"] for r in statement["Resource"]]
    assert resources == ["arn:aws:s3:::jars/*", "arn:aws:s3:::jars"]
