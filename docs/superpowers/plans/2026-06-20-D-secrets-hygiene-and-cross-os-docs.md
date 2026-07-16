# Plan D — Secrets Hygiene + Cross-OS Docs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (1) Close the secrets-hygiene gap — guarantee no shipped placeholder secret can survive to runtime — and (2) correct the README's overstated cross-OS claim to state the real Windows story (WSL / Git Bash).

**Architecture:** Two small, independent changes. For secrets: add a startup assertion + a test that every placeholder-bearing secret is covered by the rotation logic in `key_generator.py`, and (decision point) either keep the documented `kong_password`/`redis_password`/`neo4j_password` placeholders (rotated on first run) or blank them in the manifests. For docs: a two-line README edit.

**Tech Stack:** `bootstrapper/utils/key_generator.py`; pytest; README markdown.

## 1. Global Constraints

- `main` protected — PR with 3 green checks; no direct push.
- Commits terse third-person, no emoji, no Claude trailer.
- `.env.example` is generated; placeholder defaults live in `services/<svc>/service.yml` (`DASHBOARD_PASSWORD` in `kong/service.yml`, `REDIS_PASSWORD` in `redis/service.yml`, `GRAPH_DB_PASSWORD` in `neo4j/service.yml`) — change manifests, not `.env.example`. Byte-equivalence enforced by `tests/test_env_assembler.py`.
- The placeholder-rotation contract is encoded in `bootstrapper/utils/key_generator.py::PLACEHOLDER_DEFAULTS` + per-secret `generate_and_update_*` methods, and tested in `tests/test_credential_placeholder_rotation.py`.

---

### 1.1. Task 1: Guard that every placeholder secret is rotation-covered

**Files:**
- Modify: `bootstrapper/tests/test_credential_placeholder_rotation.py` (strengthen the coverage assertion)
- Modify: `bootstrapper/utils/key_generator.py` (only if a gap is found)

**Interfaces:**
- Consumes: `PLACEHOLDER_DEFAULTS` (8 entries: `N8N_ENCRYPTION_KEY`, `SUPABASE_DB_PASSWORD`, `SUPABASE_DB_APP_PASSWORD`, `GRAPH_DB_PASSWORD`, `REDIS_PASSWORD`, `DASHBOARD_PASSWORD`, `OPEN_WEB_UI_ADMIN_PASSWORD`, `OPEN_WEB_UI_SECRET_KEY`) and `generate_missing_keys()`.

- [ ] **Step 1: Write a test asserting no manifest ships a non-empty secret default that isn't a known rotated placeholder**

Add to `bootstrapper/tests/test_credential_placeholder_rotation.py`:
```python
def test_no_unrotated_nonempty_secret_defaults():
    """Every env var marked secret: true in a manifest must either ship an
    EMPTY default (generated-when-absent) or be a known PLACEHOLDER_DEFAULTS
    value (rotated-when-placeholder). A non-empty secret default that is
    NOT in PLACEHOLDER_DEFAULTS would ship a real-looking credential that
    nothing rotates."""
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from services.manifests import load_manifests
    from utils.key_generator import PLACEHOLDER_DEFAULTS
    repo = pathlib.Path(__file__).resolve().parents[2]
    manifests = load_manifests(repo / "services")
    offenders = []
    for m in manifests:
        for env in m.env:
            if not getattr(env, "secret", False):
                continue
            default = getattr(env, "default", "") or ""
            if default == "":
                continue  # generated-when-absent: fine
            if env.name in PLACEHOLDER_DEFAULTS and PLACEHOLDER_DEFAULTS[env.name] == default:
                continue  # rotated placeholder: fine
            offenders.append((m.name, env.name, default))
    assert not offenders, f"unrotated non-empty secret defaults: {offenders}"
```

- [ ] **Step 2: Run it**

Run: `cd bootstrapper && uv run pytest tests/test_credential_placeholder_rotation.py::test_no_unrotated_nonempty_secret_defaults -v`
Expected: PASS if every secret is covered. If it FAILS, the offender is a real hygiene bug — fix by EITHER blanking that secret's `default` in its manifest (preferred for anything not needed for first-run convenience) OR adding it to `PLACEHOLDER_DEFAULTS` + a `generate_and_update_*` rotator + the `ROTATORS` list. Re-run until green.

- [ ] **Step 3: If a manifest default changed, regenerate `.env.example`**

```bash
cd bootstrapper && uv run python -m services.env_assembler
cd bootstrapper && uv run pytest tests/test_env_assembler.py -q
```
Expected: PASS (byte-equivalence holds).

- [ ] **Step 4: Commit**

```bash
git add bootstrapper/tests/test_credential_placeholder_rotation.py bootstrapper/utils/key_generator.py services/*/service.yml .env.example
git commit -m "Guard: every placeholder secret is rotation-covered"
```

---

### 1.2. Task 2: Startup hard-stop if a placeholder secret reaches a prod launch

**Files:**
- Modify: `bootstrapper/utils/key_generator.py` (add a verifier) and its call site in `bootstrapper/start.py`
- Test: `bootstrapper/tests/test_credential_placeholder_rotation.py`

**Interfaces:**
- Produces: `KeyGenerator.assert_no_placeholders_remaining() -> None` (raises with the offending var names if any `.env` value still equals its `PLACEHOLDER_DEFAULTS` literal). Called when `--profile prod` (Plan B) is active.

- [ ] **Step 1: Write the failing test**

```python
def test_assert_no_placeholders_detects_unrotated(tmp_path):
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from utils.key_generator import KeyGenerator
    env = tmp_path / ".env"
    env.write_text("REDIS_PASSWORD=redis_password\nDASHBOARD_PASSWORD=already-rotated-xyz\n", encoding="utf-8")
    kg = KeyGenerator(str(env))
    import pytest
    with pytest.raises(Exception) as ei:
        kg.assert_no_placeholders_remaining()
    assert "REDIS_PASSWORD" in str(ei.value)
```

- [ ] **Step 2: Run it (fails — method missing)**

Run: `cd bootstrapper && uv run pytest tests/test_credential_placeholder_rotation.py::test_assert_no_placeholders_detects_unrotated -v`
Expected: FAIL (`assert_no_placeholders_remaining` not defined).

- [ ] **Step 3: Implement the method** in `key_generator.py` (mirror its existing `.env` read helpers):
```python
def assert_no_placeholders_remaining(self) -> None:
    """Raise if any managed secret in .env still equals its shipped
    placeholder literal. Used as a prod-launch gate."""
    values = self._read_env_values()  # use the class's existing reader
    offenders = [
        name for name, placeholder in PLACEHOLDER_DEFAULTS.items()
        if (values.get(name, "") or "") == placeholder
    ]
    if offenders:
        raise RuntimeError(
            "Refusing to launch: placeholder secrets not rotated: "
            + ", ".join(sorted(offenders))
            + ". Run ./start.sh once (auto-rotates) or set strong values in .env."
        )
```
(Use the class's actual env-reading helper; if none exists, parse `KEY=VALUE` lines from `self.env_file_path`, ignoring comments.)

- [ ] **Step 4: Run the test (passes)**

Run: `cd bootstrapper && uv run pytest tests/test_credential_placeholder_rotation.py::test_assert_no_placeholders_detects_unrotated -v`
Expected: PASS.

- [ ] **Step 5: Wire the gate into prod launches** in `bootstrapper/start.py` — after key generation, when `--profile prod` (Plan B):
```python
if getattr(self, "profile", "default") == "prod":
    self.key_generator.assert_no_placeholders_remaining()
```

- [ ] **Step 6: Commit**

```bash
git add bootstrapper/utils/key_generator.py bootstrapper/start.py bootstrapper/tests/test_credential_placeholder_rotation.py
git commit -m "Add prod-launch gate for unrotated placeholder secrets"
```

---

### 1.3. Task 3: Correct the README cross-OS claim

**Files:**
- Modify: `README.md:160`, `README.md:173`

- [ ] **Step 1: Edit line 160** — replace:
```
- **Cross-platform support**: Python-based bootstrapping works on all OS
```
with:
```
- **Cross-platform support**: runs on Linux and macOS (Intel/Apple Silicon); on Windows via WSL2 or Git Bash (the `start.sh`/`stop.sh` entrypoints are POSIX shell — there is no native PowerShell/cmd wrapper)
```

- [ ] **Step 2: Edit line 173** — replace:
```
- **Cross-platform Python scripts**: consistent behavior across Windows, macOS, Linux
```
with:
```
- **Cross-platform Python core**: the bootstrapper is OS-aware; Linux and macOS run natively, Windows runs under WSL2 / Git Bash
```

- [ ] **Step 3: Verify doc-link + drift gates unaffected**

Run: `cd /Users/kaveh/repos/genai-vanilla && python scripts/check_doc_links.py && PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen --all --check`
Expected: both exit 0.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: correct cross-OS claim (Windows = WSL/Git Bash)"
```

---

### 1.4. Task 4: Full suite + CHANGELOG

- [ ] **Step 1: Run the whole suite**

Run: `cd bootstrapper && uv run pytest -q`
Expected: PASS (all green).

- [ ] **Step 2: CHANGELOG entry** under `## [Unreleased]` describing the secrets-hygiene guard + gate and the README correction.

- [ ] **Step 3: Commit**

```bash
git add docs/CHANGELOG.md
git commit -m "docs: changelog for secrets hygiene + cross-OS correction"
```

---

## 2. Self-Review

- **Spec coverage:** Implements P0-4 (secrets hygiene — coverage guard + prod-launch gate) and P0-9 (README cross-OS correction). The prod-launch gate (Task 2) depends on Plan B's `--profile prod`/`self.profile`; if Plan B isn't merged first, gate the call on `getattr(self, "profile", "default")` which safely no-ops in default mode.
- **Placeholders:** none — exact line replacements for the README, complete test + method bodies. The one conditional ("if the coverage test fails, fix by blanking or adding a rotator") is a genuine branch with both arms specified, not a placeholder.
- **Type consistency:** `PLACEHOLDER_DEFAULTS`, `generate_missing_keys`, `assert_no_placeholders_remaining`, `self.profile` used consistently; `ROTATORS` list in the test mirrors `PLACEHOLDER_DEFAULTS`.
- **Cross-plan dependency:** Task 2's gate references `self.profile` from Plan B; sequence Plan B before Plan D, or land Task 2's wiring in the same PR as Plan B.
