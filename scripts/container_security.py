"""Validate container-scan policy and emit the manifest image inventory."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import re
import sys
from typing import Sequence

import yaml

from scripts.upstream_drift_watch import load_manifest_image_refs


ROOT = Path(__file__).resolve().parents[1]
_ALLOWED_ROOT_KEYS = frozenset({"vulnerabilities"})
_ALLOWED_EXCEPTION_KEYS = frozenset(
    {"id", "paths", "purls", "expired_at", "statement"}
)
_MAX_EXCEPTION_DAYS = 90
_PURL_RE = re.compile(
    r"^pkg:[a-z0-9.+-]+/[A-Za-z0-9._~%+/-]+@[A-Za-z0-9._~%+:-]+(?:\?[^*\[\]\s]+)?$"
)
_COMPOSE_IMAGE_RE = re.compile(
    r"^\$\{(?P<var>[A-Za-z_][A-Za-z0-9_]*)(?:(?::-|-)(?P<default>.+))?\}$"
)
_DEFAULT_SCAN_PLATFORMS = ("linux/amd64", "linux/arm64")
_PINNED_IMAGE_TAGS = (
    re.compile(r"^\d+\.\d+\.\d+(?:\.\d+)?(?:$|[-+._][A-Za-z0-9][A-Za-z0-9.+_-]*)$"),
    re.compile(r"^v\d+\.\d+\.\d+(?:$|[-+._][A-Za-z0-9][A-Za-z0-9.+_-]*)$"),
    re.compile(r"^RELEASE\.\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z$"),
    re.compile(r"^python-\d+\.\d+\.\d+(?:$|[-+._][A-Za-z0-9][A-Za-z0-9.+_-]*)$"),
    re.compile(r"^\d{2}\.\d{2}-py\d+$"),
    re.compile(r"^.+[-_]v?\d+\.\d+\.\d+$"),
)
_DIGEST_PIN = re.compile(r"@sha256:[0-9a-fA-F]{64}$")
_EXACT_IMAGE_RELEASES = (
    re.compile(r"^postgres:\d+\.\d+-alpine$"),
    re.compile(r"^trinodb/trino:\d+$"),
)


@dataclass(frozen=True, slots=True)
class VulnerabilityException:
    vulnerability_id: str
    paths: tuple[str, ...]
    purls: tuple[str, ...]
    expired_at: date
    statement: str


@dataclass(frozen=True, slots=True)
class ImageScan:
    image: str
    platform: str


@dataclass(frozen=True, slots=True)
class ComposeBuild:
    context: str
    dockerfile: str

    @property
    def target(self) -> str:
        return f"{self.context}|{self.dockerfile}"


def load_image_inventory(services_dir: Path) -> tuple[str, ...]:
    """Return the sorted, de-duplicated defaults owned by service manifests."""

    images = load_manifest_image_refs(services_dir)
    floating = [image for image in images if not image_reference_is_pinned(image)]
    if floating:
        raise ValueError(
            "Manifest image defaults contain floating or untagged references: "
            + ", ".join(floating)
        )
    return images


def image_reference_is_pinned(image: str) -> bool:
    """Return whether an image uses an immutable digest or exact release tag."""

    if _DIGEST_PIN.search(image):
        return True
    final_component = image.rsplit("/", 1)[-1]
    if ":" not in final_component:
        return False
    tag = final_component.rsplit(":", 1)[1]
    return any(pattern.match(tag) for pattern in _PINNED_IMAGE_TAGS) or any(
        pattern.match(image) for pattern in _EXACT_IMAGE_RELEASES
    )


def _manifest_image_variables(services_dir: Path) -> dict[str, str]:
    variables: dict[str, str] = {}
    for manifest_path in sorted(services_dir.glob("*/service.yml")):
        document = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        for row in document.get("images", []):
            variables[row["var"]] = row["default"]
    return variables


def load_compose_image_refs(services_dir: Path) -> tuple[str, ...]:
    """Independently resolve remote final images declared by Compose fragments."""

    variables = _manifest_image_variables(services_dir)
    refs: set[str] = set()
    for compose_path in sorted(services_dir.glob("*/compose.yml")):
        refs.update(_compose_image_refs(compose_path, variables))
    return tuple(sorted(refs))


def _resolve_compose_image(image: str, variables: dict[str, str], *, owner: str) -> str:
    match = _COMPOSE_IMAGE_RE.fullmatch(image.strip())
    if match:
        resolved = match.group("default") or variables.get(match.group("var"))
        if resolved is None:
            raise ValueError(
                f"{owner} image variable {match.group('var')} has no manifest default"
            )
        return resolved
    if "${" not in image:
        return image.strip()
    raise ValueError(f"{owner} uses an unsupported image expression")


def _compose_image_refs(
    compose_path: Path, variables: dict[str, str]
) -> set[str]:
    refs: set[str] = set()
    document = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
    for name, service in (document.get("services") or {}).items():
        if not isinstance(service, dict) or "build" in service:
            continue
        image = service.get("image")
        if not isinstance(image, str) or not image.strip():
            raise ValueError(f"{compose_path}: service {name} lacks a valid image")
        refs.add(
            _resolve_compose_image(
                image, variables, owner=f"{compose_path}: service {name}"
            )
        )
    return refs


def _validate_build_image_args(
    compose_path: Path,
    service_name: str,
    build: dict,
    variables: dict[str, str],
) -> None:
    args = build.get("args") or {}
    if not isinstance(args, dict):
        raise ValueError(f"{compose_path}: service {service_name} build.args must be a mapping")
    for name, value in args.items():
        if not str(name).endswith("IMAGE"):
            continue
        if not isinstance(value, str):
            raise ValueError(
                f"{compose_path}: service {service_name} build arg {name} must be a string"
            )
        resolved = _resolve_compose_image(
            value,
            variables,
            owner=f"{compose_path}: service {service_name} build arg {name}",
        )
        if not image_reference_is_pinned(resolved):
            raise ValueError(
                f"{compose_path}: service {service_name} build arg {name} "
                f"contains floating or untagged image {resolved!r}"
            )


def load_image_scans(services_dir: Path) -> tuple[ImageScan, ...]:
    """Return reconciled image scans for every supported image architecture."""

    platforms: dict[str, set[str]] = {}
    for manifest_path in sorted(services_dir.glob("*/service.yml")):
        document = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        for row in document.get("images", []):
            image = row["default"]
            declared = row.get("platform")
            platforms.setdefault(image, set()).update(
                (declared,) if declared else _DEFAULT_SCAN_PLATFORMS
            )
    inventory = load_image_inventory(services_dir)
    compose_images = load_compose_image_refs(services_dir)
    missing = sorted(set(compose_images) - set(inventory))
    if missing:
        raise ValueError(
            "Compose remote images missing from manifest scan inventory: "
            + ", ".join(missing)
        )
    return tuple(
        ImageScan(image=image, platform=platform)
        for image in inventory
        for platform in sorted(platforms[image])
    )


def load_changed_image_scans(
    services_dir: Path, changed_paths: Sequence[str]
) -> tuple[ImageScan, ...]:
    """Return scans owned by changed service manifests or Compose fragments."""

    selected_manifest_services, changed_compose_services = _changed_services(
        changed_paths
    )
    if not selected_manifest_services and not changed_compose_services:
        return ()

    selected_images = _selected_manifest_images(
        services_dir, selected_manifest_services
    )
    variables = _manifest_image_variables(services_dir)
    for service in changed_compose_services:
        compose_path = services_dir / service / "compose.yml"
        if compose_path.is_file():
            selected_images.update(_compose_image_refs(compose_path, variables))
    return tuple(
        scan for scan in load_image_scans(services_dir) if scan.image in selected_images
    )


def _changed_services(
    changed_paths: Sequence[str],
) -> tuple[set[str], set[str]]:
    selected_manifest_services: set[str] = set()
    changed_compose_services: set[str] = set()
    for raw_path in changed_paths:
        parts = Path(raw_path.strip()).parts
        if len(parts) < 3 or parts[0] != "services":
            continue
        if parts[2] == "service.yml":
            selected_manifest_services.add(parts[1])
        elif parts[2] == "compose.yml":
            changed_compose_services.add(parts[1])
    return selected_manifest_services, changed_compose_services


def _selected_manifest_images(
    services_dir: Path, selected_services: set[str]
) -> set[str]:
    selected_images: set[str] = set()
    for service in selected_services:
        path = services_dir / service / "service.yml"
        if not path.is_file():
            continue
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        selected_images.update(row["default"] for row in document.get("images", []))
    return selected_images


def load_compose_builds(services_dir: Path) -> tuple[ComposeBuild, ...]:
    """Derive unique final-image build targets from every Compose fragment."""

    root = services_dir.parent.resolve()
    variables = _manifest_image_variables(services_dir)
    builds: dict[str, ComposeBuild] = {}
    for compose_path in sorted(services_dir.glob("*/compose.yml")):
        document = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
        for service_name, service in (document.get("services") or {}).items():
            if not isinstance(service, dict) or "build" not in service:
                continue
            build = service["build"]
            if isinstance(build, str):
                context, dockerfile = build, "Dockerfile"
            else:
                _validate_build_image_args(
                    compose_path, service_name, build, variables
                )
                context = build.get("context", ".")
                dockerfile = build.get("dockerfile", "Dockerfile")
            if "://" not in context:
                context = (compose_path.parent / context).resolve().relative_to(root).as_posix()
            target = ComposeBuild(context=context, dockerfile=dockerfile)
            builds[target.target] = target
    return tuple(builds[key] for key in sorted(builds))


def load_build_exclusions(path: Path, *, today: date | None = None) -> tuple[str, ...]:
    """Load explicit, reasoned, expiring final-image scan exclusions."""

    review_date = today or date.today()
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = document.get("builds")
    allowed_keys = {"builds", "remote_base_digests"}
    if set(document) - allowed_keys or not isinstance(rows, list):
        raise ValueError(
            f"{path} must contain a builds list and only reviewed scan-control keys"
        )
    targets: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {"target", "reason", "expired_at"}:
            raise ValueError(f"builds[{index}] must define target, reason, and expired_at")
        target = row["target"]
        reason = row["reason"]
        if not isinstance(target, str) or target.count("|") != 1:
            raise ValueError(f"builds[{index}].target must be context|dockerfile")
        if not isinstance(reason, str) or len(reason.strip()) < 20:
            raise ValueError(f"builds[{index}].reason must be substantive")
        expired_at = _expiry(row["expired_at"], row=index)
        if expired_at <= review_date or (expired_at - review_date).days > _MAX_EXCEPTION_DAYS:
            raise ValueError(f"builds[{index}] has a stale or excessive review horizon")
        if target in targets:
            raise ValueError(f"builds[{index}] duplicates {target}")
        targets.append(target)
    return tuple(sorted(targets))


def _nonempty_strings(value: object, *, field: str, row: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not value:
        raise ValueError(f"vulnerabilities[{row}].{field} must be a non-empty list")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(
                f"vulnerabilities[{row}].{field}[{index}] must be a non-empty string"
            )
        result.append(item.strip())
    return tuple(result)


def _expiry(value: object, *, row: int) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(
                f"vulnerabilities[{row}].expired_at must be an ISO date"
            ) from exc
    raise ValueError(f"vulnerabilities[{row}].expired_at is required")


def load_exceptions(path: Path, *, today: date | None = None) -> tuple[VulnerabilityException, ...]:
    """Load narrow, reasoned, time-bounded Trivy suppressions.

    Trivy itself consumes the same YAML. This stricter validation prevents a
    CVE-only suppression from silently hiding the finding in every image.
    """

    review_date = today or date.today()
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"could not read container exception policy {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{path} must contain a mapping")
    unknown_root = set(document) - _ALLOWED_ROOT_KEYS
    if unknown_root:
        raise ValueError(f"{path} has unsupported sections: {sorted(unknown_root)}")
    rows = document.get("vulnerabilities", [])
    if not isinstance(rows, list):
        raise ValueError(f"{path}: vulnerabilities must be a list")

    exceptions: list[VulnerabilityException] = []
    identities: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"vulnerabilities[{index}] must be a mapping")
        unknown = set(row) - _ALLOWED_EXCEPTION_KEYS
        if unknown:
            raise ValueError(
                f"vulnerabilities[{index}] has unsupported fields: {sorted(unknown)}"
            )
        vulnerability_id = row.get("id")
        if not isinstance(vulnerability_id, str) or not vulnerability_id.strip():
            raise ValueError(f"vulnerabilities[{index}].id must be a non-empty string")
        statement = row.get("statement")
        if not isinstance(statement, str) or len(statement.strip()) < 20:
            raise ValueError(
                f"vulnerabilities[{index}].statement must be a substantive review rationale"
            )
        paths = _nonempty_strings(row.get("paths"), field="paths", row=index)
        purls = _nonempty_strings(row.get("purls"), field="purls", row=index)
        if not paths and not purls:
            raise ValueError(
                f"vulnerabilities[{index}] must scope the exception with paths or purls"
            )
        for path_scope in paths:
            if (
                path_scope in {"/", ".", ".."}
                or path_scope.startswith("/")
                or any(char in path_scope for char in "*?[]")
                or ".." in Path(path_scope).parts
            ):
                raise ValueError(
                    f"vulnerabilities[{index}].paths must contain exact relative paths"
                )
        for purl in purls:
            if not _PURL_RE.fullmatch(purl):
                raise ValueError(
                    f"vulnerabilities[{index}].purls must contain exact versioned PURLs"
                )
        expired_at = _expiry(row.get("expired_at"), row=index)
        if expired_at <= review_date:
            raise ValueError(
                f"vulnerabilities[{index}] expired on {expired_at.isoformat()}"
            )
        if (expired_at - review_date).days > _MAX_EXCEPTION_DAYS:
            raise ValueError(
                f"vulnerabilities[{index}] review horizon exceeds {_MAX_EXCEPTION_DAYS} days"
            )
        identity = (vulnerability_id.strip(), paths, purls)
        if identity in identities:
            raise ValueError(f"vulnerabilities[{index}] duplicates an earlier exception")
        identities.add(identity)
        exceptions.append(
            VulnerabilityException(
                vulnerability_id=vulnerability_id.strip(),
                paths=paths,
                purls=purls,
                expired_at=expired_at,
                statement=statement.strip(),
            )
        )
    return tuple(exceptions)


def render_images_json(images: Sequence[str]) -> str:
    return json.dumps(list(images), separators=(",", ":"))


def render_scan_matrix_json(scans: Sequence[ImageScan]) -> str:
    return json.dumps(
        [{"image": scan.image, "platform": scan.platform} for scan in scans],
        separators=(",", ":"),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--services-dir", type=Path, default=ROOT / "services")
    parser.add_argument("--ignorefile", type=Path, default=ROOT / ".trivyignore.yaml")
    parser.add_argument(
        "--build-exclusions",
        type=Path,
        default=ROOT / ".container-scan-exclusions.yml",
    )
    parser.add_argument("--images-json", action="store_true")
    parser.add_argument(
        "--changed-paths-stdin",
        action="store_true",
        help="Limit image output to service.yml/compose.yml owners read from stdin.",
    )
    args = parser.parse_args(argv)

    exceptions = load_exceptions(args.ignorefile)
    build_exclusions = load_build_exclusions(args.build_exclusions)
    scans = (
        load_changed_image_scans(args.services_dir, sys.stdin.read().splitlines())
        if args.changed_paths_stdin
        else load_image_scans(args.services_dir)
    )
    if args.images_json:
        print(render_scan_matrix_json(scans))
    else:
        print(
            f"Container security policy OK: {len(scans)} manifest image(s), "
            f"{len(exceptions)} active exception(s), "
            f"{len(build_exclusions)} reviewed build exclusion(s)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
