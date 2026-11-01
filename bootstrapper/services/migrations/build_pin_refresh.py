"""Refresh build pins that still hold a default an earlier Atlas shipped.

Backfill never changes a non-blank ``.env`` value, and every ``.env`` starts
as a copy of ``.env.example``. So when an update moved a build pin, an
existing install kept the old value: the asset-worker build stopped with a
glTF version mismatch, and Local Deep Researcher rejected its new runtime lock
and restarted in a loop.

``RETIRED_DEFAULTS`` lists every value Atlas shipped before the current one.
A refresh rewrites an assignment only when its value is one of them, and only
to the current ``.env.example`` value. Any other value is an operator override
and stays. It runs on every start, so unlike the numbered migrations it keeps
no sentinel: a later bump only appends the value it replaces.
"""

from __future__ import annotations

import re
from pathlib import Path

from utils.atomic_write import atomic_write_text, create_private_backup, env_lines


#: Tool-owned build pins, each with every default an earlier Atlas shipped.
#: When a bump lands, append the value it replaces.
RETIRED_DEFAULTS: dict[str, frozenset[str]] = {
    "ASSET_BAKER_BLENDER_VERSION": frozenset(),
    "ASSET_BAKER_BLENDER_SHA256": frozenset(),
    "ASSET_WORKER_GLTF_TRANSFORM_VERSION": frozenset({"4.4.1", "4.5.0"}),
    "LOCAL_DEEP_RESEARCHER_REF": frozenset(),
    "LOCAL_DEEP_RESEARCHER_LANGGRAPH_CLI_VERSION": frozenset({"0.4.31"}),
    "LOCAL_DEEP_RESEARCHER_UPSTREAM_LOCK_SHA256": frozenset(),
}

_ASSIGNMENT = re.compile(
    r"^(?P<prefix>[ \t]*(?:export[ \t]+)?(?P<key>[A-Z0-9_]+)[ \t]*=[ \t]*)"
    r"(?P<quote>[\"']?)(?P<value>[^\"'#\r\n]*?)(?P=quote)"
    r"(?P<suffix>[ \t]*(?:#.*)?)(?P<newline>\r?\n)?$"
)


def current_defaults(example_text: str) -> dict[str, str]:
    """The ``.env.example`` value of each pin in ``RETIRED_DEFAULTS``."""
    matches = (_ASSIGNMENT.match(line) for line in env_lines(example_text))
    return {
        match["key"]: match["value"]
        for match in matches
        if match and match["key"] in RETIRED_DEFAULTS
    }


def refresh_text(
    text: str, current: dict[str, str], keep: frozenset[str] = frozenset(),
) -> tuple[str, list[tuple[str, str, str]]]:
    """Rewrite each pin assignment that holds a retired default.

    ``keep`` names keys an operator pinned this run (``.env.user``,
    ``ATLAS_ENV_USER_FILE`` or a consumer manifest); they are left alone.
    Returns the new text and one ``(key, old, new)`` per rewritten line.
    Quoting, ``export``, inline comments and line endings are kept.
    """
    lines = env_lines(text, keepends=True)
    changes: list[tuple[str, str, str]] = []
    for index, line in enumerate(lines):
        match = _ASSIGNMENT.match(line)
        if not match or match["key"] in keep or match["value"] not in RETIRED_DEFAULTS.get(match["key"], ()):
            continue
        new = current.get(match["key"], "")
        if new and new != match["value"]:
            quote = match["quote"]
            lines[index] = (f"{match['prefix']}{quote}{new}{quote}"
                            f"{match['suffix']}{match['newline'] or ''}")
            changes.append((match["key"], match["value"], new))
    return "".join(lines), changes


def refresh(
    env_path: Path, example_path: Path, keep: frozenset[str] = frozenset(),
) -> tuple[list[tuple[str, str, str]], Path | None]:
    """Refresh retired pin defaults in *env_path* from *example_path*.

    Backs ``.env`` up privately before the atomic rewrite, and writes nothing
    when no pin holds a retired default. Returns the changes and the backup.
    """
    if not env_path.is_file() or not example_path.is_file():
        return [], None
    current = current_defaults(example_path.read_text(encoding="utf-8"))
    with env_path.open(encoding="utf-8", newline="") as handle:
        updated, changes = refresh_text(handle.read(), current, keep)
    if not changes:
        return [], None
    backup = create_private_backup(env_path, version="build-pins")
    atomic_write_text(env_path, updated, mode=0o600)
    return changes, backup
