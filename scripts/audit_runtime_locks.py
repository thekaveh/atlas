"""Fail CI on unreviewed advisories in every repository runtime graph."""

from __future__ import annotations

import json
import re
import time
import tempfile
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

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
_PIP_AUDIT_ATTEMPTS = 3
_NPM_AUDIT_ATTEMPTS = 3


@dataclass(frozen=True)
class AuditSpec:
    lock: str
    reviewed_advisories: frozenset[str] = frozenset()
    reviewed_local_versions: frozenset[str] = frozenset()
    display_name: str | None = None
    review_by: date | None = None
    marker_environments: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class SourceSpec:
    requirements: str
    python_platform: str = "x86_64-manylinux_2_28"
    reviewed_advisories: frozenset[str] = frozenset()


def _cpython_marker_environment(
    system: str, machine: str, python_full: str
) -> dict[str, str]:
    """Return the PEP 508 marker environment of one CPython target."""
    return {
        "implementation_name": "cpython", "implementation_version": python_full,
        "os_name": "posix", "platform_machine": machine,
        "platform_release": "", "platform_system": system, "platform_version": "",
        "python_full_version": python_full, "platform_python_implementation": "CPython",
        "python_version": ".".join(python_full.split(".")[:2]),
        "sys_platform": system.lower(),
    }


# Project the shipped LDR container, independently of pip-audit's interpreter.
_LDR_LINUX311_MARKER_ENVIRONMENTS = tuple(
    _cpython_marker_environment("Linux", machine, "3.11.15")
    for machine in ("x86_64", "aarch64")
)


# PYSEC-2026-2447 (diskcache) and PYSEC-2026-3046 (ragas) were re-verified on
# 2026-09-10 for the #998 review pass. Neither has a fixed release, and both
# packages are already at their newest published version -- diskcache 5.6.3 and
# ragas 0.4.3 -- so there is nothing to upgrade to. The reachability mitigations
# recorded beside the ragas pin in requirements.txt still hold: rag_eval_service
# imports only the four text collection metrics (AnswerRelevancy,
# ContextPrecision, ContextRecall, Faithfulness) and references no multimodal,
# image, or URL helper, which is where the SSRF lives.
AUDIT_SPECS = (
    AuditSpec(
        "services/backend/app/app/requirements-locked.txt",
        frozenset({"PYSEC-2026-2447", "PYSEC-2026-3046"}),
        review_by=date(2026, 12, 9),
    ),
    AuditSpec("services/airflow/build/requirements-locked.txt"),
    AuditSpec("services/mlflow/build/requirements-locked.txt"),
    AuditSpec(
        "services/jupyterhub/build/requirements-locked.txt",
        # PYSEC-2026-3552 (cryptography) and PYSEC-2026-3865 (mlflow) were
        # removed here once MLflow 3.16.0 widened its cryptography cap from <50
        # to <51: cryptography moves to 50.0.1, taking the PKCS#7 fix, and
        # mlflow 3.16.0 falls outside the AI Gateway advisory's affected range.
        # Both are now real fixes rather than unreachability arguments.
        # diskcache==5.6.3: PYSEC-2026-2447 has no fixed release; notebook 14
        # passes no cache backend, so Ragas never creates or reads a disk cache.
        # ragas==0.4.3: PYSEC-2026-3046 has no fixed release; notebook 14 imports
        # only four text metrics and never the multimodalfaithfulness helper.
        # NLTK 3.10.4 routes the six model-artifact APIs through pathsec;
        # PYSEC-2026-3740 is fixed rather than retained as an exception.
        # Atlas maintainers own re-review by 2026-11-27.
        frozenset(
            {
                "PYSEC-2026-2447",
                "PYSEC-2026-3046",
            }
        ),
        review_by=date(2026, 11, 27),
    ),
    AuditSpec(
        "services/parakeet/provider/gpu/requirements-locked.txt",
        # nemo-toolkit==3.0.0 still caps Lightning at <=2.4.0 and Hydra at
        # <=1.3.2; the lock resolves lightning==2.4.0 (PYSEC-2026-3624,
        # PYSEC-2026-3972) and hydra-core==1.3.2 (PYSEC-2026-3850, formerly
        # CVE-2026-68508), below their fixes. Re-verified 2026-09-10: hydra
        # 1.3.4 and lightning fixes are published but remain out of reach
        # behind those caps. pytorch-lightning is NOT capped by NeMo, so
        # PYSEC-2026-3967 was fixed by flooring it at 2.6.6 rather than
        # excepted. NeMo leaves
        # Transformers unconstrained. Atlas calls
        # only ASRModel.from_pretrained at transcribe.py:37 for the
        # operator-configured PARAKEET_MODEL. NeMo restore routes Hydra through
        # safe_instantiate's recursive target allowlist and bypasses Lightning
        # load_from_checkpoint; request payloads cannot choose or upload a model.
        # PYSEC-2026-4217 (fsspec 2024.12.0, fixed in 2026.6.0) and
        # PYSEC-2026-4221 (hydra-core 1.3.2, fixed in 1.3.6) sit behind the same
        # caps: Lightning 2.4.0 requires fsspec[http]<2026.0, and NeMo admits only
        # hydra-core>1.3,<=1.3.2. Their reachability is NOT proven; the owner
        # approved a time-boxed exception on 2026-10-29 instead of a red gate.
        # Atlas maintainers own re-review by 2026-11-27.
        frozenset(
            {
                "PYSEC-2026-3624",
                "PYSEC-2026-3850",
                "PYSEC-2026-3972",
                "PYSEC-2026-4217",
                "PYSEC-2026-4221",
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
        review_by=date(2026, 12, 9),
    ),
    AuditSpec("services/mcp-servers/runtime/requirements-test-locked.txt"),
    AuditSpec("services/asset-worker/app/requirements-test-locked.txt"),
    AuditSpec(
        "services/local-deep-researcher/build/config/runtime-requirements.lock",
        marker_environments=_LDR_LINUX311_MARKER_ENVIRONMENTS,
    ),
    AuditSpec("services/requirements-init-locked.txt"),
    AuditSpec("services/comfyui/custom-node-locks/instantid.txt"),
    AuditSpec("services/comfyui/custom-node-locks/gguf.txt"),
)

SOURCE_SPECS: tuple[SourceSpec, ...] = ()

UV_PROJECTS = (
    "bootstrapper",
    "services/docling/provider/localhost",
)

# uv exports are universal, but pip-audit drops every row whose marker is false
# on the CI runner. A project listed here is projected onto each named target
# and audited once per target, so a platform-only branch such as the Docling
# localhost provider's macOS SciPy is audited too. Each target applies the
# project's reviewed exceptions on its own, so an exception that matches no live
# finding on one target fails there as stale. A platform-only advisory therefore
# needs target-scoped exceptions before it can be reviewed here.
_DOCLING_LOCALHOST_MARKER_ENVIRONMENTS = {
    f"{system.lower()}-{machine}": _cpython_marker_environment(system, machine, "3.11.15")
    for system, machine in (("Linux", "x86_64"), ("Linux", "aarch64"), ("Darwin", "arm64"))
}
UV_PROJECT_MARKER_ENVIRONMENTS: dict[str, dict[str, dict[str, str]]] = {
    "services/docling/provider/localhost": _DOCLING_LOCALHOST_MARKER_ENVIRONMENTS,
}

# Reviewed advisory exceptions for uv-managed projects, keyed by project path.
# Same contract as AuditSpec: every entry needs a rationale and a review
# deadline inside the 90-day horizon, and an entry that stops matching a live
# finding fails as a stale allowlist entry.
UV_PROJECT_EXCEPTIONS: dict[str, tuple[frozenset[str], date]] = {
    # The Docling localhost provider's transformers and accelerate entries
    # (PYSEC-2026-3929, PYSEC-2026-3804) were retired on 2026-10-01: its
    # pyproject now floors transformers>=5.10.0 and accelerate>=1.15.0, which
    # also clears CVE-2026-80047. No uv-managed project carries a reviewed
    # exception today.
}

NPM_PROJECTS = (
    "services/asset-worker/app",
    "services/n8n/init/config",
)

# Reviewed npm advisory exceptions, keyed by project path, with the same
# contract as the pip ones: a review deadline inside the 90-day horizon, and an
# entry that stops matching a live finding fails as stale. npm audit reports
# each advisory as a GitHub advisory URL, so entries are GHSA ids.
NPM_PROJECT_EXCEPTIONS: dict[str, tuple[frozenset[str], date]] = {
    # zod 3.25.76 (GHSA-mrhx-c7xc-m3w5, fixed in 4.6.6): n8n-workflow 2.45.1 and
    # @n8n/expression-runtime 0.36.1 pin it exactly, and 0.36.2 (newest on
    # 2026-10-29) still does. Reachability through the community nodes is NOT
    # proven; the owner approved a time-boxed exception on 2026-10-29.
    "services/n8n/init/config": (frozenset({"GHSA-mrhx-c7xc-m3w5"}), date(2026, 11, 28)),
}

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


def _review_deadline_failures(
    display_name: str,
    reviewed: frozenset[str],
    review_by: date | None,
    today: date | None = None,
) -> list[str]:
    if not reviewed:
        return []
    review_date = today or date.today()
    if review_by is None:
        return [f"{display_name}: advisory exceptions lack a review deadline"]
    if review_by <= review_date:
        return [
            f"{display_name}: advisory exception review expired on "
            f"{review_by.isoformat()}"
        ]
    if (review_by - review_date).days > 90:
        return [f"{display_name}: advisory exception review horizon exceeds 90 days"]
    return []


def _requirement_blocks(text: str):
    """Read complete exported requirement/hash blocks without changing the lock."""
    parts: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        continued = line.endswith("\\")
        parts.append(line[:-1].rstrip() if continued else line)
        if not continued:
            yield " ".join(parts)
            parts = []
    if parts:
        raise ValueError("unterminated requirement block")


def _pinned_requirement(block: str) -> tuple[Requirement, str]:
    parts = re.split(r"\s+--hash=", block)
    if any(not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", item) for item in parts[1:]):
        raise ValueError("invalid requirement hash")
    requirement = Requirement(parts[0])
    bounds = list(requirement.specifier)
    if requirement.url or len(bounds) != 1 or bounds[0].operator != "==":
        raise ValueError("requirement is not an exact pin")
    # These contexts have no kernel, extras or dependency-group selection.
    if requirement.marker and re.search(
        r"\b(platform_release|platform_version|extra|extras|dependency_groups)\b",
        str(requirement.marker),
    ):
        raise ValueError("unsupported marker context")
    return requirement, str(Version(bounds[0].version))


def _project_requirements(text: str, environments: tuple[dict[str, str], ...]):
    selected: dict[str, str] = {}
    for block in _requirement_blocks(text):
        requirement, version = _pinned_requirement(block)
        if requirement.marker and not any(
            requirement.marker.evaluate(environment) for environment in environments
        ):
            continue
        name = canonicalize_name(requirement.name)
        if name in selected and selected[name] != version:
            raise ValueError("conflicting selected requirement versions")
        selected[name] = version
    return "".join(f"{name}=={version}\n" for name, version in selected.items()), frozenset(selected.items())


def _reported_pairs(payload: dict) -> frozenset[tuple[str, str]]:
    dependencies = payload["dependencies"]
    if not isinstance(dependencies, list):
        raise ValueError("dependencies are not a list")
    pairs: set[tuple[str, str]] = set()
    for dependency in dependencies:
        pair = _reported_pair(dependency)
        if pair in pairs:
            raise ValueError("duplicate reported dependency")
        pairs.add(pair)
    return frozenset(pairs)


def _reported_pair(dependency: dict) -> tuple[str, str]:
    if not isinstance(dependency, dict) or "skip_reason" in dependency:
        raise ValueError("dependency was not collected")
    name, version = dependency["name"], dependency["version"]
    if not isinstance(name, str) or not isinstance(version, str):
        raise ValueError("invalid dependency identity")
    if not isinstance(dependency.get("vulns"), list):
        raise ValueError("missing vulnerability results")
    return canonicalize_name(name, validate=True), str(Version(version))


def _coverage_failures(display_name: str, expected: frozenset, payload: dict) -> list[str]:
    try:
        reported = _reported_pairs(payload)
    except (KeyError, TypeError, ValueError):
        return [f"{display_name}: invalid pip-audit dependency coverage (details redacted)"]
    if reported != expected:
        return [
            f"{display_name}: incomplete pip-audit dependency coverage: "
            f"{len(expected - reported)} missing, {len(reported - expected)} unexpected"
        ]
    return []


def audit_spec(
    spec: AuditSpec, *, root: Path = ROOT, today: date | None = None
) -> list[str]:
    lock = root / spec.lock
    display_name = spec.display_name or spec.lock
    public_requirements, local_versions = _public_requirements(lock)
    failures = _review_deadline_failures(
        display_name, spec.reviewed_advisories, spec.review_by, today
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
    expected = None
    if spec.marker_environments:
        try:
            public_requirements, expected = _project_requirements(
                public_requirements, spec.marker_environments
            )
        except ValueError:
            return failures + [f"{display_name}: invalid marker projection (details redacted)"]
    payload, audit_failures = _pip_audit_report(
        display_name, public_requirements, root=root
    )
    if payload is None:
        return failures + audit_failures
    if expected is not None:
        coverage_failures = _coverage_failures(display_name, expected, payload)
        if coverage_failures:
            return failures + coverage_failures
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


def _pip_audit_report(
    display_name: str,
    public_requirements: str,
    *,
    root: Path = ROOT,
) -> tuple[dict | None, list[str]]:
    """Run pip-audit for one lock, retrying only a silent transient failure.

    pip-audit resolves advisories over the network, and a PyPI or OSV hiccup on
    shared CI egress surfaces as an ordinary exit code with *empty* stdout --
    reported downstream as unparseable JSON even though the lock is fine.
    Retrying only that case keeps the gate meaningful: a real vulnerability set,
    a non-empty malformed payload, a bad exit code, and a timeout each still
    fail on the first attempt.
    """
    for attempt in range(1, _PIP_AUDIT_ATTEMPTS + 1):
        payload, failures, empty = _pip_audit_report_once(
            display_name, public_requirements, root=root
        )
        if not empty:
            return payload, failures
        if attempt < _PIP_AUDIT_ATTEMPTS:
            time.sleep(attempt * 5)
    return None, [_pip_audit_empty_failure(display_name)]


def _pip_audit_empty_failure(display_name: str) -> str:
    return f"{display_name}: pip-audit returned no report (details redacted)"


def _pip_audit_report_once(
    display_name: str,
    public_requirements: str,
    *,
    root: Path = ROOT,
) -> tuple[dict | None, list[str], bool]:
    """Return ``(payload, failures, empty)`` for a single pip-audit attempt."""
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
            return None, [
                f"{display_name}: pip-audit timed out after "
                f"{COMMAND_TIMEOUT_SECONDS} seconds"
            ], False
        except (CommandLaunchError, CommandOutputTooLarge):
            return None, [
                f"{display_name}: pip-audit could not complete "
                "(subprocess details redacted)"
            ], False
    if result.returncode not in {0, 1}:
        return None, [
            redacted_failure(f"{display_name}: pip-audit", result.returncode)
        ], False
    if not result.stdout.strip():
        return None, [_pip_audit_empty_failure(display_name)], True
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return None, [f"{display_name}: invalid pip-audit JSON: {exc}"], False
    if not isinstance(payload, dict):
        return None, [
            f"{display_name}: invalid pip-audit JSON: report is not an object"
        ], False
    return payload, [], False


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
        return _audit_uv_export(
            project,
            AuditSpec(
                str(lock),
                reviewed,
                display_name=f"{project}/uv.lock",
                review_by=review_by,
            ),
        )


def _audit_uv_export(project: str, spec: AuditSpec) -> list[str]:
    """Audit one exported uv lock, once per declared marker target."""
    environments = UV_PROJECT_MARKER_ENVIRONMENTS.get(project)
    if not environments:
        return audit_spec(spec, root=Path("/"))
    failures: list[str] = []
    for label, environment in environments.items():
        target = replace(
            spec,
            display_name=f"{spec.display_name} [{label}]",
            marker_environments=(environment,),
        )
        failures.extend(audit_spec(target, root=Path("/")))
    return failures


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
    return _npm_finding_failures(project, payload, total)


def _npm_advisory_ids(payload: dict) -> frozenset[str]:
    vulnerabilities = payload.get("vulnerabilities")
    if not isinstance(vulnerabilities, dict):
        return frozenset()
    return frozenset(
        via["url"].rstrip("/").rsplit("/", 1)[-1]
        for entry in vulnerabilities.values()
        if isinstance(entry, dict)
        for via in entry.get("via", [])
        if isinstance(via, dict) and isinstance(via.get("url"), str)
    )


def _npm_finding_failures(
    project: str, payload: dict, total: int, *, today: date | None = None
) -> list[str]:
    reviewed, review_by = NPM_PROJECT_EXCEPTIONS.get(project, (frozenset(), None))
    failures = _review_deadline_failures(project, reviewed, review_by, today)
    found = _npm_advisory_ids(payload) if total else frozenset()
    if total and not found:
        failures.append(f"{project}: npm audit found {total} vulnerability(s)")
    unexpected = sorted(found - reviewed)
    if unexpected:
        failures.append(f"{project}: unreviewed advisories: {', '.join(unexpected)}")
    stale = sorted(reviewed - found)
    if stale:
        failures.append(f"{project}: stale allowlist entries: {', '.join(stale)}")
    if not failures:
        suffix = f" ({len(found)} reviewed exception(s))" if found else ""
        print(f"PASS {project}/package-lock.json{suffix}")
    return failures


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
