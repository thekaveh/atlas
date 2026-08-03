"""Fail CI on unreviewed advisories in every repository runtime graph."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_LOCAL_VERSION_RE = re.compile(r"^[A-Za-z0-9_.-]+==[^\s]+\+[^\s]+$")


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
        frozenset({"PYSEC-2026-2447", "PYSEC-2026-3046"}),
        frozenset({"pyg-lib==0.8.0+pt213cpu"}),
    ),
    AuditSpec(
        "services/parakeet/provider/gpu/requirements-locked.txt",
        frozenset(
            {
                "PYSEC-2025-217",
                "PYSEC-2026-2288",
                "PYSEC-2026-2289",
                "PYSEC-2026-2290",
            }
        ),
    ),
    AuditSpec("services/asset-baker/app/requirements-locked.txt"),
    AuditSpec("services/asset-worker/app/requirements-locked.txt"),
    AuditSpec("services/docling/provider/gpu/requirements-locked.txt"),
    AuditSpec("services/docling/provider/adapter/requirements-locked.txt"),
    AuditSpec("services/mcp-servers/runtime/requirements-locked.txt"),
    AuditSpec("services/local-deep-researcher/build/config/runtime-requirements.lock"),
)

SOURCE_SPECS = (
    SourceSpec(
        "services/parakeet/provider/mlx/requirements.txt",
        python_platform="aarch64-apple-darwin",
        # mlx 0.29.4 is the published fix but has no compatible wheel in the
        # resolved Apple Silicon graph yet; fail closed when these IDs clear or
        # when any different advisory appears.
        reviewed_advisories=frozenset({"PYSEC-2025-138", "PYSEC-2025-139"}),
    ),
)

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
        "bootstrapper/uv.lock",
        "services/airflow/build/requirements.txt",
        "services/airflow/build/requirements-locked.txt",
        "services/asset-baker/app/requirements.txt",
        "services/asset-baker/app/requirements-locked.txt",
        "services/asset-worker/app/package-lock.json",
        "services/asset-worker/app/package.json",
        "services/asset-worker/app/requirements.txt",
        "services/asset-worker/app/requirements-locked.txt",
        "services/backend/app/app/requirements.txt",
        "services/backend/app/app/requirements-locked.txt",
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
        "services/n8n/init/config/package-lock.json",
        "services/n8n/init/config/package.json",
        "services/parakeet/provider/gpu/requirements.txt",
        "services/parakeet/provider/gpu/requirements-locked.txt",
        "services/parakeet/provider/mlx/requirements.txt",
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
                if path.name == "requirements-dev.txt":
                    continue
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
        result = subprocess.run(
            [
                "pip-audit", "-r", str(audit_input), "--no-deps", "--disable-pip",
                "--strict", "--format", "json",
            ],
            cwd=root, capture_output=True, text=True, check=False,
        )
    if result.returncode not in {0, 1}:
        return [f"{display_name}: pip-audit failed: {result.stderr.strip()}"]
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
        result = subprocess.run(
            [
                "uv", "pip", "compile", str(root / spec.requirements),
                "--python-version", "3.12", "--python-platform", spec.python_platform,
                "--no-header", "--output-file", str(lock),
            ],
            cwd=root, capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return [f"{spec.requirements}: uv compile failed: {result.stderr.strip()}"]
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
        result = subprocess.run(
            [
                "uv", "export", "--project", str(root / project), "--locked",
                "--no-hashes", "--no-emit-project", "--output-file", str(lock),
            ],
            cwd=root, capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return [f"{project}: uv export failed: {result.stderr.strip()}"]
        return audit_spec(
            AuditSpec(str(lock), display_name=f"{project}/uv.lock"),
            root=Path("/"),
        )


def audit_npm_project(project: str, *, root: Path = ROOT) -> list[str]:
    result = subprocess.run(
        ["npm", "audit", "--package-lock-only", "--omit=dev", "--json"],
        cwd=root / project, capture_output=True, text=True, check=False,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return [f"{project}: invalid npm audit JSON: {exc}"]
    if result.returncode not in {0, 1}:
        return [f"{project}: npm audit failed: {result.stderr.strip()}"]
    if payload.get("error"):
        message = payload.get("message") or payload["error"]
        return [f"{project}: npm audit failed: {message}"]
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
