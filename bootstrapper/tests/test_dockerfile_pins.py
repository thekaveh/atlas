"""Every `services/**/Dockerfile` must pin its FROM image to a digest or
patch-versioned tag — never to a floating tag like `latest`, `slim`,
`stable`, `edge`, or a major-only tag like `python:3.12` (which silently
tracks the latest patch).

A floating tag means rebuilds can pick up a future supply-chain-compromised
or behaviorally-changed base image without lockstep visibility. Today the
stack uses patch-version tags everywhere (e.g. `python:3.12.13-slim`,
`apache/airflow:3.3.1`, `pytorch/pytorch:2.13.0-cuda12.6-cudnn9-runtime`).
This test locks that posture in CI so a future contributor can't
re-introduce a floating tag silently.

ARG-defaulted FROMs (e.g. `FROM ${BASE_IMAGE}`) remain user-overridable; this
gate resolves and validates every checked-in default, including nested
`${VAR:-fallback}` expressions.
Non-semver channel tags that cannot be replaced with a release tag are pinned
to the registry's immutable multi-platform digest.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts.container_security import image_reference_is_pinned


REPO_ROOT = Path(__file__).resolve().parents[2]

# Whole-tag floating markers — block immediately. A future addition like
# `mainline` / `latest-slim` should be added here too.
FORBIDDEN_TAGS = {"latest", "slim", "stable", "edge", "bookworm", "bullseye"}

FROM_RE = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)")
ARG_RE = re.compile(
    r"^\s*ARG\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:=(?P<default>\S+))?\s*$",
    re.MULTILINE,
)
VARIABLE_RE = re.compile(
    r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)(?:(?P<operator>:-|-)(?P<fallback>[^}]*))?\}|(?P<plain>[A-Za-z_][A-Za-z0-9_]*))"
)


def _is_pinned_enough(tag: str) -> bool:
    """Returns True if the tag is sufficiently specific (digest or
    patch-versioned). Used to reject `:slim` / `:3.12` / `:3.12-slim` etc.
    """
    return tag not in FORBIDDEN_TAGS and image_reference_is_pinned(f"image:{tag}")


def _resolve_variables(value: str, defaults: dict[str, str | None]) -> str:
    """Resolve checked-in ARG defaults used by a FROM reference."""

    seen: set[str] = set()
    while value not in seen:
        seen.add(value)
        match = VARIABLE_RE.search(value)
        if not match:
            return value
        name = match.group("braced") or match.group("plain")
        default = defaults.get(name)
        fallback = match.group("fallback")
        operator = match.group("operator")
        if operator == ":-":
            replacement = default or fallback
        elif operator == "-":
            replacement = default if default is not None else fallback
        else:
            replacement = default
        if replacement is None:
            raise AssertionError(f"FROM variable {name} has no checked-in default")
        value = value[: match.start()] + replacement + value[match.end() :]
    raise AssertionError("FROM ARG defaults contain a reference cycle")


def _dockerfile_images(text: str) -> list[str]:
    defaults: dict[str, str | None] = {}
    images: list[str] = []
    saw_from = False
    for line in text.splitlines():
        from_match = FROM_RE.match(line)
        if from_match:
            saw_from = True
            images.append(_resolve_variables(from_match.group(1), defaults))
            continue
        if not saw_from:
            arg_match = ARG_RE.fullmatch(line)
            if arg_match:
                defaults[arg_match.group("name")] = arg_match.group("default")
    return images


def _assert_pinned_images(text: str, path: Path) -> None:
    images = _dockerfile_images(text)
    assert images, f"{path} has no FROM directive"
    for image in images:
        if "@sha256:" in image:
            assert image_reference_is_pinned(image), (
                f"{path} FROM {image!r}: malformed sha256 digest"
            )
            continue
        assert ":" in image.rsplit("/", 1)[-1], (
            f"{path} FROM {image!r}: untagged image (rebuilds resolve to "
            ":latest). Pin to an exact release tag."
        )
        tag = image.rsplit(":", 1)[1].lower()
        assert _is_pinned_enough(tag), (
            f"{path} FROM {image!r}: tag {tag!r} is not patch-version-pinned. "
            "Use an exact release tag or immutable digest."
        )


def _discover_dockerfiles() -> list[Path]:
    return sorted(REPO_ROOT.glob("services/**/Dockerfile"))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "ARG BASE_IMAGE=python:3.12.13-slim\nFROM ${BASE_IMAGE}\n",
            ["python:3.12.13-slim"],
        ),
        (
            "ARG BACKEND_IMAGE\n"
            "ARG BASE_IMAGE=${BACKEND_IMAGE:-python:3.12.13}\n"
            "FROM ${BASE_IMAGE}\n",
            ["python:3.12.13"],
        ),
        (
            "ARG BASE_IMAGE\nFROM ${BASE_IMAGE:-python:3.12.13-slim}\n",
            ["python:3.12.13-slim"],
        ),
    ],
)
def test_from_arg_defaults_are_resolved(source: str, expected: list[str]) -> None:
    assert _dockerfile_images(source) == expected


@pytest.mark.parametrize(
    "reference",
    ["python:latest", "python:3", "python"],
)
def test_from_arg_defaults_reject_floating_references(reference: str) -> None:
    source = f"ARG BASE_IMAGE={reference}\nFROM ${{BASE_IMAGE}}\n"

    with pytest.raises(AssertionError, match="not patch-version-pinned|untagged"):
        _assert_pinned_images(source, Path("Dockerfile"))


def test_later_stage_arg_cannot_hide_floating_global_default() -> None:
    source = (
        "ARG BASE_IMAGE=python:latest\n"
        "FROM ${BASE_IMAGE} AS first\n"
        "ARG BASE_IMAGE=python:3.12.13\n"
        "FROM ${BASE_IMAGE}\n"
    )

    with pytest.raises(AssertionError, match="not patch-version-pinned"):
        _assert_pinned_images(source, Path("Dockerfile"))


def test_arg_declared_after_from_is_not_available_to_from() -> None:
    source = "FROM ${BASE_IMAGE}\nARG BASE_IMAGE=python:3.12.13\n"

    with pytest.raises(AssertionError, match="no checked-in default"):
        _assert_pinned_images(source, Path("Dockerfile"))


@pytest.mark.parametrize(
    "dockerfile",
    _discover_dockerfiles(),
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_dockerfile_from_is_pinned(dockerfile: Path) -> None:
    """Every literal or ARG-defaulted FROM must use an exact release or digest."""
    _assert_pinned_images(
        dockerfile.read_text(encoding="utf-8"),
        dockerfile.relative_to(REPO_ROOT),
    )


def test_at_least_one_dockerfile_discovered() -> None:
    """Belt-and-suspenders: if the glob returns zero, fail loudly."""
    files = _discover_dockerfiles()
    assert files, "No Dockerfiles discovered under services/**/Dockerfile"


@pytest.mark.parametrize(
    ("relative_path", "floating_reference"),
    [
        ("services/chatterbox/service.yml", "travisvn/chatterbox-tts-api:gpu\""),
        (
            "services/tei-reranker/service.yml",
            "text-embeddings-inference:cpu-arm64-latest\"",
        ),
        (
            "services/tei-reranker/service.yml",
            "text-embeddings-inference:cpu-1.9\"",
        ),
        (
            "services/tei-reranker/service.yml",
            "text-embeddings-inference:1.9\"",
        ),
        ("services/jenkins/service.yml", "jenkins/jenkins:lts-jdk21\""),
        ("services/jenkins/build/Dockerfile", "jenkins/jenkins:lts-jdk21\n"),
        ("services/asset-worker/app/Dockerfile", "node:22-bookworm-slim\n"),
    ],
)
def test_known_channel_image_defaults_are_digest_pinned(
    relative_path: str, floating_reference: str
) -> None:
    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    assert floating_reference not in text
    assert "@sha256:" in text

def test_jupyterhub_pyg_lib_pin_is_satisfiable(  # #776 regression
) -> None:
    """The PyG ABI3 wheel and index must move with the Torch CPU family."""
    requirements = (
        REPO_ROOT / "services" / "jupyterhub" / "build" / "requirements.txt"
    ).read_text(encoding="utf-8")
    lines = [line.strip() for line in requirements.splitlines()]
    pyg_lib_lines = [l for l in lines if l.startswith("pyg_lib")]
    assert pyg_lib_lines == ["pyg_lib==0.8.0"]
    assert "--find-links https://data.pyg.org/whl/torch-2.13.0+cpu.html" in lines
    for retired in ("torch-scatter", "torch-sparse", "torch-cluster"):
        assert not any(line.startswith(retired) for line in lines)
