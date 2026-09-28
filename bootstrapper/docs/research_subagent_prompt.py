"""Programmatic prompt builder for Phase B research subagents.

`build_research_prompt(doc_folder)` returns a self-contained prompt string
that the Phase B controller passes to an Explore subagent. The prompt:

  - names the target doc folder + the manifests it aggregates
  - lists the other doc folders with category + one-line description
  - lists the target's current upstream dependencies as a "do not propose
    these" set (the subagent must research GAPS, not re-derive what's wired)
  - cites the strict row-file + candidate schemas
  - states the WebFetch budget (8 fetches) and hard caps (800 words, 5
    candidates)
  - directs output to docs/research/rows/<doc_folder>.md

All data is pulled from manifests + topology so the prompt is current.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

from services.topology import get_topology     # noqa: E402

from .deps_resolver import build_doc_graph, doc_folder_to_manifests  # noqa: E402

SERVICES_DIR = REPO_ROOT / "services"


def _all_doc_folders() -> list[str]:
    """List the doc folders (matches services/<X>/ subdirs that hold a README)."""
    return sorted(
        p.name for p in SERVICES_DIR.iterdir()
        if p.is_dir()
        and not p.name.startswith(("_", "."))
        and (p / "README.md").exists()
    )


def _do_not_propose_set(focus: str) -> list[str]:
    """Services already wired to `focus`. Pulled from focus's DepGraph."""
    try:
        g = build_doc_graph(focus, SERVICES_DIR)
    except KeyError:
        return []
    wired: set[str] = set()
    for e in g.upstream:
        wired.add(e.other)
    for e in g.downstream:
        wired.add(e.other)
    return sorted(wired)


def _other_services_table(focus: str) -> str:
    """Markdown table of the other doc folders (one row each)."""
    topo = get_topology()
    rows_by_folder = {}
    for row in topo.rows:
        rows_by_folder.setdefault(row.manifest, []).append(row)

    lines = ["| Service | Category | Description |", "|---|---|---|"]
    for folder in _all_doc_folders():
        if folder == focus:
            continue
        members = doc_folder_to_manifests(folder)
        desc = ""
        for m in members:
            for row in rows_by_folder.get(m, []):
                if row.description:
                    desc = row.description
                    break
            if desc:
                break
        category = ""
        for m in members:
            for row in rows_by_folder.get(m, []):
                category = row.category
                break
            if category:
                break
        lines.append(f"| {folder} | {category or '?'} | {desc or '_(see manifest)_'} |")

    return "\n".join(lines)


def build_research_prompt(doc_folder: str) -> str:
    """Build the standardized Phase B subagent prompt for one service."""

    members = doc_folder_to_manifests(doc_folder)
    member_note = ""
    if members and members != (doc_folder,):
        member_note = (
            f"\n\n**Note:** `{doc_folder}` is an aggregate doc folder; the "
            f"underlying manifests are: {', '.join(members)}. Treat the whole "
            f"aggregate as one logical service for the purposes of research.\n"
        )
    elif not members:
        member_note = (
            f"\n\n**Note:** `{doc_folder}` is a pointer-only doc folder (no "
            f"underlying manifest). Research it as a logical service that lives "
            f"alongside the stack — e.g. multi2vec-clip is a Weaviate feature.\n"
        )

    do_not_propose = _do_not_propose_set(doc_folder)
    dnp_block = (
        ", ".join(do_not_propose) if do_not_propose else "_(no current wiring detected)_"
    )

    others_table = _other_services_table(doc_folder)
    others_count = len([f for f in _all_doc_folders() if f != doc_folder])

    primary_manifest = members[0] if members else "(none)"

    return f"""You are the Phase B research subagent for **{doc_folder}** in Atlas.

Your job is to research **integration opportunities** for this service and produce a single markdown file at `docs/research/rows/{doc_folder}.md` following the strict schema below.

## Scope

Three research deliverables, in order:

1. **Missing-pair integrations** — pairs of services ALREADY IN the stack that should be wired together but aren't. For each candidate pair, the bullet must include: *Why valuable*, *Mechanism sketch* (named endpoint or protocol), *Effort* (small | medium | large), *Risks / open questions*, *Confidence* (high | medium | low). Confidence must be backed by an entry in `sources_consulted`.

2. **Candidate new services** — services NOT currently in the stack that would plug in cleanly here. Maximum **5 candidates per row file**. For each, write a full one-pager at `docs/research/candidates/<slug>.md` per the candidate schema below — UNLESS a one-pager already exists, in which case append a `## Cross-references` line to your row pointing at the existing file.

3. **Per-service feature gaps** — capabilities the upstream project ({doc_folder}) exposes that we don't yet leverage in this stack.

## Target context

- **Doc folder:** `{doc_folder}`
- **Existing manifest(s):** `services/{primary_manifest}/service.yml`{member_note}
- **Existing doc:** `services/{doc_folder}/README.md`

## Do NOT propose these (already wired)

The following services are already wired to {doc_folder} in the manifest's `data_flow.calls` dependency graph (its upstream and downstream edges). Do not propose new wiring with these — your job is to find GAPS, not duplicate existing edges.

{dnp_block}

## Other services in the stack

These are the {others_count} other doc folders. Each pair (`{doc_folder}` × <other>) is a candidate for "missing-pair integration" if not already wired.

{others_table}

## Row file schema (write this to `docs/research/rows/{doc_folder}.md`)

The file MUST begin with a YAML frontmatter block (the `---` fenced header below) and contain exactly the three numbered sections shown.

```markdown
---
service: {doc_folder}
category: <one of: infra | data | llm | media | agents | apps>
generated: 2026-05-18
generator: phase-b-subagent
sources_consulted:
  - https://...                # upstream docs/repo
  - services/{doc_folder}/service.yml
  - services/{doc_folder}/README.md
  - ...
---

# {doc_folder} — Integration Research

## 1. Missing-pair integrations

- **{doc_folder} ↔ <Other>**
  - Why valuable: <1-2 sentences>
  - Mechanism sketch: <named endpoint or protocol — e.g. `bolt://neo4j:7687`>
  - Effort: small | medium | large
  - Risks / open questions: <bullets, may be empty>
  - Confidence: high | medium | low

(or `_No high-confidence opportunities identified._` if nothing meets the bar)

## 2. Candidate new services

- **<Candidate Name>** → `../candidates/<slug>.md`
  - Headline: <one sentence>
  - Other consumers in stack: <bulleted services>

## 3. Per-service feature gaps

- **<feature>** — Why pursue: <reason>. Effort: <small|medium|large>.
```

## Candidate one-pager schema (write to `docs/research/candidates/<slug>.md`)

If a one-pager already exists at that path, do NOT overwrite it — instead, append a `## Cross-references` line to it linking from your row. The merge step will reconcile `referenced-by:` later. Do NOT re-propose a candidate whose existing one-pager has `lifecycle: shipped` or `lifecycle: rejected`: that proposal is closed, and its record is history.

A new one-pager starts at `lifecycle: proposed`. Only a maintainer decision moves it to `planned`, `deferred`, `rejected` or `shipped`, adding `decided: YYYY-MM-DD` and, where one exists, `superseded-by: <issue URL or repo path>`.

```markdown
---
slug: <kebab-case>
name: <Display Name>
type: external-service
category-fit: <one of: infra | data | llm | media | agents | apps>
generated: 2026-05-18
upstream: <URL>
license: <SPDX or "unknown">
lifecycle: proposed
referenced-by: [{doc_folder}]
---

# <Name>

## Headline
<one sentence>

## Problem it solves
<2-3 sentences>

## Stack wiring sketch
- <service A> → <this candidate> via <protocol/endpoint>
- <this candidate> → <service B> via <...>

(Every bullet MUST name a real service in the current topology.)

## Effort
<small | medium | large> — <one sentence on what dominates the cost>

## Risks & open questions
- <bullet> (may be empty but heading is required)

## Why now (and why not sooner)
<optional, one paragraph>

## Upstream evidence
<at least one URL>
```

## Hard caps

- **800 words** total per row file.
- **5 candidates** maximum per row file. Rank and trim if you have more.
- **WebFetch budget: 8** for this subagent. Use them on upstream project docs for high-confidence claims.

## Quality bar

**Good output:**
- "**hermes ↔ neo4j** — Why: persistent agent memory across sessions. Mechanism: skill writing session graphs via `bolt://neo4j:7687`. Effort: medium. Confidence: medium (Hermes skill system supports custom tools per https://github.com/NousResearch/hermes/blob/main/docs/skills.md)."

**Bad output:**
- "Could maybe use neo4j somehow." (vague, no mechanism, no confidence reason)
- Naming a service that doesn't exist in the topology table above.
- Re-proposing a pair already in the "Do NOT propose" list.

## Process

1. Read `services/{primary_manifest}/service.yml`, the existing README at `services/{doc_folder}/README.md`, and any init scripts.
2. Identify the strongest 3-7 missing-pair integration candidates from the other-20-services table.
3. Identify 0-5 candidate new services that would slot in cleanly.
4. Identify 0-N per-service feature gaps from upstream docs.
5. Write the row file at the path above following the schema EXACTLY.
6. For each candidate, write the one-pager (or append `## Cross-references` if it exists).
7. Validate yourself: run `python scripts/validate_research_schema.py docs/research/rows/{doc_folder}.md` from the repo root, then the same command on each candidate file you wrote. Exit 0 = pass.

## When you are done

Report:
- **Status:** DONE | DONE_WITH_CONCERNS | BLOCKED
- The row-file path you wrote.
- The candidate-file paths you created (and which you cross-referenced instead of created).
- Validator exit code.
- Word count of the row file body.

Do NOT modify code under `bootstrapper/` or `services/`. Do NOT touch other doc folders or other row/candidate files. Stay within your `docs/research/rows/{doc_folder}.md` and the candidate files you author.
"""
