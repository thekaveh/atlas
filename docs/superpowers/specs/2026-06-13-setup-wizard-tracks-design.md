# Setup-wizard tracks — design

**Date:** 2026-06-13
**Status:** Approved (brainstorming complete; ready for implementation plan)
**Author:** Kaveh Razavi
**Scope:** Add a preselected-profile ("track") layer to the Atlas setup wizard so users land on a curated subset of services for their role.

---

## 1. Problem

The wizard walks every user through ~23 source-configurable services, regardless of intent. A user building a RAG system answers prompts about ComfyUI, Spark, and Airflow — none of which they need. The cognitive load of the full prompt list is the friction.

Tracks compress this: pick a profile up front, then the wizard prompts only for the services that profile cares about. Out-of-track services are force-disabled and skipped.

## 2. Goals & non-goals

**Goals:**
- New step 1 prompt: pick a track.
- Each track curates a subset of source-configurable services; the always-on tier (LLM Engine, Prometheus, Grafana, cloud-provider keys, locked-by-topology services) is implicit and asked in every track.
- Subsequent prompts iterate only the in-track services. Out-of-track configurable services are written to `.env` as `*_SOURCE=disabled`.
- CLI flag `--track <key>` skips the picker. `--list-tracks` prints the registry and exits.
- Back from step 2 returns to the picker; switching track re-defaults downstream selections.
- Explicit `--*-source` flags override the track with a stderr warning.

**Non-goals (deferred):**
- Per-track default `*_SOURCE` values (e.g. ml-eng → Ray defaults to `container-gpu`). Today: per-track membership only.
- User-defined custom tracks. Today: 6 baked-in.
- A `--track save-as <name>` flag for snapshotting current selections.
- Persisting the chosen track across runs (one-shot per invocation by design).

## 3. Track taxonomy

Always-on for every track (implicit, computed at runtime, not enumerated per track):

- Locked-by-topology manifests — `Supabase, Redis, Kong, LiteLLM, Backend`. Already invisible to the wizard via `ServiceDiscovery._is_locked`, so they never reach the track-skip predicate — no protection needed.
- Cloud-provider key/multiselect prompts — `cloud-openai`, `cloud-anthropic`, `cloud-openrouter`. Already filtered out of `services_info` via `CLOUD_PROVIDER_KEYS` and presented through bespoke secret-input steps that splice in after the LLM Engine source step. They never reach the track-skip predicate either.
- The three services that DO appear in `services_info` and must be exempted from track-skip: `llm-provider` (LLM Engine + Ollama+cloud sub-steps), `prometheus`, `grafana`. These are the keys returned by `compute_always_on()` and matched against the `service_key` argument of `_make_track_skip`.

Per-track curated additions:

| Track key         | Display name                  | Configurable services added on top of always-on |
| ----------------- | ----------------------------- | ----------------------------------------------- |
| `gen-ai-rag`      | Generative AI · RAG           | open-webui, weaviate, neo4j-graph-db, lightrag, doc-processor, tei-reranker, searxng, local-deep-researcher |
| `gen-ai-eng`      | Generative AI · Engineering   | open-webui, n8n, hermes, openclaw, jupyterhub, comfyui, stt-provider, tts-provider, searxng, local-deep-researcher |
| `gen-ai-creative` | Generative AI · Creative      | open-webui, comfyui, stt-provider, tts-provider, multi2vec-clip, doc-processor |
| `ml-eng`          | ML Engineering                | spark, ray, jupyterhub, zeppelin, open-webui, minio, tei-reranker |
| `data-eng`        | Data Engineering              | spark, airflow, jupyterhub, zeppelin, minio, weaviate, neo4j-graph-db |
| `all`             | All / Custom                  | every source-configurable service (no filtering — `services: "*"` sentinel) |

Display order in the picker matches the table order (`gen-ai-rag` first; `all` last).

## 4. Data model

### 4.1. Track registry — `bootstrapper/tracks.yml`

```yaml
# Per-track configurable-service membership.
#
# The always-on tier (locked manifests + LLM Engine + Prometheus + Grafana
# + cloud-provider keys) is implicit and applies to every track — it is
# NOT enumerated here. Adding a new always-on cloud provider in
# bootstrapper/utils/cloud_providers.py automatically extends the implicit
# set via compute_always_on(); tracks.yml stays unchanged.
#
# `services: "*"` is the "all" sentinel — no filtering is applied.
tracks:
  - key: gen-ai-rag
    display_name: Generative AI · RAG
    description: Retrieval-augmented generation — vectors, graph, reranker, doc ingest, web search.
    services: [open-webui, weaviate, neo4j-graph-db, lightrag, doc-processor,
               tei-reranker, searxng, local-deep-researcher]

  - key: gen-ai-eng
    display_name: Generative AI · Engineering
    description: Agentic apps + workflows with voice, vision, and search.
    services: [open-webui, n8n, hermes, openclaw, jupyterhub, comfyui,
               stt-provider, tts-provider, searxng, local-deep-researcher]

  - key: gen-ai-creative
    display_name: Generative AI · Creative
    description: Multimodal generation — image, voice, vision, doc.
    services: [open-webui, comfyui, stt-provider, tts-provider,
               multi2vec-clip, doc-processor]

  - key: ml-eng
    display_name: ML Engineering
    description: Distributed training/inference + notebooks + experiment storage.
    services: [spark, ray, jupyterhub, zeppelin, open-webui, minio, tei-reranker]

  - key: data-eng
    display_name: Data Engineering
    description: Batch + lakehouse + graph + vector with orchestration.
    services: [spark, airflow, jupyterhub, zeppelin, minio, weaviate, neo4j-graph-db]

  - key: all
    display_name: All / Custom
    description: Every configurable service — full wizard, no filtering.
    services: "*"
```

### 4.2. JSON Schema — `bootstrapper/schemas/tracks.schema.json`

Validated at load time, parallel to `service.schema.json`. Enforces:

- `tracks` is a non-empty array of objects.
- Each entry has `key` (slug, kebab-case), `display_name` (string), `description` (string), `services` (either `"*"` or a non-empty array of slugs).
- `key` is unique across the array.

### 4.3. Runtime types — `bootstrapper/tracks.py`

```python
@dataclass(frozen=True)
class Track:
    key: str                          # slug
    display_name: str
    description: str
    services: frozenset[str] | None   # None == "*" sentinel

@dataclass(frozen=True)
class TrackRegistry:
    tracks: tuple[Track, ...]         # canonical display order
    by_key: dict[str, Track]
    always_on: frozenset[str]         # computed once at load

def load_tracks(path: Path | None = None) -> TrackRegistry: ...
def compute_always_on(config_parser) -> frozenset[str]:
    """Returns the set of wizard-step `service_key`s that must NEVER be
    skipped by a track-skip predicate. Concretely: `{llm-provider,
    prometheus, grafana}` today — the three services that survive
    `ServiceDiscovery.discover()` filtering and must be exempt from
    track-based skipping. Locked manifests and cloud-provider keys are
    filtered out upstream and never see this predicate."""
def is_in_track(track: Track, service_key: str, *, always_on: frozenset[str]) -> bool:
    """True iff service is asked / enabled-by-default under this track."""
def format_track_list(registry: TrackRegistry) -> str:
    """Rich-formatted table for --list-tracks output."""
```

## 5. Wizard UX

### 5.1. New step 1 — the track picker

Inserted at index 0 in `_build_steps_and_rows`. "Base port" becomes step 2; service prompts follow.

```
─────────────────────────────────────────────────────────────────────
 Step 1 of N                                Welcome to Atlas
 Track  ·  pick your profile
─────────────────────────────────────────────────────────────────────
 Which profile fits what you're building?

   ▶ Generative AI · RAG
       Retrieval-augmented generation — vectors, graph, reranker, doc.
       (Open WebUI + Weaviate + Neo4j + LightRAG + Doc Processor +
        TEI Reranker + Searxng + Local Deep Researcher)

     Generative AI · Engineering
       Agentic apps + workflows with voice, vision, and search.
       (Open WebUI + n8n + Hermes + OpenClaw + JupyterHub + ComfyUI +
        STT + TTS + Searxng + Local Deep Researcher)

     Generative AI · Creative
       Multimodal generation — image, voice, vision, doc.
       (Open WebUI + ComfyUI + STT + TTS + Multi2Vec CLIP + Doc Processor)

     ML Engineering
       Distributed training/inference + notebooks + experiment storage.
       (Spark + Ray + JupyterHub + Zeppelin + Open WebUI + MinIO + TEI Reranker)

     Data Engineering
       Batch + lakehouse + graph + vector with orchestration.
       (Spark + Airflow + JupyterHub + Zeppelin + MinIO + Weaviate + Neo4j)

     All / Custom
       Every configurable service — full wizard, no filtering.

  Always-on for every track: LLM Engine + Prometheus + Grafana + cloud keys.
─────────────────────────────────────────────────────────────────────
```

Picker uses standard `PromptStep` + `PromptOption`; each option's `hint` carries the service-list parenthetical and `description` carries the one-liner. Default highlight: first option (`gen-ai-rag`).

### 5.2. Flow rules

1. Picker is the new step 1; Base port is step 2.
2. Every existing service-source `PromptStep` gets `skip_if_prev=_make_track_skip(service_key, ...)`. The predicate reads the picker selection and returns `True` (skip) when the service is not in the chosen track AND not in the always-on set AND not in the explicit-override set.
3. Out-of-track services are silently skipped in the wizard AND get `*_SOURCE=disabled` written at confirmation by `_selections_to_args`.
4. Back from step 2 returns to the picker — handled by the existing `WizardScreen.action_back` walking past skipped steps; index 0 is a natural floor. Switching the picker selection RE-EVALUATES every downstream step's `skip_if_prev` predicate against the new track on the next forward walk: services in the new track that were skipped before now appear; services no longer in the new track get skipped. The wizard's `selections` dict preserves any per-service choice the user already made for a service that is still in-track — only out-of-track services lose their effective contribution (they're force-written to `disabled` at confirmation).
5. The Service Summary on the right pane renders out-of-track configurable rows with a dim category color and the annotation `disabled (off-track)`. Locked rows are unaffected.
6. The Step-count footer recomputes from `len(self._steps)` minus skipped — no change needed.
7. The pre-launch summary banner adds a single line: `Track: Generative AI · RAG`.

## 6. CLI surface

### 6.1. New options in `bootstrapper/start.py`

```python
@click.option(
    '--track', type=str, default=None,
    help='Pre-select a profile: gen-ai-rag, gen-ai-eng, gen-ai-creative, '
         'ml-eng, data-eng, all. Skips the wizard track-picker. '
         'In-track services are prompted as usual; out-of-track services '
         'are disabled. Use --list-tracks to see members.'
)
@click.option(
    '--list-tracks', is_flag=True,
    help='Print the available tracks and their service membership, then exit.'
)
```

### 6.2. Behavior

- `./start.sh --track gen-ai-rag` — wizard opens at step 2 (Base port). Track is set internally and applied to every step's skip predicate as if the user had picked it.
- `./start.sh --list-tracks` — prints the registry in a Rich-formatted table and exits 0. Runs BEFORE the Supabase key generator and any other side-effect.
- `./start.sh --track foo` (unknown) — hard error, exits 2. Message: `Error: unknown track 'foo'. Available: gen-ai-rag, gen-ai-eng, gen-ai-creative, ml-eng, data-eng, all.`
- `./start.sh --no-tui --track gen-ai-rag` — linear stdout flow uses the same filtering; pre-launch summary shows the track banner.
- `./start.sh` (no `--track`, TUI capable) — wizard opens at the picker (step 1).
- `./start.sh --no-tui` (no `--track`, no TTY) — stdin prompt asks for the track on one line, displays the available keys, defaults to `gen-ai-rag` (first entry in `tracks.yml`) if input is empty. Matches the TUI picker's default highlight for symmetry.

### 6.3. Interaction with `--*-source` flags

CLI flags win; track issues an advisory stderr warning. Implemented at the top of `start.py` after Click parses, before the wizard launches:

1. Compute the track's in-track set (or the `"*"` sentinel).
2. For each explicit `--*-source` flag the user passed: if the service is NOT in the track AND NOT always-on, emit one stderr line per flag:
   ```
   [warn] --comfyui-source container-gpu overrides the gen-ai-rag track,
          which excludes ComfyUI. Enabling ComfyUI anyway.
   ```
3. The actual override happens through the existing `source_args` plumbing — flags always win, the warning is purely advisory.
4. The same set of "explicitly overridden services" is threaded into the wizard step builder so the corresponding service prompts FLIP BACK ON (track-skip respects the override). User can finish configuring those services interactively.

### 6.4. Persistence

`TRACK` is NOT written to `.env`. One-shot per invocation. Service `*_SOURCE` values force-written by the track ARE persisted as today.

## 7. Implementation surface

### 7.1. New files (3)

| Path                                       | Purpose                                                                        |
| ------------------------------------------ | ------------------------------------------------------------------------------ |
| `bootstrapper/tracks.yml`                  | Track registry (Section 4.1).                                                  |
| `bootstrapper/tracks.py`                   | `Track`, `TrackRegistry`, `load_tracks`, `compute_always_on`, `is_in_track`, `format_track_list`. Pure data + lookup. |
| `bootstrapper/schemas/tracks.schema.json`  | JSON Schema (Section 4.2).                                                     |

### 7.2. Modified files (~6)

| Path                                                       | Change                                                                                                   |
| ---------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `bootstrapper/start.py`                                    | Add `--track` + `--list-tracks` Click options; early-exit on `--list-tracks`; track resolution + override warning loop. |
| `bootstrapper/ui/textual/integration.py`                   | Insert picker `PromptStep` at index 0; per-service step attaches `skip_if_prev=_make_track_skip(...)`; `_selections_to_args` synthesizes `*_SOURCE=disabled` for `configurable - in_track - overridden`; pre-launch summary gains the track banner. |
| `bootstrapper/ui/textual/widgets/prompt_panel.py`          | No schema change. Picker uses existing `PromptOption` fields (`hint` for service list, `description` for one-liner). |
| `bootstrapper/ui/textual/screens/wizard_screen.py`         | No logic change. Add `Track: X` line in `_refresh_info_panel`.                                           |
| `bootstrapper/ui/state_builder.py`                         | Accept optional `in_track` predicate AND an `overridden` set; dim category color + add `disabled (off-track)` annotation ONLY for out-of-track configurable rows that are NOT in the override set. Out-of-track services force-enabled by an explicit `--*-source` flag render with their normal color and no annotation (they're effectively in-track for this run). |
| `bootstrapper/wizard/service_discovery.py`                 | No change. Track filtering happens at step-build time, not discovery time.                               |

### 7.3. Helper signatures (load-bearing)

```python
# tracks.py
def load_tracks(path: Path | None = None) -> TrackRegistry: ...
def compute_always_on(config_parser) -> frozenset[str]: ...

# integration.py — internal
def _make_track_skip(
    service_key: str,
    *,
    always_on: frozenset[str],
    overridden: frozenset[str],
) -> Callable[[dict], bool]:
    """skip_if_prev predicate. Reads selections[<picker title>] to find
    the active track key, looks up the Track, returns True (skip) when:
        service_key NOT in always_on
        AND service_key NOT in track.services      (None means "*" — never skip)
        AND service_key NOT in overridden
    """
```

## 8. Edge cases & error handling

1. **Empty track service list** — load-time validation error (`tracks.schema.json` minItems: 1 on `services` unless value is `"*"`).
2. **Track lists a non-existent service** — load-time cross-check in `tracks.py::load_tracks()` against the synthesized configurable-service list; reject unknowns with a list of valid keys.
3. **User disables an in-track service** (`--track ml-eng --ray-source disabled`) — allowed, no warning. Track says "you'll be prompted"; user said "but I want it off."
4. **User overrides an always-on service** (`--prometheus-source disabled`) — allowed, no warning. Always-on means "asked in every track," not "can't be disabled."
5. **`--track all` + any `--*-source` flag** — no warning. `all` includes everything.
6. **Non-TTY / `--no-tui`** — linear stdout flow honors `--track` exactly like the TUI path. If no `--track` and no TTY, stdin prompt collects the choice (default `gen-ai-rag` on empty input — matches the TUI picker's default highlight).
7. **`all` sentinel** — `services: "*"` in YAML → `None` on the `Track` dataclass → `_make_track_skip` short-circuits to "never skip" for any service.
8. **Track-skip vs the dynamic LLM/Ray/ComfyUI splices** — splice predicates already exist; track-skip is additive, combined via `or`. Cloud-provider sub-steps are always-on so the combination is a no-op for them today; the wrap is defensive.
9. **`--list-tracks` early exit** — runs before Supabase key generation; idempotent and side-effect-free.

## 9. Test plan

| Test file                                  | Coverage                                                                                  |
| ------------------------------------------ | ----------------------------------------------------------------------------------------- |
| `tests/test_tracks.py`                     | `load_tracks()`, `compute_always_on()`, `is_in_track()`, schema validation, unknown-service rejection, `"*"` sentinel. |
| `tests/test_tracks_wizard_skip.py`         | For each track × each configurable service: `_make_track_skip` predicate returns the right bool. Always-on services never skipped. Override-set re-enables skipped steps. |
| `tests/test_tracks_cli.py`                 | `--list-tracks` exit 0 + stdout snapshot; `--track unknown` exits 2; `--track gen-ai-rag --comfyui-source container-gpu` emits expected stderr warning; `--track all` suppresses warnings. |
| `tests/test_tracks_no_tui.py`              | Linear flow honors `--track`; stdin prompt fallback when no `--track` and no TTY.         |
| extend `tests/test_cli_seam_parity.py`     | New flag must be present at all 4 known CLI seams (Click decl, source_mapping, collector dict, wizard_screen lambda). Existing pattern from `project_cli_source_flag_three_seams.md`. |
| extend `tests/test_selections_to_args.py`  | `_selections_to_args` synthesizes `*_SOURCE=disabled` for every configurable service that is `not in track AND not overridden`. |

### 9.1. Audit scripts

`scripts/check-track-membership.py` — sweeps `tracks.yml` and ensures:
- Every service listed exists in the synthesized configurable-service set.
- Every source-configurable service appears in at least one track other than `all`.
Hooked into the existing `services-lint` audit-script CI job.

## 10. Documentation

- `CLAUDE.md` — short "Tracks" subsection under Architecture, pointing at `tracks.yml` and the `--track` flag.
- `README.md` — new "Quickstart by track" section showing `./start.sh --track gen-ai-rag` and the others.
- `bootstrapper/tracks.yml` — inline YAML comments at the top describing the schema + the always-on tier rule.
- No architecture-diagram changes (tracks don't affect deployment topology — only the wizard surface).

## 11. Rollout

Single PR, additive. Recommended commit order:

1. `tracks.yml` + `tracks.py` + `tracks.schema.json` + unit tests. No wizard or CLI wiring. Safe checkpoint.
2. `--track` + `--list-tracks` Click options in `start.py`. CLI tests. Flag is stored but not consumed yet.
3. Picker step + `_make_track_skip` in `integration.py::_build_steps_and_rows`. Wizard tests.
4. Dim-row treatment in `state_builder.py` + track banner in `_refresh_info_panel`. Visual polish.
5. Audit script + CLAUDE.md + README updates. Docs-drift gate stays green.

## 12. Risks (ranked)

- **R1 — Track-skip ordering vs dynamic LLM/Ray/ComfyUI splices** (Medium). Spliced sub-steps already carry their own predicates; combining with track-skip needs the wrap explicit. Mitigation: test each track × each sub-step variant.
- **R2 — Back-button walks past the picker** (Low). `action_back` floors at index 0; existing tests cover. One new assertion.
- **R3 — `_selections_to_args` must synthesize `disabled` for force-skipped services** (Medium). Today the function only writes `source_args` for visited steps. New pass for `configurable - in_track - overridden`. Mitigation: dedicated test.
- **R4 — `--list-tracks` interferes with normal `start.sh` invocation** (Low). Early-exit branch before any side-effect. Simple test.
- **R5 — Force-write of `*_SOURCE=disabled` silently overwrites a prior choice** (Medium). User who configured `MINIO_SOURCE=container` previously and then runs `--track gen-ai-rag` loses that config. Mitigation: pre-launch summary surfaces which services the track is disabling so the user sees it before launch.
- **R6 — `tracks.yml` drift vs canonical service list** (Low). Handled by load-time cross-check + audit script.

## 13. Out of scope (future PRs)

- Per-track default `*_SOURCE` values (e.g. ml-eng → Ray defaults to `container-gpu`).
- User-defined custom tracks loaded from `$XDG_CONFIG_HOME/atlas/tracks.yml`.
- A `--track save-as <name>` flag to snapshot current selections as a new track.
- Per-track default cloud-provider enablement (e.g. gen-ai-rag pre-enables OpenAI).
- A "Track:" column in the live service-table during the wizard (today: dim color + annotation only).
- Persisting the chosen track to `.env` for sticky re-runs.

## 14. Open questions

None — all clarifying questions resolved during brainstorming. The design as written is implementation-ready.
