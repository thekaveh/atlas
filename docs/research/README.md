# Cross-service integration research

This directory holds integration research: one opportunity file per service,
one-pagers for candidate new services and the generated integration matrix.
The first set was produced in the "Phase B" research run (§6). Its plans and
specs live under [`docs/superpowers/`](../superpowers/).

## 1. Layout

| Path | Purpose |
|---|---|
| `rows/<service>.md` | One per service doc folder under `services/`. Captures the service's missing-pair integrations, candidate new services, and feature gaps. **One file = one author = one subagent run.** |
| `candidates/<slug>.md` | One per candidate new service (e.g. `obsidian-mcp.md`, `langfuse.md`). Multiple rows may reference the same candidate. |
| `integration-matrix.md` | Generated index that aggregates all rows by service, by category, plus a global candidates table. **Do not edit by hand.** Re-generate with `PYTHONPATH=bootstrapper uv run --project bootstrapper python -m bootstrapper.docs.merge_research`. |

## 2. Row file schema

Each `rows/<service>.md` is a markdown file with frontmatter:

```yaml
---
service: <doc-folder-name>          # e.g. hermes, stt-provider
category: infra | data | llm | media | agents | apps
generated: YYYY-MM-DD
generator: phase-b-subagent | phase-b-controller | phase-b-hand-edit
sources_consulted:
  - <URL or local path>
  - ...
---
```

Followed by exactly three numbered sections:

1. `## 1. Missing-pair integrations` — pairs of EXISTING stack services that should be wired together but aren't yet. Each bullet must include: Why valuable, Mechanism sketch, Effort (small | medium | large), Risks / open questions, Confidence (high | medium | low).
2. `## 2. Candidate new services` — services NOT in the stack today that would slot in cleanly. Each bullet points at a full one-pager in `candidates/<slug>.md`. Each row file caps at **5 candidates**.
3. `## 3. Per-service feature gaps` — features the upstream project exposes that we don't yet leverage.

If a section has no findings, use the exact placeholder line:

```
_No high-confidence opportunities identified._
```

**Hard caps:**
- 800 words total per row file.
- 5 candidate cross-references per row file.

## 3. Candidate one-pager schema

Each `candidates/<slug>.md`:

```yaml
---
slug: <kebab-case>
name: <Display Name>
type: external-service
category-fit: agents | data | media | infra | llm | apps
generated: YYYY-MM-DD
upstream: <URL>
license: <SPDX identifier or "unknown">
lifecycle: proposed | planned | deferred | rejected | shipped
decided: YYYY-MM-DD                  # required unless lifecycle is proposed
superseded-by: <URL or repo path>    # optional: the issue, decision doc or service that settled it
referenced-by: [<service>, ...]      # maintained by merge script, not by author
---
```

`lifecycle` records where the proposal stands. `proposed` is the only
undecided value. `planned` and `deferred` keep the record a live candidate,
listed in `integration-matrix.md` and eligible for a service README's
`Future — Candidate new services` block. `shipped` and `rejected` close it:
`python -m bootstrapper.docs.regen --all` drops it from both of those derived
indexes in the same pass, and the file stays here as history. A closed record
may keep its original wiring sketch, effort and risks under a
`## Historical proposal (<lifecycle> YYYY-MM-DD)` heading, as `###`
subsections, instead of presenting them as a live plan (see
`candidates/open-webui-pipelines.md`).

Six required sections:

1. `## Headline` — one sentence.
2. `## Problem it solves` — 2-3 sentences.
3. `## Stack wiring sketch` — bullets naming real services in the current topology.
4. `## Effort` — small / medium / large + one sentence.
5. `## Risks & open questions` — bullets (may be empty but heading is required).
6. `## Upstream evidence` — at least one URL backing the claim.

`## Why now (and why not sooner)` is optional.

Research one-pagers number their headings like every other page (`## 1. Headline`).
The schema validator matches the title after the number, so the titles above stay fixed.

## 4. Validation

A schema validator lives at `scripts/validate_research_schema.py`. Run it on
one file or all:

```bash
uv run --project bootstrapper python scripts/validate_research_schema.py docs/research/rows/hermes.md
uv run --project bootstrapper python scripts/validate_research_schema.py --all
```

Exit 0 = valid, 1 = errors (printed with file:line refs).

## 5. Merging

`PYTHONPATH=bootstrapper uv run --project bootstrapper python -m bootstrapper.docs.merge_research` reads all rows + candidates,
emits `integration-matrix.md`, and reconciles `referenced-by:` on each
candidate. Deterministic and idempotent. `--check` reports the files it would
rewrite and exits 2 without writing. `python -m bootstrapper.docs.regen --all`
runs the same merge, so the service READMEs and the matrix change together.
The docs drift gate (`regen --all --check`) covers the matrix and each
candidate's normalized front matter too. After adding or editing a record, run
`PYTHONPATH=bootstrapper uv run --project bootstrapper python -m bootstrapper.docs.regen --all`
and commit what it rewrites.

## 6. Dispatch pattern (how Phase B was produced)

21 parallel `Explore`-type subagents, one per doc folder, each given a
programmatically-generated prompt by `bootstrapper.docs.research_subagent_prompt.build_research_prompt(name)`.
WebFetch budget: 8 per subagent. Subagents write to their own row file
(no contention) and may create candidate one-pagers (collision-safe via
file-exists check + `## Cross-references` append).
