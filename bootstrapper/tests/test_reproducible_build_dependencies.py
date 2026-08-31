"""Regression contracts for reproducible image/init dependency installation."""

from __future__ import annotations

from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[2]


def _runtime_package_install_lines() -> list[str]:
    findings: list[str] = []
    for path in sorted((ROOT / "services").rglob("*")):
        if not path.is_file() or path.name.startswith("Dockerfile"):
            continue
        if path.suffix not in {".sh", ".yml", ".yaml"}:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.search(r"(?:^|\s)apk\s+add(?:\s|$)", stripped):
                findings.append(f"{path.relative_to(ROOT)}:{number}: {stripped}")
    return findings


def test_runtime_paths_never_install_alpine_packages() -> None:
    """Init jobs must not resolve mutable packages after a container starts."""
    assert _runtime_package_install_lines() == []


def test_debian_refresh_images_use_reviewed_immutable_refs() -> None:
    """Task 9 base refreshes must be visible, immutable registry contracts."""
    expected = {
        "services/litellm/init/Dockerfile": (
            "python:3.12.14-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579"
        ),
        "services/open-webui/init/Dockerfile": (
            "python:3.12.14-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579"
        ),
        "services/backend/app/Dockerfile": (
            "python:3.12.14-bookworm@sha256:581429e3df12d76e6af4be5ab7d0e7fc2013eb57dc23d2de691411c8efdbb970"
        ),
        "services/jupyterhub/build/Dockerfile": (
            "quay.io/jupyter/datascience-notebook:2026-08-24@sha256:"
            "e5029672ab8a861345f117dc466a4dab91ad7c299f3c7c853b9b193860b16aaf"
        ),
    }
    for relative, reference in expected.items():
        assert reference in (ROOT / relative).read_text(encoding="utf-8")


def test_jupyter_refresh_restores_checksummed_node_runtime() -> None:
    """The refreshed upstream no longer bundles Node; retain it immutably."""
    dockerfile = (ROOT / "services/jupyterhub/build/Dockerfile").read_text(
        encoding="utf-8"
    )

    assert (
        "ARG NODE_IMAGE=node:22.23.2-bookworm-slim@sha256:"
        "83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5"
    ) in dockerfile
    assert "FROM ${NODE_IMAGE} AS node-runtime" in dockerfile
    assert "COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node" in dockerfile
    assert "COPY --from=node-runtime /usr/local/lib/node_modules" in dockerfile


def test_runtime_init_images_bake_their_tools_in_build_contexts() -> None:
    expected = {
        "backup": {"openssl"},
        "comfyui": {"wget", "ca-certificates"},
        "hermes": {"bash", "gettext", "curl", "jq", "ca-certificates"},
        "lightrag": {
            "bash",
            "curl",
            "jq",
            "postgresql17-client",
            "ca-certificates",
            "python3",
        },
        "ollama": {"curl"},
        "openclaw": {"jq"},
    }
    locations = {
        "backup": ("backup", "init"),
        "comfyui": ("comfyui-init", "init"),
        "hermes": ("hermes-init", "init"),
        "lightrag": ("lightrag-init", "init"),
        "ollama": ("ollama-pull", "pull"),
        "openclaw": ("openclaw-init", "init"),
    }

    for service, packages in expected.items():
        manifest = yaml.safe_load(
            (ROOT / f"services/{service}/service.yml").read_text(encoding="utf-8")
        )
        init_image = next(
            image
            for image in manifest["images"]
            if image["container"] == locations[service][0]
        )
        assert re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", init_image["default"])
        compose = yaml.safe_load(
            (ROOT / f"services/{service}/compose.yml").read_text(encoding="utf-8")
        )
        container, context = locations[service]
        build = compose["services"][container]["build"]
        assert build["context"] == context
        dockerfile = ROOT / f"services/{service}/{context}/Dockerfile"
        text = dockerfile.read_text(encoding="utf-8")
        assert "apk add --no-cache" in text
        for package in packages:
            assert re.search(rf"(?:^|\s){re.escape(package)}(?:[=\s\\]|$)", text), (
                service,
                package,
            )
