"""COMFYUI_MODEL_SET → COMFYUI_USER_MODELS .env schema migration (v2 → v3).

Translates the old ``COMFYUI_MODEL_SET=minimal|sd15|sdxl|full`` enum to
``COMFYUI_USER_MODELS`` (a CSV of catalog names) and adds the sidecar
var introduced in the model-picker feature.

This module is the FROZEN snapshot of the v2→v3 migration at 2026-05-29.
Do NOT edit when the schema changes again — author a sibling migration_v4.py.

Triggered from start.py::run_port_migration when needs_migration() returns True.
After successful apply, call stamp_version() to update the sentinel to 3.

Per project_env_read_inline_comment_bug.md: inline comments on blank-value
lines silently break auto-gen; this migration appends new vars without inline
comments.

Per project_env_write_semantics.md: new vars are appended to the bottom.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from utils.atomic_write import atomic_write_text, create_private_backup


_SENTINEL = "BOOTSTRAPPER_PORT_LAYOUT_VERSION"

# Tolerant sentinel matcher (mirrors migration_v1 / migration_v2 conventions).
_SENTINEL_RE = re.compile(
    r"""^\s*BOOTSTRAPPER_PORT_LAYOUT_VERSION\s*=\s*
        (["']?)(\d*)\1
        \s*(?:\#.*)?\s*$""",
    re.VERBOSE,
)

_OLD_VAR = "COMFYUI_MODEL_SET"

# Translation table: enum value → CSV of catalog names.
# Names must match CATALOG rows (curated layer — always merged), or
# comfyui-catalog-init activates nothing and the translated selection is
# a silent no-op. The earlier sd15-pruned-emaonly / sdxl-base-1.0
# spellings existed nowhere in the catalog.
_TRANSLATION: dict[str, str] = {
    "minimal": "v1-5-pruned-emaonly,vae-ft-mse-840000-ema-pruned",
    "sd15":    "v1-5-pruned-emaonly,vae-ft-mse-840000-ema-pruned",
    "sdxl":    "sd_xl_base_1.0,sdxl-vae",
    "full":    "v1-5-pruned-emaonly,vae-ft-mse-840000-ema-pruned,sd_xl_base_1.0,sdxl-vae",
    "":        "",
}


def _translate_model_set(value: str) -> str:
    """Return the CSV of catalog names for *value*, or ``""`` with a warning."""
    v = value.strip()
    if v in _TRANSLATION:
        return _TRANSLATION[v]
    print(
        f"[migration_v3] Unrecognized COMFYUI_MODEL_SET={v!r}; "
        f"leaving COMFYUI_USER_MODELS empty.",
        file=sys.stderr,
        flush=True,
    )
    return ""


def _parse_env(text: str) -> dict[str, str]:
    """Plain k=v parser; handles CRLF, ignores blank/comment lines."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.rstrip("\r\n").strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            # Strip inline comments like migration_v1 does — without this,
            # `COMFYUI_MODEL_SET=sdxl  # note` parsed as "sdxl  # note",
            # missed the translation table, and silently dropped the
            # user's model selection.
            v, _, _ = v.partition("#")
            result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def _strip_old_var_lines(text: str, var: str) -> str:
    """Remove ``VAR=...`` lines + any preceding consecutive comment block."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].lstrip()
        if stripped.startswith(f"{var}=") or (
            # handle CRLF: the stripped form has the rstrip applied
            stripped.rstrip("\r\n").startswith(f"{var}=")
        ):
            # drop the preceding inline comment block too
            while out and out[-1].lstrip().startswith("#"):
                out.pop()
            i += 1
            continue
        out.append(lines[i])
        i += 1
    return "".join(out)


def _union_csv(a: str, b: str) -> str:
    """Merge two CSV strings, preserving order and deduplicating."""
    items = [x.strip() for x in f"{a},{b}".split(",") if x.strip()]
    seen: list[str] = []
    for x in items:
        if x not in seen:
            seen.append(x)
    return ",".join(seen)


def _replace_or_append(text: str, key: str, value: str) -> str:
    """Find a ``KEY=...`` line and replace it; otherwise append at end."""
    new_lines: list[str] = []
    replaced = False
    for raw in text.splitlines(keepends=True):
        stripped = raw.lstrip()
        if stripped.startswith(f"{key}="):
            # Preserve any leading indent the user may have.
            indent = raw[: len(raw) - len(stripped)]
            new_lines.append(f"{indent}{key}={value}\n")
            replaced = True
        else:
            new_lines.append(raw)
    if not replaced:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(f"{key}={value}\n")
    return "".join(new_lines)


def needs_migration(env_path: Path) -> bool:
    """True iff .env's BOOTSTRAPPER_PORT_LAYOUT_VERSION < 3 (or absent)."""
    if not env_path.is_file():
        return False  # fresh install — schema already current
    for line in env_path.read_text(encoding="utf-8").splitlines():
        m = _SENTINEL_RE.match(line)
        if m:
            try:
                return int(m.group(2) or 0) < 3
            except ValueError:
                return True
    return True


def apply(env_path: Path) -> None:
    """Rewrite .env in place. Idempotent on re-run.

    * Reads COMFYUI_MODEL_SET and translates it to COMFYUI_USER_MODELS.
    * Unions with any existing COMFYUI_USER_MODELS value.
    * Removes the old COMFYUI_MODEL_SET line (plus preceding comment block).
    * Appends COMFYUI_CUSTOM_MODELS_FILE if absent.
    * Backs up .env to .env.backup.v3.<YYYYMMDDTHHMMSS> before any write
      (version-stamped so it can't collide with v1/v2 backups in one chain).
    * Does nothing if sentinel is already >= 3.
    """
    if not env_path.is_file():
        return

    text = env_path.read_text(encoding="utf-8")
    parsed = _parse_env(text)

    try:
        current = int(parsed.get(_SENTINEL, "0"))
    except ValueError:
        current = 0

    if current >= 3:
        return  # already migrated — idempotent

    # Backup. Version-stamped so it can't collide with v1's backup in a single
    # v1→v2→v3 chain (both used to name it ``.env.backup.<ts>`` at second
    # precision, so v3 silently overwrote v1's pristine snapshot).
    backup = create_private_backup(env_path, version="v3")

    # Translate COMFYUI_MODEL_SET → catalog CSV.
    old_value = parsed.get(_OLD_VAR, "")
    translated = _translate_model_set(old_value)

    # Union with any pre-existing COMFYUI_USER_MODELS.
    existing_user_models = parsed.get("COMFYUI_USER_MODELS", "")
    # Order matters: existing user-set entries first, enum-derived second,
    # so user customizations sort before the migrated defaults.
    final_user_models = _union_csv(existing_user_models, translated)

    # Strip old var and its preceding comment block.
    new_text = _strip_old_var_lines(text, _OLD_VAR)

    # Insert / update new vars.
    new_text = _replace_or_append(new_text, "COMFYUI_USER_MODELS", final_user_models)
    # Append-if-absent only: a user-customized sidecar path must survive
    # the migration (the docstring contract; overwriting was a bug).
    if "COMFYUI_CUSTOM_MODELS_FILE" not in parsed:
        new_text = _replace_or_append(new_text, "COMFYUI_CUSTOM_MODELS_FILE", "/custom-models.yaml")

    atomic_write_text(env_path, new_text, mode=0o600)

    print(
        f"[migration_v3] COMFYUI_MODEL_SET={old_value!r} → "
        f"COMFYUI_USER_MODELS={final_user_models!r} (backup: {backup.name})",
        flush=True,
    )


def stamp_version(env_path: Path, version: int = 3) -> None:
    """Append or update BOOTSTRAPPER_PORT_LAYOUT_VERSION in .env to 3."""
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
    atomic_write_text(env_path, "".join(lines), mode=0o600)
