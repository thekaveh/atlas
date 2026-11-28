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
SILO_MANIFEST = REPO_ROOT / "services" / "silo" / "service.yml"
SILO_COMPOSE = REPO_ROOT / "services" / "silo" / "compose.yml"
SILO_INIT = REPO_ROOT / "services" / "silo" / "init" / "scripts" / "init-silo.sh"
SILO_README = REPO_ROOT / "services" / "silo" / "README.md"
REUSING_ATLAS = REPO_ROOT / "docs" / "operations" / "reusing-atlas.md"
SUBMODULE_USAGE = REPO_ROOT / "docs" / "operations" / "submodule-usage.md"
ENV_EXAMPLE = REPO_ROOT / ".env.example"


BUILT_IN_CONSUMER_ENV = {
    "SILO_ROOT_USER": "minioadmin",
    "SILO_ROOT_PASSWORD": "silo-root-password",
    "SILO_BUCKET_COMFYUI": "comfyui",
    "SILO_COMFYUI_ACCESS_KEY": "comfyui-ak",
    "SILO_COMFYUI_SECRET_KEY": "comfyui-sk",
    "SILO_BUCKET_BACKEND": "backend",
    "SILO_BACKEND_ACCESS_KEY": "backend-ak",
    "SILO_BACKEND_SECRET_KEY": "backend-sk",
    "SILO_BUCKET_N8N": "n8n",
    "SILO_N8N_ACCESS_KEY": "n8n-ak",
    "SILO_N8N_SECRET_KEY": "n8n-sk",
    "SILO_BUCKET_JUPYTER": "jupyter",
    "SILO_JUPYTER_ACCESS_KEY": "jupyter-ak",
    "SILO_JUPYTER_SECRET_KEY": "jupyter-sk",
    "SILO_BUCKET_SPARK_HISTORY": "spark-history",
    "SILO_SPARK_ACCESS_KEY": "spark-ak",
    "SILO_SPARK_SECRET_KEY": "spark-sk",
    "SILO_BUCKET_DOCLING": "docling",
    "SILO_DOCLING_ACCESS_KEY": "docling-ak",
    "SILO_DOCLING_SECRET_KEY": "docling-sk",
    "SILO_BUCKET_LANGFUSE": "langfuse",
    "SILO_LANGFUSE_ACCESS_KEY": "langfuse-ak",
    "SILO_LANGFUSE_SECRET_KEY": "langfuse-sk",
    "SILO_BUCKET_MLFLOW": "mlflow",
    "SILO_MLFLOW_ACCESS_KEY": "mlflow-ak",
    "SILO_MLFLOW_SECRET_KEY": "mlflow-sk",
    "SILO_BUCKET_LABEL_STUDIO": "label-studio",
    "SILO_LABEL_STUDIO_ACCESS_KEY": "label-studio-ak",
    "SILO_LABEL_STUDIO_SECRET_KEY": "label-studio-sk",
    "SILO_BUCKET_ICEBERG_LAKEHOUSE": "lakehouse",
    "SILO_BUCKET_ICEBERG_JARS": "jars",
    "SILO_BUCKET_ICEBERG_CHECKPOINTS": "checkpoints",
    "SILO_BUCKET_ICEBERG_LANDING": "landing",
    "SILO_ICEBERG_ACCESS_KEY": "iceberg-ak",
    "SILO_JENKINS_ACCESS_KEY": "jenkins-ak",
    "SILO_JENKINS_SECRET_KEY": "jenkins-sk",
    "SILO_ICEBERG_SECRET_KEY": "iceberg-sk",
    "SILO_BUCKET_ASSET_INPUTS": "raw-assets",
    "SILO_ASSET_INGEST_ACCESS_KEY": "asset-ingest-ak",
    "SILO_ASSET_INGEST_SECRET_KEY": "asset-ingest-sk",
    "ASSET_WORKER_SILO_BUCKET": "asset-worker",
    "SILO_ASSET_WORKER_ACCESS_KEY": "asset-worker-ak",
    "SILO_ASSET_WORKER_SECRET_KEY": "asset-worker-sk",
    "ASSET_BAKER_SILO_BUCKET": "asset-baker",
    "SILO_ASSET_BAKER_ACCESS_KEY": "asset-baker-ak",
    "SILO_ASSET_BAKER_SECRET_KEY": "asset-baker-sk",
}


pytestmark = pytest.mark.usefixtures("throwaway_admin_secrets")


def test_asset_processors_use_scoped_silo_accounts() -> None:
    manifest = yaml.safe_load(SILO_MANIFEST.read_text(encoding="utf-8"))
    env_vars = {entry["name"]: entry for entry in manifest["env"]}
    assert env_vars["SILO_BUCKET_ASSET_INPUTS"]["default"] == "raw-assets"
    assert env_vars["SILO_ASSET_INGEST_ACCESS_KEY"]["secret"] is True
    assert env_vars["SILO_ASSET_INGEST_SECRET_KEY"]["secret"] is True

    compose = yaml.safe_load(SILO_COMPOSE.read_text(encoding="utf-8"))
    init_env = compose["services"]["silo-init"]["environment"]
    init_script = SILO_INIT.read_text(encoding="utf-8")
    assert (
        "asset-ingest:SILO_BUCKET_ASSET_INPUTS:SILO_ASSET_INGEST_ACCESS_KEY:"
        "SILO_ASSET_INGEST_SECRET_KEY"
    ) in init_script
    for service in ("ASSET_WORKER", "ASSET_BAKER"):
        assert env_vars[f"SILO_{service}_ACCESS_KEY"]["secret"] is True
        assert env_vars[f"SILO_{service}_SECRET_KEY"]["secret"] is True
        assert init_env[f"{service}_SILO_BUCKET"] == f"${{{service}_SILO_BUCKET}}"
        assert init_env[f"SILO_{service}_ACCESS_KEY"] == f"${{SILO_{service}_ACCESS_KEY}}"
        assert (
            f"{service}_SILO_BUCKET:SILO_{service}_ACCESS_KEY:"
            f"SILO_{service}_SECRET_KEY::SILO_BUCKET_ASSET_INPUTS"
        ) in init_script


def test_spark_consumer_uses_scoped_lakehouse_account() -> None:
    manifest = yaml.safe_load(SILO_MANIFEST.read_text(encoding="utf-8"))
    env_vars = {entry["name"]: entry for entry in manifest["env"]}
    compose = yaml.safe_load(SILO_COMPOSE.read_text(encoding="utf-8"))
    init_env = compose["services"]["silo-init"]["environment"]
    init_script = SILO_INIT.read_text(encoding="utf-8")

    assert env_vars["SILO_BUCKET_SPARK_HISTORY"]["default"] == "spark-history"
    assert env_vars["SILO_SPARK_ACCESS_KEY"]["secret"] is True
    assert env_vars["SILO_SPARK_SECRET_KEY"]["secret"] is True
    assert init_env["SILO_SPARK_ACCESS_KEY"] == "${SILO_SPARK_ACCESS_KEY}"
    assert (
        "spark:SILO_BUCKET_SPARK_HISTORY:SILO_SPARK_ACCESS_KEY:"
        "SILO_SPARK_SECRET_KEY:SILO_BUCKET_ICEBERG_LAKEHOUSE,"
        "SILO_BUCKET_ICEBERG_JARS,SILO_BUCKET_ICEBERG_CHECKPOINTS,"
        "SILO_BUCKET_ICEBERG_LANDING"
    ) in init_script


def test_asset_processor_input_policy_is_read_only() -> None:
    script = SILO_INIT.read_text(encoding="utf-8")
    assert 'read_only_statements=' in script
    assert '"Action": ["s3:GetObject"]' in script
    assert '"Action": ["s3:ListBucket", "s3:GetBucketLocation"]' in script  # still read-only
    assert 'writable_buckets="$bucket"' in script


def test_asset_processor_compose_never_falls_back_to_silo_root() -> None:
    for service in ("asset-worker", "asset-baker"):
        compose_text = (REPO_ROOT / "services" / service / "compose.yml").read_text(
            encoding="utf-8"
        )
        token = service.upper().replace("-", "_")
        assert "SILO_ROOT_USER" not in compose_text
        assert "SILO_ROOT_PASSWORD" not in compose_text
        assert f"${{SILO_{token}_ACCESS_KEY}}" in compose_text
        assert f"${{SILO_{token}_SECRET_KEY}}" in compose_text
        assert "service_completed_successfully" in compose_text


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI unavailable")
def test_renamed_asset_input_bucket_reaches_both_processor_allowlists() -> None:
    env = os.environ.copy()
    env.update(
        {
            "SILO_BUCKET_ASSET_INPUTS": "incoming-assets",
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


def test_silo_extra_consumers_are_declared_in_manifest_compose_and_env_example() -> None:
    manifest = yaml.safe_load(SILO_MANIFEST.read_text(encoding="utf-8"))
    env_vars = {entry["name"]: entry for entry in manifest["env"]}

    assert env_vars["SILO_EXTRA_CONSUMERS"]["default"] == ""
    assert "CONSUMER:BUCKET_VAR:ACCESS_VAR:SECRET_VAR" in env_vars[
        "SILO_EXTRA_CONSUMERS"
    ]["description"]

    compose = yaml.safe_load(SILO_COMPOSE.read_text(encoding="utf-8"))
    silo_init_env = compose["services"]["silo-init"]["environment"]
    assert silo_init_env["SILO_EXTRA_CONSUMERS"] == "${SILO_EXTRA_CONSUMERS:-}"

    assert "SILO_EXTRA_CONSUMERS=" in ENV_EXAMPLE.read_text(encoding="utf-8")


# Fake mc: logs every call and captures, at call time, each named policy and
# each service account's inline --policy file (init-silo.sh deletes it next).
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


def _run_silo_init(tmp_path: Path, overrides: dict[str, str]) -> tuple[subprocess.CompletedProcess[str], str]:
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
    result = subprocess.run(["/bin/sh", str(SILO_INIT)], env=env, text=True, capture_output=True, check=False)
    return result, command_log.read_text(encoding="utf-8") if command_log.exists() else ""


_DAYDREAMS_ENV = {
    "SILO_EXTRA_CONSUMERS": (
        "daydreams:SILO_BUCKET_DAYDREAMS:"
        "SILO_DAYDREAMS_ACCESS_KEY:SILO_DAYDREAMS_SECRET_KEY"
    ),
    "SILO_BUCKET_DAYDREAMS": "daydreams-artifacts",
    "SILO_DAYDREAMS_ACCESS_KEY": "daydreams-ak",
    "SILO_DAYDREAMS_SECRET_KEY": "daydreams-sk",
}


def _policy(tmp_path: Path, name: str) -> dict:
    return json.loads((tmp_path / "capture" / name).read_text(encoding="utf-8"))


def test_silo_init_provisions_parent_owned_extra_consumer_bucket(tmp_path: Path) -> None:
    result, log = _run_silo_init(tmp_path, {**_DAYDREAMS_ENV, "ASSET_WORKER_SILO_BUCKET": "custom-worker-output"})

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
    Without it a key inherits its parent's (SILO_ROOT_USER) access."""
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
def test_silo_service_accounts_carry_their_scoped_inline_policy(tmp_path: Path, existing: bool) -> None:
    result, log = _run_silo_init(tmp_path, {**_DAYDREAMS_ENV, "FAKE_SVCACCT_EXISTS": "1" if existing else ""})

    assert result.returncode == 0, result.stderr + result.stdout
    _assert_scoped_inline_grants(tmp_path, log, "edit" if existing else "add")
    if existing:
        assert "admin user svcacct edit local daydreams-ak --secret-key daydreams-sk --policy /tmp/daydreams-policy.json" in log
        assert "svcacct add " not in log
    # /tmp is shared with concurrent test runs, so check the cleanup statically.
    assert 'rm -f "$policy_file"' in SILO_INIT.read_text(encoding="utf-8")


@pytest.mark.parametrize("case", [
    ("daydreams:SILO_BUCKET_DAYDREAMS", {}, "invalid consumer entry"),
    ("daydreams:SILO_BUCKET_DAYDREAMS:SILO_DAYDREAMS_ACCESS_KEY:SILO_DAYDREAMS_SECRET_KEY",
     {"SILO_DAYDREAMS_SECRET_KEY": ""}, "(SILO_BUCKET_DAYDREAMS/SILO_DAYDREAMS_ACCESS_KEY/SILO_DAYDREAMS_SECRET_KEY)"),
    ("daydreams:SILO_BUCKET_DAYDREAMS:SILO_DAYDREAMS_ACCESS_KEY:SILO_DAYDREAMS_SECRET_KEY:SILO_BUCKET_UNSET_RW",
     {}, "(SILO_BUCKET_UNSET_RW)"),
    ("daydreams:SILO_BUCKET_DAYDREAMS:SILO_DAYDREAMS_ACCESS_KEY:SILO_DAYDREAMS_SECRET_KEY::SILO_BUCKET_UNSET_RO",
     {}, "(SILO_BUCKET_UNSET_RO)"),
], ids=["short-entry", "missing-secret", "missing-writable-bucket", "missing-read-only-bucket"])
def test_silo_init_fails_closed_on_an_incomplete_consumer(tmp_path: Path, case) -> None:
    entry, overrides, message = case
    result, log = _run_silo_init(tmp_path, {**_DAYDREAMS_ENV, "SILO_EXTRA_CONSUMERS": entry, **overrides})

    assert result.returncode == 1, result.stdout
    assert message in result.stderr
    assert "--access-key daydreams-ak" not in log
    assert "provisioning complete" not in result.stdout


def test_docs_cover_parent_owned_silo_extra_consumers() -> None:
    canonical_docs = "\n".join(
        [
            SILO_README.read_text(encoding="utf-8"),
            REUSING_ATLAS.read_text(encoding="utf-8"),
            SUBMODULE_USAGE.read_text(encoding="utf-8"),
        ]
    )
    for expected in (
        "SILO_EXTRA_CONSUMERS",
        "daydreams:SILO_BUCKET_DAYDREAMS:SILO_DAYDREAMS_ACCESS_KEY:SILO_DAYDREAMS_SECRET_KEY",
        "services/_user/<name>/compose.yml",
        "parent-owned",
    ):
        assert expected in canonical_docs

    for text in (
        surface_text("docs/development.md", "site"),
        surface_text("docs/development.md", "wiki"),
    ):
        assert "SILO_EXTRA_CONSUMERS" in text
        assert "daydreams:SILO_BUCKET_DAYDREAMS" in text


def test_silo_root_password_is_never_an_mc_argument():
    """`mc alias set local URL USER PASSWORD` put the root password in
    /proc/*/cmdline on every start; the keys go on stdin."""
    import re
    from pathlib import Path

    script = (Path(__file__).resolve().parents[2] / "services/silo/init/scripts/init-silo.sh").read_text(encoding="utf-8")
    mc_lines = [line for line in script.splitlines() if re.search(r"\bmc\b", line) and not line.lstrip().startswith("#")]
    assert not [line for line in mc_lines if "SILO_ROOT_PASSWORD" in line.split("| mc", 1)[-1]]


@pytest.mark.parametrize("existing", [False, True], ids=["create", "refresh"])
def test_silo_init_output_carries_no_service_account_secret(tmp_path: Path, existing: bool) -> None:
    """#1974: mc echoes each account's keys on stdout, and that output became
    the container log. Only account names may reach it."""
    result, log = _run_silo_init(tmp_path, {**_DAYDREAMS_ENV, "FAKE_SVCACCT_EXISTS": "1" if existing else ""})
    output = result.stdout + result.stderr
    secrets = [value for key, value in {**BUILT_IN_CONSUMER_ENV, **_DAYDREAMS_ENV}.items()
               if key.endswith("_SECRET_KEY")]

    assert (
        result.returncode,
        "Secret Key:" in output,
        [secret for secret in secrets if secret in output],
        f"admin user svcacct {'edit' if existing else 'add'} " in log,
    ) == (0, False, [], True)


def test_silo_init_still_reports_a_failed_service_account_write(tmp_path: Path) -> None:
    """#1974: silencing stdout must not hide a failure; mc's stderr and the
    nonzero exit still reach the operator."""
    result, _ = _run_silo_init(tmp_path, {**_DAYDREAMS_ENV, "FAKE_SVCACCT_FAIL": "1"})

    assert (result.returncode != 0, "Unable to add service account" in result.stderr) == (True, True)


def test_silo_init_revokes_only_superseded_atlas_managed_accounts(tmp_path):
    """#1385: an account Atlas created for a consumer whose key is no longer
    managed is revoked. A current Atlas account and an account an operator made
    by hand (no atlas-managed description) survive."""
    accounts = {
        "old-ak": "atlas-managed:backend",
        "daydreams-ak": "atlas-managed:daydreams",
        "handmade-ak": "",
    }
    result, _ = _run_silo_init(tmp_path, {
        **_DAYDREAMS_ENV, "FAKE_ACCOUNTS": " ".join(accounts),
        **{}})
    for access, description in accounts.items():
        (tmp_path / "capture" / f"desc-{access}").write_text(description, encoding="utf-8")
    result, _ = _run_silo_init(tmp_path, {
        **_DAYDREAMS_ENV, "FAKE_ACCOUNTS": " ".join(accounts)})

    removed = (tmp_path / "capture" / "removed").read_text(encoding="utf-8").split()
    assert (result.returncode, removed) == (0, ["old-ak"])


def test_jenkins_gets_an_account_that_can_write_only_the_jars_bucket(tmp_path):
    """#1385: Jenkins publishes JARs, so its key reaches the jars bucket only;
    the Iceberg key stays on the Iceberg consumer."""
    result, log = _run_silo_init(tmp_path, {**_DAYDREAMS_ENV})

    assert result.returncode == 0, result.stderr + result.stdout
    assert "svcacct add local minioadmin --access-key jenkins-ak" in log
    policy = _policy(tmp_path, "named-jenkins-policy.json")
    resources = [r for statement in policy["Statement"] for r in statement["Resource"]]
    assert resources == ["arn:aws:s3:::jars/*", "arn:aws:s3:::jars"]
