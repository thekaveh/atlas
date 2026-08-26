"""Stale curated Ollama model reference cleanup (v3 → v4).

The curated Ollama catalog (``services/ollama/models.yaml``) is
maintainer-edited and its entries get renamed or retired over time —
e.g. ``qwen3.6`` was retired in favor of ``qwen3.8`` (2026-08, #535
follow-ups). A ``.env`` written while the OLD name was curated keeps
pinning it after the rename, and nothing in the wizard notices:

  * ``LITELLM_DEFAULT_MODEL`` / ``LITELLM_VISION_MODEL`` point LiteLLM
    at a model ``ollama-pull`` no longer knows to fetch and the
    upstream registry may not even serve any more.
  * ``OLLAMA_USER_MODELS`` carries the retired tag forward, so
    ``ollama-pull`` tries (and fails) to pull it on every start.
  * The wizard's own "default chat model" step re-derives its options
    from the (stale) ``OLLAMA_USER_MODELS`` CSV and — because an
    unrecognized name is classified as a plain content model rather
    than excluded — happily re-offers the retired name as the
    pre-selected default, forever, without the user doing anything
    wrong. Worse: the "default vision model" step's pre-check for
    *whether to even show the step* also derives from that same stale
    CSV, and a retired name is never classified as vision-capable —
    so the step that would let the user fix ``LITELLM_VISION_MODEL``
    silently SKIPS itself. Runtime detection inside the wizard step
    cannot repair that on its own; the ``.env`` value has to be fixed
    directly. Hence a migration (matching v1–v3's existing "rewrite
    stale persisted values once" pattern).

    Deliberately NOT a generic runtime fallback in the wizard step
    ("if the persisted default isn't in the curated catalog, don't
    offer it"): ``LITELLM_DEFAULT_MODEL`` / ``LITELLM_VISION_MODEL``
    are explicitly allowed to point at ANY Ollama model the user
    pulled from the live ollama.com/library, not just the curated
    handful — see ``wizard/llm_steps.py::_classify``'s "not in
    catalog → live-discovered / custom" branch. A blanket "not
    curated → treat as stale" rule would silently override that
    legitimate choice. Only a name this migration POSITIVELY KNOWS
    was a retired curated default (below) is ever rewritten.

This module is the FROZEN snapshot of the v3→v4 migration at
2026-08-23. Do NOT edit when the catalog changes again — author a
sibling migration_v5.py and add the new retirement to
``_RENAMED_OLLAMA_MODELS`` there.

Triggered from start.py::run_port_migration when needs_migration()
returns True. After successful apply, call stamp_version() to update
the sentinel to 4.

Per project_env_read_inline_comment_bug.md: inline comments on blank-
value lines silently break auto-gen; this migration strips them like
v1-v3 already do.
"""
from __future__ import annotations
from utils.atomic_write import env_lines

import re
from pathlib import Path

from utils.atomic_write import atomic_write_text, create_private_backup


_SENTINEL = "BOOTSTRAPPER_PORT_LAYOUT_VERSION"

# Tolerant sentinel matcher (mirrors migration_v1 / v2 / v3 conventions).
_SENTINEL_RE = re.compile(
    r"""^\s*BOOTSTRAPPER_PORT_LAYOUT_VERSION\s*=\s*
        (["']?)(\d*)\1
        \s*(?:\#.*)?\s*$""",
    re.VERBOSE,
)

# Known curated-model retirements: OLD family root → NEW family root.
# Family root = the model name with any ":tag" suffix stripped. Applied
# to LITELLM_DEFAULT_MODEL / LITELLM_VISION_MODEL (any tag of the old
# family is replaced by the new family's current catalog entry) AND to
# individual OLLAMA_USER_MODELS entries (same rule, scoped to exactly
# this table so a user's OTHER, deliberately-picked, non-curated
# ollama.com/library selections are never touched).
#
# Extend this table (don't replace it) the next time a curated default
# gets renamed — this migration only fires once per .env, gated by the
# sentinel, so a row added later has no effect on a .env that already
# migrated past v4; it only matters for the NEXT rename's migration.
_RENAMED_OLLAMA_MODELS: dict[str, str] = {
    "qwen3.6": "qwen3.8",
}

# LiteLLM default-model vars this migration inspects, and which
# CatalogEntry capability field ("content" / "vision") governs whether
# a catalog entry is a sane replacement for that role.
_MODEL_VAR_ROLES: dict[str, str] = {
    "LITELLM_DEFAULT_MODEL": "content",
    "LITELLM_VISION_MODEL": "vision",
}


def _family_root(name: str) -> str:
    """Strip an ``ollama/`` provider prefix and a trailing ``:tag``."""
    n = name[len("ollama/"):] if name.startswith("ollama/") else name
    return n.split(":", 1)[0]


def _current_ollama_catalog():
    """Live curated Ollama catalog, imported lazily so a catalog-load
    failure (e.g. a malformed models.yaml mid-edit) can't prevent the
    OTHER migrations in the chain from running. ``None`` distinguishes a
    retryable load failure from a successfully loaded (possibly empty) catalog.
    """
    try:
        from utils.llm_catalog import ollama_entries
        return ollama_entries()
    except Exception:  # noqa: BLE001 — see docstring above
        return None


def _resolve_stale_litellm_value(value: str, *, role: str, catalog) -> str | None:
    """Return a replacement ``ollama/<name>`` for *value* if its family
    root is a KNOWN curated retirement (``_RENAMED_OLLAMA_MODELS``);
    ``None`` otherwise — including when the value simply isn't in the
    curated catalog at all.

    Deliberately conservative: ``LITELLM_DEFAULT_MODEL`` /
    ``LITELLM_VISION_MODEL`` are NOT restricted to the curated catalog
    — ``wizard/llm_steps.py::build_default_model_steps`` happily lets
    a user pin any Ollama model they pulled from the live
    ollama.com/library (not just the ~5-entry curated set) as their
    default. Treating "absent from the curated catalog" alone as
    "stale" would silently clobber that deliberate choice. Only a
    name this migration POSITIVELY KNOWS was a retired curated
    default (the explicit rename table) gets rewritten — same
    philosophy as migration_v3's explicit ``_TRANSLATION`` table for
    ``COMFYUI_MODEL_SET``, no generic "not found → guess" heuristic.
    The ``role`` parameter is accepted for symmetry with the call site
    and potential future per-role rename tables; it isn't needed to
    resolve a same-family rename (the table already encodes intent).
    """
    v = (value or "").strip()
    if not v.startswith("ollama/"):
        return None
    root = _family_root(v)
    new_root = _RENAMED_OLLAMA_MODELS.get(root)
    if new_root is None:
        return None  # not a known rename — leave it alone, even if uncurated
    match = next(
        (e for e in catalog if e.name.split(":", 1)[0] == new_root), None,
    )
    if match is not None:
        return f"ollama/{match.name}"
    return None  # rename target itself isn't curated (shouldn't happen) — no-op


def _resolve_stale_ollama_user_models(csv_value: str, catalog) -> tuple[str, list[str]]:
    """Rewrite only ``OLLAMA_USER_MODELS`` entries whose family root is
    a KNOWN rename (``_RENAMED_OLLAMA_MODELS``). Every other entry —
    including anything simply absent from the curated catalog, which
    is completely normal for a model the user hand-picked from the
    live ollama.com/library multiselect — is left untouched; this
    migration only repairs a known-stale curated reference, not
    "anything not curated".

    Returns ``(new_csv, changes)`` where ``changes`` is a list of
    ``"old -> new"`` strings for the printed changelog.
    """
    items = [x.strip() for x in csv_value.split(",") if x.strip()]
    out: list[str] = []
    changes: list[str] = []
    for item in items:
        root = item.split(":", 1)[0]
        new_root = _RENAMED_OLLAMA_MODELS.get(root)
        if new_root is None:
            out.append(item)
            continue
        match = next(
            (e for e in catalog if e.name.split(":", 1)[0] == new_root), None,
        )
        replacement = match.name if match is not None else new_root
        if replacement != item:
            changes.append(f"{item} -> {replacement}")
        out.append(replacement)
    # dict.fromkeys de-dups preserving first-seen order — a rename can
    # collide with an already-present current-name entry, e.g. both
    # "qwen3.6:latest" and "qwen3.8:latest" present at once.
    return ",".join(dict.fromkeys(out)), changes


def _parse_env(text: str) -> dict[str, str]:
    """Plain k=v parser; handles CRLF, ignores blank/comment lines."""
    result: dict[str, str] = {}
    for line in env_lines(text):
        line = line.rstrip("\r\n").strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            v, _, _ = v.partition("#")
            result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def _replace_or_append(text: str, key: str, value: str) -> str:
    """Find a ``KEY=...`` line and replace it; otherwise append at end."""
    new_lines: list[str] = []
    replaced = False
    for raw in env_lines(text, keepends=True):
        stripped = raw.lstrip()
        if stripped.startswith(f"{key}="):
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
    """True iff .env's BOOTSTRAPPER_PORT_LAYOUT_VERSION < 4 (or absent)."""
    if not env_path.is_file():
        return False  # fresh install — schema already current
    for line in env_lines(env_path.read_text(encoding="utf-8")):
        m = _SENTINEL_RE.match(line)
        if m:
            try:
                return int(m.group(2) or 0) < 4
            except ValueError:
                return True
    return True


def _rewrite_stale_references(text: str, parsed: dict[str, str], catalog) -> tuple[str, list[str]]:
    """Apply every known-stale rewrite (the two LiteLLM default-model
    vars + OLLAMA_USER_MODELS) to *text*. Returns ``(new_text,
    changes)`` — ``changes`` is empty when nothing was stale, in which
    case ``new_text is text`` (no-op, safe to skip the write).
    """
    changes: list[str] = []
    for var, role in _MODEL_VAR_ROLES.items():
        old_value = parsed.get(var, "")
        if not old_value:
            continue
        replacement = _resolve_stale_litellm_value(old_value, role=role, catalog=catalog)
        if replacement is None or replacement == old_value:
            continue
        text = _replace_or_append(text, var, replacement)
        changes.append(f"{var}: {old_value!r} -> {replacement!r}")

    old_user_models = parsed.get("OLLAMA_USER_MODELS", "")
    if old_user_models:
        new_user_models, um_changes = _resolve_stale_ollama_user_models(
            old_user_models, catalog,
        )
        if um_changes:
            text = _replace_or_append(text, "OLLAMA_USER_MODELS", new_user_models)
            changes.append("OLLAMA_USER_MODELS: " + "; ".join(um_changes))
    return text, changes


def apply(env_path: Path) -> bool:
    """Rewrite .env in place. Idempotent on re-run.

    * Replaces LITELLM_DEFAULT_MODEL / LITELLM_VISION_MODEL when they
      reference a retired curated Ollama model.
    * Rewrites any OLLAMA_USER_MODELS entry matching a known rename.
    * Backs up .env to .env.backup.v4.<YYYYMMDDTHHMMSS> before any
      write (version-stamped so it can't collide with v1/v2/v3 backups
      in one chain).
    * Does nothing if sentinel is already >= 4, or if there's nothing
      stale to rewrite (still safe/idempotent to call).
    """
    if not env_path.is_file():
        return True

    text = env_path.read_text(encoding="utf-8")
    parsed = _parse_env(text)

    try:
        current = int(parsed.get(_SENTINEL, "0"))
    except ValueError:
        current = 0
    if current >= 4:
        return True  # already migrated — idempotent

    catalog = _current_ollama_catalog()
    if catalog is None:
        return False  # retry on the next startup; caller must not stamp v4

    new_text, changes = _rewrite_stale_references(text, parsed, catalog)
    if not changes:
        return True

    backup = create_private_backup(env_path, version="v4")
    atomic_write_text(env_path, new_text, mode=0o600)
    print(
        "[migration_v4] rewrote stale curated Ollama model reference(s) "
        f"(backup: {backup.name}):",
        flush=True,
    )
    for c in changes:
        print(f"[migration_v4]   {c}", flush=True)
    return True


def stamp_version(env_path: Path, version: int = 4) -> None:
    """Append or update BOOTSTRAPPER_PORT_LAYOUT_VERSION in .env to 4."""
    if not env_path.is_file():
        return
    lines = env_lines(env_path.read_text(encoding="utf-8"), keepends=True)
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
