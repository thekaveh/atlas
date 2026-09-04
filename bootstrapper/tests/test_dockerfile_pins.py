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

import os
import re
from pathlib import Path
import subprocess
from typing import NamedTuple

import pytest

from scripts import container_security
from scripts import upstream_drift_watch as watch
from scripts.container_security import image_reference_is_pinned


REPO_ROOT = Path(__file__).resolve().parents[2]
TRIVY_REMOTE_SCAN = REPO_ROOT / "scripts/run_trivy_remote_scan.sh"

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

def test_jupyterhub_pyg_stack_needs_no_external_wheel_index(  # #776 regression
) -> None:
    """torch-geometric resolves from PyPI; no compiled PyG extension is pinned.

    ``pyg_lib`` was only ever published at ``data.pyg.org``, which became
    unresolvable on 2026-09-02, and it is absent from PyPI. Depending on it
    made both the lock check and the image build fail closed on a third-party
    CDN, so the optional accelerators stay out of the image.
    """
    requirements = (
        REPO_ROOT / "services" / "jupyterhub" / "build" / "requirements.txt"
    ).read_text(encoding="utf-8")
    lines = [line.strip() for line in requirements.splitlines()]
    # Comments explain why the accelerators are absent, so assert against the
    # directives pip actually acts on rather than the raw file text.
    directives = [line for line in lines if line and not line.startswith("#")]
    assert "torch_geometric==2.7.0" in directives
    assert not any(line.startswith("--find-links") for line in directives)
    assert not any("data.pyg.org" in line for line in directives)
    for absent in ("pyg_lib", "torch-scatter", "torch-sparse", "torch-cluster"):
        assert not any(line.startswith(absent) for line in directives)


def test_remote_contexts_only_cli_skips_unrelated_watch_probes(
    monkeypatch, tmp_path: Path
) -> None:
    report_path = tmp_path / "remote-report.md"
    captured = {}

    def _remote_probe(**kwargs):
        captured.update(kwargs)
        return watch.ProbeResult("remote build contexts", True, "validated")

    monkeypatch.setattr(watch, "probe_configured_remote_build_contexts", _remote_probe)
    monkeypatch.setattr(
        watch, "run_watch", lambda **_kwargs: pytest.fail("full watcher ran")
    )
    args = [
        "--remote-contexts-only",
        "--services-dir", str(tmp_path / "services"),
        "--remote-base-digests", str(tmp_path / "policy.yml"),
        "--report-file", str(report_path),
        "--http-timeout", "2.0",
        "--image-timeout", "3.0",
    ]

    assert watch.main(args) == 0
    assert captured == {
        "services_dir": tmp_path / "services",
        "remote_base_digests": tmp_path / "policy.yml",
        "http_timeout": 2.0,
        "image_timeout": 3.0,
    }
    assert "remote build contexts" in report_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("dockerfile", "expected"),
    (
        (
            "FROM vendor/safe:1.2.3\n"
            "RUN <<'E'OF\n"
            "E\n"
            "FROM scratch AS trusted\n"
            "EOF\n"
            "COPY --from=trusted /x /x\n",
            ("trusted", "vendor/safe:1.2.3"),
        ),
        (
            "FROM vendor/safe:1.2.3\n"
            "RUN echo ' <<\"COPY --from=vendor/hidden:latest /x /x\"'\n"
            "COPY --from=vendor/hidden:latest /x /x\n",
            ("vendor/hidden:latest", "vendor/safe:1.2.3"),
        ),
    ),
)
def test_remote_dockerfile_heredoc_tokens_cannot_hide_external_sources(
    dockerfile: str, expected: tuple[str, ...]
) -> None:
    assert watch._literal_dockerfile_bases(dockerfile) == expected


@pytest.mark.parametrize(
    "instruction",
    (
        'ENV MARKER << "COPY --from=vendor/hidden:latest /x /x"',
        'LABEL marker << "COPY --from=vendor/hidden:latest /x /x"',
        'ONBUILD ENV MARKER << "COPY --from=vendor/hidden:latest /x /x"',
    ),
)
def test_non_heredoc_instruction_cannot_consume_real_copy(
    tmp_path: Path, instruction: str
) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM vendor/safe:1.2.3\n"
        f"{instruction}\n"
        "COPY --from=vendor/hidden:latest /x /x\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="vendor/hidden:latest"):
        container_security._validate_dockerfile_build_contract(
            dockerfile, {}, {}
        )


@pytest.mark.parametrize(
    "instruction", ("RUN <<EOF", "COPY <<EOF /x", "ADD <<EOF /x", "ONBUILD RUN <<EOF")
)
def test_buildkit_heredoc_capable_instructions_still_skip_their_bodies(
    tmp_path: Path, instruction: str
) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM vendor/safe:1.2.3\n"
        f"{instruction}\n"
        "FROM scratch AS trusted\n"
        "EOF\n"
        "COPY --from=trusted /x /x\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="trusted"):
        container_security._validate_dockerfile_build_contract(
            dockerfile, {}, {}
        )


@pytest.mark.parametrize(
    "instruction",
    (
        'RUN ["echo", "<<EOF"]',
        'COPY ["<<EOF", "/x"]',
        'ADD ["<<EOF", "/x"]',
        'ONBUILD RUN ["echo", "<<EOF"]',
    ),
)
def test_json_form_instruction_never_enters_heredoc_body_parser(
    monkeypatch, instruction: str
) -> None:
    monkeypatch.setattr(
        container_security,
        "_skip_dockerfile_heredocs",
        lambda *_args: pytest.fail("JSON-form instruction entered heredoc parser"),
    )

    assert container_security._dockerfile_logical_lines([instruction]) == (
        instruction,
    )


def test_shell_test_command_starting_with_bracket_remains_heredoc_capable() -> None:
    document = (
        "FROM vendor/safe:1.2.3\n"
        "RUN [ x = x ] <<EOF\n"
        "FROM vendor/not-an-image:latest\n"
        "EOF\n"
    )

    assert container_security.load_dockerfile_source_images(document) == (
        "vendor/safe:1.2.3",
    )


def test_repeated_carriage_returns_cannot_extend_a_heredoc_body(
    tmp_path: Path,
) -> None:
    document = (
        'FROM vendor/safe:1.2.3\nRUN <<"RUN true"\n'
        "RUN true\r\r\n"
        "COPY --from=vendor/hidden:latest /x /x\n"
        "RUN true\n"
    )
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(document, encoding="utf-8", newline="")

    with pytest.raises(ValueError, match="vendor/hidden:latest"):
        container_security._validate_dockerfile_build_contract(
            dockerfile, {}, {}
        )
    assert watch._literal_dockerfile_bases(document) == (
        "vendor/hidden:latest",
        "vendor/safe:1.2.3",
    )


@pytest.mark.parametrize(
    "context",
    (
        "ssh://git@github.com/evil/repo.git#main",
        "http://github.com/evil/repo.git#${GRAPH_REF}:app",
        "git://github.com/evil/repo.git#main",
        "docker-image://vendor/base:latest",
    ),
)
def test_unreviewed_remote_context_transport_fails_both_inventories(
    tmp_path: Path, context: str
) -> None:
    service = tmp_path / "services" / "demo"
    service.mkdir(parents=True)
    (service / "service.yml").write_text("env: []\n", encoding="utf-8")
    (service / "compose.yml").write_text(
        "services:\n  app:\n    build:\n"
        f"      context: {context}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported or unpinned"):
        container_security.load_compose_builds(tmp_path / "services")
    with pytest.raises(ValueError, match="unsupported or unpinned"):
        watch.load_remote_build_contexts(tmp_path / "services")


class _FakeRemoteScanResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str
    counter: str
    arguments: str
    sleeps: str
    temporary_files_exist: tuple[bool, bool]


_FAKE_TRIVY = (
    "#!/bin/sh\n"
    "set -eu\n"
    'count_file="${TRIVY_COUNTER_FILE:?}"\n'
    'count="$(cat "$count_file" 2>/dev/null || printf 0)"\n'
    "count=$((count + 1))\n"
    'printf "%s\\n" "$count" > "$count_file"\n'
    'printf "%s\\n" "$*" >> "${TRIVY_ARGUMENT_LOG:?}"\n'
    'report_file=""\n'
    'previous=""\n'
    'for argument in "$@"; do\n'
    '  if [ "$previous" = output ]; then report_file="$argument"; previous=""; continue; fi\n'
    '  if [ "$argument" = --output ]; then previous=output; fi\n'
    'done\n'
    '[ -n "$report_file" ]\n'
    'clean_report() { printf \'%s\\n\' \'{"Results":[]}\' > "$report_file"; }\n'
    "vulnerability_report() {\n"
    "  printf '%s\\n' \"{\\\"Results\\\":[{\\\"Target\\\":\\\"fake-image\\\",\\\"Vulnerabilities\\\":[{\\\"VulnerabilityID\\\":\\\"CVE-2099-0001\\\",\\\"Severity\\\":\\\"$1\\\",\\\"PkgName\\\":\\\"openssl\\\",\\\"InstalledVersion\\\":\\\"3.0\\\",\\\"FixedVersion\\\":\\\"4.0\\\"}]}]}\" > \"$report_file\"\n"
    "}\n"
    'case "${TRIVY_TEST_MODE:?}" in\n'
    "  throttle-once)\n"
    '    if [ "$count" -eq 1 ]; then\n'
    '      echo "FATAL GET manifest: TOOMANYREQUESTS: pull rate limit" >&2\n'
    "      exit 1\n"
    "    fi\n"
    "    clean_report; exit 0 ;;\n"
    "  throttle-multiline-once)\n"
    '    if [ "$count" -eq 1 ]; then\n'
    '      echo "FATAL Fatal error: image scan error" >&2\n'
    '      echo "  * remote error: GET manifest: TOOMANYREQUESTS: pull rate limit" >&2\n'
    "      exit 1\n"
    "    fi\n"
    "    clean_report; exit 0 ;;\n"
    "  throttle-before-fatal-once)\n"
    '    if [ "$count" -eq 1 ]; then\n'
    '      echo "ERROR registry remote error: GET manifest: TOOMANYREQUESTS" >&2\n'
    '      echo "FATAL Fatal error" >&2\n'
    "      exit 1\n"
    "    fi\n"
    "    clean_report; exit 0 ;;\n"
    "  http-429-before-fatal-once)\n"
    '    if [ "$count" -eq 1 ]; then\n'
    '      echo "ERROR remote registry returned HTTP 429" >&2\n'
    '      echo "FATAL Fatal error" >&2\n'
    "      exit 1\n"
    "    fi\n"
    "    clean_report; exit 0 ;;\n"
    "  standalone-http-after-fatal-once)\n"
    '    if [ "$count" -eq 1 ]; then\n'
    '      echo "FATAL image scan error" >&2\n'
    '      echo "HTTP/1.1 429 Too Many Requests" >&2\n'
    "      exit 1\n"
    "    fi\n"
    "    clean_report; exit 0 ;;\n"
    "  standalone-http-before-fatal-once)\n"
    '    if [ "$count" -eq 1 ]; then\n'
    '      echo "HTTP 429" >&2\n'
    '      echo "FATAL image scan error" >&2\n'
    "      exit 1\n"
    "    fi\n"
    "    clean_report; exit 0 ;;\n"
    "  standalone-full-phrase-after-fatal-once)\n"
    '    if [ "$count" -eq 1 ]; then\n'
    '      echo "FATAL image scan error" >&2\n'
    '      echo "429 Too Many Requests" >&2\n'
    "      exit 1\n"
    "    fi\n"
    "    clean_report; exit 0 ;;\n"
    "  mixedcase-no-newline-once)\n"
    '    if [ "$count" -eq 1 ]; then\n'
    '      echo "FaTaL Fatal error: image scan error" >&2\n'
    '      printf "* remote error: GET manifest: toomanyrequests" >&2\n'
    "      exit 1\n"
    "    fi\n"
    "    clean_report; exit 0 ;;\n"
    "  pull-rate-only-once)\n"
    '    if [ "$count" -eq 1 ]; then\n'
    '      echo "FATAL remote registry pull rate limit" >&2\n'
    "      exit 1\n"
    "    fi\n"
    "    clean_report; exit 0 ;;\n"
    "  get-url-numeric-once)\n"
    '    if [ "$count" -eq 1 ]; then\n'
    '      echo "FATAL download failed" >&2\n'
    '      echo "GET https://example.invalid/v2/foo returned HTTP 429" >&2\n'
    "      exit 1\n"
    "    fi\n"
    "    clean_report; exit 0 ;;\n"
    "  unrelated-separated-throttle)\n"
    '    echo "FATAL disk or cache corruption" >&2\n'
    '    echo "ordinary unrelated diagnostic" >&2\n'
    '    echo "warning: prior request got 429 Too Many Requests" >&2\n'
    "    exit 37 ;;\n"
    "  blank-separated-throttle)\n"
    '    echo "FATAL disk or cache corruption" >&2\n'
    '    echo "" >&2\n'
    '    echo "warning: prior request got 429 Too Many Requests" >&2\n'
    "    exit 38 ;;\n"
    "  unrelated-adjacent-429)\n"
    '    echo "FATAL disk or cache corruption" >&2\n'
    '    echo "warning: a prior unrelated request got 429 Too Many Requests" >&2\n'
    "    exit 39 ;;\n"
    "  digest-contains-429)\n"
    '    echo "FATAL cache corruption" >&2\n'
    '    echo "manifest digest sha256:abc429def failed verification" >&2\n'
    "    exit 40 ;;\n"
    "  status-code-1429)\n"
    '    echo "FATAL cache corruption" >&2\n'
    '    echo "registry returned status code 1429" >&2\n'
    "    exit 41 ;;\n"
    "  artifact-byte-429)\n"
    '    echo "FATAL local database corruption" >&2\n'
    '    echo "artifact parser failed at byte 429" >&2\n'
    "    exit 42 ;;\n"
    "  status-code-429-suffix)\n"
    '    echo "FATAL cache corruption" >&2\n'
    '    echo "registry rejected malformed status code 429abc" >&2\n'
    "    exit 43 ;;\n"
    "  http-429-suffix)\n"
    '    echo "FATAL cache corruption" >&2\n'
    '    echo "registry rejected malformed HTTP 429error" >&2\n'
    "    exit 44 ;;\n"
    "  named-throttle-prefix)\n"
    '    echo "FATAL cache corruption" >&2\n'
    '    echo "registry rejected NOTOOMANYREQUESTS" >&2\n'
    "    exit 45 ;;\n"
    "  named-throttle-suffix)\n"
    '    echo "FATAL cache corruption" >&2\n'
    '    echo "registry rejected TOOMANYREQUESTS_FAKE" >&2\n'
    "    exit 46 ;;\n"
    "  pull-rate-limiter)\n"
    '    echo "FATAL cache corruption" >&2\n'
    '    echo "registry pull rate limiter engaged" >&2\n'
    "    exit 47 ;;\n"
    "  split-pull-rate-limit)\n"
    '    echo "FATAL failed to pull" >&2\n'
    '    echo "rate limit policy is disabled" >&2\n'
    "    exit 48 ;;\n"
    "  split-http-429)\n"
    '    echo "FATAL image scan error HTTP" >&2\n'
    '    echo "429 cache record is corrupt" >&2\n'
    "    exit 49 ;;\n"
    "  split-status-code-429)\n"
    '    echo "FATAL image scan error status" >&2\n'
    '    echo "code 429 cache record is corrupt" >&2\n'
    "    exit 50 ;;\n"
    "  split-full-phrase)\n"
    '    echo "FATAL image scan error 429 Too" >&2\n'
    '    echo "Many Requests were recorded previously" >&2\n'
    "    exit 51 ;;\n"
    "  prefixed-registry-context)\n"
    '    echo "FATAL cache corruption" >&2\n'
    '    echo "notregistry HTTP 429 Too Many Requests" >&2\n'
    "    exit 52 ;;\n"
    "  prefixed-manifest-context)\n"
    '    echo "FATAL cache corruption" >&2\n'
    '    echo "manifestation status code 429" >&2\n'
    "    exit 53 ;;\n"
    "  prefixed-artifact-context)\n"
    '    echo "FATAL cache corruption" >&2\n'
    '    echo "nonartifact HTTP 429" >&2\n'
    "    exit 54 ;;\n"
    "  throttle-then-vulnerabilities)\n"
    '    if [ "$count" -eq 1 ]; then\n'
    '      echo "FATAL GET manifest: TOOMANYREQUESTS: pull rate limit" >&2\n'
    "      exit 1\n"
    "    fi\n"
    "    vulnerability_report CRITICAL; exit 1 ;;\n"
    "  mixed-vulnerability-throttle)\n"
    "    vulnerability_report CRITICAL\n"
    '    echo "FATAL GET manifest: 429 Too Many Requests" >&2\n'
    "    exit 1 ;;\n"
    "  malformed-report-throttle)\n"
    '    printf \'{\' > "$report_file"\n'
    '    echo "FATAL GET manifest: 429 Too Many Requests" >&2\n'
    "    exit 1 ;;\n"
    "  empty-success) exit 0 ;;\n"
    '  empty-object-success) printf \'{}\\n\' > "$report_file"; exit 0 ;;\n'
    '  null-success) printf \'null\\n\' > "$report_file"; exit 0 ;;\n'
    '  invalid-shape-success) printf \'{"Results":"invalid"}\\n\' > "$report_file"; exit 0 ;;\n'
    "  invalid-shape-throttle)\n"
    '    printf \'{"Results":"invalid"}\\n\' > "$report_file"\n'
    '    echo "FATAL GET manifest: 429 Too Many Requests" >&2\n'
    "    exit 1 ;;\n"
    "  jq-classification-error) clean_report; exit 0 ;;\n"
    "  status-zero-fatal)\n"
    "    clean_report\n"
    '    echo "FATAL GET manifest: 429 Too Many Requests" >&2\n'
    "    exit 0 ;;\n"
    "  cleanup-success) clean_report; exit 0 ;;\n"
    "  cleanup-failure) vulnerability_report HIGH; exit 1 ;;\n"
    "  throttle-always)\n"
    '    echo "FATAL image scan error: unexpected status code 429 Too Many Requests" >&2\n'
    "    exit 1 ;;\n"
    "  vulnerabilities) vulnerability_report HIGH; exit 1 ;;\n"
    "esac\n"
)


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _install_fake_scan_commands(bin_dir: Path, tmp_path: Path, mode: str) -> None:
    _write_executable(bin_dir / "trivy", _FAKE_TRIVY)
    _write_executable(
        bin_dir / "sleep",
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "${TRIVY_SLEEP_LOG:?}"\n',
    )
    if mode == "jq-classification-error":
        jq_counter = tmp_path / "jq-counter"
        _write_executable(
            bin_dir / "jq",
            "#!/bin/sh\n"
            f'count="$(cat {str(jq_counter)!r} 2>/dev/null || printf 0)"\n'
            "count=$((count + 1))\n"
            f'printf "%s\\n" "$count" > {str(jq_counter)!r}\n'
            'if [ "$count" -eq 1 ]; then exit 0; fi\n'
            "exit 5\n",
        )
    if mode in {"cleanup-success", "cleanup-failure", "second-mktemp-failure"}:
        counter = tmp_path / "mktemp-counter"
        output_file = tmp_path / "fake-output"
        report_file = tmp_path / "fake-report"
        second_action = (
            "exit 71"
            if mode == "second-mktemp-failure"
            else f'touch {str(report_file)!r}; printf "%s\\n" {str(report_file)!r}'
        )
        _write_executable(
            bin_dir / "mktemp",
            "#!/bin/sh\n"
            f'count="$(cat {str(counter)!r} 2>/dev/null || printf 0)"\n'
            "count=$((count + 1))\n"
            f'printf "%s\\n" "$count" > {str(counter)!r}\n'
            f'if [ "$count" -eq 1 ]; then touch {str(output_file)!r}; printf "%s\\n" {str(output_file)!r}; exit 0; fi\n'
            f'if [ "$count" -eq 2 ]; then {second_action}; exit 0; fi\n'
            "exit 72\n",
        )
    elif mode == "mktemp-failure":
        _write_executable(bin_dir / "mktemp", "#!/bin/sh\nexit 71\n")


def _run_fake_remote_scan(tmp_path: Path, mode: str) -> _FakeRemoteScanResult:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _install_fake_scan_commands(bin_dir, tmp_path, mode)
    counter = tmp_path / "counter"
    arguments = tmp_path / "arguments"
    sleep_log = tmp_path / "sleep"
    environment = {
        **os.environ,
        "PATH": str(bin_dir) if mode == "jq-absent" else f"{bin_dir}:{os.environ['PATH']}",
        "TRIVY_TEST_MODE": mode,
        "TRIVY_COUNTER_FILE": str(counter),
        "TRIVY_ARGUMENT_LOG": str(arguments),
        "TRIVY_SLEEP_LOG": str(sleep_log),
    }
    result = subprocess.run(
        [
            "/bin/bash",
            str(TRIVY_REMOTE_SCAN),
            "cr.weaviate.io/semitechnologies/weaviate:1.38.13",
            "linux/arm64",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    return _FakeRemoteScanResult(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        counter=counter.read_text(encoding="utf-8") if counter.exists() else "0",
        arguments=arguments.read_text(encoding="utf-8") if arguments.exists() else "",
        sleeps=sleep_log.read_text(encoding="utf-8") if sleep_log.exists() else "",
        temporary_files_exist=(
            (tmp_path / "fake-output").exists(),
            (tmp_path / "fake-report").exists(),
        ),
    )


def _assert_scan_arguments(arguments: str, attempts: int) -> None:
    for option in (
        "--image-src remote",
        "--platform linux/arm64",
        "--severity HIGH,CRITICAL",
        "--scanners vuln",
        "--ignorefile .trivyignore.yaml",
        "--timeout 30m",
        "--format json",
        "--output ",
        "--exit-code 1",
    ):
        assert arguments.count(option) == attempts


@pytest.mark.parametrize(
    "mode",
    [
        "throttle-once",
        "throttle-multiline-once",
        "throttle-before-fatal-once",
        "http-429-before-fatal-once",
        "standalone-http-after-fatal-once",
        "standalone-http-before-fatal-once",
        "standalone-full-phrase-after-fatal-once",
        "mixedcase-no-newline-once",
        "pull-rate-only-once",
        "get-url-numeric-once",
    ],
)
def test_remote_scan_retries_only_a_bounded_registry_throttle(
    tmp_path: Path, mode: str
) -> None:
    result = _run_fake_remote_scan(tmp_path, mode)

    assert result.returncode == 0, result.stderr
    assert result.counter == "2\n"
    assert result.sleeps == "5\n"
    _assert_scan_arguments(result.arguments, attempts=2)
    assert "transient registry throttle" in result.stderr


@pytest.mark.parametrize("mode", ["vulnerabilities", "mixed-vulnerability-throttle"])
def test_remote_scan_never_retries_a_vulnerability_result(
    tmp_path: Path, mode: str
) -> None:
    result = _run_fake_remote_scan(tmp_path, mode)

    assert result.returncode == 1
    assert result.counter == "1\n"
    assert result.sleeps == ""
    assert "CVE-2099-0001\t" in result.stdout
    assert "transient registry throttle" not in result.stderr


def test_remote_scan_preserves_vulnerability_failure_after_throttle(tmp_path: Path) -> None:
    result = _run_fake_remote_scan(tmp_path, "throttle-then-vulnerabilities")

    assert result.returncode == 1
    assert result.counter == "2\n"
    assert result.sleeps == "5\n"
    assert "CVE-2099-0001\tCRITICAL" in result.stdout


@pytest.mark.parametrize(
    ("mode", "expected_status"),
    [
        ("unrelated-separated-throttle", 37),
        ("blank-separated-throttle", 38),
        ("unrelated-adjacent-429", 39),
        ("digest-contains-429", 40),
        ("status-code-1429", 41),
        ("artifact-byte-429", 42),
        ("status-code-429-suffix", 43),
        ("http-429-suffix", 44),
        ("named-throttle-prefix", 45),
        ("named-throttle-suffix", 46),
        ("pull-rate-limiter", 47),
        ("split-pull-rate-limit", 48),
        ("split-http-429", 49),
        ("split-status-code-429", 50),
        ("split-full-phrase", 51),
        ("prefixed-registry-context", 52),
        ("prefixed-manifest-context", 53),
        ("prefixed-artifact-context", 54),
    ],
)
def test_remote_scan_does_not_associate_nonadjacent_throttle_text(
    tmp_path: Path, mode: str, expected_status: int
) -> None:
    result = _run_fake_remote_scan(tmp_path, mode)

    assert result.returncode == expected_status
    assert result.counter == "1\n"
    assert result.sleeps == ""
    assert "transient registry throttle" not in result.stderr


@pytest.mark.parametrize(
    "mode",
    [
        "malformed-report-throttle",
        "empty-success",
        "empty-object-success",
        "null-success",
        "invalid-shape-success",
        "invalid-shape-throttle",
    ],
)
def test_remote_scan_never_retries_an_invalid_structured_report(
    tmp_path: Path, mode: str
) -> None:
    result = _run_fake_remote_scan(tmp_path, mode)

    assert result.returncode != 0
    assert result.counter == "1\n"
    assert result.sleeps == ""
    assert "structured report" in result.stderr


def test_remote_scan_fails_closed_on_jq_classification_error(tmp_path: Path) -> None:
    result = _run_fake_remote_scan(tmp_path, "jq-classification-error")

    assert result.returncode == 2
    assert result.counter == "1\n"
    assert result.sleeps == ""
    assert "classification failed" in result.stderr


def test_remote_scan_fails_closed_on_zero_status_with_fatal_output(tmp_path: Path) -> None:
    result = _run_fake_remote_scan(tmp_path, "status-zero-fatal")

    assert result.returncode == 2
    assert result.counter == "1\n"
    assert result.sleeps == ""
    assert "fatal diagnostic" in result.stderr


def test_remote_scan_stops_after_three_registry_throttle_attempts(
    tmp_path: Path,
) -> None:
    result = _run_fake_remote_scan(tmp_path, "throttle-always")

    assert result.returncode == 1
    assert result.counter == "3\n"
    assert result.sleeps == "5\n10\n"
    assert "failed after 3 attempts" in result.stderr


def test_remote_scan_fails_closed_before_trivy_when_mktemp_fails(
    tmp_path: Path,
) -> None:
    result = _run_fake_remote_scan(tmp_path, "mktemp-failure")

    assert result.returncode == 2
    assert result.counter == "0"
    assert result.arguments == ""
    assert "could not create secure scan output" in result.stderr


def test_remote_scan_fails_closed_before_trivy_when_jq_is_absent(
    tmp_path: Path,
) -> None:
    result = _run_fake_remote_scan(tmp_path, "jq-absent")

    assert result.returncode == 2
    assert result.counter == "0"
    assert result.arguments == ""
    assert "jq is required" in result.stderr


def test_remote_scan_removes_first_tempfile_when_second_mktemp_fails(
    tmp_path: Path,
) -> None:
    result = _run_fake_remote_scan(tmp_path, "second-mktemp-failure")

    assert result.returncode == 2
    assert result.counter == "0"
    assert result.temporary_files_exist == (False, False)
    assert "could not create secure scan report" in result.stderr


@pytest.mark.parametrize(
    ("mode", "expected_status"),
    [("cleanup-success", 0), ("cleanup-failure", 1)],
)
def test_remote_scan_removes_both_tempfiles_on_exit(
    tmp_path: Path, mode: str, expected_status: int
) -> None:
    result = _run_fake_remote_scan(tmp_path, mode)

    assert result.returncode == expected_status
    assert result.temporary_files_exist == (False, False)
