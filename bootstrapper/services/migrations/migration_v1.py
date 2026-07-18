"""
Port-layout v0 → v1 migration.

v0 layout: hand-edited per-manifest `default:` values in service.yml.
v1 layout: topology slot allocator (services/topology.py).

This module:
  * Records the v0 OFFSETS (each port_var's offset from BASE_PORT at the time
    of this migration's authoring). Used to detect "user is on default" so we
    only rewrite ports the user has not customized.
  * Applies the rewrite: for each port_var, if .env[var] == BASE_PORT + v0_offset,
    rewrite to BASE_PORT + v1_offset. Otherwise leave alone.
  * Backs up .env to .env.backup.v1.<YYYYMMDDTHHMMSS> before any write
    (version-stamped so it can't collide with v2/v3 backups in one chain).

This is the FROZEN snapshot from 2026-05-15. Do NOT edit when the layout
changes again — author a sibling migration_v2.py with its own snapshot.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


# Tolerant matcher for ``BOOTSTRAPPER_PORT_LAYOUT_VERSION=1`` lines.
# Accepts surrounding whitespace, quoted or bare values, optional trailing
# comments, and CR-terminated lines (CRLF .env files on Windows-edited
# checkouts). Group 2 captures the numeric value.
_SENTINEL_RE = re.compile(
    r"""^\s*BOOTSTRAPPER_PORT_LAYOUT_VERSION\s*=\s*
        (["']?)(\d*)\1
        \s*(?:\#.*)?\s*$""",
    re.VERBOSE,
)


# Frozen v0 layout: port_var → offset-from-BASE_PORT at the time the
# topology rework shipped. Pulled by hand from each manifest's `default:`
# field in the pre-migration codebase (commit 87ba9c3 baseline + later
# adds). DO NOT EDIT — this is a historical snapshot.
V0_OFFSETS: dict[str, int] = {
    "SUPABASE_DB_PORT": 0,
    "REDIS_PORT": 1,
    "KONG_HTTP_PORT": 2,
    "KONG_HTTPS_PORT": 3,
    "SUPABASE_META_PORT": 4,
    "SUPABASE_STORAGE_PORT": 5,
    "SUPABASE_AUTH_PORT": 6,
    "SUPABASE_API_PORT": 7,
    "SUPABASE_REALTIME_PORT": 8,
    "SUPABASE_STUDIO_PORT": 9,
    "GRAPH_DB_PORT": 10,
    "GRAPH_DB_DASHBOARD_PORT": 11,
    "LITELLM_PORT": 12,
    "LOCAL_DEEP_RESEARCHER_PORT": 13,
    "SEARXNG_PORT": 14,
    "OPEN_WEB_UI_PORT": 15,
    "BACKEND_PORT": 16,
    "N8N_PORT": 17,
    "COMFYUI_PORT": 18,
    "WEAVIATE_PORT": 19,
    "WEAVIATE_GRPC_PORT": 20,
    "DOC_PROCESSOR_PORT": 21,
    "STT_PROVIDER_PORT": 22,
    "TTS_PROVIDER_PORT": 23,
    "OPENCLAW_GATEWAY_PORT": 24,
    "OPENCLAW_BRIDGE_PORT": 25,
    "SPEACHES_PORT": 26,
    "CHATTERBOX_PORT": 27,
    "HERMES_API_PORT": 28,
    "HERMES_DASHBOARD_PORT": 29,
    "MINIO_PORT": 30,
    "MINIO_CONSOLE_PORT": 31,
    "JUPYTERHUB_PORT": 48,
}


@dataclass
class MigrationResult:
    rewritten: dict[str, tuple[str, str]]   # var → (old_value, new_value)
    preserved: list[str]                    # vars the user had customized
    backup_path: Optional[Path]


def apply(
    env_path: Path,
    new_defaults: dict[str, int],
    base_port: int,
) -> MigrationResult:
    """Rewrite .env in place; back it up first.

    Only port vars whose current value EQUALS `base_port + V0_OFFSETS[var]` are
    rewritten — that is "the user accepted the default." Anything else (custom
    port, blank line, missing var) is left alone.
    """
    if not env_path.is_file():
        return MigrationResult({}, [], None)

    # Version-stamp the backup name so it can't collide with a later migration's
    # backup in the same chain (v1→v2→v3 all run sub-second; a shared
    # ``.env.backup.<ts>`` name at second precision let v3 overwrite v1's
    # pristine snapshot — the one that matters for rollback).
    backup_path = env_path.with_name(
        f"{env_path.name}.backup.v1.{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    )
    # Clamp the backup to the source's mode BEFORE writing — by the time
    # migrations run, .env holds generated secrets, and a user-chmod'd 0600 .env
    # must not be backed up at the umask default (0644). (Mirrors migration_v3.)
    backup_path.touch()
    os.chmod(backup_path, os.stat(env_path).st_mode)
    backup_path.write_text(env_path.read_text(encoding="utf-8"), encoding="utf-8")

    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    rewritten: dict[str, tuple[str, str]] = {}
    preserved: list[str] = []
    out: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key, _, raw_value = stripped.partition("=")
        key = key.strip()
        # Split value vs. trailing inline comment, preserving the
        # whitespace that separated them so the rewritten line keeps
        # the user's formatting (e.g. ``LITELLM_PORT=63012  # label``).
        value_part, sep, comment_part = raw_value.partition("#")
        value = value_part.strip()
        if sep:
            # Recover the whitespace between the value and the ``#``
            # by stripping the *right* side of value_part only.
            trailing_ws = value_part[len(value_part.rstrip()):]
            comment_tail = f"{trailing_ws}#{comment_part}"
        else:
            comment_tail = ""
        # Preserve the line's existing newline style (LF / CRLF / none).
        if line.endswith("\r\n"):
            eol = "\r\n"
        elif line.endswith("\n"):
            eol = "\n"
        else:
            eol = ""
        if key in V0_OFFSETS and key in new_defaults:
            expected_old = str(base_port + V0_OFFSETS[key])
            new_value = str(new_defaults[key])
            if value == expected_old and new_value != expected_old:
                out.append(f"{key}={new_value}{comment_tail}{eol}")
                rewritten[key] = (expected_old, new_value)
                continue
            if value != expected_old:
                preserved.append(key)
        out.append(line)

    # Atomic write (tmp + replace), mirroring migration_v2/v3: a crash or
    # disk-full mid-write must not truncate the user's .env (all their secrets).
    # A version-stamped backup already exists above, but the live file should
    # never be left half-written.
    tmp = env_path.with_name(f"{env_path.name}.v1.tmp")
    tmp.touch()
    os.chmod(tmp, os.stat(env_path).st_mode)
    tmp.write_text("".join(out), encoding="utf-8")
    tmp.replace(env_path)
    return MigrationResult(rewritten, preserved, backup_path)


def needs_migration(env_path: Path) -> bool:
    """True iff .env is missing the v1 sentinel or has it at < 1.

    Tolerant of whitespace around ``=``, quoted values, CRLF line
    endings, and trailing ``#`` comments — any line that *looks like*
    a sentinel assignment counts, so a hand-edited ``VAR = 1`` doesn't
    silently re-trigger the migration.
    """
    if not env_path.is_file():
        return False  # fresh install — defaults already correct
    for line in env_path.read_text(encoding="utf-8").splitlines():
        m = _SENTINEL_RE.match(line)
        if m:
            try:
                return int(m.group(2) or 0) < 1
            except ValueError:
                return True
    return True


def stamp_version(env_path: Path, version: int = 1) -> None:
    """Append or update BOOTSTRAPPER_PORT_LAYOUT_VERSION in .env.

    Matches existing sentinel lines tolerantly (see ``needs_migration``)
    so an in-place rewrite finds the user's hand-edited variant rather
    than appending a duplicate.
    """
    if not env_path.is_file():
        return
    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    found = False
    for i, line in enumerate(lines):
        if _SENTINEL_RE.match(line):
            lines[i] = f"BOOTSTRAPPER_PORT_LAYOUT_VERSION={version}\n"
            found = True
            break
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"BOOTSTRAPPER_PORT_LAYOUT_VERSION={version}\n")
    env_path.write_text("".join(lines), encoding="utf-8")
