"""Validate container-scan policy and emit the manifest image inventory."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import re
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

    return load_manifest_image_refs(services_dir)


def load_image_scans(services_dir: Path) -> tuple[ImageScan, ...]:
    """Return de-duplicated image scans with explicit single-arch metadata."""

    platforms: dict[str, str] = {}
    for manifest_path in sorted(services_dir.glob("*/service.yml")):
        document = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        for row in document.get("images", []):
            image = row["default"]
            platform = row.get("platform", "linux/amd64")
            previous = platforms.setdefault(image, platform)
            if previous != platform:
                raise ValueError(
                    f"image {image!r} declares conflicting platforms: {previous}, {platform}"
                )
    inventory = load_image_inventory(services_dir)
    return tuple(ImageScan(image=image, platform=platforms[image]) for image in inventory)


def load_compose_builds(services_dir: Path) -> tuple[ComposeBuild, ...]:
    """Derive unique final-image build targets from every Compose fragment."""

    root = services_dir.parent.resolve()
    builds: dict[str, ComposeBuild] = {}
    for compose_path in sorted(services_dir.glob("*/compose.yml")):
        document = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
        for service in (document.get("services") or {}).values():
            if not isinstance(service, dict) or "build" not in service:
                continue
            build = service["build"]
            if isinstance(build, str):
                context, dockerfile = build, "Dockerfile"
            else:
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
    if set(document) != {"builds"} or not isinstance(rows, list):
        raise ValueError(f"{path} must contain only a builds list")
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
    args = parser.parse_args(argv)

    exceptions = load_exceptions(args.ignorefile)
    build_exclusions = load_build_exclusions(args.build_exclusions)
    scans = load_image_scans(args.services_dir)
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
