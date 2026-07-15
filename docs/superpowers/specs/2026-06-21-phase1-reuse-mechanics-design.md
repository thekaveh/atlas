# Phase 1 — Reuse Mechanics — Design

**Date:** 2026-06-21
**Status:** Design (for implementation)
**Scope:** Phase 1 of the production-readiness & reuse roadmap (`docs/superpowers/specs/2026-06-20-production-readiness-and-reuse-roadmap-design.md`, Part 5). Phase 0 merged in PR #124.

---

## 1. Goal

Make Atlas genuinely turnkey to **reuse and extend** from a downstream project, by closing the two open Phase 1 gaps:

- **P1-1 — `services/_user/` overlay services actually launch.** Today a downstream consumer can drop `services/_user/<name>/{service.yml,compose.yml}` into their submodule, but nothing starts it: the compose `include:` list is static/hand-maintained and the `docker compose` invocation relies on cwd auto-discovery of `docker-compose.yml` with no `-f`. The overlay is invisible to launch.
- **P1-3 — Release tagging for pinning.** There are no semver tags, so a submodule consumer can only pin to a commit SHA. Establish a tag convention and cut the first tag.

P1-2 (a "consume Atlas from a sibling repo" guide) is **already shipped** as `docs/deployment/reusing-atlas.md` (PR #124) and is out of scope here beyond a readiness-table update.

## 2. Non-goals (explicit scope guard)

Phase 1 makes `_user/` overlay services **launch**. It does **not** integrate them into the upstream bootstrapper's manifest pipeline:

- No topology port-slot allocation for `_user/` services (they declare their own host ports in their `compose.yml`).
- No `.env.example` generation for `_user/` env vars (upstream `.env.example` is unaffected; `services/_user/` is gitignored, so there is nothing upstream to render).
- No wizard prompts / `*_SOURCE` toggles for `_user/` services.
- The manifest loader continues to skip `_`-prefixed dirs (the existing `test_underscore_prefixed_folders_are_ignored` contract stays green).

A `_user/` overlay service is therefore a **self-contained Compose fragment**: it brings its own image, host ports, environment, and joins the shared `backend-network`. This matches the "drop-in overlay" mental model and keeps the change minimal and non-invasive to the core stack. (Deeper wizard/topology integration is a possible Phase 2+ item, only if a real need appears.)

## 3. P1-1 — Design

### 3.1. Mechanism

`bootstrapper/core/docker_manager.py` builds the `docker compose` command in two places:
- `execute_compose_command()` (linear flow)
- `_build_compose_command()` (used by `stream_compose()`, the TUI flow)

Neither passes `-f`; Compose auto-discovers `docker-compose.yml` from `cwd` (the repo root). We add a single shared helper that returns the compose-file args:

```python
def _compose_file_args(self) -> list[str]:
    """Compose `-f` args. Empty by default (Compose auto-discovers
    docker-compose.yml from cwd — preserves current behavior + the
    byte-equivalence baseline). When one or more downstream overlay
    fragments exist under services/_user/<name>/compose.yml, return an
    explicit base + overlay list so those services are merged and launched."""
    user_dir = self.root_dir / "services" / "_user"
    overlays = sorted(user_dir.glob("*/compose.yml")) if user_dir.is_dir() else []
    if not overlays:
        return []
    args = ["-f", "docker-compose.yml"]
    for f in overlays:
        args += ["-f", str(f.relative_to(self.root_dir))]
    return args
```

Both command builders insert `self._compose_file_args()` immediately after the `--env-file` flag and before the caller's `args`.

### 3.2. Why this shape

- **No overlay present (upstream, CI, default users) → zero behavior change.** `_compose_file_args()` returns `[]`, so the command is byte-identical to today and `docker compose ... config` (the byte-equivalence test) is untouched. This is the key safety property.
- **Overlay present → explicit `-f docker-compose.yml -f services/_user/<svc>/compose.yml …`.** Once any `-f` is passed, Compose stops auto-discovering the default file, so we must list the base explicitly. Multiple `-f` files merge in order (overlays last).
- **Deterministic** (sorted glob) and **gitignore-safe** (the dir is gitignored; absent in upstream).

### 3.3. Tests

`bootstrapper/tests/test_user_overlay_compose.py`:
- With no `services/_user/`, `_compose_file_args()` returns `[]` (default behavior preserved).
- With a synthetic `services/_user/demo/compose.yml` (created under a temp root), `_compose_file_args()` returns `["-f", "docker-compose.yml", "-f", "services/_user/demo/compose.yml"]`.
- Two overlay services → both `-f` entries present, sorted.

(These test the pure arg-builder against a temp `root_dir`; they do not invoke Docker, so they run in CI without Docker.)

## 4. P1-3 — Design

- **Convention:** semantic-version tags `vMAJOR.MINOR.PATCH` on `main`. Downstream submodule consumers pin to a tag and bump deliberately. Document in a short `docs/deployment/releasing.md` and reference it from `reusing-atlas.md` (§4 / §7).
- **First tag:** cut `v0.1.0` at the current `main` tip (conservative initial version; the project predates formal versioning). Pushed to origin.

## 5. Doc updates

- `docs/deployment/reusing-atlas.md`: flip the `_user/` overlay row in the §7 readiness table from **Partial** to **Ready**; update the §6 `_user/` row and the §1 TL;DR caveat; add a short "extend via `services/_user/`" subsection under Method A or §6 with the self-contained-fragment contract + an example. Remove the "until this is wired, use Method A instead" caveat.
- `docs/CONTRIBUTING-services.md` §21: update to state that overlay services now launch automatically (the bootstrapper appends their `compose.yml` via `-f`), and the self-contained-fragment contract.
- `docs/CHANGELOG.md`: an "Added" entry.
- `docs/deployment/releasing.md`: new (the tag convention).

## 6. Acceptance

- `_compose_file_args()` returns `[]` with no overlay, correct `-f` list with overlays (unit-tested).
- Full bootstrapper suite green (incl. `test_underscore_prefixed_folders_are_ignored` unchanged; byte-equivalence unaffected because the default invocation is unchanged).
- Doc gates green (doc-links, docs-drift, docs.regen --check, validate_fragments).
- `v0.1.0` tag exists on `main` after merge.

## 7. Out of scope / deferred

Everything in Phase 2+ (Infisical, Loki/Tempo, image scanning/signing, deeper hardening, staging env, managed offload, k8s) — see the Phase 0 roadmap spec Part 5.
