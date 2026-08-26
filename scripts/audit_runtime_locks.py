"""Fail CI on unreviewed advisories in every repository runtime graph."""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from scripts.bounded_subprocess import (
    CommandLaunchError,
    CommandOutputTooLarge,
    CommandTimedOut,
    DEFAULT_TIMEOUT_SECONDS,
    redacted_failure,
    run_bounded,
)


ROOT = Path(__file__).resolve().parents[1]
_LOCAL_VERSION_RE = re.compile(r"^[A-Za-z0-9_.-]+==[^\s]+\+[^\s]+$")
COMMAND_TIMEOUT_SECONDS = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class AuditSpec:
    lock: str
    reviewed_advisories: frozenset[str] = frozenset()
    reviewed_local_versions: frozenset[str] = frozenset()
    display_name: str | None = None


@dataclass(frozen=True)
class SourceSpec:
    requirements: str
    python_platform: str = "x86_64-manylinux_2_28"
    reviewed_advisories: frozenset[str] = frozenset()


AUDIT_SPECS = (
    AuditSpec(
        "services/backend/app/app/requirements-locked.txt",
        frozenset({"PYSEC-2026-2447", "PYSEC-2026-3046"}),
    ),
    AuditSpec("services/airflow/build/requirements-locked.txt"),
    AuditSpec(
        "services/jupyterhub/build/requirements-locked.txt",
        # cryptography 50 fixes PYSEC-2026-3552 (the PKCS#7 EnvelopedData
        # Bleichenbacher oracle; previously indexed as CVE-2026-69247, since
        # withdrawn from the advisory DB), but current MLflow caps it below 50.
        # Atlas exposes no PKCS#7 decrypt endpoint; retain this exact fail-closed
        # exception only until MLflow relaxes the upstream cap.
        # Atlas maintainers own re-review by 2026-09-01.
        frozenset(
            {"PYSEC-2026-3552", "PYSEC-2026-2447", "PYSEC-2026-3046"}
        ),
        frozenset({"pyg-lib==0.8.0+pt213cpu"}),
    ),
    AuditSpec(
        "services/parakeet/provider/gpu/requirements-locked.txt",
        # PYSEC-2026-3624 (CVE-2026-58659) is RCE in PyTorch Lightning's
        # _load_state, reached only through LightningModule.load_from_checkpoint
        # on an attacker-supplied checkpoint. No released 2.x carries the fix:
        # upstream fixed it in commit d710d68, and OSV's range reports "fixed in
        # 2022.6.15" — a CalVer artifact of the pre-1.x line, so the range is
        # unusable as an upgrade target. Atlas never calls load_from_checkpoint;
        # lightning is transitive via nemo-toolkit, and the provider loads
        # through nemo_asr.models.ASRModel.from_pretrained (transcribe.py:37) on
        # the operator-pinned PARAKEET_MODEL repo, not on user-supplied files.
        # Drop this exception once a lightning 2.x release ships d710d68.
        #
        # CVE-2026-68508 is RCE in hydra-core, via hydra.utils.instantiate()
        # resolving a `_target_` import path out of a config the caller does not
        # control. hydra-core 1.3.4 ships the fix, but nemo-toolkit caps
        # hydra-core<=1.3.2 in every extra this lock pulls -- including the
        # newest 3.0.0 -- so `--upgrade-package hydra-core` resolves to a
        # zero-line diff and there is no reachable upgrade target. Atlas never
        # imports hydra: the provider's only NeMo entry point is
        # nemo_asr.models.ASRModel.from_pretrained (transcribe.py:37) on the
        # operator-pinned PARAKEET_MODEL repo, and request payloads cannot
        # select that model. Drop this exception once nemo-toolkit relaxes the
        # upstream cap to admit 1.3.4.
        # Atlas maintainers own re-review by 2026-09-01.
        frozenset(
            {
                "PYSEC-2025-217",
                "PYSEC-2026-2288",
                "PYSEC-2026-2289",
                "PYSEC-2026-2290",
                "PYSEC-2026-3624",
                "CVE-2026-68508",
            }
        ),
    ),
    AuditSpec(
        "services/parakeet/provider/mlx/requirements-locked.txt",
        # PYSEC-2025-138 and PYSEC-2025-139 are memory-safety findings in
        # mlx<0.29.4. The Apple Silicon lock resolves mlx 0.29.3 because no
        # 0.29.4 wheel is published for this exact Python/platform graph yet;
        # the provider accepts audio only through the bounded parser and does
        # not expose arbitrary MLX program/model loading to request callers.
        # Atlas maintainers own re-review by 2026-09-15; remove these IDs as
        # soon as the fixed wheel resolves for aarch64-apple-darwin.
        frozenset({"PYSEC-2025-138", "PYSEC-2025-139"}),
    ),
    AuditSpec("bootstrapper/requirements-locked.txt"),
    AuditSpec("services/asset-baker/app/requirements-locked.txt"),
    AuditSpec("services/asset-worker/app/requirements-locked.txt"),
    AuditSpec("services/docling/provider/gpu/requirements-locked.txt"),
    AuditSpec("services/docling/provider/adapter/requirements-locked.txt"),
    AuditSpec("services/mcp-servers/runtime/requirements-locked.txt"),
    AuditSpec(
        "services/backend/app/app/requirements-test-locked.txt",
        frozenset({"PYSEC-2026-2447", "PYSEC-2026-3046"}),
    ),
    AuditSpec("services/mcp-servers/runtime/requirements-test-locked.txt"),
    AuditSpec("services/asset-worker/app/requirements-test-locked.txt"),
    AuditSpec("services/local-deep-researcher/build/config/runtime-requirements.lock"),
)

SOURCE_SPECS: tuple[SourceSpec, ...] = ()

UV_PROJECTS = (
    "bootstrapper",
    "services/docling/provider/localhost",
)

NPM_PROJECTS = (
    "services/asset-worker/app",
    "services/n8n/init/config",
)

# Every runtime dependency manifest must be represented here, either directly
# or by the exact compiled lock that constrains its installation. The inventory
# test makes adding an unaudited dependency graph a CI failure.
AUDITED_RUNTIME_MANIFESTS = frozenset(
    {
        "bootstrapper/pyproject.toml",
        "bootstrapper/requirements-locked.txt",
        "bootstrapper/uv.lock",
        "services/airflow/build/requirements.txt",
        "services/airflow/build/requirements-locked.txt",
        "services/asset-baker/app/requirements.txt",
        "services/asset-baker/app/requirements-locked.txt",
        "services/asset-worker/app/requirements-test.txt",
        "services/asset-worker/app/requirements-test-locked.txt",
        "services/asset-worker/app/package-lock.json",
        "services/asset-worker/app/package.json",
        "services/asset-worker/app/requirements.txt",
        "services/asset-worker/app/requirements-locked.txt",
        "services/backend/app/app/requirements.txt",
        "services/backend/app/app/requirements-dev.txt",
        "services/backend/app/app/requirements-locked.txt",
        "services/backend/app/app/requirements-test.txt",
        "services/backend/app/app/requirements-test-locked.txt",
        "services/docling/provider/gpu/requirements.txt",
        "services/docling/provider/gpu/requirements-locked.txt",
        "services/docling/provider/adapter/requirements.txt",
        "services/docling/provider/adapter/requirements-locked.txt",
        "services/docling/provider/localhost/pyproject.toml",
        "services/docling/provider/localhost/uv.lock",
        "services/jupyterhub/build/requirements.txt",
        "services/jupyterhub/build/requirements-locked.txt",
        "services/local-deep-researcher/build/config/runtime-requirements.lock",
        "services/local-deep-researcher/locks/runtime-pyproject.toml",
        "services/local-deep-researcher/locks/runtime.uv.lock",
        "services/mcp-servers/runtime/requirements.txt",
        "services/mcp-servers/runtime/requirements-locked.txt",
        "services/mcp-servers/runtime/requirements-test.txt",
        "services/mcp-servers/runtime/requirements-test-locked.txt",
        "services/n8n/init/config/package-lock.json",
        "services/n8n/init/config/package.json",
        "services/parakeet/provider/gpu/requirements.txt",
        "services/parakeet/provider/gpu/requirements-locked.txt",
        "services/parakeet/provider/mlx/requirements.txt",
        "services/parakeet/provider/mlx/requirements-locked.txt",
    }
)


def discover_runtime_manifests(root: Path = ROOT) -> frozenset[str]:
    found: set[str] = set()
    for base in (root / "bootstrapper", root / "services"):
        for path in base.rglob("*"):
            relative = path.relative_to(root)
            if (
                not path.is_file()
                or any(part.startswith(".") for part in relative.parts)
                or "site-packages" in relative.parts
            ):
                continue
            if (
                path.name in {"pyproject.toml", "uv.lock", "package.json", "package-lock.json"}
                or (path.name.startswith("requirements") and path.suffix == ".txt")
            ):
                found.add(relative.as_posix())
    nonstandard = {
        "services/local-deep-researcher/build/config/runtime-requirements.lock",
        "services/local-deep-researcher/locks/runtime-pyproject.toml",
        "services/local-deep-researcher/locks/runtime.uv.lock",
    }
    found.update(path for path in nonstandard if (root / path).is_file())
    return frozenset(found)


def _public_requirements(lock: Path) -> tuple[str, frozenset[str]]:
    """Omit local-version wheels that public vulnerability APIs cannot query."""
    public: list[str] = []
    local_versions: set[str] = set()
    for line in lock.read_text(encoding="utf-8").splitlines(keepends=True):
        requirement = line.strip()
        if _LOCAL_VERSION_RE.fullmatch(requirement):
            local_versions.add(requirement)
        else:
            public.append(line)
    return "".join(public), frozenset(local_versions)


def audit_spec(spec: AuditSpec, *, root: Path = ROOT) -> list[str]:
    lock = root / spec.lock
    display_name = spec.display_name or spec.lock
    public_requirements, local_versions = _public_requirements(lock)
    failures: list[str] = []
    unexpected_local = sorted(local_versions - spec.reviewed_local_versions)
    if unexpected_local:
        failures.append(
            f"{display_name}: unreviewed local-version exclusions: "
            f"{', '.join(unexpected_local)}"
        )
    stale_local = sorted(spec.reviewed_local_versions - local_versions)
    if stale_local:
        failures.append(
            f"{display_name}: stale local-version exclusions: {', '.join(stale_local)}"
        )
    with tempfile.TemporaryDirectory(prefix="atlas-pip-audit-") as raw_temp:
        audit_input = Path(raw_temp) / "requirements.txt"
        audit_input.write_text(public_requirements, encoding="utf-8")
        try:
            result = run_bounded(
                [
                    "pip-audit", "-r", str(audit_input), "--no-deps", "--disable-pip",
                    "--strict", "--format", "json",
                ],
                cwd=root,
                timeout_seconds=COMMAND_TIMEOUT_SECONDS,
            )
        except CommandTimedOut:
            return [
                f"{display_name}: pip-audit timed out after "
                f"{COMMAND_TIMEOUT_SECONDS} seconds"
            ]
        except (CommandLaunchError, CommandOutputTooLarge):
            return [
                f"{display_name}: pip-audit could not complete "
                "(subprocess details redacted)"
            ]
    if result.returncode not in {0, 1}:
        return [redacted_failure(f"{display_name}: pip-audit", result.returncode)]
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return [f"{display_name}: invalid pip-audit JSON: {exc}"]
    found = {
        str(vulnerability["id"])
        for dependency in payload.get("dependencies", [])
        for vulnerability in dependency.get("vulns", [])
    }
    unexpected = sorted(found - spec.reviewed_advisories)
    if unexpected:
        failures.append(f"{display_name}: unreviewed advisories: {', '.join(unexpected)}")
    stale = sorted(spec.reviewed_advisories - found)
    if stale:
        failures.append(f"{display_name}: stale allowlist entries: {', '.join(stale)}")
    if not failures:
        suffix = f" ({len(found)} reviewed exception(s))" if found else ""
        print(f"PASS {display_name}{suffix}")
    return failures


def audit_source_spec(spec: SourceSpec, *, root: Path = ROOT) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="atlas-runtime-compile-") as raw_temp:
        lock = Path(raw_temp) / "requirements-locked.txt"
        try:
            result = run_bounded(
                [
                    "uv", "pip", "compile", str(root / spec.requirements),
                    "--python-version", "3.12", "--python-platform", spec.python_platform,
                    "--no-header", "--output-file", str(lock),
                ],
                cwd=root,
                timeout_seconds=COMMAND_TIMEOUT_SECONDS,
            )
        except CommandTimedOut:
            return [
                f"{spec.requirements}: uv compile timed out after "
                f"{COMMAND_TIMEOUT_SECONDS} seconds"
            ]
        except (CommandLaunchError, CommandOutputTooLarge):
            return [
                f"{spec.requirements}: uv compile could not complete "
                "(subprocess details redacted)"
            ]
        if result.returncode != 0:
            return [
                redacted_failure(
                    f"{spec.requirements}: uv compile", result.returncode
                )
            ]
        return audit_spec(
            AuditSpec(
                str(lock), spec.reviewed_advisories,
                display_name=spec.requirements,
            ),
            root=Path("/"),
        )


def audit_uv_project(project: str, *, root: Path = ROOT) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="atlas-uv-export-") as raw_temp:
        lock = Path(raw_temp) / "requirements-locked.txt"
        try:
            result = run_bounded(
                [
                    "uv", "export", "--project", str(root / project), "--locked",
                    "--no-hashes", "--no-emit-project", "--output-file", str(lock),
                ],
                cwd=root,
                timeout_seconds=COMMAND_TIMEOUT_SECONDS,
            )
        except CommandTimedOut:
            return [
                f"{project}: uv export timed out after "
                f"{COMMAND_TIMEOUT_SECONDS} seconds"
            ]
        except (CommandLaunchError, CommandOutputTooLarge):
            return [
                f"{project}: uv export could not complete "
                "(subprocess details redacted)"
            ]
        if result.returncode != 0:
            return [redacted_failure(f"{project}: uv export", result.returncode)]
        return audit_spec(
            AuditSpec(str(lock), display_name=f"{project}/uv.lock"),
            root=Path("/"),
        )


def audit_npm_project(project: str, *, root: Path = ROOT) -> list[str]:
    try:
        result = run_bounded(
            ["npm", "audit", "--package-lock-only", "--omit=dev", "--json"],
            cwd=root / project,
            timeout_seconds=COMMAND_TIMEOUT_SECONDS,
        )
    except CommandTimedOut:
        return [
            f"{project}: npm audit timed out after "
            f"{COMMAND_TIMEOUT_SECONDS} seconds"
        ]
    except (CommandLaunchError, CommandOutputTooLarge):
        return [
            f"{project}: npm audit could not complete "
            "(subprocess details redacted)"
        ]
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return [f"{project}: invalid npm audit JSON: {exc}"]
    if result.returncode not in {0, 1}:
        return [redacted_failure(f"{project}: npm audit", result.returncode)]
    if payload.get("error"):
        return [f"{project}: npm audit registry request failed (details redacted)"]
    metadata = payload.get("metadata")
    vulnerabilities = metadata.get("vulnerabilities") if isinstance(metadata, dict) else None
    if not isinstance(vulnerabilities, dict) or not isinstance(
        vulnerabilities.get("total"), int
    ):
        return [f"{project}: npm audit response omitted vulnerability totals"]
    total = vulnerabilities["total"]
    if result.returncode == 1 and total == 0:
        return [f"{project}: npm audit exited 1 without reported vulnerabilities"]
    if total:
        return [f"{project}: npm audit found {total} vulnerability(s)"]
    print(f"PASS {project}/package-lock.json")
    return []


def main() -> int:
    failures: list[str] = []
    missing = sorted(discover_runtime_manifests() - AUDITED_RUNTIME_MANIFESTS)
    stale = sorted(AUDITED_RUNTIME_MANIFESTS - discover_runtime_manifests())
    if missing:
        failures.append(f"unaudited runtime manifests: {', '.join(missing)}")
    if stale:
        failures.append(f"stale runtime manifest inventory: {', '.join(stale)}")
    failures.extend(f for spec in AUDIT_SPECS for f in audit_spec(spec))
    failures.extend(f for spec in SOURCE_SPECS for f in audit_source_spec(spec))
    failures.extend(f for project in UV_PROJECTS for f in audit_uv_project(project))
    failures.extend(f for project in NPM_PROJECTS for f in audit_npm_project(project))
    if failures:
        print("\n".join(f"FAIL {failure}" for failure in failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
