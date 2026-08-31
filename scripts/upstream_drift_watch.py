"""Bounded source discovery and live probes for Atlas's upstream drift watch."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import http.client
import json
import math
from pathlib import Path
import re
import socket
import subprocess
from typing import Any, Sequence
import urllib.error
import urllib.parse
import urllib.request

import yaml
from utils import ollama_library


_MAX_DETAIL_LENGTH = 450
_REPORT_MARKER = "<!-- atlas-upstream-drift-watch -->"
_USER_AGENT = "Atlas upstream-drift-watch/1.0"
_DEFAULT_OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
_DEFAULT_REPORT_FILE = Path("upstream-drift-report.md")
_DEFAULT_REMOTE_BASE_DIGESTS = Path(".container-scan-exclusions.yml")
# The watch only resolves registry manifests, but it still keeps this small so
# a caller cannot turn one nightly check into an unbounded registry fan-out.
_MAX_IMAGE_WORKERS = 8
# Both HTTP and image requests use this practical ceiling; it is well above
# the 5s/15s defaults while remaining representable by their wait APIs.
_MAX_TIMEOUT_SECONDS = 60.0
_MAX_REMOTE_DOCKERFILE_BYTES = 256 * 1024
_OLLAMA_MODEL_SECTIONS = ("content", "embeddings", "vision")
_REMOTE_GITHUB_CONTEXT = re.compile(
    r"^https://github\.com/(?P<repository>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\.git"
    r"#\$\{(?P<variable>[A-Z][A-Z0-9_]*)\}:(?P<subdir>[^:#]+)$"
)
_DOCKERFILE_HEREDOC_INSTRUCTION = re.compile(
    r"^\s*(?:ONBUILD\s+)?(?:RUN|COPY|ADD)(?:\s+(?P<payload>.*)|\s*)$",
    re.IGNORECASE,
)
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """The immutable result of one drift-watch probe."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class RemoteBuildContext:
    """A manifest-pinned GitHub build context and its Dockerfile path."""

    repository: str
    ref: str
    subdir: str
    dockerfile: str


def dockerfile_instruction_can_contain_heredoc(instruction: str) -> bool:
    """Mirror BuildKit's heredoc-capable command and JSON-form gate."""

    match = _DOCKERFILE_HEREDOC_INSTRUCTION.fullmatch(instruction)
    if match is None:
        return False
    payload = (match.group("payload") or "").lstrip()
    if not payload.startswith("["):
        return True
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError:
        return True
    return not (
        isinstance(decoded, list)
        and all(isinstance(value, str) for value in decoded)
    )


def _read_yaml_mapping(path: Path) -> dict[Any, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"could not read YAML source {path}: {exc}") from exc
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"YAML source {path} must contain a mapping")
    return value


def _sorted_unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


def _section_key_label(key: object) -> str:
    if isinstance(key, str):
        return key
    return f"{type(key).__name__}({key!r})"


def _unknown_section_labels(document: dict[Any, Any]) -> tuple[str, ...]:
    return tuple(
        sorted(_section_key_label(key) for key in document if key not in _OLLAMA_MODEL_SECTIONS)
    )


def load_curated_ollama_models(path: Path) -> tuple[str, ...]:
    """Return all named models in the curated Ollama catalog.

    The catalog is organized into the canonical content, embeddings, and
    vision role sections. Names are treated as artifact references, so
    multimodal entries appearing in more than one section are collapsed.
    """

    document = _read_yaml_mapping(path)
    unknown_sections = _unknown_section_labels(document)
    if unknown_sections:
        raise ValueError(f"{path}: unknown top-level section: {', '.join(unknown_sections)}")
    names: list[str] = []
    for section_name in _OLLAMA_MODEL_SECTIONS:
        section = document.get(section_name, [])
        if not isinstance(section, list):
            raise ValueError(f"{path}: {section_name} must be a list")
        for index, entry in enumerate(section):
            if not isinstance(entry, dict):
                raise ValueError(f"{path}: {section_name}[{index}] must be a mapping")
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"{path}: {section_name}[{index}].name must be a non-empty string")
            names.append(name.strip())
    if not names:
        raise ValueError(f"{path}: expected at least one curated Ollama model")
    return _sorted_unique(names)


def _literal_image_default(manifest_path: Path, index: int, image: dict[str, Any]) -> str:
    values: dict[str, str] = {}
    for field in ("var", "container", "default"):
        value = image.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"{manifest_path}: images[{index}].{field} must be a non-empty string"
            )
        values[field] = value.strip()
        if "$" in values[field]:
            raise ValueError(f"{manifest_path}: images[{index}].{field} must be a literal value")
    return values["default"]


def load_manifest_image_refs(services_dir: Path) -> tuple[str, ...]:
    """Return literal image defaults declared by service manifests."""

    if not services_dir.is_dir():
        raise ValueError(f"services inventory {services_dir} must be an existing directory")
    refs: list[str] = []
    try:
        manifest_paths = sorted(services_dir.glob("*/service.yml"))
    except OSError as exc:
        raise ValueError(f"could not discover service manifests in {services_dir}: {exc}") from exc
    if not manifest_paths:
        raise ValueError(f"services inventory {services_dir} contains no service manifests")

    for manifest_path in manifest_paths:
        document = _read_yaml_mapping(manifest_path)
        if "images" not in document:
            continue
        images = document["images"]
        if not isinstance(images, list):
            raise ValueError(f"manifest {manifest_path} images must be a list")
        for index, image in enumerate(images):
            if not isinstance(image, dict):
                raise ValueError(f"{manifest_path}: images[{index}] must be a mapping")
            refs.append(_literal_image_default(manifest_path, index, image))
    if not refs:
        raise ValueError(f"services inventory {services_dir} must declare at least one image reference")
    return _sorted_unique(refs)


def load_expected_remote_base_digests(path: Path) -> dict[str, str]:
    """Load the reviewed digest baseline for mutable remote-context bases."""

    document = _read_yaml_mapping(path).get("remote_base_digests")
    if not isinstance(document, dict) or not document:
        raise ValueError(
            f"{path}: remote_base_digests must be a non-empty image-to-digest mapping"
        )
    result: dict[str, str] = {}
    for reference, digest in document.items():
        if not isinstance(reference, str) or not reference or "$" in reference:
            raise ValueError(f"{path}: image references must be non-empty literals")
        if not isinstance(digest, str) or _IMAGE_DIGEST.fullmatch(digest) is None:
            raise ValueError(f"{path}: {reference} must map to a sha256 digest")
        result[reference] = digest
    return result


def load_reviewed_remote_base_refs(path: Path) -> tuple[str, ...]:
    """Return immutable image references for the reviewed remote bases."""

    if not path.is_file():
        return ()
    return tuple(
        f"{reference}@{digest}"
        for reference, digest in sorted(
            load_expected_remote_base_digests(path).items()
        )
    )


def _manifest_env_defaults(manifest_path: Path) -> dict[str, str]:
    document = _read_yaml_mapping(manifest_path)
    rows = document.get("env", [])
    if not isinstance(rows, list):
        raise ValueError(f"{manifest_path}: env must be a list")
    defaults: dict[str, str] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"{manifest_path}: env[{index}] must be a mapping")
        name = row.get("name")
        default = row.get("default")
        if isinstance(name, str) and isinstance(default, str):
            defaults[name] = default.strip()
    return defaults


def _compose_build_fields(
    compose_path: Path, service_name: object, service: object
) -> tuple[object, object] | None:
    if not isinstance(service, dict):
        raise ValueError(f"{compose_path}: services.{service_name} must be a mapping")
    build = service.get("build")
    if isinstance(build, str):
        return build, "Dockerfile"
    if isinstance(build, dict):
        return build.get("context"), build.get("dockerfile", "Dockerfile")
    return None


def validate_remote_build_context(
    context: str, owner: str
) -> re.Match[str]:
    """Return the sole reviewed remote-context match or fail closed."""

    match = _REMOTE_GITHUB_CONTEXT.fullmatch(context)
    if match is None:
        raise ValueError(f"{owner} is an unsupported or unpinned remote build context")
    return match


def _remote_build_context(
    compose_path: Path, service_name: object, service: object
) -> RemoteBuildContext | None:
    fields = _compose_build_fields(compose_path, service_name, service)
    if fields is None:
        return None
    context_value, dockerfile = fields
    if not isinstance(context_value, str) or "://" not in context_value:
        return None
    match = validate_remote_build_context(
        context_value, f"{compose_path}: services.{service_name}.build.context"
    )
    if not isinstance(dockerfile, str) or not dockerfile or "$" in dockerfile:
        raise ValueError(f"{compose_path}: services.{service_name}.build.dockerfile must be literal")
    variable = match.group("variable")
    ref = _manifest_env_defaults(compose_path.with_name("service.yml")).get(variable, "")
    if not _COMMIT_SHA.fullmatch(ref):
        raise ValueError(f"{compose_path}: {variable} default must be a 40-character commit SHA")
    return RemoteBuildContext(
        repository=match.group("repository"),
        ref=ref,
        subdir=match.group("subdir").strip("/"),
        dockerfile=dockerfile.strip("/"),
    )


def load_remote_build_contexts(services_dir: Path) -> tuple[RemoteBuildContext, ...]:
    """Discover GitHub build contexts whose refs are manifest-owned commit pins."""

    if not services_dir.is_dir():
        raise ValueError(f"services inventory {services_dir} must be an existing directory")
    contexts: set[RemoteBuildContext] = set()
    for compose_path in sorted(services_dir.glob("*/compose.yml")):
        services = _read_yaml_mapping(compose_path).get("services", {})
        if not isinstance(services, dict):
            raise ValueError(f"{compose_path}: services must be a mapping")
        for service_name, service in services.items():
            context = _remote_build_context(compose_path, service_name, service)
            if context is not None:
                contexts.add(context)
    return tuple(
        sorted(
            contexts,
            key=lambda item: (item.repository, item.ref, item.subdir, item.dockerfile),
        )
    )


def _bounded_detail(detail: str) -> str:
    normalized = " ".join(str(detail).split())
    if len(normalized) <= _MAX_DETAIL_LENGTH:
        return normalized
    return normalized[: _MAX_DETAIL_LENGTH - 1] + "…"


def render_report(results: Sequence[ProbeResult], generated_at: datetime) -> str:
    """Render a stable Markdown report suitable for an issue body."""

    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    timestamp = generated_at.astimezone(timezone.utc).isoformat()
    failures = [result for result in results if not result.ok]
    total = len(results)
    failed = len(failures)
    passed = total - failed
    lines = [
        _REPORT_MARKER,
        "# Atlas upstream drift watch",
        "",
        f"Generated at: `{timestamp}`",
        "",
        f"Status: **{'FAIL' if failures else 'OK'}**",
        "",
        f"Summary: **{passed} passed, {failed} failed, {total} total**",
        "",
        "## Probe results",
        "",
    ]
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        lines.extend(
            [
                f"### {result.name}",
                "",
                f"- Status: **{status}**",
                f"- Detail: {_bounded_detail(result.detail)}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _response_status(response: Any) -> int:
    """Return the HTTP response status for real and test-double responses."""

    status = getattr(response, "status", None)
    if status is None:
        status = response.getcode()
    return int(status)


def _http_failure_detail(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}: {exc.reason}"
    return f"request failed: {exc}"


def _valid_timeout(timeout: object) -> bool:
    return (
        isinstance(timeout, (int, float))
        and not isinstance(timeout, bool)
        and timeout > 0
        and timeout <= _MAX_TIMEOUT_SECONDS
        and math.isfinite(timeout)
    )


def _valid_image_workers(workers: object) -> bool:
    return isinstance(workers, int) and not isinstance(workers, bool) and 1 <= workers <= _MAX_IMAGE_WORKERS


def _timeout_argument(value: str) -> float:
    try:
        timeout = float(value)
    except (ValueError, OverflowError) as exc:
        raise argparse.ArgumentTypeError("must be a positive finite number") from exc
    if not _valid_timeout(timeout):
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return timeout


def _image_workers_argument(value: str) -> int:
    try:
        workers = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"must be an integer from 1 to {_MAX_IMAGE_WORKERS}") from exc
    if not _valid_image_workers(workers):
        raise argparse.ArgumentTypeError(f"must be an integer from 1 to {_MAX_IMAGE_WORKERS}")
    return workers


def probe_ollama_library() -> ProbeResult:
    """Check that the live Ollama library remains plausibly populated."""

    entries = ollama_library.list_library_entries()
    observed = len(entries)
    required = ollama_library.MIN_PLAUSIBLE_ENTRIES
    if observed < required:
        return ProbeResult(
            "ollama library",
            False,
            f"observed {observed} entries; require at least {required}",
        )
    return ProbeResult("ollama library", True, f"observed {observed} entries")


def _validate_ollama_tags_document(document: object) -> str | None:
    """Return a contract violation for an Ollama tags document, if any."""

    if not isinstance(document, dict):
        return "response must be a JSON object"
    models = document.get("models")
    if not isinstance(models, list):
        return "response.models must be a list"
    for index, model in enumerate(models):
        if not isinstance(model, dict):
            return f"response.models[{index}] must be an object"
        names = (model.get("name"), model.get("model"))
        if not any(isinstance(name, str) and name.strip() for name in names):
            return f"response.models[{index}] needs a non-empty name or model"
    return None


def probe_ollama_tags(url: str, *, timeout: float) -> ProbeResult:
    """Validate the bounded ``/api/tags`` response contract."""

    if not _valid_timeout(timeout):
        return ProbeResult("ollama tags", False, "timeout must be a positive finite number")
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = _response_status(response)
            if not 200 <= status < 300:
                return ProbeResult("ollama tags", False, f"HTTP {status}")
            payload = response.read()
    except (
        urllib.error.URLError,
        http.client.HTTPException,
        socket.timeout,
        ConnectionError,
        OSError,
        ValueError,
    ) as exc:
        return ProbeResult("ollama tags", False, _http_failure_detail(exc))

    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return ProbeResult("ollama tags", False, f"invalid JSON: {exc}")
    validation_error = _validate_ollama_tags_document(document)
    if validation_error:
        return ProbeResult("ollama tags", False, validation_error)
    models = document["models"]
    return ProbeResult("ollama tags", True, f"valid response with {len(models)} model(s)")


def probe_curated_models(models: Sequence[str], *, timeout: float) -> ProbeResult:
    """Check that every curated model family still has a public library page."""

    if not _valid_timeout(timeout):
        return ProbeResult("curated Ollama models", False, "timeout must be a positive finite number")
    failures: list[str] = []
    for model in models:
        family = model.split(":", 1)[0]
        encoded_family = urllib.parse.quote(family, safe="-._")
        try:
            request = urllib.request.Request(
                f"https://ollama.com/library/{encoded_family}",
                headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = _response_status(response)
                if not 200 <= status < 300:
                    failures.append(f"{model} (HTTP {status})")
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            socket.timeout,
            ConnectionError,
            OSError,
            ValueError,
        ) as exc:
            failures.append(f"{model} ({_http_failure_detail(exc)})")
    if failures:
        return ProbeResult("curated Ollama models", False, _bounded_detail("; ".join(failures)))
    return ProbeResult("curated Ollama models", True, f"checked {len(models)} model(s)")


def _inspect_image(reference: str, timeout: float) -> str | None:
    try:
        result = subprocess.run(
            ["docker", "buildx", "imagetools", "inspect", reference],
            timeout=timeout,
            check=False,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        return f"{reference} (timed out after {timeout}s)"
    except OSError as exc:
        return f"{reference} ({exc})"
    if result.returncode == 0:
        return None
    output = " ".join(part for part in (result.stdout, result.stderr) if part).strip()
    detail = f"{reference} (exit {result.returncode})"
    return f"{detail}: {_bounded_detail(output)}" if output else detail


def _resolve_image_digest(reference: str, timeout: float) -> tuple[str | None, str | None]:
    try:
        result = subprocess.run(
            ["docker", "buildx", "imagetools", "inspect", reference],
            timeout=timeout,
            check=False,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        return None, f"{reference} (timed out after {timeout}s)"
    except OSError as exc:
        return None, f"{reference} ({exc})"
    if result.returncode != 0:
        output = " ".join(part for part in (result.stdout, result.stderr) if part).strip()
        detail = f"{reference} (exit {result.returncode})"
        return None, f"{detail}: {_bounded_detail(output)}" if output else detail
    match = re.search(r"^Digest:\s+(sha256:[0-9a-f]{64})\s*$", result.stdout, re.MULTILINE)
    if match is None:
        return None, f"{reference} (inspect output contained no index digest)"
    return match.group(1), None


def probe_manifest_images(
    refs: Sequence[str], *, timeout: float, workers: int
) -> ProbeResult:
    """Resolve manifest-owned images without downloading their layers."""

    if not _valid_timeout(timeout):
        return ProbeResult("manifest images", False, "timeout must be a positive finite number")
    if not _valid_image_workers(workers):
        return ProbeResult(
            "manifest images",
            False,
            f"workers must be an integer from 1 to {_MAX_IMAGE_WORKERS}",
        )
    with ThreadPoolExecutor(max_workers=workers) as executor:
        failures = [
            failure
            for failure in executor.map(lambda reference: _inspect_image(reference, timeout), refs)
            if failure is not None
        ]
    if failures:
        return ProbeResult("manifest images", False, _bounded_detail("; ".join(failures)))
    return ProbeResult("manifest images", True, f"resolved {len(refs)} image(s)")


def _remote_dockerfile_url(context: RemoteBuildContext) -> str:
    path = "/".join((context.subdir, context.dockerfile))
    return f"https://raw.githubusercontent.com/{context.repository}/{context.ref}/{path}"


def _load_remote_dockerfile(context: RemoteBuildContext, timeout: float) -> str:
    request = urllib.request.Request(
        _remote_dockerfile_url(context),
        headers={"User-Agent": _USER_AGENT, "Accept": "text/plain"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = _response_status(response)
        if not 200 <= status < 300:
            raise ValueError(f"HTTP {status}")
        payload = response.read(_MAX_REMOTE_DOCKERFILE_BYTES + 1)
    if len(payload) > _MAX_REMOTE_DOCKERFILE_BYTES:
        raise ValueError(f"Dockerfile exceeds {_MAX_REMOTE_DOCKERFILE_BYTES} bytes")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Dockerfile is not UTF-8: {exc}") from exc


def _literal_dockerfile_bases(document: str) -> tuple[str, ...]:
    # Kept local to avoid a module-load cycle: container_security imports the
    # manifest inventory helpers from this module.
    from scripts.container_security import load_dockerfile_source_images

    return load_dockerfile_source_images(
        document,
        owner="remote Dockerfile",
        require_pinned=False,
        allow_variables=False,
    )


def probe_remote_build_contexts(
    contexts: Sequence[RemoteBuildContext], *, expected_digests: dict[str, str],
    http_timeout: float, image_timeout: float
) -> ProbeResult:
    """Validate pinned remote Dockerfiles and resolve every literal base image."""

    if not _valid_timeout(http_timeout) or not _valid_timeout(image_timeout):
        return ProbeResult(
            "remote build contexts", False, "timeouts must be positive finite numbers"
        )
    bases: set[str] = set()
    failures: list[str] = []
    for context in contexts:
        label = f"{context.repository}@{context.ref}:{context.subdir}/{context.dockerfile}"
        try:
            bases.update(_literal_dockerfile_bases(_load_remote_dockerfile(context, http_timeout)))
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            socket.timeout,
            ConnectionError,
            OSError,
            ValueError,
        ) as exc:
            failures.append(f"{label} ({_http_failure_detail(exc)})")
    if bases != set(expected_digests):
        missing = sorted(bases - set(expected_digests))
        stale = sorted(set(expected_digests) - bases)
        failures.append(f"digest baseline mismatch (missing={missing}, stale={stale})")
    for reference in sorted(bases & set(expected_digests)):
        digest, failure = _resolve_image_digest(reference, image_timeout)
        if failure is not None:
            failures.append(failure)
        elif digest != expected_digests[reference]:
            failures.append(
                f"{reference} digest drift: expected {expected_digests[reference]}, observed {digest}"
            )
    if failures:
        return ProbeResult(
            "remote build contexts", False, _bounded_detail("; ".join(failures))
        )
    return ProbeResult(
        "remote build contexts",
        True,
        f"validated {len(contexts)} pinned Dockerfile(s) and matched "
        f"{len(bases)} reviewed base-image digest(s)",
    )


def probe_configured_remote_build_contexts(
    *, services_dir: Path, remote_base_digests: Path,
    http_timeout: float, image_timeout: float
) -> ProbeResult:
    """Discover and validate the repository's pinned remote build inputs."""

    try:
        remote_contexts = load_remote_build_contexts(services_dir)
    except ValueError as exc:
        return ProbeResult(
            "remote build contexts", False, f"discovery failed: {exc}"
        )
    if not remote_contexts:
        return ProbeResult("remote build contexts", True, "no remote contexts")
    try:
        expected_digests = load_expected_remote_base_digests(
            remote_base_digests
        )
    except ValueError as exc:
        return ProbeResult(
            "remote build contexts", False, f"digest baseline failed: {exc}"
        )
    return probe_remote_build_contexts(
        remote_contexts,
        expected_digests=expected_digests,
        http_timeout=http_timeout,
        image_timeout=image_timeout,
    )


def run_watch(
    *,
    ollama_tags_url: str,
    services_dir: Path,
    ollama_models: Path,
    http_timeout: float,
    image_timeout: float,
    image_workers: int,
    remote_base_digests: Path = _DEFAULT_REMOTE_BASE_DIGESTS,
) -> tuple[ProbeResult, ...]:
    """Run every independent probe and retain all result details."""

    try:
        models = load_curated_ollama_models(ollama_models)
    except ValueError as exc:
        curated_result = ProbeResult("curated Ollama models", False, f"discovery failed: {exc}")
    else:
        curated_result = probe_curated_models(models, timeout=http_timeout)
    try:
        refs = load_manifest_image_refs(services_dir)
    except ValueError as exc:
        images_result = ProbeResult("manifest images", False, f"discovery failed: {exc}")
    else:
        images_result = probe_manifest_images(refs, timeout=image_timeout, workers=image_workers)
    remote_result = probe_configured_remote_build_contexts(
        services_dir=services_dir,
        remote_base_digests=remote_base_digests,
        http_timeout=http_timeout,
        image_timeout=image_timeout,
    )
    return (
        probe_ollama_library(),
        probe_ollama_tags(ollama_tags_url, timeout=http_timeout),
        curated_result,
        images_result,
        remote_result,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the watcher, write its report, and return aggregate health."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ollama-tags-url", default=_DEFAULT_OLLAMA_TAGS_URL)
    parser.add_argument("--services-dir", type=Path, default=Path("services"))
    parser.add_argument("--ollama-models", type=Path, default=Path("services/ollama/models.yaml"))
    parser.add_argument("--report-file", type=Path, default=_DEFAULT_REPORT_FILE)
    parser.add_argument(
        "--remote-base-digests", type=Path, default=_DEFAULT_REMOTE_BASE_DIGESTS
    )
    parser.add_argument("--http-timeout", type=_timeout_argument, default=5.0)
    parser.add_argument("--image-timeout", type=_timeout_argument, default=15.0)
    parser.add_argument("--image-workers", type=_image_workers_argument, default=4)
    parser.add_argument(
        "--remote-contexts-only",
        action="store_true",
        help="Run only the pinned remote-build source and digest contract.",
    )
    args = parser.parse_args(argv)

    if args.remote_contexts_only:
        results = (
            probe_configured_remote_build_contexts(
                services_dir=args.services_dir,
                remote_base_digests=args.remote_base_digests,
                http_timeout=args.http_timeout,
                image_timeout=args.image_timeout,
            ),
        )
    else:
        results = run_watch(
            ollama_tags_url=args.ollama_tags_url,
            services_dir=args.services_dir,
            ollama_models=args.ollama_models,
            http_timeout=args.http_timeout,
            image_timeout=args.image_timeout,
            image_workers=args.image_workers,
            remote_base_digests=args.remote_base_digests,
        )
    report = render_report(results, datetime.now(timezone.utc))
    args.report_file.write_text(report, encoding="utf-8")
    print(report, end="")
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
