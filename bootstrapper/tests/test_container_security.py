from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from scripts import container_security
from scripts.upstream_drift_watch import load_manifest_image_refs


ROOT = Path(__file__).resolve().parents[2]
GPU_DOCKERFILE_EXCLUSIONS = {
    "services/docling/provider/gpu/Dockerfile",
    "services/parakeet/provider/gpu/Dockerfile",
}


def test_container_security_inventory_covers_every_manifest_default() -> None:
    expected = load_manifest_image_refs(ROOT / "services")

    assert container_security.load_image_inventory(ROOT / "services") == expected
    assert json.loads(container_security.render_images_json(expected)) == list(expected)

    scans = container_security.load_image_scans(ROOT / "services")
    assert tuple(scan.image for scan in scans) == expected
    arm_image = next(image for image in expected if "cpu-arm64-latest" in image)
    assert next(scan.platform for scan in scans if scan.image == arm_image) == "linux/arm64"


def test_container_security_exception_file_is_empty_or_narrow_and_reviewed() -> None:
    exceptions = container_security.load_exceptions(
        ROOT / ".trivyignore.yaml", today=date.today()
    )

    assert isinstance(exceptions, tuple)


def test_local_final_image_inventory_is_scanned_or_explicitly_excluded() -> None:
    _, scheduled = _workflow_sources()
    build_spec = re.compile(r'^\s+"(services/[^"|]+\|[^"|]+)\|[^"]*"', re.MULTILINE)
    scanned = set(build_spec.findall(scheduled))
    excluded = set(
        container_security.load_build_exclusions(
            ROOT / ".container-scan-exclusions.yml", today=date.today()
        )
    )
    composed = {
        build.target
        for build in container_security.load_compose_builds(ROOT / "services")
    }

    assert not scanned & excluded
    assert composed == scanned | excluded


_POLICY_REJECTION_CASES = (
        (
            {"vulnerabilities": [{"id": "CVE-2099-0001", "statement": "reviewed"}]},
            "substantive",
        ),
        (
            {
                "vulnerabilities": [
                    {
                        "id": "CVE-2099-0001",
                        "paths": ["usr/lib/example"],
                        "expired_at": (date.today() + timedelta(days=1)).isoformat(),
                    }
                ]
            },
            "statement",
        ),
        (
            {
                "vulnerabilities": [
                    {
                        "id": "CVE-2099-0001",
                        "purls": ["pkg:pypi/example@1.0"],
                        "statement": "not reachable in Atlas",
                        "expired_at": (date.today() - timedelta(days=1)).isoformat(),
                    }
                ]
            },
            "expired",
        ),
        (
            {
                "vulnerabilities": [
                    {
                        "id": "CVE-2099-0001",
                        "paths": ["**"],
                        "statement": "The affected parser is unreachable in Atlas.",
                        "expired_at": (date.today() + timedelta(days=1)).isoformat(),
                    }
                ]
            },
            "exact relative paths",
        ),
        (
            {
                "vulnerabilities": [
                    {
                        "id": "CVE-2099-0001",
                        "purls": ["pkg:pypi/example"],
                        "statement": "The affected parser is unreachable in Atlas.",
                        "expired_at": (date.today() + timedelta(days=1)).isoformat(),
                    }
                ]
            },
            "exact versioned PURLs",
        ),
        (
            {
                "vulnerabilities": [
                    {
                        "id": "CVE-2099-0001",
                        "purls": ["pkg:pypi/example@1.0"],
                        "statement": "The affected parser is unreachable in Atlas.",
                        "expired_at": (date.today() + timedelta(days=91)).isoformat(),
                    }
                ]
            },
            "review horizon",
        ),
)


@pytest.mark.parametrize(("document", "expected"), _POLICY_REJECTION_CASES)
def test_container_security_rejects_broad_or_stale_exceptions(
    tmp_path: Path, document: dict, expected: str
) -> None:
    path = tmp_path / "exceptions.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ValueError, match=expected):
        container_security.load_exceptions(path, today=date.today())


def _workflow_sources() -> tuple[str, str]:
    services_lint = (ROOT / ".github/workflows/services-lint.yml").read_text(
        encoding="utf-8"
    )
    scheduled = (ROOT / ".github/workflows/container-security.yml").read_text(
        encoding="utf-8"
    )
    return services_lint, scheduled


def test_required_workflow_validates_container_policy() -> None:
    services_lint, _ = _workflow_sources()
    assert "python -m scripts.container_security" in services_lint


def test_container_security_workflow_pins_scanner_and_failure_policy() -> None:
    _, scheduled = _workflow_sources()

    setup_ref = (
        "aquasecurity/setup-trivy@81e514348e19b6112ce2a7e3ecbafe19c1e1f567"
    )
    assert setup_ref in scheduled
    assert "version: v0.74.0" in scheduled
    assert scheduled.count("--severity HIGH,CRITICAL") == 2
    assert scheduled.count("--ignorefile .trivyignore.yaml") == 2
    assert scheduled.count("--exit-code 1") == 2
    assert '--platform "$IMAGE_PLATFORM"' in scheduled


def test_container_security_workflow_covers_both_image_inventories() -> None:
    _, scheduled = _workflow_sources()

    assert "atlas-ci-images.txt" in scheduled
    assert "docker buildx build --load -t" in scheduled
    assert "python -m scripts.container_security --images-json" in scheduled
    assert "max-parallel: 4" in scheduled


def test_scheduled_and_required_workflows_build_the_same_local_images() -> None:
    services_lint, scheduled = _workflow_sources()
    build_spec = re.compile(r'^\s+"(services/[^"|]+\|[^"|]+\|[^"]*)"', re.MULTILINE)
    assert build_spec.findall(scheduled) == build_spec.findall(services_lint)


def _workflow_dockerfiles(source: str) -> set[str]:
    build_spec = re.compile(
        r'^\s+"(services/[^"|]+)\|([^"|]+)\|[^"]*"', re.MULTILINE
    )
    return {
        str((Path(context) / dockerfile).resolve().relative_to(ROOT))
        for context, dockerfile in build_spec.findall(source)
    }


def test_both_build_workflows_cover_every_tracked_non_gpu_dockerfile() -> None:
    tracked = set(
        subprocess.run(
            ["git", "ls-files", "services/**/Dockerfile"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    )
    assert GPU_DOCKERFILE_EXCLUSIONS <= tracked
    expected = tracked - GPU_DOCKERFILE_EXCLUSIONS
    services_lint, scheduled = _workflow_sources()

    assert _workflow_dockerfiles(services_lint) == expected
    assert _workflow_dockerfiles(scheduled) == expected
