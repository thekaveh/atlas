# Service capability contracts — design

- **Ticket:** [#967](https://github.com/thekaveh/atlas/issues/967)
- **Branch:** `codex/967-service-capability-contracts` (cut from `develop`)
- **Integration target:** `develop`, followed by the normal `develop` → `main` promotion
- **Date:** 2026-08-24
- **Baseline commit:** `f257ccc5` (`origin/develop`)

## 1. Goal

Give every service manifest an explicit, machine-validated capability contract
that tells operators what Atlas supports, what has meaningful limits, what is
only scaffolded, and what is deliberately unavailable. Render that contract
into the canonical per-service README and make missing or stale contracts fail
the existing documentation gates.

The first release covers all 57 manifest-backed service families. The three
README-only aggregate folders remain derived documentation rather than new
runtime manifests: `doc-processor` and `stt-provider` aggregate member
contracts, while `multi2vec-clip` remains an explicit pointer document because
it has neither a manifest nor an independently runnable Atlas service.

No running Atlas stack, GPU, model download, or external service is needed.
Claims are grounded in committed manifests, compose fragments, implementation,
tests, and existing service documentation.

### 1.1. Non-goals

- Do not execute live service smoke tests or certify upstream behavior.
- Do not add, remove, or reconfigure services, containers, images, or sources.
- Do not turn future-integration proposals into supported capabilities.
- Do not create manifests for README-only aggregate or pointer folders.
- Do not rewrite the three-surface documentation architecture, navigation, or
  diagram pipeline.
- Do not renumber existing README sections merely to insert the new section.

## 2. Manifest contract

Every `services/<name>/service.yml` declares a non-empty `capabilities:` list.
Each entry has exactly four required fields:

```yaml
capabilities:
  - name: Host-managed headless scene control
    status: partial
    verification: tested
    note: Scene, object, and code commands work, but viewport screenshots are unavailable in headless mode.
```

`name` is a concise operator-facing capability label. `note` is a single-line
explanation that states the useful boundary rather than repeating the status.
Declaration order is presentation order, allowing authors to put the primary
service purpose before secondary or negative contracts.

### 2.1. Runtime status vocabulary

| Status | Meaning |
|---|---|
| `supported` | Atlas exposes and configures the capability without a known material limitation in its declared scope. |
| `partial` | The capability works only for a subset of modes, hosts, requests, or workflows, and the note names that boundary. |
| `stubbed` | A setting, route, or scaffold exists but does not currently produce the advertised runtime behavior. |
| `not-supported` | Atlas deliberately does not provide the capability; the note names the alternative or reason when useful. |

### 2.2. Verification vocabulary

| Verification | Meaning |
|---|---|
| `tested` | Committed automated Atlas coverage directly exercises the capability or its load-bearing configuration contract. |
| `documented` | Committed implementation/configuration and documentation ground the claim, but no direct automated behavioral test certifies it. |
| `untested` | Atlas has no trustworthy automated or documented validation of the runtime behavior; the note makes the uncertainty explicit. |

Runtime status and verification are intentionally orthogonal. A supported
capability can be untested, and a stubbed capability can be tested as inert.
This preserves the issue's status vocabulary while exposing validation debt
without inventing a fifth runtime status.

### 2.3. Validation rules

The JSON Schema requires `capabilities`, at least one entry, all four fields,
the exact enums above, non-empty labels/notes, and no undeclared fields. Loader
and cross-manifest tests reject duplicate capability names within a service.
The typed manifest loader exposes immutable `Capability` entries so renderers
do not parse raw YAML independently.

## 3. Pilot-first authoring

Before the renderer or completeness gate is implemented, four known-limit
services receive hand-authored contracts and pass schema/loader validation:

1. `blender-mcp`: host-only scene control, managed headless limitations,
   arbitrary-code exposure, and lack of viewport screenshots in headless mode;
2. `speaches`: unified TTS support, incomplete STT validation, and the inert
   `SPEACHES_STT_MODEL` selector;
3. `comfyui`: container/MPS image generation, workflow/model provisioning
   boundaries, and the inert Supabase upload placeholders;
4. `lightrag`: graph RAG and storage integration, plus the adapter-only rerank
   path caused by direct LightRAG-to-TEI payload incompatibility.

The schema initially permits the new key while these examples are authored.
Only after all manifests are populated does `capabilities` become globally
required. This sequence tests whether the contract can describe real edge
cases before tooling makes it mandatory.

## 4. Documentation generation

`bootstrapper/docs/regen.py` gains a separate capability resolver and section
writer rather than extending dependency graph objects with unrelated data.
The renderer consumes typed manifests and emits a deterministic Markdown table:

| Capability | Status | Verification | Notes |
|---|---|---|---|

For aggregate READMEs, a leading `Service` column identifies the source
manifest. Singleton pages omit that redundant column. Values are escaped for
Markdown tables, while manifest declaration order is preserved.

The first generation appends `## N. Capabilities & limitations`, where `N` is
one greater than the README's highest existing top-level numeric section. Later
runs replace that same fenced section in place and preserve its number. The
upsert remains code-fence-aware and idempotent, matching the safety properties
of the existing Dependencies & Integrations writer.

### 4.1. README-only folders

- `doc-processor` renders the contract from `docling`.
- `stt-provider` renders the combined contracts from `parakeet` and `speaches`.
- `tts-provider` is manifest-backed and also aggregates its TTS members using
  the existing resolver membership; duplicate self entries are removed.
- `multi2vec-clip` is skipped deliberately. Its README points to Weaviate's
  built-in module and does not represent an independently configured service.

The exception is explicit in tests so a future README-only folder cannot be
silently omitted.

## 5. Drift and completeness gates

The existing manifest schema/loader gate fails when any of the 57 manifests
lacks a capability block or contains malformed entries. A targeted repository
test additionally checks duplicate names and the exact README-only exception
set.

The existing `bootstrapper.docs.regen --all --check` gate fails when generated
capability sections are missing or stale. `test_docs_drift.py`, `make
docs-check`, and the link checker therefore protect the rendered contract
without adding a parallel documentation system.

## 6. Rollout discipline

Non-pilot manifests are authored in category-sized batches. Every entry must be
traceable to repository evidence. Authors use `tested` only for direct automated
coverage, `documented` for implemented/configured contracts without direct
tests, and `untested` when runtime confidence is genuinely absent. Generic
boilerplate such as "service is supported" is rejected during review.

The rollout changes documentation metadata only. If research uncovers a real
runtime defect, missing feature, or architectural decision, it is recorded in
the capability note and left for a separate ticket rather than fixed under
#967.

## 7. Test strategy

Focused tests cover:

- schema acceptance and rejection for every field and enum;
- typed loader projection, order preservation, and duplicate-name rejection;
- pilot manifests before renderer implementation;
- singleton and aggregate rendering, Markdown escaping, status labels, and
  declaration order;
- initial append, in-place replacement, code-fence safety, dynamic numbering,
  and second-pass byte idempotence;
- all 57 manifests having non-empty contracts;
- the closed set of README-only aggregate/pointer exceptions;
- full `--all --check` drift detection.

Final verification runs the focused suite, the full bootstrapper suite,
documentation regeneration in check mode, `make docs-check`, internal link
validation, and the other required Atlas audit scripts. No live Atlas stack is
part of acceptance.

## 8. Acceptance mapping

| Ticket requirement | Evidence |
|---|---|
| Capability block in every service manifest | Required JSON Schema field plus repository completeness test |
| Supported / partial / stubbed / not-supported statuses | Schema enum and generated status column |
| One-line notes | Required non-empty single-line schema field rendered in every row |
| What is untested | Orthogonal `verification` enum and generated verification column |
| New numbered README section | Deterministic capability section writer and regenerated service READMEs |
| Drift gate rejects missing contracts | Manifest validation plus `regen --all --check` |
| Hand-author known-limit services first | Pilot task precedes renderer and repository-wide requirement |
| No live Atlas stack | Static evidence, unit tests, schema validation, and docs checks only |
