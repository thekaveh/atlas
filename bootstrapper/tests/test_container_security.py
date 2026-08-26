from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from scripts import container_security
from scripts.upstream_drift_watch import load_manifest_image_refs, load_remote_build_contexts


ROOT = Path(__file__).resolve().parents[2]
GPU_DOCKERFILE_EXCLUSIONS = {
    "services/docling/provider/gpu/Dockerfile",
    "services/parakeet/provider/gpu/Dockerfile",
}


def test_container_security_inventory_covers_every_manifest_default() -> None:
    expected = load_manifest_image_refs(ROOT / "services")

    assert container_security.load_image_inventory(ROOT / "services") == expected
    assert json.loads(container_security.render_images_json(expected)) == list(expected)


def test_container_security_scans_multiarch_images_on_both_platforms() -> None:
    expected = load_manifest_image_refs(ROOT / "services")
    scans = container_security.load_image_scans(ROOT / "services")

    assert {scan.image for scan in scans} == set(expected)
    multiarch_image = next(image for image in expected if image.startswith("redis:"))
    assert {
        scan.platform for scan in scans if scan.image == multiarch_image
    } == {"linux/amd64", "linux/arm64"}


def test_container_security_preserves_explicit_single_arch_platforms() -> None:
    expected = load_manifest_image_refs(ROOT / "services")
    scans = container_security.load_image_scans(ROOT / "services")

    arm_image = next(image for image in expected if "cpu-arm64-latest" in image)
    assert {
        scan.platform for scan in scans if scan.image == arm_image
    } == {"linux/arm64"}
    amd_image = next(image for image in expected if "cpu-1.9@sha256" in image)
    assert {
        scan.platform for scan in scans if scan.image == amd_image
    } == {"linux/amd64"}


@pytest.mark.parametrize(
    "image_var", ["CHATTERBOX_IMAGE", "COMFYUI_IMAGE", "DOCLING_GPU_IMAGE"]
)
def test_registry_verified_amd64_images_do_not_schedule_arm64(image_var: str) -> None:
    image_by_var = container_security._manifest_image_variables(ROOT / "services")
    image = image_by_var[image_var]
    scans = container_security.load_image_scans(ROOT / "services")

    assert {scan.platform for scan in scans if scan.image == image} == {"linux/amd64"}


def test_compose_only_remote_image_cannot_escape_scan_inventory(tmp_path: Path) -> None:
    service = tmp_path / "example"
    service.mkdir()
    (service / "service.yml").write_text(
        "images:\n  - var: KNOWN_IMAGE\n    container: known\n    default: vendor/known:v1\n",
        encoding="utf-8",
    )
    (service / "compose.yml").write_text(
        "services:\n  known:\n    image: ${KNOWN_IMAGE}\n"
        "  missed:\n    image: vendor/missed:v1\n",
        encoding="utf-8",
    )

    assert container_security.load_compose_image_refs(tmp_path) == (
        "vendor/known:v1",
        "vendor/missed:v1",
    )
    with pytest.raises(ValueError, match="vendor/missed:v1"):
        container_security.load_image_scans(tmp_path)


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


def test_remote_build_exclusions_have_executable_drift_watch_controls() -> None:
    excluded = set(
        container_security.load_build_exclusions(
            ROOT / ".container-scan-exclusions.yml", today=date.today()
        )
    )
    remote_exclusions = {target for target in excluded if target.startswith("https://")}
    controlled = {
        (
            f"https://github.com/{context.repository}.git#${{LLM_GRAPH_BUILDER_REF}}:"
            f"{context.subdir}|{context.dockerfile}"
        )
        for context in load_remote_build_contexts(ROOT / "services")
    }

    assert remote_exclusions == controlled


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


def test_container_security_workflow_builds_every_supported_architecture() -> None:
    _, scheduled = _workflow_sources()

    assert "docker/setup-qemu-action@c7c53464625b32c7a7e944ae62b3e17d2b600130" in scheduled
    assert "platforms=\"linux/amd64 linux/arm64\"" in scheduled
    assert "services/asset-baker/app|Dockerfile" in scheduled
    assert 'platforms="linux/amd64"' in scheduled
    assert '--platform "$platform"' in scheduled


def test_required_build_validation_builds_every_supported_architecture() -> None:
    required, _ = _workflow_sources()

    assert "docker/setup-qemu-action@c7c53464625b32c7a7e944ae62b3e17d2b600130" in required
    assert 'platforms="linux/amd64 linux/arm64"' in required
    assert 'platforms="linux/amd64"' in required
    assert '--platform "$platform"' in required


def test_container_security_workflow_covers_both_image_inventories() -> None:
    _, scheduled = _workflow_sources()

    assert "atlas-ci-images.txt" in scheduled
    assert "docker buildx build --load --platform" in scheduled
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
