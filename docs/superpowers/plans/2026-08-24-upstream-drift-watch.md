# Scheduled Upstream-Drift Watch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a nightly, externally grounded drift watcher that reports all failed probes through one self-healing GitHub issue without requiring a live Atlas stack.

**Architecture:** A standalone Python CLI discovers Atlas-owned catalog and image inputs, runs four bounded probes, and writes a deterministic Markdown report. A least-privilege GitHub Actions workflow supplies an ephemeral empty Ollama server and reconciles one marker issue from the CLI result.

**Tech Stack:** Python 3.12 (`urllib`, `subprocess`, `concurrent.futures`, PyYAML), pytest, GitHub Actions YAML, GitHub CLI.

## Global Constraints

- Implement only issue #969.
- Do not require a running Atlas stack or download model weights.
- Read model and image references from their existing canonical YAML sources.
- Bound every network request and subprocess.
- Aggregate all probe failures instead of stopping after the first.
- Keep workflow permissions to `contents: read` and `issues: write`.
- Pin third-party actions by commit SHA.
- Preserve all existing CI workflows and tests.

---

### Task 1: Probe result, source discovery, and report contract

**Files:**
- Create: `scripts/upstream_drift_watch.py`
- Create: `bootstrapper/tests/test_upstream_drift_watch.py`

**Interfaces:**
- Produces: `ProbeResult(name: str, ok: bool, detail: str)`
- Produces: `load_curated_ollama_models(path: Path) -> tuple[str, ...]`
- Produces: `load_manifest_image_refs(services_dir: Path) -> tuple[str, ...]`
- Produces: `render_report(results: Sequence[ProbeResult], generated_at: datetime) -> str`

- [ ] **Step 1: Write failing source-discovery and rendering tests**

```python
def test_load_curated_models_deduplicates_multimodal_entries(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text("content:\n  - name: qwen:latest\nvision:\n  - name: qwen:latest\n")
    assert watch.load_curated_ollama_models(path) == ("qwen:latest",)

def test_report_contains_stable_marker_and_all_failures():
    report = watch.render_report(
        [watch.ProbeResult("library", False, "too few"),
         watch.ProbeResult("images", False, "missing ref")],
        datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    assert "<!-- atlas-upstream-drift-watch -->" in report
    assert "too few" in report and "missing ref" in report
```

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run --project bootstrapper pytest bootstrapper/tests/test_upstream_drift_watch.py -q`

Expected: collection fails because `scripts.upstream_drift_watch` does not exist.

- [ ] **Step 3: Implement immutable results, YAML discovery, validation, and report rendering**

Implement literal manifest defaults only, sorted/deduplicated tuples, stable
Markdown headings, bounded detail formatting, and UTC timestamps. Do not add
network or subprocess behavior in this task.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `uv run --project bootstrapper pytest bootstrapper/tests/test_upstream_drift_watch.py -q`

Expected: source-discovery and rendering tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/upstream_drift_watch.py bootstrapper/tests/test_upstream_drift_watch.py
git commit -m "test(ci): define upstream drift report contract"
```

### Task 2: Bounded live probes and aggregate CLI

**Files:**
- Modify: `scripts/upstream_drift_watch.py`
- Modify: `bootstrapper/tests/test_upstream_drift_watch.py`

**Interfaces:**
- Produces: `probe_ollama_library() -> ProbeResult`
- Produces: `probe_ollama_tags(url: str, *, timeout: float) -> ProbeResult`
- Produces: `probe_curated_models(models: Sequence[str], *, timeout: float) -> ProbeResult`
- Produces: `probe_manifest_images(refs: Sequence[str], *, timeout: float, workers: int) -> ProbeResult`
- Produces: `run_watch(...) -> tuple[ProbeResult, ...]`
- Produces: `main(argv: Sequence[str] | None = None) -> int`

- [ ] **Step 1: Write failing tests for each success and failure boundary**

Use complete HTTP response doubles at the `urllib.request.urlopen` seam and a
real temporary executable/subprocess result seam for image inspection where
practical. Cover HTTP errors, invalid JSON, wrong `/api/tags` types, registry
non-2xx responses, subprocess non-zero, timeout, output truncation, aggregation,
CLI exit status, and report-file creation.

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run --project bootstrapper pytest bootstrapper/tests/test_upstream_drift_watch.py -q`

Expected: failures name the missing probe and CLI functions.

- [ ] **Step 3: Implement the minimal bounded probes**

Use `urllib.request.Request` with an Atlas user agent, explicit timeout values,
`subprocess.run(..., timeout=..., check=False, capture_output=True, text=True)`,
and a fixed-size `ThreadPoolExecutor` for image references. Catch only expected
network, JSON, OS, and subprocess timeout failures and convert them to failed
`ProbeResult` values.

- [ ] **Step 4: Implement aggregate CLI behavior**

Support `--ollama-tags-url`, `--services-dir`, `--ollama-models`,
`--report-file`, `--http-timeout`, `--image-timeout`, and `--image-workers`.
Return `0` only when every result is healthy and `1` otherwise.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `uv run --project bootstrapper pytest bootstrapper/tests/test_upstream_drift_watch.py -q`

Expected: all watcher tests pass with no warnings.

- [ ] **Step 6: Commit**

```bash
git add scripts/upstream_drift_watch.py bootstrapper/tests/test_upstream_drift_watch.py
git commit -m "feat(ci): add bounded upstream drift probes"
```

### Task 3: Scheduled workflow and single-issue reconciliation

**Files:**
- Create: `.github/workflows/upstream-drift-watch.yml`
- Modify: `bootstrapper/tests/test_upstream_drift_watch.py`

**Interfaces:**
- Consumes: `python -m scripts.upstream_drift_watch --report-file <path>`
- Produces: scheduled/manual workflow with an `outcome` step output and one exact marker issue

- [ ] **Step 1: Write failing workflow contract tests**

Parse the workflow with PyYAML and assert:

```python
assert workflow["on"]["schedule"]
assert workflow["on"]["workflow_dispatch"] is None
assert workflow["permissions"] == {"contents": "read", "issues": "write"}
assert workflow["concurrency"]["cancel-in-progress"] is False
```

Also assert SHA-pinned actions, a job timeout, manifest-derived Ollama image,
bounded readiness, an `always()` cleanup, watcher report capture, exact issue
marker/title matching, create/edit/reopen/close commands, and no model pull.

- [ ] **Step 2: Run workflow tests and verify RED**

Run: `uv run --project bootstrapper pytest bootstrapper/tests/test_upstream_drift_watch.py -q`

Expected: failure because `.github/workflows/upstream-drift-watch.yml` is absent.

- [ ] **Step 3: Add the scheduled workflow**

Use a non-top-of-hour daily cron, `workflow_dispatch`, a 30-minute job timeout,
locked `uv sync`, manifest image extraction, an ephemeral loopback Ollama
container, bounded readiness, the watcher command with captured exit code,
unconditional cleanup, and `gh`-based exact-marker issue reconciliation.

- [ ] **Step 4: Run workflow tests and verify GREEN**

Run: `uv run --project bootstrapper pytest bootstrapper/tests/test_upstream_drift_watch.py -q`

Expected: all watcher and workflow tests pass.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/upstream-drift-watch.yml bootstrapper/tests/test_upstream_drift_watch.py
git commit -m "ci: schedule upstream drift watch"
```

### Task 4: Verification, review, and gitflow delivery

**Files:**
- Verify all changed files and generated artifacts; no new production files.

- [ ] **Step 1: Run focused verification**

```bash
uv run --project bootstrapper pytest \
  bootstrapper/tests/test_upstream_drift_watch.py \
  bootstrapper/tests/test_ollama_library.py \
  bootstrapper/tests/test_bounded_subprocess.py -q
```

- [ ] **Step 2: Run required repository verification**

```bash
uv run --project bootstrapper pytest bootstrapper/tests -q
PYTHONPATH=bootstrapper uv run --project bootstrapper python -m bootstrapper.docs.regen --all --check
make docs-check
uv run --project bootstrapper python scripts/check_doc_links.py
uv run --project bootstrapper python scripts/check-compose-source-deps.py
uv run --project bootstrapper python scripts/check-kong-routes.py
uv run --project bootstrapper python scripts/validate_research_schema.py --all
uv run --project bootstrapper python scripts/check-track-membership.py
```

Expected: every command exits `0` with no drift.

- [ ] **Step 3: Run a live external probe without Atlas**

Run the CLI against an ephemeral empty Ollama container and the public
catalog/registries. Expected: report generated; any upstream failure is
investigated rather than waived.

- [ ] **Step 4: Review complete diff against the design and ticket**

Check `git diff origin/develop...HEAD`, the plan acceptance mapping, workflow
permissions, timeouts, issue deduplication, and absence of secrets/model pulls.
Request an independent code review and resolve every Critical or Important
finding.

- [ ] **Step 5: Push and merge through gitflow**

Push `codex/969-upstream-drift-watch`, create a PR to `develop`, wait for all
required checks and conversation resolution, merge, then promote `develop` to
`main` through a second checked PR. Do not begin #967 until both merges are
verified.

