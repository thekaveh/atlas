"""Fail CI on unreviewed advisories in every repository runtime graph."""

from __future__ import annotations

import json
import re
import time
import tempfile
from dataclasses import dataclass
from datetime import date
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
# `npm audit` contacts the registry for the whole dependency graph and takes
# just over four minutes for services/asset-worker/app, so the shared 300s
# default sits right on the edge and tips over on slower CI runners.
NPM_AUDIT_TIMEOUT_SECONDS = 900
_NPM_AUDIT_ATTEMPTS = 3


@dataclass(frozen=True)
class AuditSpec:
    lock: str
    reviewed_advisories: frozenset[str] = frozenset()
    reviewed_local_versions: frozenset[str] = frozenset()
    display_name: str | None = None
    review_by: date | None = None


@dataclass(frozen=True)
class SourceSpec:
    requirements: str
    python_platform: str = "x86_64-manylinux_2_28"
    reviewed_advisories: frozenset[str] = frozenset()


AUDIT_SPECS = (
    AuditSpec(
        "services/backend/app/app/requirements-locked.txt",
        frozenset({"PYSEC-2026-2447", "PYSEC-2026-3046"}),
        review_by=date(2026, 9, 15),
    ),
    AuditSpec("services/airflow/build/requirements-locked.txt"),
    AuditSpec("services/mlflow/build/requirements-locked.txt"),
    AuditSpec(
        "services/jupyterhub/build/requirements-locked.txt",
        # cryptography==49.0.0: PYSEC-2026-3552's PKCS#7 EnvelopedData decrypt
        # oracle is fixed in 50.0.0, but installed MLflow 3.15.1 and current
        # 3.15.2 require cryptography<50. Atlas has no PKCS#7 decrypt endpoint.
        # diskcache==5.6.3: PYSEC-2026-2447 has no fixed release; notebook 14
        # passes no cache backend, so Ragas never creates or reads a disk cache.
        # ragas==0.4.3: PYSEC-2026-3046 has no fixed release; notebook 14 imports
        # only four text metrics and never the multimodalfaithfulness helper.
        # mlflow==3.15.1: CVE-2026-71211 has no fixed release; it affects the
        # AI Gateway server's operator-supplied auth_config.api_base. Jupyter's
        # entrypoint starts JupyterHub, and notebook 11 uses only MLflow's
        # tracking client. The separately deployed service blocks every Gateway
        # route family at its outer ASGI boundary in services/mlflow/atlas_server.py.
        # nltk==3.10.3: PYSEC-2026-3740 has no fixed release — the advisory's
        # affected range ends at last_affected 3.10.3, the same version that
        # fixed the earlier PYSEC-2026-3733..3741 set. It needs a
        # caller-controlled model path to reach the model-artifact APIs. Atlas
        # touches nltk only through install_nlp_assets.py, which resolves the
        # VADER lexicon from a build-time constant and is integrity-locked by
        # nlp-assets.toml; notebooks run SentimentIntensityAnalyzer over
        # in-memory text and never supply an artifact location.
        # Atlas maintainers own re-review by 2026-11-27.
        frozenset(
            {
                "PYSEC-2026-3552",
                "PYSEC-2026-2447",
                "PYSEC-2026-3046",
                "CVE-2026-71211",
                "PYSEC-2026-3740",
            }
        ),
        review_by=date(2026, 11, 27),
    ),
    AuditSpec(
        "services/parakeet/provider/gpu/requirements-locked.txt",
        # nemo-toolkit==3.0.0 still caps Lightning at <=2.4.0 and Hydra at
        # <=1.3.2; the lock resolves lightning==2.4.0 (PYSEC-2026-3624) and
        # hydra-core==1.3.2 (CVE-2026-68508), below their fixes. NeMo leaves
        # Transformers unconstrained. Atlas calls
        # only ASRModel.from_pretrained at transcribe.py:37 for the
        # operator-configured PARAKEET_MODEL. NeMo restore routes Hydra through
        # safe_instantiate's recursive target allowlist and bypasses Lightning
        # load_from_checkpoint; request payloads cannot choose or upload a model.
        # Atlas maintainers own re-review by 2026-11-27.
        frozenset(
            {
                "PYSEC-2026-3624",
                "CVE-2026-68508",
            }
        ),
        review_by=date(2026, 11, 27),
    ),
    AuditSpec(
        "services/parakeet/provider/mlx/requirements-locked.txt",
        # mlx==0.29.3 retains PYSEC-2025-138/PYSEC-2025-139 because the fixed
        # 0.29.4 Python 3.12 arm64 wheels require macOS 14/15, while Atlas'
        # aarch64-apple-darwin lock preserves its generic macOS target. Atlas
        # calls only parakeet_mlx.from_pretrained at api_server.py:64 for the
        # operator-configured config.json + model.safetensors; neither that path
        # nor audio transcription calls vulnerable .npy or GGUF loaders.
        frozenset({"PYSEC-2025-138", "PYSEC-2025-139"}),
        review_by=date(2026, 11, 27),
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
        review_by=date(2026, 9, 15),
    ),
    AuditSpec("services/mcp-servers/runtime/requirements-test-locked.txt"),
    AuditSpec("services/asset-worker/app/requirements-test-locked.txt"),
    AuditSpec("services/local-deep-researcher/build/config/runtime-requirements.lock"),
    AuditSpec("services/requirements-init-locked.txt"),
    AuditSpec("services/comfyui/custom-node-locks/instantid.txt"),
    AuditSpec("services/comfyui/custom-node-locks/gguf.txt"),
)

SOURCE_SPECS: tuple[SourceSpec, ...] = ()

UV_PROJECTS = (
    "bootstrapper",
    "services/docling/provider/localhost",
)

# Reviewed advisory exceptions for uv-managed projects, keyed by project path.
# Same contract as AuditSpec: every entry needs a rationale and a review
# deadline inside the 90-day horizon, and an entry that stops matching a live
# finding fails as a stale allowlist entry.
UV_PROJECT_EXCEPTIONS: dict[str, tuple[frozenset[str], date]] = {
    # transformers==5.8.1: CVE-2026-9856 is fixed in 5.10.0, which
    # docling-core[chunking]'s transformers<5.9.0 cap makes unreachable while
    # the docling family stays pinned at 2.102.1 across the gpu, adapter, and
    # localhost providers. The advisory is a path traversal requiring user
    # interaction against a caller-supplied model artifact; this provider never
    # imports transformers, exposes no model-path parameter, and its
    # /v1/document/convert and /internal/lightrag/bundle endpoints accept
    # documents rather than model locations.
    # Atlas maintainers own re-review by 2026-10-15.
    "services/docling/provider/localhost": (
        frozenset({"CVE-2026-9856"}),
        date(2026, 10, 15),
    ),
}

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
        "services/mlflow/build/requirements.txt",
        "services/mlflow/build/requirements-locked.txt",
        "services/local-deep-researcher/build/config/runtime-requirements.lock",
        "services/local-deep-researcher/locks/runtime-pyproject.toml",
        "services/local-deep-researcher/locks/runtime.uv.lock",
        "services/mcp-servers/runtime/requirements.txt",
        "services/mcp-servers/runtime/requirements-locked.txt",
        "services/mcp-servers/runtime/requirements-test.txt",
        "services/mcp-servers/runtime/requirements-test-locked.txt",
        "services/requirements-init-locked.txt",
        "services/comfyui/custom-node-locks/instantid.in",
        "services/comfyui/custom-node-locks/instantid.txt",
        "services/comfyui/custom-node-locks/gguf.in",
        "services/comfyui/custom-node-locks/gguf.txt",
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
        "services/comfyui/custom-node-locks/instantid.in",
        "services/comfyui/custom-node-locks/instantid.txt",
        "services/comfyui/custom-node-locks/gguf.in",
        "services/comfyui/custom-node-locks/gguf.txt",
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


def audit_spec(
    spec: AuditSpec, *, root: Path = ROOT, today: date | None = None
) -> list[str]:
    lock = root / spec.lock
    display_name = spec.display_name or spec.lock
    public_requirements, local_versions = _public_requirements(lock)
    failures: list[str] = []
    if spec.reviewed_advisories:
        review_date = today or date.today()
        if spec.review_by is None:
            failures.append(f"{display_name}: advisory exceptions lack a review deadline")
        elif spec.review_by <= review_date:
            failures.append(
                f"{display_name}: advisory exception review expired on "
                f"{spec.review_by.isoformat()}"
            )
        elif (spec.review_by - review_date).days > 90:
            failures.append(
                f"{display_name}: advisory exception review horizon exceeds 90 days"
            )
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
        reviewed, review_by = UV_PROJECT_EXCEPTIONS.get(
            project, (frozenset(), None)
        )
        return audit_spec(
            AuditSpec(
                str(lock),
                reviewed,
                display_name=f"{project}/uv.lock",
                review_by=review_by,
            ),
            root=Path("/"),
        )


def audit_npm_project(project: str, *, root: Path = ROOT) -> list[str]:
    """Audit one npm project, retrying only transient registry failures.

    `npm audit` resolves the advisory set over the network, and shared CI egress
    draws registry errors that have nothing to do with the lock under audit —
    observed on two different projects across consecutive runs while both audit
    clean locally. Retrying that one case keeps the gate meaningful: a real
    vulnerability count, a malformed response, and a timeout all still fail on
    the first attempt.
    """
    for attempt in range(1, _NPM_AUDIT_ATTEMPTS + 1):
        failures = _audit_npm_project_once(project, root=root)
        if failures != [_npm_registry_failure(project)]:
            return failures
        if attempt < _NPM_AUDIT_ATTEMPTS:
            time.sleep(attempt * 5)
    return [_npm_registry_failure(project)]


def _npm_registry_failure(project: str) -> str:
    return f"{project}: npm audit registry request failed (details redacted)"


def _audit_npm_project_once(project: str, *, root: Path = ROOT) -> list[str]:
    try:
        result = run_bounded(
            ["npm", "audit", "--package-lock-only", "--omit=dev", "--json"],
            cwd=root / project,
            timeout_seconds=NPM_AUDIT_TIMEOUT_SECONDS,
        )
    except CommandTimedOut:
        return [
            f"{project}: npm audit timed out after "
            f"{NPM_AUDIT_TIMEOUT_SECONDS} seconds"
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
        return [_npm_registry_failure(project)]
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
