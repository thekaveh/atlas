"""
Cross-manifest validator.

Per-file/per-manifest checks live in services.manifests (loader). This module
runs the checks that need to see ALL manifests together — global uniqueness,
dependency closure, the source/export/effect declaration contract.

Phase A scope: validations that need only the manifest set. Validations that
need the compose.yml fragments (volume namespacing, depends_on closure across
container graph, healthcheck rule, env-example freshness vs. assembled
.env.example) will land alongside the fragments themselves in later phases.

Design notes:
- Functions return a list of ValidationIssue records rather than raising.
  Callers (CI lint, start.py) decide whether to abort.
- Issues are sorted (kind, manifest, message) for deterministic test output.
- Every issue carries the offending manifest name so users can locate it fast.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from pathlib import Path
import re
from typing import Callable, Iterable
import unicodedata

import yaml

from services.manifests import Manifest


# ────────────────────────────────────────────────────────────────────────────
# Public result type
# ────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ValidationIssue:
    """A single cross-manifest validation failure."""

    kind: str
    manifest: str
    message: str


@dataclass(frozen=True)
class ValidatorRule:
    """One executable validator rule and its public diagnostic contract."""

    name: str
    diagnostics: tuple[str, ...]
    description: str
    check: Callable[..., list[ValidationIssue]]
    needs_services_root: bool = False


# ────────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────────


def validate_manifests(
    manifests: Iterable[Manifest],
    services_root: Path | None = None,
) -> list[ValidationIssue]:
    """Run every cross-manifest check and return the aggregated issue list.

    Args:
        manifests: The manifest set under test.
        services_root: Optional path to the services/ tree. When provided, the
            validator also reads each manifest's sibling compose.yml and runs
            fragment-level cross-checks (containers[] ↔ services keys 1:1,
            missing/unexpected fragment file). When None, only manifest-only
            checks run — used by unit tests that build manifest objects in
            memory without on-disk fragments.
    """
    manifests = list(manifests)
    issues: list[ValidationIssue] = []
    for rule in VALIDATOR_RULES:
        if rule.needs_services_root:
            if services_root is None:
                continue
            produced = rule.check(manifests, services_root)
        else:
            produced = rule.check(manifests)
        unexpected = sorted({issue.kind for issue in produced} - set(rule.diagnostics))
        if unexpected:
            raise RuntimeError(
                f"validator rule '{rule.name}' emitted unregistered diagnostics: "
                f"{unexpected}"
            )
        issues.extend(produced)

    issues.sort(key=lambda i: (i.kind, i.manifest, i.message))
    return issues


# ────────────────────────────────────────────────────────────────────────────
# Individual rules
# ────────────────────────────────────────────────────────────────────────────


# Aggregate doc-folder names are valid data_flow.calls targets even though
# some own no manifest (they fold one or more real manifests under a single
# user-facing role). Mirror of docs.deps_resolver._AGGREGATE_DOC_FOLDERS keys;
# kept local to avoid a services/ -> docs/ import. The two are guarded against
# drift by test_aggregate_doc_folder_names_match_deps_resolver in
# tests/test_manifest_validator.py.
_AGGREGATE_DOC_FOLDER_NAMES = frozenset(
    {"stt-provider", "tts-provider", "doc-processor", "multi2vec-clip"}
)


def _check_data_flow_targets(manifests: list[Manifest]) -> list[ValidationIssue]:
    """Every data_flow.calls target must name a real manifest or an aggregate
    doc-folder. An unknown target (typo / renamed / deleted service) otherwise
    silently renders as a phantom 'external' node in the generated deps graph
    and architecture diagram. NOTE: name-existence only — this does NOT verify
    the caller actually reaches the target at runtime (semantic reachability is
    not statically checkable here without unacceptable false positives)."""
    valid = {m.name for m in manifests} | _AGGREGATE_DOC_FOLDER_NAMES
    issues: list[ValidationIssue] = []
    for m in manifests:
        calls = (m.data_flow or {}).get("calls") or []
        for target in calls:
            if target not in valid:
                issues.append(
                    ValidationIssue(
                        kind="data_flow_unknown_target",
                        manifest=m.name,
                        message=(
                            f"data_flow.calls references unknown target '{target}' "
                            f"— not a manifest name or aggregate doc-folder. Fix the "
                            f"name or remove the edge."
                        ),
                    )
                )
    return issues


def _check_unique_env_vars(manifests: list[Manifest]) -> list[ValidationIssue]:
    """Every env-var name must have exactly one owning manifest."""
    owners: dict[str, list[str]] = {}
    for m in manifests:
        for entry in m.env:
            owners.setdefault(entry.name, []).append(m.name)

    issues: list[ValidationIssue] = []
    for var, names in owners.items():
        if len(names) > 1:
            sorted_names = sorted(names)
            for name in sorted_names:
                issues.append(
                    ValidationIssue(
                        kind="duplicate_env_var",
                        manifest=name,
                        message=(
                            f"env var '{var}' is declared by multiple manifests: "
                            f"{sorted_names}. Each variable must have exactly one owner."
                        ),
                    )
                )
    return issues


def _check_unique_containers(manifests: list[Manifest]) -> list[ValidationIssue]:
    """Every container name must appear in exactly one manifest."""
    owners: dict[str, list[str]] = {}
    for m in manifests:
        for c in m.containers:
            owners.setdefault(c, []).append(m.name)

    issues: list[ValidationIssue] = []
    for container, names in owners.items():
        if len(names) > 1:
            sorted_names = sorted(names)
            for name in sorted_names:
                issues.append(
                    ValidationIssue(
                        kind="duplicate_container",
                        manifest=name,
                        message=(
                            f"container '{container}' is claimed by multiple manifests: "
                            f"{sorted_names}."
                        ),
                    )
                )
    return issues


def _check_unique_capabilities(manifests: list[Manifest]) -> list[ValidationIssue]:
    """Capability names must be unique within each service contract."""
    issues: list[ValidationIssue] = []
    for manifest in manifests:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for capability in manifest.capabilities:
            if capability.name in seen:
                duplicates.add(capability.name)
            seen.add(capability.name)
        for name in sorted(duplicates):
            issues.append(
                ValidationIssue(
                    kind="duplicate_capability",
                    manifest=manifest.name,
                    message=f"capability name '{name}' is repeated within the manifest",
                )
            )
    return issues


def _check_dependency_closure(manifests: list[Manifest]) -> list[ValidationIssue]:
    """Every depends_on entry must reference an existing manifest."""
    known = {m.name for m in manifests}
    issues: list[ValidationIssue] = []
    for m in manifests:
        for dep in m.depends_on.required:
            if dep not in known:
                issues.append(
                    ValidationIssue(
                        kind="unknown_dependency",
                        manifest=m.name,
                        message=f"depends_on.required references unknown manifest '{dep}'",
                    )
                )
        for dep in m.depends_on.optional:
            if dep not in known:
                issues.append(
                    ValidationIssue(
                        kind="unknown_dependency",
                        manifest=m.name,
                        message=f"depends_on.optional references unknown manifest '{dep}'",
                    )
                )
    return issues


def _check_export_consumer_closure(manifests: list[Manifest]) -> list[ValidationIssue]:
    """Every exports[].consumers entry must name a known manifest."""
    known = {m.name for m in manifests}
    issues: list[ValidationIssue] = []
    for m in manifests:
        for exp in m.exports:
            for consumer in exp.consumers:
                if consumer not in known:
                    issues.append(
                        ValidationIssue(
                            kind="unknown_consumer",
                            manifest=m.name,
                            message=(
                                f"exports[].name='{exp.name}' lists unknown consumer "
                                f"'{consumer}'"
                            ),
                        )
                    )
    return issues


def _check_tier_members(manifests: list[Manifest]) -> list[ValidationIssue]:
    """Every name in runtime_dependency_tiers must be a declared container.

    Globals owns `runtime_dependency_tiers:` (data_tier, init_tier, core_services,
    app_tier). Each entry there must match some manifest's containers[] — otherwise
    the dependency manager will silently skip a dangling tier member (which was
    the actual XTTS retirement bug post-Tier-3 move).
    """
    known_containers: set[str] = set()
    for m in manifests:
        known_containers.update(m.containers)

    issues: list[ValidationIssue] = []
    for m in manifests:
        if not m.runtime_dependency_tiers:
            continue
        for tier_name, members in m.runtime_dependency_tiers.items():
            if not isinstance(members, list):
                continue
            for member in members:
                if member not in known_containers:
                    issues.append(
                        ValidationIssue(
                            kind="undeclared_tier_member",
                            manifest=m.name,
                            message=(
                                f"runtime_dependency_tiers.{tier_name} entry "
                                f"'{member}' is not a container declared by any "
                                f"manifest's containers[]."
                            ),
                        )
                    )
    return issues


def _check_fragment_containers(
    manifests: list[Manifest], services_root: Path
) -> list[ValidationIssue]:
    """Each non-virtual manifest's containers[] must equal its compose.yml's
    services keys 1:1 (no extras either way).

    Virtual manifests (cloud-providers, globals) MUST NOT have a compose.yml.
    """
    issues: list[ValidationIssue] = []
    for m in manifests:
        fragment_path = services_root / m.name / "compose.yml"

        if m.virtual:
            if fragment_path.is_file():
                issues.append(
                    ValidationIssue(
                        kind="unexpected_fragment",
                        manifest=m.name,
                        message=(
                            f"virtual: true manifest has a sibling compose.yml at "
                            f"{fragment_path}. Virtual manifests MUST NOT ship a "
                            f"Compose fragment."
                        ),
                    )
                )
            continue

        if not fragment_path.is_file():
            issues.append(
                ValidationIssue(
                    kind="missing_fragment",
                    manifest=m.name,
                    message=(
                        f"non-virtual manifest has no compose.yml at {fragment_path}."
                    ),
                )
            )
            continue

        try:
            fragment = yaml.safe_load(fragment_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            issues.append(
                ValidationIssue(
                    kind="fragment_container_drift",
                    manifest=m.name,
                    message=f"compose.yml could not be parsed: {e}",
                )
            )
            continue

        fragment_services = list((fragment.get("services") or {}).keys())
        manifest_containers = list(m.containers)

        extra_in_manifest = sorted(set(manifest_containers) - set(fragment_services))
        extra_in_fragment = sorted(set(fragment_services) - set(manifest_containers))

        if extra_in_manifest:
            issues.append(
                ValidationIssue(
                    kind="fragment_container_drift",
                    manifest=m.name,
                    message=(
                        f"manifest containers[] lists {extra_in_manifest!r} but "
                        f"compose.yml has no matching `services:` entry."
                    ),
                )
            )
        if extra_in_fragment:
            issues.append(
                ValidationIssue(
                    kind="fragment_container_drift",
                    manifest=m.name,
                    message=(
                        f"compose.yml `services:` keys include {extra_in_fragment!r} "
                        f"that the manifest's containers[] does not declare."
                    ),
                )
            )
    return issues


def _normalized_include_path(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(value).as_posix()


def _normalized_include_paths(value: object) -> tuple[list[str], str | None]:
    if isinstance(value, str):
        normalized = _normalized_include_path(value)
        if normalized is None:
            return [], "include paths must not be blank"
        return [normalized], None
    if not isinstance(value, list):
        return [], "include path must be a string or list of strings"
    if not value:
        return [], "include path lists must not be empty"
    normalized_paths = [_normalized_include_path(item) for item in value]
    if any(item is None for item in normalized_paths):
        return [], "include path list members must be nonblank strings"
    return [item for item in normalized_paths if item is not None], None


def _include_paths(entry: object) -> tuple[list[str], str | None]:
    """Normalize one Compose ``include`` entry to relative POSIX paths."""
    if isinstance(entry, str):
        return _normalized_include_paths(entry)
    if not isinstance(entry, dict):
        return [], "each include entry must be a string or mapping"
    if "path" not in entry:
        return [], "include mappings must define path"
    return _normalized_include_paths(entry["path"])


def _fragment_include_issue(message: str) -> ValidationIssue:
    return ValidationIssue(
        kind="fragment_include_drift",
        manifest="_compose",
        message=message,
    )


def _load_compose_includes(compose_path: Path) -> tuple[list[str], str | None]:
    try:
        document = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as error:
        return [], str(error)
    if not isinstance(document, dict):
        return [], "top-level document must be a mapping"
    entries = document.get("include", [])
    if not isinstance(entries, list):
        return [], "include must be a list"
    includes: list[str] = []
    for entry in entries:
        paths, shape_error = _include_paths(entry)
        if shape_error is not None:
            return [], shape_error
        includes.extend(paths)
    return includes, None


def _missing_fragment_manifest_issues(
    manifests: list[Manifest], expected: set[str]
) -> list[ValidationIssue]:
    manifest_names = {manifest.name for manifest in manifests}
    return [
        ValidationIssue(
            kind="missing_fragment_manifest",
            manifest=Path(path).parent.name,
            message=f"{path} has no sibling service.yml manifest",
        )
        for path in sorted(expected)
        if Path(path).parent.name not in manifest_names
    ]


def _fragment_include_drift_issues(
    expected: set[str], includes: list[str]
) -> list[ValidationIssue]:
    actual = set(includes)
    drift = (
        ("missing fragment include(s)", sorted(expected - actual)),
        ("unknown fragment include(s)", sorted(actual - expected)),
        (
            "duplicate fragment include(s)",
            sorted({path for path in includes if includes.count(path) > 1}),
        ),
    )
    return [
        _fragment_include_issue(f"docker-compose.yml has {label}: {paths}")
        for label, paths in drift
        if paths
    ]


def _check_fragment_includes(
    manifests: list[Manifest], services_root: Path
) -> list[ValidationIssue]:
    """Every on-disk fragment must appear in the top-level Compose exactly once."""
    expected = {
        path.relative_to(services_root.parent).as_posix()
        for path in services_root.glob("*/compose.yml")
    }
    manifest_issues = _missing_fragment_manifest_issues(manifests, expected)
    compose_path = services_root.parent / "docker-compose.yml"
    if not compose_path.is_file():
        if not expected:
            return manifest_issues
        return manifest_issues + [
            _fragment_include_issue("docker-compose.yml is missing")
        ]
    includes, shape_error = _load_compose_includes(compose_path)
    if shape_error is not None:
        return manifest_issues + [
            _fragment_include_issue(
                f"docker-compose.yml has invalid include shape: {shape_error}"
            )
        ]
    return manifest_issues + _fragment_include_drift_issues(expected, includes)


_GENERIC_DOCUMENTATION_EXCEPTIONS = frozenset(
    {
        "n a",
        "none",
        "no docs",
        "no docs needed",
        "not applicable",
        "not needed",
        "documentation not required",
        "todo document later",
        "tbd",
    }
)
_DOCUMENTATION_EXCEPTION_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "applicable",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "being",
        "by",
        "docs",
        "document",
        "documentation",
        "documented",
        "exception",
        "for",
        "from",
        "has",
        "have",
        "internal",
        "is",
        "it",
        "its",
        "link",
        "manifest",
        "manifests",
        "need",
        "needed",
        "no",
        "not",
        "of",
        "on",
        "only",
        "operator",
        "operators",
        "or",
        "rationale",
        "reason",
        "required",
        "see",
        "service",
        "services",
        "that",
        "the",
        "their",
        "them",
        "this",
        "to",
        "tracking",
        "url",
        "use",
        "used",
        "using",
        "with",
        "without",
    }
)
_ALPHABETIC_TOKEN = re.compile(r"[^\W\d_]+", re.UNICODE)
_MARKDOWN_INLINE_LINK = re.compile(r"\[[^\]\r\n]*\]\([^\)\r\n]*\)")
_MARKDOWN_REFERENCE_LINK = re.compile(r"\[[^\]\r\n]*\]\s*\[[^\]\r\n]*\]")
_MARKDOWN_LABEL = re.compile(r"\[(?P<label>[^\]\r\n]*)\]")
_ANGLE_AUTOLINK = re.compile(r"<[^>\r\n]+>")
_RFC_URI_TOKEN = re.compile(
    r"(?<![\w.])(?P<scheme>[a-z][a-z0-9+.-]*):"
    r"(?P<payload>[^\s<>\[\]]+)",
    re.IGNORECASE,
)
_EMAIL_LIKE = re.compile(
    r"(?<![\w.@])"
    r"(?:\"(?:\\.|[^\"\\\r\n])*\"|[^\s<>()\[\],;:@\"\\]+)@"
    r"(?:\[(?:IPv6:)?[^\]\r\n]+\]|"
    r"[^\s<>()\[\],;:@\"\\]+(?:\.[^\s<>()\[\],;:@\"\\]+)+)"
    r"(?![\w@])",
    re.IGNORECASE,
)
_HOST_CANDIDATE = re.compile(
    r"(?<![\w@])"
    r"(?P<host>(?:[^\s<>()[\]{}/:?#@,;\"']+\.)+"
    r"[^\s<>()[\]{}/:?#@,;\"']+)"
    r"(?P<suffix>(?::\d{1,5}|[/#?])[^\s<>\[\]]*)?",
)
_IPV4_CANDIDATE = re.compile(
    r"(?<![\w.])(?P<ip>(?:\d{1,3}\.){3}\d{1,3})"
    r"(?P<suffix>(?::\d{1,5}|[/#?])[^\s<>\[\]]*)?(?![\w.])"
)
_IPV6_CANDIDATE = re.compile(
    r"\[(?P<ip>(?:IPv6:)?[0-9a-f:.]+(?:%(?:25)?[A-Za-z0-9._~-]+)?)\]"
    r"(?P<suffix>(?::\d{1,5}|[/#?])[^\s<>\[\]]*)?",
    re.IGNORECASE,
)
_MALFORMED_BRACKETED_ADDRESS_LINK = re.compile(
    r"\[(?=[^\]\r\n]*:)[^\]\r\n]*\]"
    r"(?::\d{1,5}|[/#?])[^\s<>\[\]]*"
)
_KNOWN_URI_SCHEMES = frozenset(
    {
        "data",
        "file",
        "ftp",
        "geo",
        "git",
        "http",
        "https",
        "mailto",
        "news",
        "s3",
        "sms",
        "ssh",
        "tel",
        "urn",
        "ws",
        "wss",
    }
)
_HOST_PORT_PAYLOAD = re.compile(r"\d{1,5}(?:[/#?][^\s<>\[\]()]*)?")
_HOST_LIKE_SCHEME = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*",
    re.IGNORECASE,
)


def _nfkc_exception_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value)


def _normalized_exception_text(value: str) -> str:
    return _nfkc_exception_text(value).casefold()


def _normalized_reason(value: str) -> str:
    lowered = "".join(
        character if character.isalnum() else " "
        for character in _normalized_exception_text(value)
    )
    return " ".join(lowered.split())


def _is_generic_documentation_exception(reason: str) -> bool:
    normalized = _normalized_reason(reason)
    return (
        ("://" in reason and not any(character.isspace() for character in reason))
        or normalized in _GENERIC_DOCUMENTATION_EXCEPTIONS
        or normalized.startswith(
            (
                "todo ",
                "tbd ",
                "no docs ",
                "documentation is not required ",
                "documentation not required ",
            )
        )
    )


def _contains_mixed_script_token(value: str) -> bool:
    """Detect confusable Latin/Greek/Cyrillic mixing within one word."""
    for token in _ALPHABETIC_TOKEN.findall(value):
        scripts = {
            name.split(" ", 1)[0]
            for character in token
            if (name := unicodedata.name(character, ""))
            and name.split(" ", 1)[0] in {"LATIN", "GREEK", "CYRILLIC"}
        }
        if len(scripts) > 1:
            return True
    return False


def _validated_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    candidate = value[5:] if value[:5].casefold() == "ipv6:" else value
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


def _strip_ip_link(match: re.Match[str]) -> str:
    if _validated_ip(match.group("ip")) is not None and match.group("suffix"):
        return " "
    return match.group(0)


def _strip_markdown_label(match: re.Match[str]) -> str:
    if _validated_ip(match.group("label")) is not None:
        return match.group(0)
    return " "


def _idna_host(value: str) -> str | None:
    try:
        labels = [label.encode("idna").decode("ascii") for label in value.split(".")]
    except UnicodeError:
        return None
    if not labels or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label, re.I)
        for label in labels
    ):
        return None
    return ".".join(labels).casefold()


def _is_numeric_host(ascii_host: str) -> bool:
    labels = ascii_host.split(".")
    return all(label.isdigit() for label in labels)


def _is_reserved_host(ascii_host: str) -> bool:
    return ascii_host.rsplit(".", 1)[-1] in {"internal", "local", "localhost"}


def _bare_host_is_link_like(host: str, ascii_host: str) -> bool:
    if ascii_host.startswith("www."):
        return True
    if host != host.lower() and host.isascii():
        return False
    tld = ascii_host.rsplit(".", 1)[-1]
    return bool(re.fullmatch(r"[a-z]{2,63}", tld) or tld.startswith("xn--"))


def _strip_host_candidate(match: re.Match[str]) -> str:
    host = match.group("host")
    ascii_host = _idna_host(host)
    if ascii_host is None or _is_numeric_host(ascii_host):
        return match.group(0)
    if match.group("suffix"):
        return " "
    if _is_reserved_host(ascii_host):
        return match.group(0)
    if ascii_host.startswith("www."):
        return " "
    return " " if _bare_host_is_link_like(host, ascii_host) else match.group(0)


def _nonspace_token(match: re.Match[str]) -> str:
    source = match.string
    start = match.start()
    end = match.end()
    while start and not source[start - 1].isspace():
        start -= 1
    while end < len(source) and not source[end].isspace():
        end += 1
    return source[start:end].strip("<>[](),;.!?")


def _strip_rfc_uri(match: re.Match[str]) -> str:
    scheme = match.group("scheme")
    payload = match.group("payload")
    if _validated_ip(_nonspace_token(match)) is not None:
        return match.group(0)
    if (
        scheme.casefold() not in _KNOWN_URI_SCHEMES
        and _HOST_LIKE_SCHEME.fullmatch(scheme)
        and _HOST_PORT_PAYLOAD.fullmatch(payload)
    ):
        return match.group(0)
    return " "


def _strip_link_like_text(value: str) -> str:
    """Remove bounded single-line link forms before rationale scoring."""
    stripped = _MARKDOWN_INLINE_LINK.sub(" ", value)
    stripped = _MARKDOWN_REFERENCE_LINK.sub(" ", stripped)
    stripped = _ANGLE_AUTOLINK.sub(" ", stripped)
    stripped = _EMAIL_LIKE.sub(" ", stripped)
    stripped = _IPV6_CANDIDATE.sub(_strip_ip_link, stripped)
    stripped = _MALFORMED_BRACKETED_ADDRESS_LINK.sub(" ", stripped)
    stripped = _IPV4_CANDIDATE.sub(_strip_ip_link, stripped)
    stripped = _MARKDOWN_LABEL.sub(_strip_markdown_label, stripped)
    stripped = _RFC_URI_TOKEN.sub(_strip_rfc_uri, stripped)
    return _HOST_CANDIDATE.sub(_strip_host_candidate, stripped)


def _is_specific_documentation_exception(reason: str) -> bool:
    """Require a printable, concrete rationale rather than blacklist evasion."""
    nfkc = _nfkc_exception_text(reason)
    normalized = _normalized_exception_text(reason)
    if any(
        unicodedata.category(character).startswith("C")
        for character in reason + normalized
    ):
        return False
    if _contains_mixed_script_token(normalized):
        return False
    if _is_generic_documentation_exception(normalized):
        return False
    without_links = _strip_link_like_text(nfkc).casefold()
    because = re.search(r"\bbecause\b(?P<rationale>.*)$", without_links)
    if because is None:
        return False
    rationale = because.group("rationale")
    content = [
        token
        for token in _ALPHABETIC_TOKEN.findall(rationale)
        if len(token) >= 3 and token not in _DOCUMENTATION_EXCEPTION_STOPWORDS
    ]
    return len(content) >= 4 and len(set(content)) >= 3


def _safe_documentation_file(repo_root: Path, relative: Path) -> bool:
    """Accept only regular, non-symlinked Markdown below ``repo_root``."""
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        return False
    if relative.suffix != ".md":
        return False
    resolved_root = repo_root.resolve()
    current = repo_root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return False
    try:
        current.resolve().relative_to(resolved_root)
    except (OSError, ValueError):
        return False
    return current.is_file()


def _check_manifest_documentation(
    manifests: list[Manifest], services_root: Path
) -> list[ValidationIssue]:
    """Every manifest must own safe Markdown or a specific documented exception."""
    repo_root = services_root.parent
    return [
        issue
        for manifest in manifests
        for issue in _manifest_documentation_issues(manifest, repo_root)
    ]


def _manifest_documentation_issues(
    manifest: Manifest, repo_root: Path
) -> list[ValidationIssue]:
    declared = Path(manifest.docs) if manifest.docs else None
    declared_is_valid = bool(
        declared is not None and _safe_documentation_file(repo_root, declared)
    )
    sibling = Path("services") / manifest.name / "README.md"
    sibling_is_valid = _safe_documentation_file(repo_root, sibling)
    issues = _declared_documentation_issues(manifest, declared, declared_is_valid)

    exception = manifest.docs_exception.strip()
    if exception:
        return issues + _documentation_exception_issues(
            manifest, exception, declared is not None, sibling_is_valid
        )
    if not declared_is_valid and not sibling_is_valid:
        issues.append(
            ValidationIssue(
                kind="missing_documentation",
                manifest=manifest.name,
                message=(
                    "manifest has no safe documentation; add docs, a sibling "
                    "README.md, or a specific docs_exception"
                ),
            )
        )
    return issues


def _declared_documentation_issues(
    manifest: Manifest, declared: Path | None, declared_is_valid: bool
) -> list[ValidationIssue]:
    if declared is None or declared_is_valid:
        return []
    return [
        ValidationIssue(
            kind="invalid_documentation",
            manifest=manifest.name,
            message=(
                f"docs='{manifest.docs}' must name an existing, regular, "
                "non-symlinked repository-relative Markdown file"
            ),
        )
    ]


def _documentation_exception_issues(
    manifest: Manifest,
    exception: str,
    has_declared_documentation: bool,
    sibling_is_valid: bool,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not _is_specific_documentation_exception(exception):
        issues.append(
            ValidationIssue(
                kind="invalid_documentation_exception",
                manifest=manifest.name,
                message=(
                    "docs_exception must give a printable, specific 'because' "
                    "rationale with at least four substantive words and three "
                    "distinct terms"
                ),
            )
        )
    if has_declared_documentation or sibling_is_valid:
        issues.append(
            ValidationIssue(
                kind="documentation_exception_conflict",
                manifest=manifest.name,
                message=(
                    "docs_exception is only allowed when neither docs nor a "
                    "sibling README.md is available"
                ),
            )
        )
    return issues


def _check_per_manifest_contract(manifests: list[Manifest]) -> list[ValidationIssue]:
    """Within a single manifest, sources/exports must reference declared env vars."""
    issues: list[ValidationIssue] = []
    for m in manifests:
        declared_env = {e.name for e in m.env}

        # 1. Source var declared as env
        if m.sources is not None and m.sources.var not in declared_env:
            issues.append(
                ValidationIssue(
                    kind="undeclared_source_var",
                    manifest=m.name,
                    message=(
                        f"sources.var='{m.sources.var}' must also appear as an entry in env[]"
                    ),
                )
            )

        # 2. (intentionally no check) runtime_sc.<container>.<source>
        # .environment blocks routinely carry container-INTERNAL keys
        # (AIRFLOW__*, GF_*, weaviate module config, ~80 across the repo)
        # that are deliberately NOT declared in any manifest's env[] —
        # they never surface in .env. A "must be declared" rule here
        # would force all of them into .env.example. Typo protection for
        # the user-facing subset comes from the env-example consistency
        # and localhost-port symmetry tests instead.

        # 3. Every export name must be either a declared env var OR produced
        # by this manifest's runtime_sc.
        produced_by_runtime: set[str] = set()
        for sc_block in m.runtime_sc.values():
            if not isinstance(sc_block, dict):
                continue
            for source_block in sc_block.values():
                if not isinstance(source_block, dict):
                    continue
                env_block = source_block.get("environment") or {}
                if isinstance(env_block, dict):
                    produced_by_runtime.update(env_block.keys())
        producible = declared_env | produced_by_runtime
        for exp in m.exports:
            if exp.name not in producible:
                issues.append(
                    ValidationIssue(
                        kind="undeclared_export",
                        manifest=m.name,
                        message=(
                            f"exports[].name='{exp.name}' is not declared in this manifest's "
                            f"env[] and is not produced by any runtime_sc environment"
                        ),
                    )
                )

    return issues


def _check_topology_cycle(manifests: list[Manifest]) -> list[ValidationIssue]:
    """The combined depends_on graph must be acyclic."""
    from services.topology import TopologyError, validate_acyclic
    try:
        validate_acyclic(manifests)
        return []
    except TopologyError as e:
        return [ValidationIssue(kind="topology_cycle", manifest="<graph>", message=str(e))]


def _check_alias_uniqueness(manifests: list[Manifest]) -> list[ValidationIssue]:
    """Every rows[].alias must be unique across manifests."""
    seen: dict[str, list[str]] = {}
    for m in manifests:
        for r in m.rows:
            if r.alias:
                seen.setdefault(r.alias, []).append(m.name)
    issues: list[ValidationIssue] = []
    for alias, owners in seen.items():
        if len(owners) > 1:
            for owner in sorted(owners):
                issues.append(ValidationIssue(
                    kind="duplicate_alias",
                    manifest=owner,
                    message=f"alias '{alias}' is claimed by multiple manifests: {sorted(owners)}",
                ))
    return issues


def _check_category_overflow(manifests: list[Manifest]) -> list[ValidationIssue]:
    """Total slot-consuming *_PORT vars per category must fit in that category's block.

    Mirrors the skip filter in ``services.topology._allocate_slots`` so the
    validator and the allocator agree on what counts. Without this, the
    validator over-counts ``BASE_PORT`` and ``*_LOCALHOST_*_PORT`` env vars
    (which the allocator deliberately skips because they are external hints,
    not stack slots) and raises false-positive category overflow.
    """
    from services.topology import CATEGORY_SLOTS

    def _consumes_slot(name: str) -> bool:
        if not name.endswith("_PORT"):
            return False
        if name == "BASE_PORT":
            return False
        if "_LOCALHOST_" in name:
            return False
        return True

    by_cat: dict[str, int] = {c: 0 for c in CATEGORY_SLOTS}
    for m in manifests:
        if m.category not in by_cat:
            continue
        by_cat[m.category] += sum(1 for e in m.env if _consumes_slot(e.name))
    issues: list[ValidationIssue] = []
    for cat, count in by_cat.items():
        _, block_size = CATEGORY_SLOTS[cat]
        if count > block_size:
            issues.append(ValidationIssue(
                kind="category_overflow",
                manifest=f"<{cat}>",
                message=f"category '{cat}' has {count} *_PORT vars but block size is {block_size}",
            ))
    return issues


def _check_engine_orphans(manifests: list[Manifest]) -> list[ValidationIssue]:
    """Engine-only manifests (no rows, not virtual) must be referenced as a source variant.

    An "engine-only" manifest is one that:
    - has containers (it runs something)
    - has no rows (it never presents a wizard row of its own)
    - is not virtual
    - depends_on at least one manifest that owns a sources block (it is a child-engine
      activated by a parent's source toggle, not a freestanding infrastructure service)

    The last guard prevents false positives for pure infrastructure services (e.g. redis)
    that have no rows because they are always-on foundations, not selectable engines.
    """
    issues: list[ValidationIssue] = []

    # Build the set of manifests that own a sources block.
    source_owners: set[str] = {m.name for m in manifests if m.sources is not None}

    all_source_option_ids: set[str] = set()
    for m in manifests:
        if m.sources is not None:
            for opt in m.sources.options:
                all_source_option_ids.add(opt.id)

    for m in manifests:
        if m.virtual or m.rows or not m.containers:
            continue
        # Only apply the rule to manifests that depend on a source-owning parent.
        # This distinguishes engine services (speaches, chatterbox) from
        # always-on infrastructure services (redis, neo4j, etc.).
        depends_on_source_owner = any(
            dep in source_owners for dep in m.depends_on.required
        )
        if not depends_on_source_owner:
            continue
        # An engine-only manifest's name must appear as a prefix of at least one source option id.
        if not any(opt_id.startswith(m.name) for opt_id in all_source_option_ids):
            issues.append(ValidationIssue(
                kind="engine_orphan",
                manifest=m.name,
                message=(
                    f"engine-only manifest '{m.name}' is not referenced by any source variant id. "
                    f"Add a source option whose id begins with '{m.name}' to its parent manifest."
                ),
            ))
    return issues
def _check_runtime_sc_source_coverage(
    manifests: list[Manifest],
) -> list[ValidationIssue]:
    """Main runtime_sc slices must cover every declared source option.

    A typo'd or forgotten variant key is otherwise silent:
    ``get_service_config()`` returns ``{}`` for it and consumers fall
    back to hardcoded defaults. The main slice is the one named after
    ``sources.var`` (sans ``_SOURCE``, underscore or hyphen form) or the
    family; multi-container families with no single main slice (airflow,
    ray, spark) treat every slice speaking the pure sources vocabulary
    as a per-container main slice. Init slices that use their own
    ``container/disabled`` vocabulary — or intentionally omit
    ``localhost`` because service_config handles it imperatively — are
    out of scope by construction.
    """
    issues: list[ValidationIssue] = []
    for m in manifests:
        if not m.sources or not m.runtime_sc:
            continue
        if not m.sources.var.endswith("_SOURCE"):
            continue
        opts = {o.id for o in m.sources.options}
        stem = m.sources.var[: -len("_SOURCE")].lower()
        main_names = {stem, stem.replace("_", "-"), m.name}
        targets = [n for n in m.runtime_sc if n in main_names]
        if not targets:
            # OVERLAP, not subset. `set(d) <= opts` meant a single typo'd
            # variant key made the subset test false, dropping the WHOLE slice
            # out of `targets` — so the check that exists to catch exactly that
            # typo was disabled by it. Six families take this fallback path
            # (airflow, celery, langfuse, llm-graph-builder, ray, spark) and
            # the schema puts no constraint on variant key names.
            targets = [
                n
                for n, d in m.runtime_sc.items()
                if isinstance(d, dict) and set(d) & opts
            ]
        for n in targets:
            d = m.runtime_sc.get(n)
            if isinstance(d, dict):
                issues.extend(_runtime_sc_variant_issues(m, n, set(d.keys()), opts))
    return issues


def _runtime_sc_variant_issues(
    m: Manifest, slice_name: str, declared: set, opts: set
) -> list[ValidationIssue]:
    """Unknown and missing source variants for one `runtime_sc` slice."""
    issues = [
        ValidationIssue(
            kind="runtime_sc_unknown_variant",
            manifest=m.name,
            message=(
                f"runtime_sc['{slice_name}'] declares '{unknown}', which is "
                f"not a source option of {m.sources.var} — "
                f"get_service_config() would never read it"
            ),
        )
        for unknown in sorted(declared - opts)
    ]
    issues.extend(
        ValidationIssue(
            kind="runtime_sc_missing_variant",
            manifest=m.name,
            message=(
                f"runtime_sc['{slice_name}'] has no entry for source "
                f"option '{opt}' — get_service_config() would "
                f"silently return {{}} for that variant"
            ),
        )
        for opt in sorted(opts - declared)
    )
    return issues


def _check_prod_option_availability(
    manifests: list[Manifest],
) -> list[ValidationIssue]:
    """Every multi-source service must keep at least one option available in prod.

    An option is available in prod when it is unannotated (profiles=None) or
    its profiles list includes "prod". A service that marks ALL of its options
    as dev-only (profiles=[default]) would leave users unable to configure it
    under --profile prod — this lint catches accidental total exclusion.

    Services with no sources block (or an empty options list) are exempt —
    there is nothing to gate. A service with a single option that is marked
    dev-only IS checked: that single option being dev-only would leave the
    service unconfigurable under prod, which is exactly the bug to catch.
    """
    issues: list[ValidationIssue] = []
    for m in manifests:
        if m.sources is None or len(m.sources.options) < 1:
            continue
        has_prod_option = any(
            opt.profiles is None or "prod" in opt.profiles
            for opt in m.sources.options
        )
        if not has_prod_option:
            issues.append(
                ValidationIssue(
                    kind="no_prod_option",
                    manifest=m.name,
                    message=(
                        f"all {len(m.sources.options)} source options are annotated "
                        f"profiles=[default], leaving no option available under "
                        f"--profile prod. At least one option must be unannotated "
                        f"or include 'prod' in its profiles list."
                    ),
                )
            )
    return issues


def _secondary_number_value_messages(config) -> list[str]:
    messages: list[str] = []
    try:
        default = int(config.default)
    except ValueError:
        default = None
        messages.append(f"default '{config.default}' must be an integer string")
    if config.number_min > config.number_max:
        messages.append(
            f"minimum {config.number_min} exceeds maximum {config.number_max}"
        )
    elif default is not None and not config.number_min <= default <= config.number_max:
        messages.append(
            f"default {default} is outside the inclusive range "
            f"{config.number_min}..{config.number_max}"
        )
    return messages


def _secondary_number_source_messages(manifest: Manifest, row, config) -> list[str]:
    messages: list[str] = []
    owned_env = next(
        (entry for entry in manifest.env if entry.name == config.env_var), None
    )
    if owned_env is None:
        messages.append(f"env_var '{config.env_var}' is not declared in env[]")
    elif str(owned_env.default) != config.default:
        messages.append(
            f"default '{config.default}' differs from env[] default "
            f"'{owned_env.default}' for {config.env_var}"
        )
    if manifest.sources is None:
        messages.append("requires a sources block so the input has a source prompt")
    elif row.source_var != manifest.sources.var:
        messages.append(
            f"row source_var '{row.source_var}' must match sources.var "
            f"'{manifest.sources.var}'"
        )
    return messages


def _secondary_number_issues(manifest: Manifest, row) -> list[ValidationIssue]:
    config = row.secondary_number
    if config is None:
        return []
    messages = _secondary_number_value_messages(config)
    messages.extend(_secondary_number_source_messages(manifest, row, config))
    option_ids = {
        option.id for option in manifest.sources.options
    } if manifest.sources is not None else set()
    unknown_options = sorted(set(config.visible_when_source) - option_ids)
    if unknown_options:
        messages.append(f"visible_when_source names unknown source option(s): {unknown_options}")
    return [
        ValidationIssue(
            kind="invalid_secondary_number",
            manifest=manifest.name,
            message=f"row '{row.display_name}' secondary_number {message}",
        )
        for message in messages
    ]


def _check_secondary_numbers(manifests: list[Manifest]) -> list[ValidationIssue]:
    """Manifest-driven numeric inputs must be safe to render and persist."""
    return [
        issue
        for manifest in manifests
        for row in manifest.rows
        for issue in _secondary_number_issues(manifest, row)
    ]


def _auto_prefer_fallback_issue(manifest: Manifest) -> ValidationIssue | None:
    preferences = manifest.sources.auto_prefer if manifest.sources is not None else []
    if all(item.requires_capability is not None for item in preferences):
        return ValidationIssue(
            kind="auto_prefer_no_fallback",
            manifest=manifest.name,
            message=(
                "auto_prefer has no unconditional terminal entry — every entry "
                "requires a capability, so `auto` resolution could dead-end."
            ),
        )
    if any(item.requires_capability is None for item in preferences[:-1]):
        return ValidationIssue(
            kind="auto_prefer_fallback_not_terminal",
            manifest=manifest.name,
            message=(
                "auto_prefer's unconditional fallback must be the final entry; "
                "an earlier fallback makes later preferences unreachable."
            ),
        )
    return None


def _check_auto_prefer_integrity(manifests: list[Manifest]) -> list[ValidationIssue]:
    """`sources.auto_prefer` (#753) must be internally coherent.

    Three invariants, so `<SVC>_SOURCE: auto` resolution can never select a
    value the validator would then reject:
    - every auto_prefer id must be one of the declared options[].id;
    - every requires_capability must be a capability the shared probe knows
      (services/host_capabilities.py KNOWN_CAPABILITIES — the schema enum
      enforces this on load, this lint guards in-memory manifests too);
    - a non-empty list must end with at least one unconditional entry
      (no requires_capability), so resolution always has a terminal fallback
      and cannot dead-end into "no eligible option" on a capability-less host.
    """
    from services.host_capabilities import KNOWN_CAPABILITIES

    issues: list[ValidationIssue] = []
    for m in manifests:
        if m.sources is None or not m.sources.auto_prefer:
            continue
        option_ids = {opt.id for opt in m.sources.options}
        for pref in m.sources.auto_prefer:
            if pref.id not in option_ids:
                issues.append(
                    ValidationIssue(
                        kind="auto_prefer_unknown_option",
                        manifest=m.name,
                        message=(
                            f"auto_prefer id '{pref.id}' is not one of the declared "
                            f"source options: {', '.join(sorted(option_ids))}"
                        ),
                    )
                )
            if (
                pref.requires_capability is not None
                and pref.requires_capability not in KNOWN_CAPABILITIES
            ):
                issues.append(
                    ValidationIssue(
                        kind="auto_prefer_unknown_capability",
                        manifest=m.name,
                        message=(
                            f"auto_prefer entry '{pref.id}' requires unknown capability "
                            f"'{pref.requires_capability}' (known: "
                            f"{', '.join(KNOWN_CAPABILITIES)})"
                        ),
                    )
                )
        fallback_issue = _auto_prefer_fallback_issue(m)
        if fallback_issue is not None:
            issues.append(fallback_issue)
    return issues


# This ordered registry is both the execution plan and the documentation source.
# A rule that emits a diagnostic not declared here fails closed in
# ``validate_manifests`` instead of letting the public catalog drift silently.
VALIDATOR_RULES: tuple[ValidatorRule, ...] = (
    ValidatorRule(
        "unique_env_vars",
        ("duplicate_env_var",),
        "Each environment variable has exactly one owning manifest.",
        _check_unique_env_vars,
    ),
    ValidatorRule(
        "unique_containers",
        ("duplicate_container",),
        "Each Compose container name has exactly one owning manifest.",
        _check_unique_containers,
    ),
    ValidatorRule(
        "unique_capabilities",
        ("duplicate_capability",),
        "Capability names are unique within each manifest.",
        _check_unique_capabilities,
    ),
    ValidatorRule(
        "data_flow_targets",
        ("data_flow_unknown_target",),
        "Every runtime data-flow target names a manifest or approved aggregate documentation folder.",
        _check_data_flow_targets,
    ),
    ValidatorRule(
        "dependency_closure",
        ("unknown_dependency",),
        "Required and optional dependencies name existing manifests.",
        _check_dependency_closure,
    ),
    ValidatorRule(
        "export_consumer_closure",
        ("unknown_consumer",),
        "Every exported-variable consumer names an existing manifest.",
        _check_export_consumer_closure,
    ),
    ValidatorRule(
        "per_manifest_contract",
        ("undeclared_source_var", "undeclared_export"),
        "Source variables and exported values are declared or produced by their owning manifest.",
        _check_per_manifest_contract,
    ),
    ValidatorRule(
        "dependency_tier_members",
        ("undeclared_tier_member",),
        "Every runtime dependency-tier member names a declared container.",
        _check_tier_members,
    ),
    ValidatorRule(
        "topology_acyclic",
        ("topology_cycle",),
        "The combined required-dependency graph is acyclic.",
        _check_topology_cycle,
    ),
    ValidatorRule(
        "alias_uniqueness",
        ("duplicate_alias",),
        "Kong row aliases are unique across manifests.",
        _check_alias_uniqueness,
    ),
    ValidatorRule(
        "category_capacity",
        ("category_overflow",),
        "Port-owning variables fit within their category's allocated slot block.",
        _check_category_overflow,
    ),
    ValidatorRule(
        "engine_reachability",
        ("engine_orphan",),
        "Engine-only manifests are reachable from a parent source option.",
        _check_engine_orphans,
    ),
    ValidatorRule(
        "runtime_source_coverage",
        ("runtime_sc_unknown_variant", "runtime_sc_missing_variant"),
        "Main runtime slices match the manifest's declared source variants exactly.",
        _check_runtime_sc_source_coverage,
    ),
    ValidatorRule(
        "production_source_availability",
        ("no_prod_option",),
        "Every source-configurable service retains an option available in the production profile.",
        _check_prod_option_availability,
    ),
    ValidatorRule(
        "secondary_number_inputs",
        ("invalid_secondary_number",),
        "Manifest-driven numeric inputs have valid bounds, defaults, sources, and owned environment variables.",
        _check_secondary_numbers,
    ),
    ValidatorRule(
        "automatic_source_preferences",
        (
            "auto_prefer_unknown_option",
            "auto_prefer_unknown_capability",
            "auto_prefer_no_fallback",
            "auto_prefer_fallback_not_terminal",
        ),
        "Automatic source preferences use known options and capabilities with a terminal fallback.",
        _check_auto_prefer_integrity,
    ),
    ValidatorRule(
        "fragment_container_contract",
        ("fragment_container_drift", "missing_fragment", "unexpected_fragment"),
        "Non-virtual manifests match a sibling Compose fragment; virtual manifests have none.",
        _check_fragment_containers,
        needs_services_root=True,
    ),
    ValidatorRule(
        "fragment_include_contract",
        ("missing_fragment_manifest", "fragment_include_drift"),
        "Every on-disk Compose fragment has a manifest and appears exactly once in the root include list.",
        _check_fragment_includes,
        needs_services_root=True,
    ),
    ValidatorRule(
        "manifest_documentation",
        (
            "missing_documentation",
            "invalid_documentation",
            "invalid_documentation_exception",
            "documentation_exception_conflict",
        ),
        "Every manifest has safe Markdown documentation or a specific validated exception.",
        _check_manifest_documentation,
        needs_services_root=True,
    ),
)
