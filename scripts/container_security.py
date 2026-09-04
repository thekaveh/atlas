"""Validate container-scan policy and emit the manifest image inventory."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import date
import json
from pathlib import Path
import re

from scripts.changed_image_scope import (
    changed_service_files,
    select_touched,
    touched_lines_by_file,
)
import shlex
import sys
from typing import Sequence

import yaml

from scripts.upstream_drift_watch import (
    dockerfile_instruction_can_contain_heredoc,
    load_manifest_image_refs,
    load_reviewed_remote_base_refs,
    validate_remote_build_context,
)


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
_LOCAL_PROJECT_BUILD_IMAGE_RE = re.compile(
    r"^\$\{PROJECT_NAME\}-[a-z0-9](?:[a-z0-9-]*[a-z0-9])?:local$"
)
_DOCKERFILE_ARG_RE = re.compile(
    r"^\s*ARG\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:=(?P<default>\S+))?\s*$"
)
_DOCKERFILE_FROM_RE = re.compile(
    r"^\s*FROM(?:\s+--platform=\S+)?\s+(?P<image>\S+)"
    r"(?:\s+AS\s+(?P<alias>[A-Za-z0-9._-]+))?\s*$",
    re.IGNORECASE,
)
_DOCKERFILE_VARIABLE_RE = re.compile(
    r"^\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|"
    r"(?P<plain>[A-Za-z_][A-Za-z0-9_]*))$"
)
_DOCKERFILE_COPY_FROM_RE = re.compile(r"(?:^|\s)--from=(?P<source>\S+)")
_DOCKERFILE_RUN_MOUNT_RE = re.compile(r"(?:^|\s)--mount=(?P<mount>\S+)")
_DOCKERFILE_PARSER_DIRECTIVE_RE = re.compile(
    r"^#\s*(?P<name>syntax|escape|check)\s*=\s*(?P<value>\S.*?)\s*$",
    re.IGNORECASE,
)
_DOCKERFILE_HEREDOC_TOKEN_RE = re.compile(
    r"^\d*<<(?P<strip>-?)(?P<word>.*)$"
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
    base_images: tuple[str, ...] = ()

    @property
    def target(self) -> str:
        return f"{self.context}|{self.dockerfile}"


@dataclass(frozen=True, slots=True)
class _DockerfilePolicy:
    defaults: dict[str, str | None]
    path: Path
    require_pinned: bool
    allow_variables: bool


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


def _resolve_compose_build_args(
    compose_path: Path,
    service_name: str,
    build: dict,
    variables: dict[str, str],
) -> dict[str, str]:
    args = build.get("args") or {}
    if not isinstance(args, dict):
        raise ValueError(f"{compose_path}: service {service_name} build.args must be a mapping")
    resolved_args: dict[str, str] = {}
    for name, value in args.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise ValueError(
                f"{compose_path}: service {service_name} build arg {name} must be a string"
            )
        resolved_args[name] = _resolve_compose_image(
            value,
            variables,
            owner=f"{compose_path}: service {service_name} build arg {name}",
        )
    return resolved_args


def _dockerfile_arg_defaults(
    dockerfile_path: Path,
    lines: Sequence[str],
    variables: dict[str, str],
) -> dict[str, str | None]:
    defaults: dict[str, str | None] = {}
    for line in lines:
        match = _DOCKERFILE_ARG_RE.fullmatch(line)
        if not match:
            continue
        name = match.group("name")
        if name in defaults:
            raise ValueError(f"{dockerfile_path}: duplicate build arg {name}")
        raw_default = match.group("default")
        defaults[name] = None if raw_default is None else _resolve_compose_image(
            raw_default,
            variables,
            owner=f"{dockerfile_path}: build arg {name}",
        )
    return defaults


def _dockerfile_parser_directives(lines: Sequence[str]) -> dict[str, str]:
    directives: dict[str, str] = {}
    for line in lines:
        match = _DOCKERFILE_PARSER_DIRECTIVE_RE.fullmatch(line.strip())
        if not match:
            break
        name = match.group("name").lower()
        if name in directives:
            raise ValueError(f"duplicate Dockerfile parser directive {name}")
        directives[name] = match.group("value")
    escape = directives.get("escape", "\\")
    if escape not in {"\\", "`"}:
        raise ValueError(f"unsupported Dockerfile escape character {escape!r}")
    return directives


def _next_dockerfile_instruction(
    lines: Sequence[str], index: int, escape: str
) -> tuple[str | None, int]:
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        instruction = line
        while _dockerfile_line_continues(instruction, escape):
            instruction = instruction.rstrip(" \t")[:-1]
            while index < len(lines) and (
                not lines[index].strip()
                or lines[index].lstrip().startswith("#")
            ):
                index += 1
            if index >= len(lines):
                break
            instruction += lines[index]
            index += 1
        return instruction, index
    return None, index


def _dockerfile_line_continues(line: str, escape: str) -> bool:
    stripped = line.rstrip(" \t")
    return stripped == escape or (
        stripped.endswith(escape) and not stripped.endswith(escape * 2)
    )


def _dockerfile_heredoc_delimiter(word: str) -> str:
    try:
        parsed = shlex.split(word, posix=True)
    except ValueError as exc:
        raise ValueError(f"invalid Dockerfile heredoc word {word!r}") from exc
    if len(parsed) != 1 or not parsed[0]:
        raise ValueError(f"invalid Dockerfile heredoc word {word!r}")
    return parsed[0]


def _dockerfile_shell_word_end(text: str, start: int) -> int:
    quote, escaped, index = "", False, start
    while index < len(text):
        character = text[index]
        if escaped:
            escaped = False
        elif character == "\\" and quote != "'":
            escaped = True
        elif quote:
            if character == quote:
                quote = ""
        elif character in {"'", '"'}:
            quote = character
        elif character in {" ", "\t"}:
            break
        index += 1
    return index


def _dockerfile_heredoc_words(
    instruction: str,
) -> tuple[tuple[str, bool], ...]:
    heredocs: list[tuple[str, bool]] = []
    index = 0
    while index < len(instruction):
        while index < len(instruction) and instruction[index] in {" ", "\t"}:
            index += 1
        if index >= len(instruction):
            break
        end = _dockerfile_shell_word_end(instruction, index)
        token = instruction[index:end]
        match = _DOCKERFILE_HEREDOC_TOKEN_RE.fullmatch(token)
        index = end
        if not match:
            continue
        word = match.group("word")
        if not word:
            while index < len(instruction) and instruction[index] in {" ", "\t"}:
                index += 1
            if index >= len(instruction):
                raise ValueError(
                    f"unsupported Dockerfile heredoc expression {instruction!r}"
                )
            end = _dockerfile_shell_word_end(instruction, index)
            word = instruction[index:end]
            index = end
        heredocs.append((word, bool(match.group("strip"))))
    return tuple(heredocs)


def _skip_dockerfile_heredocs(
    lines: Sequence[str], index: int, instruction: str
) -> int:
    for word, strip_tabs in _dockerfile_heredoc_words(instruction):
        delimiter = _dockerfile_heredoc_delimiter(word)
        while index < len(lines):
            body_line = lines[index]
            index += 1
            candidate = body_line.lstrip("\t") if strip_tabs else body_line
            if candidate == delimiter:
                break
        else:
            raise ValueError(f"unterminated Dockerfile heredoc {delimiter!r}")
    return index


def _dockerfile_logical_lines(lines: Sequence[str]) -> tuple[str, ...]:
    directives = _dockerfile_parser_directives(lines)
    escape = directives.get("escape", "\\")
    logical = [f"# {name}={value}" for name, value in directives.items()]
    index = 0
    while index < len(lines):
        instruction, index = _next_dockerfile_instruction(lines, index, escape)
        if instruction is None:
            break
        logical.append(instruction)
        index = (
            _skip_dockerfile_heredocs(lines, index, instruction)
            if dockerfile_instruction_can_contain_heredoc(instruction)
            else index
        )
    return tuple(logical)


def _resolve_dockerfile_base(
    reference: str,
    defaults: dict[str, str | None],
    dockerfile_path: Path,
) -> str:
    match = _DOCKERFILE_VARIABLE_RE.fullmatch(reference)
    if not match:
        if "$" in reference:
            raise ValueError(f"{dockerfile_path}: unsupported FROM expression {reference!r}")
        return reference
    name = match.group("braced") or match.group("plain")
    resolved = defaults.get(name)
    if resolved is None:
        raise ValueError(f"{dockerfile_path}: FROM build arg {name} has no default")
    return resolved


def _dockerfile_external_sources(line: str) -> tuple[tuple[str, str], ...]:
    sources: list[tuple[str, str]] = []
    instruction = line.lstrip()
    prefix = ""
    if instruction.upper().startswith("ONBUILD "):
        prefix = "ONBUILD "
        instruction = instruction.split(None, 1)[1].lstrip()
    if instruction.upper().startswith("COPY "):
        match = _DOCKERFILE_COPY_FROM_RE.search(instruction)
        if match:
            sources.append((f"{prefix}COPY --from", match.group("source")))
    elif instruction.upper().startswith("RUN "):
        for match in _DOCKERFILE_RUN_MOUNT_RE.finditer(instruction):
            fields = dict(
                field.split("=", 1)
                for field in match.group("mount").split(",")
                if "=" in field
            )
            if "from" in fields:
                sources.append((f"{prefix}RUN --mount=from", fields["from"]))
    return tuple(sources)


def _resolve_external_dockerfile_image(
    reference: str, instruction: str, policy: _DockerfilePolicy
) -> str:
    if not policy.allow_variables and _DOCKERFILE_VARIABLE_RE.fullmatch(reference):
        raise ValueError(
            f"{policy.path}: {instruction} may not use a build-arg image source "
            f"{reference!r} in a remote context"
        )
    image = _resolve_dockerfile_base(reference, policy.defaults, policy.path)
    if policy.require_pinned and not image_reference_is_pinned(image):
        raise ValueError(
            f"{policy.path}: {instruction} contains floating or "
            f"untagged image {image!r}"
        )
    return image


def _dockerfile_syntax_images(
    lines: Sequence[str], policy: _DockerfilePolicy
) -> set[str]:
    syntax_image = _dockerfile_parser_directives(lines).get("syntax")
    if not syntax_image:
        return set()
    normalized = syntax_image.removeprefix("docker-image://")
    raise ValueError(
        f"{policy.path}: unsupported Dockerfile syntax frontend {normalized!r}; "
        "Atlas only reviews the default BuildKit Dockerfile grammar"
    )


def _dockerfile_source_is_internal(
    instruction: str, reference: str, aliases: set[str]
) -> bool:
    return not instruction.startswith("ONBUILD ") and (
        reference.isdigit() or reference.lower() in aliases
    )


def _dockerfile_from_source(
    match: re.Match[str], aliases: set[str], policy: _DockerfilePolicy
) -> str | None:
    reference = match.group("image")
    image = _resolve_external_dockerfile_image(
        reference, "FROM", replace(policy, require_pinned=False)
    )
    if image.lower() == "scratch" or image.lower() in aliases:
        return None
    return _resolve_external_dockerfile_image(reference, "FROM", policy)


def _dockerfile_base_images(
    lines: Sequence[str],
    policy: _DockerfilePolicy,
) -> tuple[str, ...]:
    aliases: set[str] = set()
    images = _dockerfile_syntax_images(lines, policy)
    saw_from = False
    for line in lines:
        match = _DOCKERFILE_FROM_RE.fullmatch(line)
        if match:
            saw_from = True
            image = _dockerfile_from_source(match, aliases, policy)
            if image:
                images.add(image)
            alias = match.group("alias")
            if alias:
                aliases.add(alias.lower())
            continue

        for instruction, reference in _dockerfile_external_sources(line):
            if _dockerfile_source_is_internal(instruction, reference, aliases):
                continue
            images.add(
                _resolve_external_dockerfile_image(
                    reference, instruction, policy
                )
            )
    if not saw_from:
        raise ValueError(f"{policy.path}: Dockerfile has no external FROM image")
    return tuple(sorted(images))


def _validate_dockerfile_build_contract(
    dockerfile_path: Path,
    compose_args: dict[str, str],
    variables: dict[str, str],
) -> tuple[str, ...]:
    """Require Compose and direct CI builds to resolve the same pinned bases."""

    physical_lines = [
        line.rstrip("\r")
        for line in dockerfile_path.read_text(encoding="utf-8").split("\n")
    ]
    if physical_lines and physical_lines[0].startswith("\ufeff"):
        physical_lines[0] = physical_lines[0].removeprefix("\ufeff")
    lines = _dockerfile_logical_lines(physical_lines)
    defaults = _dockerfile_arg_defaults(dockerfile_path, lines, variables)

    for name, compose_value in compose_args.items():
        dockerfile_value = defaults.get(name)
        if dockerfile_value is None:
            raise ValueError(
                f"{dockerfile_path}: Compose build arg {name} has no Dockerfile default"
            )
        if compose_value != dockerfile_value:
            raise ValueError(
                f"{dockerfile_path}: Compose build arg {name} resolves to "
                f"{compose_value!r}, but the Dockerfile default resolves to "
                f"{dockerfile_value!r}"
            )
    return _dockerfile_base_images(
        lines,
        _DockerfilePolicy(
            defaults,
            dockerfile_path,
            require_pinned=True,
            allow_variables=True,
        ),
    )


def load_dockerfile_source_images(
    document: str,
    *,
    owner: str = "Dockerfile",
    require_pinned: bool = True,
    allow_variables: bool = True,
) -> tuple[str, ...]:
    """Return every pinned external image source in a Dockerfile document."""

    physical_lines = [line.rstrip("\r") for line in document.split("\n")]
    if physical_lines and physical_lines[0].startswith("\ufeff"):
        physical_lines[0] = physical_lines[0].removeprefix("\ufeff")
    lines = _dockerfile_logical_lines(physical_lines)
    owner_path = Path(owner)
    defaults = _dockerfile_arg_defaults(owner_path, lines, {})
    return _dockerfile_base_images(
        lines,
        _DockerfilePolicy(
            defaults,
            owner_path,
            require_pinned=require_pinned,
            allow_variables=allow_variables,
        ),
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
    build_images = set().union(
        *(build.base_images for build in load_compose_builds(services_dir))
    )
    policy_path = services_dir.parent / ".container-scan-exclusions.yml"
    remote_base_images = set(load_reviewed_remote_base_refs(policy_path))
    for image in build_images | remote_base_images:
        platforms.setdefault(image, set(_DEFAULT_SCAN_PLATFORMS))
    return tuple(
        ImageScan(image=image, platform=platform)
        for image in sorted(set(inventory) | build_images | remote_base_images)
        for platform in sorted(platforms[image])
    )


def _diff_changes_remote_build_inputs(
    services_dir: Path,
    changed_diff: Sequence[str],
    changed_files: set[tuple[str, str]],
) -> bool:
    return any(
        " b/.container-scan-exclusions.yml" in line for line in changed_diff
    ) or any(
        (services_dir / service / "compose.yml").is_file()
        and "https://github.com/" in (services_dir / service / "compose.yml").read_text(
            encoding="utf-8"
        )
        for service, _filename in changed_files
    )


def _manifest_owned_images(manifest_path: Path) -> set[str]:
    document = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    return {row["default"] for row in document.get("images", [])}


def _compose_build_specs(
    compose_path: Path,
    root: Path,
    variables: dict[str, str],
) -> tuple[ComposeBuild, ...]:
    document = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
    specs: list[ComposeBuild] = []
    for service_name, service in (document.get("services") or {}).items():
        if isinstance(service, dict) and "build" in service:
            specs.append(
                _compose_build_spec(
                    compose_path, (service_name, service), root, variables
                )
            )
    return tuple(specs)


def _changed_compose_images(
    compose_path: Path,
    root: Path,
    variables: dict[str, str],
) -> set[str]:
    # Parse every local build to keep its Dockerfile/base-image contract under
    # validation, but do not schedule those bases as deployed remote images.
    # The required workflow builds and scans each resulting final image, which
    # is the artifact Atlas actually runs.
    _compose_build_specs(compose_path, root, variables)
    images = _compose_image_refs(compose_path, variables)
    return images


def _changed_service_images(
    services_dir: Path,
    changed_file: tuple[str, str],
    variables: dict[str, str],
    touched_lines: list[str] | None,
) -> set[str]:
    service, filename = changed_file
    path = services_dir / service / filename
    if not path.is_file():
        return set()
    if filename == "service.yml":
        owned = _manifest_owned_images(path) & set(
            load_compose_image_refs(services_dir)
        )
        return select_touched(owned, touched_lines)
    compose_path = services_dir / service / "compose.yml"
    if not compose_path.is_file():
        return set()
    images = _changed_compose_images(
        compose_path, services_dir.parent.resolve(), variables
    )
    if filename != "compose.yml":
        return set()
    return select_touched(images, touched_lines)


def load_changed_image_scans(
    services_dir: Path, changed_diff: Sequence[str]
) -> tuple[ImageScan, ...]:
    """Return scans for directly deployed remote images affected by a diff.

    Bases of local Compose builds remain in the complete scheduled inventory,
    while the required PR workflow scans the corresponding final build output.
    """

    variables = _manifest_image_variables(services_dir)
    selected_images: set[str] = set()
    changed_files = changed_service_files(changed_diff)
    touched_by_file = touched_lines_by_file(changed_diff)
    for changed_file in changed_files:
        selected_images.update(
            _changed_service_images(
                services_dir,
                changed_file,
                variables,
                touched_by_file.get(changed_file),
            )
        )

    if _diff_changes_remote_build_inputs(
        services_dir, changed_diff, changed_files
    ):
        policy_path = services_dir.parent / ".container-scan-exclusions.yml"
        selected_images.update(load_reviewed_remote_base_refs(policy_path))

    if not selected_images:
        return ()
    scans = [
        scan for scan in load_image_scans(services_dir) if scan.image in selected_images
    ]
    inventory_images = {scan.image for scan in scans}
    scans.extend(
        ImageScan(image=image, platform=platform)
        for image in sorted(selected_images - inventory_images)
        for platform in _DEFAULT_SCAN_PLATFORMS
    )
    return tuple(scans)


def _reject_unmodeled_compose_build_options(
    build: dict, compose_path: Path, service_name: str
) -> None:
    unsupported = sorted(
        {"additional_contexts", "dockerfile_inline", "target"} & build.keys()
    )
    if unsupported:
        raise ValueError(
            f"{compose_path}: service {service_name} uses unsupported build "
            f"option(s): {', '.join(unsupported)}"
        )


def _compose_build_spec(
    compose_path: Path,
    named_service: tuple[str, dict],
    root: Path,
    variables: dict[str, str],
) -> ComposeBuild:
    service_name, service = named_service
    final_image = service.get("image")
    if final_image is not None and (
        not isinstance(final_image, str)
        or not _LOCAL_PROJECT_BUILD_IMAGE_RE.fullmatch(final_image.strip())
    ):
        raise ValueError(
            f"{compose_path}: build service {service_name} image must be an "
            "explicit local project build tag"
        )
    build = service["build"]
    if isinstance(build, str):
        context, dockerfile, compose_args = build, "Dockerfile", {}
    elif isinstance(build, dict):
        _reject_unmodeled_compose_build_options(build, compose_path, service_name)
        context = build.get("context", ".")
        dockerfile = build.get("dockerfile", "Dockerfile")
        compose_args = {}
    else:
        raise ValueError(f"{compose_path}: service {service_name} build must be a mapping or string")
    if not isinstance(context, str) or not isinstance(dockerfile, str):
        raise ValueError(f"{compose_path}: service {service_name} build paths must be strings")
    if "://" in context:
        validate_remote_build_context(
            context, f"{compose_path}: service {service_name} build context"
        )
        return ComposeBuild(context=context, dockerfile=dockerfile)
    if isinstance(build, dict):
        compose_args = _resolve_compose_build_args(
            compose_path, service_name, build, variables
        )
    context_path = (compose_path.parent / context).resolve()
    relative_context = context_path.relative_to(root).as_posix()
    bases = _validate_dockerfile_build_contract(
        (context_path / dockerfile).resolve(), compose_args, variables
    )
    return ComposeBuild(
        context=relative_context, dockerfile=dockerfile, base_images=bases
    )


def load_compose_builds(services_dir: Path) -> tuple[ComposeBuild, ...]:
    """Derive unique final-image build targets from every Compose fragment."""

    root = services_dir.parent.resolve()
    variables = _manifest_image_variables(services_dir)
    builds: dict[str, ComposeBuild] = {}
    for compose_path in sorted(services_dir.glob("*/compose.yml")):
        for target in _compose_build_specs(compose_path, root, variables):
            prior = builds.get(target.target)
            if prior is not None and prior.base_images != target.base_images:
                raise ValueError(f"Compose build target {target.target} has conflicting bases")
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
        "--changed-diff-stdin",
        action="store_true",
        help="Limit image output to image references added by a unified diff on stdin.",
    )
    args = parser.parse_args(argv)

    exceptions = load_exceptions(args.ignorefile)
    build_exclusions = load_build_exclusions(args.build_exclusions)
    builds = load_compose_builds(args.services_dir)
    scans = (
        load_changed_image_scans(args.services_dir, sys.stdin.read().splitlines())
        if args.changed_diff_stdin
        else load_image_scans(args.services_dir)
    )
    if args.images_json:
        print(render_scan_matrix_json(scans))
    else:
        print(
            f"Container security policy OK: {len(scans)} image-platform scan(s), "
            f"{len(exceptions)} active exception(s), "
            f"{len(builds)} Compose build target(s), "
            f"{len(build_exclusions)} reviewed build exclusion(s)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
