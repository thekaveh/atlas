"""Every `services/**/Dockerfile` must pin its FROM image to a digest or
patch-versioned tag — never to a floating tag like `latest`, `slim`,
`stable`, `edge`, or a major-only tag like `python:3.12` (which silently
tracks the latest patch).

A floating tag means rebuilds can pick up a future supply-chain-compromised
or behaviorally-changed base image without lockstep visibility. Today the
stack uses patch-version tags everywhere (e.g. `python:3.12.13-slim`,
`apache/airflow:3.3.0`, `pytorch/pytorch:2.12.1-cuda12.6-cudnn9-runtime`).
This test locks that posture in CI so a future contributor can't
re-introduce a floating tag silently.

ARG-defaulted FROMs (e.g. `FROM ${BASE_IMAGE}`) are exempt because the ARG is
the user-facing override knob (compose injects `*_IMAGE` from .env at build
time). The `*_IMAGE` defaults live in the manifests and flow into .env.example
via env_assembler; most are patch-pinned, but a few documented floating
exceptions exist (chatterbox `:gpu`, tei `cpu-arm64-latest`, jenkins
`lts-jdk21`). This guard does NOT verify their pin posture — regen only keeps
.env.example in sync with the manifests, it does not check pinning (a prior
version of this docstring claimed otherwise). A `*_IMAGE` pin-lint is a
worthwhile follow-up but non-trivial: a correct predicate must distinguish
genuinely-pinned non-semver tags (postgres 2-part `17.10-alpine`, NGC calendar
`26.06-py3`, minio `RELEASE.…`, jupyter `python-3.11.10`, ai-dock
`v2-cpu-…-v0.2.7`) from floating ones (`python:3.12`), which a naive
"N-part version" rule cannot.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]

# Whole-tag floating markers — block immediately. A future addition like
# `mainline` / `latest-slim` should be added here too.
FORBIDDEN_TAGS = {"latest", "slim", "stable", "edge", "bookworm", "bullseye"}

# A tag is considered patch-version-pinned if its numeric prefix has
# at least major.minor.patch (e.g. `3.12.7`, `2.5.1`, `3.2.2`,
# `2.5.1-cuda12.4-cudnn9-runtime`). This rejects `python:3.12-slim` and
# `python:3.12` which both track the moving latest patch.
PATCH_VERSION_PREFIX = re.compile(r"^\d+\.\d+\.\d+")

# Date / SHA / hash-style tags (RELEASE.YYYY-MM-DDTHH-MM-SS, full sha256
# digests handled separately, hex SHAs, ...) are also acceptable. List
# patterns we recognise as fully-qualified.
ACCEPTABLE_NONSEMVER_PATTERNS = [
    re.compile(r"^RELEASE\.\d{4}-\d{2}-\d{2}T"),  # MinIO style
    re.compile(r"^v\d+\.\d+\.\d+"),                # `vMAJOR.MINOR.PATCH`
    re.compile(r"^\d+\.\d+\.\d+"),                 # plain `MAJOR.MINOR.PATCH`
    re.compile(r"^cpu-\d+\.\d+"),                  # TEI Reranker `cpu-1.9`
    re.compile(r"^cpu-arm64-"),                    # TEI arm64 image suffix
]

FROM_RE = re.compile(r"^\s*FROM\s+(\S+)", re.MULTILINE)


def _is_pinned_enough(tag: str) -> bool:
    """Returns True if the tag is sufficiently specific (digest or
    patch-versioned). Used to reject `:slim` / `:3.12` / `:3.12-slim` etc.
    """
    if tag in FORBIDDEN_TAGS:
        return False
    if PATCH_VERSION_PREFIX.match(tag):
        return True
    for pat in ACCEPTABLE_NONSEMVER_PATTERNS:
        if pat.match(tag):
            return True
    return False


def _discover_dockerfiles() -> list[Path]:
    return sorted(REPO_ROOT.glob("services/**/Dockerfile"))


@pytest.mark.parametrize(
    "dockerfile",
    _discover_dockerfiles(),
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_dockerfile_from_is_pinned(dockerfile: Path) -> None:
    """Every `FROM <image>:<tag>` must use a digest or a tag NOT in the
    floating-tag denylist. `FROM ${ARG}` is exempt (ARG is compose-driven).
    """
    text = dockerfile.read_text(encoding="utf-8")
    images = FROM_RE.findall(text)
    assert images, f"{dockerfile.relative_to(REPO_ROOT)} has no FROM directive"
    for image in images:
        # ARG-driven references are compose-controlled; skip.
        if image.startswith("$") or image.startswith("${"):
            continue
        # Digest-pinned (image@sha256:...) is acceptable.
        if "@sha256:" in image:
            continue
        # Otherwise, image must be `name:tag`. Reject untagged or floating-tag.
        assert ":" in image, (
            f"{dockerfile.relative_to(REPO_ROOT)} FROM {image!r}: untagged "
            f"image (rebuilds resolve to :latest). Pin to a patch-version tag."
        )
        tag = image.rsplit(":", 1)[1].lower()
        if not _is_pinned_enough(tag):
            pytest.fail(
                f"{dockerfile.relative_to(REPO_ROOT)} FROM {image!r}: "
                f"tag {tag!r} is not patch-version-pinned. Use "
                f"major.minor.patch (e.g. python:3.12.7-slim, "
                f"apache/airflow:3.3.0) or a digest (image@sha256:...)."
            )


def test_at_least_one_dockerfile_discovered() -> None:
    """Belt-and-suspenders: if the glob returns zero, fail loudly."""
    files = _discover_dockerfiles()
    assert files, "No Dockerfiles discovered under services/**/Dockerfile"
