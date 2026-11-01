"""
Env-example ↔ manifest consistency test.

Tier 2.G1/G2 deliverable: `.env.example` (auto-generated from manifests by
services/env_assembler.py; byte-equivalence enforced in
test_env_assembler.py) and the per-service manifests must stay in
lockstep on KEY OWNERSHIP. This test asserts:

  1. Every UPPER_CASE=... key in `.env.example` is claimed by exactly one
     manifest (either as an `env:` entry, a `sources.var`, or an
     `images[].var`).
  2. Every non-auto_managed, non-bootstrapper_only env var declared in a
     manifest appears in `.env.example`.
  3. Every UPPER_CASE key in `.env.example` is owned by exactly one manifest
     (no duplicates).

If this test fails: declare or remove the var in the owning
services/<name>/service.yml, then regenerate `.env.example` from the repo
root with `uv run --project bootstrapper python -m services.env_assembler`.
Never edit `.env.example` by hand; it is a generated file.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_EXAMPLE = REPO_ROOT / ".env.example"

sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))
from services.manifests import load_manifests


_ENV_KEY_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=")
# Commented examples like `# OPENCLAW_OPENAI_API_KEY=` document an
# optional override; treated as "documented presence" by this audit.
_COMMENTED_KEY_RE = re.compile(r"^#\s+([A-Z][A-Z0-9_]*)=")


def _env_example_keys() -> set[str]:
    """Active KEY=... lines."""
    if not ENV_EXAMPLE.is_file():
        pytest.skip(f"{ENV_EXAMPLE} missing")
    return {m.group(1) for line in ENV_EXAMPLE.read_text().splitlines()
            if (m := _ENV_KEY_RE.match(line))}


def _env_example_documented_keys() -> set[str]:
    """Active OR commented-out KEY=... lines — anything a user can see."""
    if not ENV_EXAMPLE.is_file():
        pytest.skip(f"{ENV_EXAMPLE} missing")
    text = ENV_EXAMPLE.read_text()
    return (
        {m.group(1) for line in text.splitlines() if (m := _ENV_KEY_RE.match(line))}
        | {m.group(1) for line in text.splitlines() if (m := _COMMENTED_KEY_RE.match(line))}
    )


def _manifest_owned_vars() -> tuple[set[str], dict[str, list[str]]]:
    """Return (all_declared_vars, owner_map). owner_map[var] = sorted list of distinct manifests."""
    manifests = load_manifests(REPO_ROOT / "services")
    owners: dict[str, set[str]] = {}
    for m in manifests:
        for entry in m.env:
            owners.setdefault(entry.name, set()).add(m.name)
        if m.sources:
            owners.setdefault(m.sources.var, set()).add(m.name)
        for img in m.images:
            owners.setdefault(img.var, set()).add(m.name)
    return set(owners.keys()), {k: sorted(v) for k, v in owners.items()}


def test_every_env_example_key_has_a_manifest_owner():
    """No orphan keys in .env.example."""
    declared, _ = _manifest_owned_vars()
    keys = _env_example_keys()
    orphans = keys - declared
    assert not orphans, (
        f"{len(orphans)} env-example keys with no manifest owner. "
        f"Declare each in the owning services/<name>/service.yml env: block, "
        f"or drop it from that manifest, then regenerate .env.example with "
        f"`uv run --project bootstrapper python -m services.env_assembler`: "
        f"{sorted(orphans)}"
    )


def test_every_non_auto_managed_manifest_var_is_in_env_example():
    """Every env var a manifest declares (excluding auto_managed and
    bootstrapper_only) should be discoverable in .env.example — either as
    an active KEY=... line OR as a commented-out `# KEY=...` documenting an
    optional override.

    Covers three manifest sources:
      - `env:` entries (modulo `auto_managed: true` and
        `bootstrapper_only: true`)
      - `images:` entries (no auto_managed concept — they're always
        user-overridable image tags)
      - `sources.var` (the source-selector env var)
    """
    manifests = load_manifests(REPO_ROOT / "services")
    keys = _env_example_documented_keys()
    missing = []
    for m in manifests:
        for entry in m.env:
            if entry.auto_managed:
                continue
            if entry.bootstrapper_only:
                continue
            if entry.name not in keys:
                missing.append(f"{m.name}.env.{entry.name}")
        for img in m.images:
            if img.var not in keys:
                missing.append(f"{m.name}.images.{img.var}")
        if m.sources and m.sources.var not in keys:
            missing.append(f"{m.name}.sources.{m.sources.var}")
    assert not missing, (
        f"{len(missing)} manifest-declared vars missing from .env.example: "
        f"{sorted(missing)}"
    )


def test_every_env_example_key_has_exactly_one_owner():
    """No two manifests should claim the same env var (uniqueness rule from
    the cross-manifest validator)."""
    _, owners = _manifest_owned_vars()
    duplicates = {var: mnfs for var, mnfs in owners.items() if len(mnfs) > 1}
    assert not duplicates, (
        f"{len(duplicates)} env vars with multiple owners: {duplicates}"
    )
