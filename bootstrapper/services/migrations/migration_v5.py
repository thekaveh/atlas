"""Ensure persisted Weaviate module lists enable native filesystem backups."""

from __future__ import annotations

import re
from pathlib import Path

from utils.atomic_write import atomic_write_text, create_private_backup, env_lines


_SENTINEL = "BOOTSTRAPPER_PORT_LAYOUT_VERSION"
_SENTINEL_RE = re.compile(
    r'^(?P<prefix>[ \t]*BOOTSTRAPPER_PORT_LAYOUT_VERSION[ \t]*=[ \t]*)'
    r'(?P<quote>["\']?)(?P<version>\d*)(?P=quote)'
    r'(?P<suffix>[ \t]*(?:#.*)?)(?P<newline>\r?\n)?$'
)
_MODULE_RE = re.compile(
    r"^(?P<prefix>[ \t]*WEAVIATE_ENABLE_MODULES[ \t]*=[ \t]*)"
    r'(?:(?:"(?P<double>[^"\r\n]*)")|(?:\'(?P<single>[^\'\r\n]*)\')|'
    r"(?P<plain>[^#\"'\r\n]*?))"
    r"(?P<suffix>[ \t]*(?:#.*)?)(?P<newline>\r?\n)?$"
)


class MigrationV5Error(RuntimeError):
    """The persisted env grammar is ambiguous and must be fixed manually."""


def _version(text: str) -> int:
    for line in env_lines(text):
        match = _SENTINEL_RE.match(line)
        if match:
            try:
                return int(match.group("version") or 0)
            except ValueError:
                return 0
    return 0


def needs_migration(env_path: Path) -> bool:
    return env_path.is_file() and _version(env_path.read_text(encoding="utf-8")) < 5


def _add_module(text: str) -> tuple[str, bool]:
    lines = env_lines(text, keepends=True)
    matches = [(index, match) for index, raw in enumerate(lines) if (match := _MODULE_RE.match(raw))]
    assignments = [
        raw for raw in lines
        if re.match(r"^[ \t]*WEAVIATE_ENABLE_MODULES[ \t]*=", raw)
    ]
    if len(assignments) != len(matches):
        raise MigrationV5Error("malformed WEAVIATE_ENABLE_MODULES assignment")
    if len(matches) > 1:
        raise MigrationV5Error("duplicate WEAVIATE_ENABLE_MODULES assignments")
    found = bool(matches)
    changed = False
    if matches:
        index, match = matches[0]
        if match.group("double") is not None:
            value, quote = match.group("double"), '"'
        elif match.group("single") is not None:
            value, quote = match.group("single"), "'"
        else:
            value, quote = match.group("plain"), ""
        modules = [item.strip() for item in value.split(",") if item.strip()]
        if "backup-filesystem" not in modules:
            modules.append("backup-filesystem")
            changed = True
            lines[index] = (
                f"{match.group('prefix')}{quote}{','.join(modules)}{quote}"
                f"{match.group('suffix')}{match.group('newline') or ''}"
            )
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append("WEAVIATE_ENABLE_MODULES=backup-filesystem\n")
        changed = True
    return "".join(lines), changed


def apply(env_path: Path) -> bool:
    if not env_path.is_file():
        return True
    text = env_path.read_text(encoding="utf-8")
    if _version(text) >= 5:
        return True
    updated, changed = _add_module(text)
    if changed:
        backup = create_private_backup(env_path, version="v5")
        atomic_write_text(env_path, updated, mode=0o600)
        print(
            "[migration_v5] enabled backup-filesystem in WEAVIATE_ENABLE_MODULES "
            f"(backup: {backup.name})",
            flush=True,
        )
    return True


def stamp_version(env_path: Path, version: int = 5) -> None:
    if not env_path.is_file():
        return
    text = env_path.read_text(encoding="utf-8")
    if _version(text) >= version:
        return
    lines = env_lines(text, keepends=True)
    for index, line in enumerate(lines):
        if match := _SENTINEL_RE.match(line):
            quote = match.group("quote")
            lines[index] = (
                f"{match.group('prefix')}{quote}{version}{quote}"
                f"{match.group('suffix')}{match.group('newline') or ''}"
            )
            break
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{_SENTINEL}={version}\n")
    atomic_write_text(env_path, "".join(lines), mode=0o600)
