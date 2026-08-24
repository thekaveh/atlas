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
import socket
import subprocess
from typing import Any, Sequence
import urllib.error
import urllib.parse
import urllib.request

import yaml
from utils import ollama_library


_MAX_DETAIL_LENGTH = 500
_REPORT_MARKER = "<!-- atlas-upstream-drift-watch -->"
_USER_AGENT = "Atlas upstream-drift-watch/1.0"
_DEFAULT_OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
_DEFAULT_REPORT_FILE = Path("upstream-drift-report.md")
# The watch only resolves registry manifests, but it still keeps this small so
# a caller cannot turn one nightly check into an unbounded registry fan-out.
_MAX_IMAGE_WORKERS = 8


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """The immutable result of one drift-watch probe."""

    name: str
    ok: bool
    detail: str


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
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


def load_curated_ollama_models(path: Path) -> tuple[str, ...]:
    """Return all named models in the curated Ollama catalog.

    The catalog is organized into role sections (content, embeddings,
    vision, and future additions).  Names are treated as artifact references,
    so multimodal entries appearing in more than one section are collapsed.
    """

    document = _read_yaml_mapping(path)
    names: list[str] = []
    for section_name, section in document.items():
        if not isinstance(section, list):
            raise ValueError(f"{path}: {section_name} must be a list")
        for index, entry in enumerate(section):
            if not isinstance(entry, dict):
                raise ValueError(f"{path}: {section_name}[{index}] must be a mapping")
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"{path}: {section_name}[{index}].name must be a non-empty string")
            names.append(name.strip())
    return _sorted_unique(names)


def load_manifest_image_refs(services_dir: Path) -> tuple[str, ...]:
    """Return literal image defaults declared by service manifests."""

    refs: list[str] = []
    try:
        manifest_paths = sorted(services_dir.glob("*/service.yml"))
    except OSError as exc:
        raise ValueError(f"could not discover service manifests in {services_dir}: {exc}") from exc

    for manifest_path in manifest_paths:
        document = _read_yaml_mapping(manifest_path)
        images = document.get("images", [])
        if images is None:
            continue
        if not isinstance(images, list):
            raise ValueError(f"manifest {manifest_path} images must be a list")
        for index, image in enumerate(images):
            if not isinstance(image, dict):
                raise ValueError(f"{manifest_path}: images[{index}] must be a mapping")
            default = image.get("default")
            if not isinstance(default, str) or not default.strip():
                raise ValueError(
                    f"{manifest_path}: images[{index}].default must be a non-empty string"
                )
            reference = default.strip()
            if "$" in reference:
                raise ValueError(
                    f"{manifest_path}: images[{index}].default must be a literal image reference"
                )
            refs.append(reference)
    return _sorted_unique(refs)


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
    lines = [
        _REPORT_MARKER,
        "# Atlas upstream drift watch",
        "",
        f"Generated at: `{timestamp}`",
        "",
        f"Status: **{'FAIL' if failures else 'OK'}**",
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
        and math.isfinite(timeout)
        and timeout > 0
    )


def _valid_image_workers(workers: object) -> bool:
    return isinstance(workers, int) and not isinstance(workers, bool) and 1 <= workers <= _MAX_IMAGE_WORKERS


def _timeout_argument(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as exc:
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
    if not isinstance(document, dict):
        return ProbeResult("ollama tags", False, "response must be a JSON object")
    models = document.get("models")
    if not isinstance(models, list):
        return ProbeResult("ollama tags", False, "response.models must be a list")
    for index, model in enumerate(models):
        if not isinstance(model, dict):
            return ProbeResult("ollama tags", False, f"response.models[{index}] must be an object")
        names = (model.get("name"), model.get("model"))
        if not any(isinstance(name, str) and name.strip() for name in names):
            return ProbeResult(
                "ollama tags",
                False,
                f"response.models[{index}] needs a non-empty name or model",
            )
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


def run_watch(
    *,
    ollama_tags_url: str,
    services_dir: Path,
    ollama_models: Path,
    http_timeout: float,
    image_timeout: float,
    image_workers: int,
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
    return (
        probe_ollama_library(),
        probe_ollama_tags(ollama_tags_url, timeout=http_timeout),
        curated_result,
        images_result,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the watcher, write its report, and return aggregate health."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ollama-tags-url", default=_DEFAULT_OLLAMA_TAGS_URL)
    parser.add_argument("--services-dir", type=Path, default=Path("services"))
    parser.add_argument("--ollama-models", type=Path, default=Path("services/ollama/models.yaml"))
    parser.add_argument("--report-file", type=Path, default=_DEFAULT_REPORT_FILE)
    parser.add_argument("--http-timeout", type=_timeout_argument, default=5.0)
    parser.add_argument("--image-timeout", type=_timeout_argument, default=15.0)
    parser.add_argument("--image-workers", type=_image_workers_argument, default=4)
    args = parser.parse_args(argv)

    results = run_watch(
        ollama_tags_url=args.ollama_tags_url,
        services_dir=args.services_dir,
        ollama_models=args.ollama_models,
        http_timeout=args.http_timeout,
        image_timeout=args.image_timeout,
        image_workers=args.image_workers,
    )
    report = render_report(results, datetime.now(timezone.utc))
    args.report_file.write_text(report, encoding="utf-8")
    print(report, end="")
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
