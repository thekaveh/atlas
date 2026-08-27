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


def test_container_security_summary_labels_platform_expansion(
    tmp_path, monkeypatch, capsys
) -> None:
    scans = (
        container_security.ImageScan("vendor/example:1.2.3", "linux/amd64"),
        container_security.ImageScan("vendor/example:1.2.3", "linux/arm64"),
    )
    monkeypatch.setattr(container_security, "load_image_scans", lambda _path: scans)
    monkeypatch.setattr(container_security, "load_exceptions", lambda _path: ())
    monkeypatch.setattr(
        container_security, "load_build_exclusions", lambda _path: ()
    )

    assert container_security.main(["--services-dir", str(tmp_path)]) == 0
    assert "2 image-platform scan(s)" in capsys.readouterr().out


def test_container_security_ledger_does_not_claim_registry_discovery() -> None:
    ledger = (ROOT / "docs/maintenance/external-contract-ledger.md").read_text()
    row = next(
        line for line in ledger.splitlines()
        if line.startswith("| Container image vulnerability gate |")
    )

    assert "attempted on both" in row
    assert "not registry discovery" in row
    assert "registry-derived" not in row
    assert "registry-supported" not in row
    images = container_security.load_image_inventory(ROOT / "services")
    scans = container_security.load_image_scans(ROOT / "services")
    assert f"All {len(images)} manifest-owned image defaults" in row
    assert f"currently producing {len(scans)} isolated image-platform scans" in row


def test_container_scan_job_name_distinguishes_platforms() -> None:
    workflow = (ROOT / ".github/workflows/container-security.yml").read_text()

    assert "name: Scan ${{ matrix.image }} (${{ matrix.platform }})" in workflow


@pytest.mark.parametrize(
    "reference",
    [
        "vendor/example:latest",
        "vendor/example:1",
        "vendor/example",
        "vendor/example:1.2.3x",
        "vendor/example:python-3.12.13latest",
    ],
)
def test_manifest_image_inventory_rejects_floating_defaults(
    tmp_path: Path, reference: str
) -> None:
    service = tmp_path / "example"
    service.mkdir()
    (service / "service.yml").write_text(
        "images:\n"
        "  - var: EXAMPLE_IMAGE\n"
        "    container: example\n"
        f"    default: {reference}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="floating or untagged"):
        container_security.load_image_inventory(tmp_path)


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


def test_changed_scan_inventory_includes_only_added_image_references() -> None:
    redis_manifest = yaml.safe_load(
        (ROOT / "services/redis/service.yml").read_text(encoding="utf-8")
    )
    image = redis_manifest["images"][0]["default"]
    scans = container_security.load_changed_image_scans(
        ROOT / "services",
        [
            "diff --git a/services/redis/service.yml b/services/redis/service.yml",
            "+    description: metadata-only changes do not select every owned image",
            f'+    default: "{image}"',
        ],
    )

    assert scans
    assert {scan.image for scan in scans} == {image}
    assert container_security.load_changed_image_scans(
        ROOT / "services",
        [
            "diff --git a/services/redis/service.yml b/services/redis/service.yml",
            "+    description: metadata-only changes do not select every owned image",
        ],
    ) == ()


def test_changed_compose_scan_includes_cross_service_inventory_image(
    tmp_path: Path,
) -> None:
    for name, image in (("owner", "vendor/owner:1.2.3"), ("consumer", "vendor/consumer:2.3.4")):
        service = tmp_path / name
        service.mkdir()
        (service / "service.yml").write_text(
            "images:\n"
            f"  - var: {name.upper()}_IMAGE\n"
            f"    container: {name}\n"
            f"    default: {image}\n",
            encoding="utf-8",
        )
    (tmp_path / "owner/compose.yml").write_text(
        "services:\n  owner:\n    image: ${OWNER_IMAGE}\n",
        encoding="utf-8",
    )
    (tmp_path / "consumer/compose.yml").write_text(
        "services:\n  consumer:\n    image: vendor/owner:1.2.3\n",
        encoding="utf-8",
    )

    scans = container_security.load_changed_image_scans(
        tmp_path,
        [
            "diff --git a/services/consumer/compose.yml b/services/consumer/compose.yml",
            "+    image: vendor/owner:1.2.3",
        ],
    )

    assert {scan.image for scan in scans} == {"vendor/owner:1.2.3"}


def test_compose_build_image_args_must_be_exactly_pinned(tmp_path: Path) -> None:
    service = tmp_path / "example"
    service.mkdir()
    (service / "service.yml").write_text(
        "images:\n"
        "  - var: EXAMPLE_IMAGE\n"
        "    container: example\n"
        "    default: vendor/example:1.2.3\n",
        encoding="utf-8",
    )
    (service / "compose.yml").write_text(
        "services:\n"
        "  example:\n"
        "    build:\n"
        "      context: build\n"
        "      args:\n"
        "        BASE_IMAGE: python:latest\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="build arg BASE_IMAGE"):
        container_security.load_compose_builds(tmp_path)


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
        "images:\n  - var: KNOWN_IMAGE\n    container: known\n    default: vendor/known:1.2.3\n",
        encoding="utf-8",
    )
    (service / "compose.yml").write_text(
        "services:\n  known:\n    image: ${KNOWN_IMAGE}\n"
        "  missed:\n    image: vendor/missed:1.2.3\n",
        encoding="utf-8",
    )

    assert container_security.load_compose_image_refs(tmp_path) == (
        "vendor/known:1.2.3",
        "vendor/missed:1.2.3",
    )
    with pytest.raises(ValueError, match="vendor/missed:1.2.3"):
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


def test_required_workflow_scans_final_images_with_pinned_trivy() -> None:
    services_lint, _ = _workflow_sources()

    required_fragments = (
        "aquasecurity/setup-trivy@81e514348e19b6112ce2a7e3ecbafe19c1e1f567",
        "version: v0.74.0",
        "atlas-ci-scan-images.txt",
        "trivy image",
        "--image-src docker",
        "--image-src remote",
        "--changed-diff-stdin",
        "--severity HIGH,CRITICAL",
        "--ignorefile .trivyignore.yaml",
        "--exit-code 1",
    )
    assert all(fragment in services_lint for fragment in required_fragments)
    assert "fetch-depth: 0" in services_lint


def test_required_final_image_scan_is_dependency_diff_scoped() -> None:
    services_lint, _ = _workflow_sources()

    assert 'git diff --quiet "$BASE_SHA" "$GITHUB_SHA"' in services_lint
    assert '"$dockerfile_path"' in services_lint
    assert "realpath --relative-to" not in services_lint
    for dependency_pathspec in (
        "requirements*.txt",
        "constraints*.txt",
        "pyproject.toml",
        "uv.lock",
        "package.json",
        "package-lock.json",
        "pom.xml",
        "*.gradle",
        "gradle.lockfile",
    ):
        assert dependency_pathspec in services_lint
    assert 'done < "${RUNNER_TEMP}/atlas-ci-scan-images.txt"' in services_lint
    assert 'done < "${RUNNER_TEMP}/atlas-ci-images.txt"' not in services_lint


def test_required_final_image_scan_blocks_only_fixable_findings() -> None:
    services_lint, _ = _workflow_sources()

    final_scan = services_lint.split("- name: Scan built final runtime images", 1)[1]
    assert "--ignore-unfixed" in final_scan


def test_required_image_validation_reclaims_ephemeral_runner_storage() -> None:
    services_lint, _ = _workflow_sources()

    assert 'docker image rm "$image_tag"' in services_lint
    assert "docker buildx prune --force" in services_lint
    final_scan = services_lint.split("- name: Scan built final runtime images", 1)[1]
    assert 'docker image rm "$image_ref"' in final_scan


def test_backend_runtime_excludes_ci_artifacts_and_build_installer() -> None:
    dockerignore = (ROOT / "services/backend/app/.dockerignore").read_text(
        encoding="utf-8"
    )
    dockerfile = (ROOT / "services/backend/app/Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "**/.ci-venv" in dockerignore
    assert "**/__pycache__" in dockerignore
    assert "apt-get upgrade --yes" in dockerfile
    assert "pip uninstall --yes uv" in dockerfile


def test_changed_debian_images_apply_available_security_updates() -> None:
    for relative in (
        "services/jupyterhub/build/Dockerfile",
        "services/litellm/init/Dockerfile",
        "services/open-webui/init/Dockerfile",
    ):
        dockerfile = (ROOT / relative).read_text(encoding="utf-8")
        assert "apt-get upgrade --yes" in dockerfile, relative


def test_jupyter_runtime_pins_patched_python_and_node_tooling() -> None:
    requirements = (ROOT / "services/jupyterhub/build/requirements.txt").read_text(
        encoding="utf-8"
    )
    locked = (ROOT / "services/jupyterhub/build/requirements-locked.txt").read_text(
        encoding="utf-8"
    )
    dockerfile = (ROOT / "services/jupyterhub/build/Dockerfile").read_text(
        encoding="utf-8"
    )

    for package in (
        "brotli==1.2.0",
        "jupyterlab==4.6.3",
        "jupyterlab-git==0.54.0",
        "notebook==7.6.2",
        "wheel==0.46.2",
    ):
        assert package in requirements.lower()
        assert package in locked.lower()
    assert "ARG NPM_VERSION=11.19.1" in dockerfile
    assert 'npm install --global "npm@${NPM_VERSION}"' in dockerfile


def test_jupyter_scala_kernels_pin_patched_transitive_libraries() -> None:
    dockerfile = (ROOT / "services/jupyterhub/build/Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "com.google.protobuf:protobuf-java:3.25.5" in dockerfile
    assert "org.lz4:lz4-java:1.8.1" in dockerfile
    assert "find /home/jovyan/.cache/coursier" in dockerfile
    assert "*-sources.jar" in dockerfile


def test_jupyter_julia_runtime_uses_a_pinned_clean_environment() -> None:
    dockerfile = (ROOT / "services/jupyterhub/build/Dockerfile").read_text(
        encoding="utf-8"
    )

    lockfiles = (
        ROOT / "services/jupyterhub/build/julia/Project.toml",
        ROOT / "services/jupyterhub/build/julia/Manifest.toml",
    )
    expected_fragments = (
        "ARG JULIA_VERSION=1.12.7",
        "9243c0b524c7f300883240a1ee5ea3916a30e070bff718acf8ccaee31a731ef2",
        "4e7e9e776634d24835250de67cde39b0d4af15bc432eb20697e6be6c28ea69e8",
        "COPY julia/Project.toml julia/Manifest.toml",
        "/opt/julia/environments/v1.12/",
        "Pkg.instantiate(; allow_autoprecomp=false)",
        "JUPYTER_DATA_DIR=/opt/conda/share/jupyter",
        "chown -R ${NB_UID}:${NB_GID} /opt/julia",
        "find /opt/julia -name Manifest.toml",
    )

    assert all(path.is_file() for path in lockfiles)
    assert all(fragment in dockerfile for fragment in expected_fragments)
    assert "Pkg.precompile()" not in dockerfile


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
        str((ROOT / context / dockerfile).resolve().relative_to(ROOT))
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
