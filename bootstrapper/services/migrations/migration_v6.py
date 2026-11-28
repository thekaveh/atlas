"""Rename the storage variables in .env from MINIO_* to SILO_* (storage rename).

Atlas's object storage is Silo (pgsty/silo, a fork of MinIO). Every Atlas-owned
MINIO_* name becomes SILO_*. The mapping lives in ``services/storage_aliases``.
Silo server keys (MINIO_BROWSER_REDIRECT_URL, MINIO_PROMETHEUS_AUTH_TYPE, and
the container-side names in the silo compose fragment) are never touched.

Rules:
* A MINIO_* line is renamed in place. Its value and inline comment are kept.
* If the SILO_* key is already assigned, the MINIO_* line is commented out
  (the SILO_* key already wins in the bootstrapper's reader).
* If a MINIO_* key is assigned more than once, only the last assignment is
  renamed. Earlier ones are commented out, so the effective value is unchanged.

Delete this migration in the release after 1.2, together with the alias
fallback in ``services/storage_aliases.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

from services.storage_aliases import silo_name_for
from utils.atomic_write import atomic_write_text, create_private_backup, env_lines


_SENTINEL = "BOOTSTRAPPER_PORT_LAYOUT_VERSION"
_SENTINEL_RE = re.compile(
    r'^(?P<prefix>[ \t]*(?:export[ \t]+)?BOOTSTRAPPER_PORT_LAYOUT_VERSION[ \t]*=[ \t]*)'
    r'(?P<quote>["\']?)(?P<version>\d*)(?P=quote)'
    r'(?P<suffix>[ \t]*(?:#.*)?)(?P<newline>\r?\n)?$'
)
_ASSIGN_RE = re.compile(
    r"^(?P<prefix>[ \t]*(?:export[ \t]+)?)(?P<key>[A-Za-z_][A-Za-z0-9_]*)(?P<rest>[ \t]*=.*)$"
)
# A value may name other storage variables (MINIO_EXTRA_CONSUMERS holds
# BUCKET_VAR:ACCESS_VAR:SECRET_VAR names), so those names are rewritten too.
_REST_RE = re.compile(r"^(?P<eq>[ \t]*=[ \t]*)(?P<value>.*?)(?P<comment>[ \t]+#.*)?$")
_TOKEN_RE = re.compile(r"\bMINIO_[A-Z0-9_]+\b")


def _read_env(env_path: Path) -> str:
    """Read with newlines untouched, so a CRLF .env keeps its line endings."""
    with open(env_path, encoding="utf-8", newline="") as handle:
        return handle.read()

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
    return env_path.is_file() and _version(_read_env(env_path)) < 6


def _rewrite(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (updated text, [(old, new) renamed]). Pure: no file I/O."""
    lines = env_lines(text, keepends=True)
    present = set()
    last_index: dict[str, int] = {}
    for index, raw in enumerate(lines):
        body = raw.rstrip("\r\n")
        match = _ASSIGN_RE.match(body)
        if match:
            present.add(match.group("key"))
            if silo_name_for(match.group("key")) is not None:
                last_index[match.group("key")] = index

    out: list[str] = []
    renamed: list[tuple[str, str]] = []
    for index, raw in enumerate(lines):
        body = raw.rstrip("\r\n")
        eol = raw[len(body):]
        match = _ASSIGN_RE.match(body)
        if match is None:
            out.append(raw)
            continue
        old = match.group("key")
        rest = _rewrite_references(match.group("rest"))
        new = silo_name_for(old)
        if new is None:
            out.append(raw if rest == match.group("rest") else f"{match.group('prefix')}{old}{rest}{eol}")
            continue
        if new in present or last_index[old] != index:
            out.append(f"# {body}  # superseded by {new}; migrated by migration_v6{eol}")
            continue
        out.append(f"{match.group('prefix')}{new}{rest}{eol}")
        renamed.append((old, new))
    return "".join(out), renamed


def _rewrite_references(rest: str) -> str:
    """Rewrite the MINIO_* names a value refers to, keeping the inline comment."""
    match = _REST_RE.match(rest)
    if match is None:
        return rest
    value = _TOKEN_RE.sub(lambda token: silo_name_for(token.group(0)) or token.group(0), match.group("value"))
    return f"{match.group('eq')}{value}{match.group('comment') or ''}"


def apply(env_path: Path) -> bool:
    if not env_path.is_file():
        return True
    text = _read_env(env_path)
    if _version(text) >= 6:
        return True
    updated, renamed = _rewrite(text)
    if renamed or updated != text:
        backup = create_private_backup(env_path, version="v6")
        atomic_write_text(env_path, updated, mode=0o600)
        names = ", ".join(f"{old}->{new}" for old, new in renamed)
        print(
            f"[migration_v6] renamed {len(renamed)} storage variable(s) MINIO_* -> SILO_* "
            f"({names or 'superseded lines commented out'}) (backup: {backup.name})",
            flush=True,
        )
    return True


def stamp_version(env_path: Path, version: int = 6) -> None:
    if not env_path.is_file():
        return
    text = _read_env(env_path)
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
