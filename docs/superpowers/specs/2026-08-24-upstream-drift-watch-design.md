# Scheduled upstream-drift watch — design

- **Ticket:** [#969](https://github.com/thekaveh/atlas/issues/969)
- **Branch:** `codex/969-upstream-drift-watch` (cut from `develop`)
- **Integration target:** `develop`, followed by the normal `develop` → `main` promotion
- **Date:** 2026-08-24
- **Baseline commit:** `40dbe59b` (`origin/develop`)

## 1. Goal

Detect external changes that deterministic pull-request tests cannot see, and
turn a failed nightly check into one actionable GitHub issue instead of an
email-only workflow failure.

The first release watches four contracts already owned by Atlas:

1. the live `ollama.com/library` page still yields a plausible model catalog;
2. the Ollama version pinned in `services/ollama/service.yml` still exposes the
   `/api/tags` response shape Atlas parses;
3. every model family in `services/ollama/models.yaml` remains reachable on
   Ollama's public library;
4. every unique manifest-owned image default in
   `services/*/service.yml::images[].default` still resolves in its registry.

No running Atlas stack or model download is required.

### 1.1. Non-goals

- Do not rerun the full Atlas test suite nightly.
- Do not pull container layers or Ollama model weights.
- Do not mutate image pins or model catalogs automatically.
- Do not scan arbitrary URLs found in documentation.
- Do not add a third-party issue-management action.
- Do not make this scheduled workflow a required pull-request check.

## 2. Architecture

`scripts/upstream_drift_watch.py` is a small command-line probe runner. Its
probe functions return immutable `ProbeResult` values rather than exiting at
the first failure, so one nightly run reports the whole drift surface. The CLI
writes a deterministic Markdown report and exits non-zero when any probe
fails.

`.github/workflows/upstream-drift-watch.yml` runs daily and on manual dispatch.
It checks out the repository, installs the locked bootstrapper environment,
starts the manifest-pinned Ollama image without pulling any model, waits for
`/api/tags`, runs the Python watcher, and always reconciles one marker issue.
The workflow closes that issue after a later healthy run.

The existing PR workflow remains hermetic. New unit tests mock only the actual
network and subprocess seams; parsing, manifest discovery, aggregation, report
rendering, and CLI exit behavior use the real implementation.

## 3. Probe contracts

### 3.1. Ollama library scrape

Call `utils.ollama_library.list_library_entries()` and require at least
`utils.ollama_library.MIN_PLAUSIBLE_ENTRIES` entries. The report includes the
observed and required counts. An empty or implausibly small result is a
failure, including network and parser failures that the library helper
intentionally normalizes to `[]` for interactive wizard fallback.

### 3.2. Ollama `/api/tags` schema

Fetch the configured URL and require a JSON object with a `models` list. Every
list entry must be an object; populated entries must carry a non-empty `name`
or `model` string. An empty list is valid because the nightly container pulls
no model weights.

The workflow obtains the image reference from
`services/ollama/service.yml::images` where `var == LLM_PROVIDER_IMAGE`; it
does not duplicate the version in workflow YAML.

### 3.3. Curated Ollama models

Load every `name` in the `content`, `embeddings`, and `vision` sections of
`services/ollama/models.yaml`, deduplicate repeated multimodal entries, strip
the optional tag for the library page path, and issue bounded HTTP requests to
`https://ollama.com/library/<family>`. A non-2xx response is drift. The report
names every unreachable catalog entry.

### 3.4. Manifest image references

Discover literal `images[].default` values from every service manifest,
deduplicate them, and reject missing, empty, interpolated, or structurally
invalid rows. Resolve each reference with
`docker buildx imagetools inspect <reference>` using a bounded subprocess and
a small fixed worker pool. This reads registry manifests only; it never pulls
layers. Failure output is truncated to a bounded diagnostic.

## 4. Reporting and issue lifecycle

The report has a stable marker (`<!-- atlas-upstream-drift-watch -->`), a UTC
timestamp, a summary count, and one section per probe. Successful details stay
brief; failures contain enough context to identify the external contract.

The workflow searches all issues for the exact marker/title pair:

- failed run, no prior issue: create one issue;
- failed run, open issue: replace its body with the latest aggregate report;
- failed run, closed issue: reopen it and replace its body;
- healthy run, open issue: add a recovery comment and close it as completed;
- healthy run, no open issue: do nothing.

The watcher itself does not call GitHub. Issue mutation stays in the workflow
using the runner's authenticated `gh` CLI and `${{ github.token }}`.

## 5. Security and reliability

- Workflow permissions are exactly `contents: read` and `issues: write`.
- No repository or third-party secret is required.
- Checkout and setup actions remain commit-SHA pinned.
- Every HTTP request and subprocess has an explicit timeout.
- The job has a workflow-level timeout and a concurrency group that prevents
  overlapping scheduled runs.
- Diagnostic output never includes environment variables, tokens, or response
  bodies from authenticated endpoints.
- The ephemeral Ollama container binds only to the runner loopback port and is
  removed in an `always()` cleanup step.

## 6. Test strategy

`bootstrapper/tests/test_upstream_drift_watch.py` covers:

- successful and implausibly small Ollama library results;
- valid, malformed, and unreachable `/api/tags` responses;
- catalog model loading and multimodal deduplication;
- service-manifest image discovery and invalid rows;
- image resolution success, failure, timeout, and diagnostic truncation;
- aggregate execution and Markdown rendering;
- CLI success/failure exit codes and report-file creation;
- workflow schedule/manual triggers, permissions, concurrency, timeouts,
  manifest-derived Ollama image, unconditional cleanup, and single-issue
  reconciliation commands.

The red/green cycle is run against that test file before implementation. Final
verification includes the targeted test, the full bootstrapper suite, the
workflow YAML/contract tests, and the repository's required audit commands.

## 7. Acceptance mapping

| Ticket requirement | Evidence |
|---|---|
| Live scraper/API probes | Library and `/api/tags` probe results |
| Image/tag drift | Manifest image resolver probe |
| Catalog reachability | Curated Ollama library probe |
| Open or update one issue | Exact-marker issue lifecycle in workflow |
| Narrow nightly scope | Dedicated workflow calls only the watcher |
| No live Atlas stack | Empty ephemeral Ollama plus external registry/library requests |
