"""One-shot reorganizer for accreted user .env files.

When ``backfill_missing_env_vars`` was broken by the banner-regex bug
(see commit 0a1565f), every run accreted a new ``(unsectioned)``
trailer at the bottom of the user's .env. This script walks
.env.example as the layout template and splices in the user's actual
values, producing a tidy .env that matches the template's section
ordering. Keys the user has but .env.example doesn't are preserved
under a ``# User-only keys`` trailer at the bottom.

This is a MANUAL operational tool — nothing in CI, the bootstrapper,
or the docs invokes it; run it yourself when an old .env has accreted
trailers. Honors ATLAS_ENV_FILE (deprecated: GENAI_ENV_FILE) like the rest of the stack.

Usage:
    python bootstrapper/scripts/reorg_user_env.py [--dry-run]

Safety: refuses to run if .env.bak.* doesn't exist alongside .env.
You did back up first — right?
"""
from __future__ import annotations
from utils.atomic_write import env_lines

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.atomic_write import atomic_write_text

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# Honor ATLAS_ENV_FILE (deprecated alias: GENAI_ENV_FILE) like every
# other .env consumer (start.py, config_parser, docker_manager,
# key_generator) — a custom-env-file user running this cleanup would
# otherwise reorganize the wrong file. Relative paths anchor at the
# repo root, matching ConfigParser._resolve_env_file_path. We can't
# import ConfigParser here without dragging the rest of the package
# in, so this mirrors the same alias logic locally.
_custom = (
    os.environ.get("ATLAS_ENV_FILE", "").strip()
    or os.environ.get("GENAI_ENV_FILE", "").strip()
)
if _custom:
    _p = Path(_custom).expanduser()
    ENV_PATH = (_p if _p.is_absolute() else REPO_ROOT / _p).resolve()
else:
    ENV_PATH = REPO_ROOT / ".env"
EXAMPLE_PATH = REPO_ROOT / ".env.example"

# `KEY=value` with optional inline comment. We deliberately don't strip
# the inline comment from the captured value here — that's a known
# pitfall (see project_env_read_inline_comment_bug.md).
KEY_VALUE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def _parse_env(text: str) -> dict[str, str]:
    """Return {KEY: raw_value_string} for every KEY=VALUE line.

    Raw value preserved (inline comments included) so we can write it
    back verbatim — the user's hand-edits in their .env stay intact.
    """
    out: dict[str, str] = {}
    for line in env_lines(text):
        m = KEY_VALUE_RE.match(line.lstrip())
        if m:
            out[m.group(1)] = m.group(2)
    return out


def reorganize(example_text: str, user_env_text: str) -> tuple[str, list[str], list[str]]:
    """Walk ``example_text`` line by line. For each KEY=VALUE line,
    splice in the user's value. Preserve the example's banners,
    comments, and ordering verbatim.

    Returns ``(new_env_text, keys_taken_from_user, user_only_keys)``.
    """
    user_values = _parse_env(user_env_text)
    example_keys: set[str] = set()
    out_lines: list[str] = []
    taken: list[str] = []

    for line in env_lines(example_text, keepends=True):
        m = KEY_VALUE_RE.match(line.lstrip())
        if not m:
            out_lines.append(line)
            continue
        key = m.group(1)
        example_keys.add(key)
        if key in user_values:
            taken.append(key)
            # Preserve the leading whitespace of the example line (none
            # in practice for env files, but defensive).
            leading = line[: len(line) - len(line.lstrip())]
            newline = "\n" if line.endswith("\n") else ""
            out_lines.append(f"{leading}{key}={user_values[key]}{newline}")
        else:
            # User doesn't have this key — keep example's default.
            out_lines.append(line)

    # User-only keys: in .env but not in .env.example. These include
    # hand-additions like ML_REPO_PATH, HOST_SSH_DIR, plus any keys
    # that USED to be in .env.example and got removed upstream.
    user_only = sorted(k for k in user_values if k not in example_keys)
    if user_only:
        joined = "".join(out_lines)
        if joined and not joined.endswith("\n"):
            out_lines.append("\n")
        out_lines.append("\n")
        out_lines.append("# " + "─" * 72 + "\n")
        out_lines.append(
            f"# User-only keys (not in .env.example as of "
            f"{date.today().isoformat()})\n"
        )
        out_lines.append(
            "# Either hand-added overrides or keys removed from "
            ".env.example upstream.\n"
        )
        out_lines.append("# " + "─" * 72 + "\n")
        for key in user_only:
            out_lines.append(f"{key}={user_values[key]}\n")

    return "".join(out_lines), taken, user_only


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="Print summary; don't touch .env.")
    args = ap.parse_args()

    if not ENV_PATH.exists():
        print(f"error: {ENV_PATH} not found", file=sys.stderr)
        return 1
    if not EXAMPLE_PATH.exists():
        print(f"error: {EXAMPLE_PATH} not found", file=sys.stderr)
        return 1
    # Backup-or-bust safety: refuse if no backup exists ALONGSIDE the
    # active env file (honors ATLAS_ENV_FILE — globbing the repo root
    # both refused legitimate custom-path backups and let stale
    # repo-root backups falsely satisfy the check).
    backups = list(ENV_PATH.parent.glob(ENV_PATH.name + ".bak.*"))
    if not backups and not args.dry_run:
        print(
            "error: no .env.bak.* found alongside .env — back up first:\n"
            f"  cp -a {ENV_PATH} {ENV_PATH}.bak.{date.today().isoformat()}",
            file=sys.stderr,
        )
        return 1

    example_text = EXAMPLE_PATH.read_text(encoding="utf-8")
    user_text = ENV_PATH.read_text(encoding="utf-8")
    new_text, taken, user_only = reorganize(example_text, user_text)

    user_keys = set(_parse_env(user_text))
    example_keys = set(_parse_env(example_text))
    missing_in_new = user_keys - set(_parse_env(new_text))
    if missing_in_new:
        print(
            "error: reorg would drop keys present in user .env: "
            f"{sorted(missing_in_new)}",
            file=sys.stderr,
        )
        return 1

    print(f"user .env keys:     {len(user_keys)}")
    print(f".env.example keys:  {len(example_keys)}")
    print(f"taken from user:    {len(taken)} (user values spliced into template)")
    print(f"user-only keys:     {len(user_only)} (trailer)")
    if user_only:
        for k in user_only:
            print(f"  - {k}")
    template_only = example_keys - user_keys
    print(f"template-only keys: {len(template_only)} (got .env.example default)")
    if template_only:
        for k in sorted(template_only):
            print(f"  - {k}")
    print(f"output bytes:       {len(new_text)} (was {len(user_text)})")

    if args.dry_run:
        print("\n(dry-run; not writing)")
        return 0

    atomic_write_text(ENV_PATH, new_text, mode=0o600)
    print(f"\nwrote {ENV_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
