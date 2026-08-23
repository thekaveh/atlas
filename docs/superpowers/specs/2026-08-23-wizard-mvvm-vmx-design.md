# Rebuild the setup-wizard TUI on MVVM (VMx ViewModels) — design

- **Ticket:** [#535](https://github.com/thekaveh/atlas/issues/535)
- **Branch:** `feat/535-rebuild-setup-wizard-tui-mvvm-vmx` (cut from `develop`)
- **Integration target:** `develop` (not `main`) until fully tested and validated
- **Date:** 2026-08-23
- **Baseline commit:** `bf2e8403` (`develop`; byte-identical to `main` `15914c3e` across the migration surface)

## 1. Goal

Rebuild the Atlas setup wizard — **both** its setup phase and its launch/streaming
phase — on a clean MVVM architecture using [VMx](https://github.com/thekaveh/VMx)
(PyPI `vmx`) as the ViewModel layer.

The success bar is behavioural, not stylistic: **zero domain logic left in the
Textual layer**, enforced by a test rather than by review.

### 1.1 Non-goals

- No layout revamp. Phase C of the original ticket shipped via #911/#917.
- No hero art work. Phase D predates the ticket (`b8787bb8`).
- No new linter, formatter, or type-checker (none is configured; see `CLAUDE.md`).
- No change to `docker_manager`, `services/`, `core/` domain behaviour.
- No behaviour change in the wizard. Parity is the invariant at every step.

## 2. Definition of "zero code-behind"

Textual requires *some* widget code. The bar is **domain logic**, not line count.

**Allowed in `wizard/view/`:**

- `compose()`, CSS, geometry, focus and key routing
- responsive breakpoints (`if self.size.width < 80: self.add_class("narrow")`)
- forwarding input to a ViewModel command (`self.vm.toggle_source.execute()`)
- rendering ViewModel state

**Banned in `wizard/view/`:**

- source resolution, track rules, dependency conflict rules
- validation, skip predicates, option construction
- env/CLI argument building
- any read of `wizard.model`

Enforcement: an AST lint test (§7, tier 5) that is itself mutation-proven — the
suite contains a case injecting domain logic into a widget and asserting the
lint fails.

## 3. Layering

The correction that shapes everything: **rules are Model, not ViewModel.** But
not everything currently tangled into the View is Model either.

| Today | Layer | Rationale |
|---|---|---|
| track force-disable, cloud-secret→enable promotion, ComfyUI/Ollama CSV→env parsing | **Model** | The CLI-flag path honours these too. Domain truth, no UI involved. |
| `_build_steps_and_rows` — step ordering, skip predicates, option labels/hints/badges | **ViewModel** | 589 lines, zero non-TUI consumers. Nothing in the domain knows what a "step" is. |
| `_selections_to_args` | **both** | The rules it applies are Model; walking a selections map and sequencing them is ViewModel. |

### 3.1 Package structure

```
bootstrapper/
  services/  core/        Domain proper — manifests, topology, sources,
                          config, docker. UNCHANGED.
  wizard/
    model/                VMx-free, Textual-free.
                          <- ui/state.py, ui/state_builder.py,
                             wizard/service_discovery.py,
                             the Model half of wizard/llm_steps.py,
                             + rules extracted from integration.py
    viewmodel/            imports vmx + model. NEVER imports textual.
                          <- wizard/llm_steps.py (step builders),
                             wizard/comfyui_steps.py, wizard/ray_steps.py,
                             + _build_steps_and_rows
    view/                 imports textual + viewmodel. NEVER imports model.
                          <- ui/textual/*
  ui/term_caps.py         Stays put. Host-environment detection consumed by
                          start.py before any wizard exists; it belongs to no
                          wizard layer.
```

**`wizard/` is not empty today.** It already holds 1,810 lines that must be
classified rather than left in place:

| Existing file | LOC | Lands in |
|---|---|---|
| `wizard/llm_steps.py` | 1,064 | **split** — see below |
| `wizard/comfyui_steps.py` | 515 | `viewmodel/` (step builders) |
| `wizard/service_discovery.py` | 207 | `model/` (`ServiceInfo`, `ServiceDiscovery`, `CLOUD_PROVIDER_KEYS`) |
| `wizard/ray_steps.py` | 23 | `viewmodel/` (step builder) |

`llm_steps.py` straddles the boundary and is the one file that must be split,
not moved:

- **Model half** — `_csv` (CSV parsing), `_is_localhost_or_external`,
  `_is_container_ollama`, `_selected_llm_source`. Domain predicates the CLI
  path honours too.
- **ViewModel half** — step-title constants (`LLM_ENGINE_TITLE`,
  `OLLAMA_MODELS_TITLE`, `cloud_secret_title`, `cloud_models_title`,
  `fal_secret_title`), `build_ollama_steps`, `_build_library_options`,
  `_merge_badges`, `_compose_hint`, `_is_legacy`, `_sort_key`,
  `_make_cloud_options_provider`, `_make_cloud_skip_predicate`.

### 3.2 Enforced import direction

A test asserts, by AST import analysis:

- `wizard/model/**` may not import `vmx` or `textual`
- `wizard/viewmodel/**` may not import `textual`
- `wizard/view/**` may not import `wizard.model`
- `core/linear_startup.py` may not import `vmx`

The last rule is what makes the `--no-tui` guarantee **structural**: the non-TTY
path cannot acquire a VMx dependency, because CI fails if it does.

## 4. VMx abstraction mapping

The governing principle: **prefer a VMx primitive over hand-written wiring.**
Where an alternative was plausible, the rejection is recorded — a mapping that
only lists what was chosen hides whether anything better was considered.

### 4.1 Setup phase

| # | Case (today) | VMx abstraction | Why this, not the alternative |
|---|---|---|---|
| 1 | step sequence, `_step_index`, `_advance_past_skipped` | `FilteredCompositeVM<StepVM>` + `FilteredCursorPolicy` | `CompositeVM` ships ordered children + `Current` + `SelectNext/PreviousCommand` — that *is* forward/back. The filtered variant turns "skip" into a predicate, so `SelectNext` lands on the next visible step. Deletes `_advance_past_skipped` + `_step_should_skip`. **Rejected `DiscriminatorVM`**: coordinates an active key but does not own children. |
| 2 | per-step edit, `action_back` | `FormVM<T>` per step | Snapshot-on-construct; `DenyCommand` = revert = `action_back`; `IsDirty` auto-derived. **Rejected whole-wizard `FormVM`**: there is no "revert the entire wizard" feature and one big snapshot makes `IsDirty` meaningless. |
| 3 | five step kinds (`options`/`multiselect`/`number`/`secret`/`text`) | five `FormVM` subclasses, dispatched on `ViewModelType` | **Rejected `DiscriminatorVM`**: it is an active-*slot* coordinator, not a sum type. Five concrete VMs is the honest encoding. |
| 4 | single-choice option list | `CompositeVM<OptionVM>` | Ordered children, exactly one `Current`. |
| 5 | multiselect option list | `GroupVM<OptionVM>`, children `ISelectionTogglable` | **Rejected `CompositeVM`**: multiselect has no single current item. `Current` would be a lie in the type. This is precisely why both types exist. |
| 6 | ComfyUI/Ollama variant trees — `expand_state`, `is_leaf`, `pulled_variants` | `HierarchicalVM<M,VM>` + `IExpandable`/`ICollapsible`/`IExpansionTogglable`, rendered via `walk_expanded()` | `option_row.py` already needs `Depth`/`IsLeaf`/`IsFirst`/`IsLast` and an expand tri-state; `walk_expanded()` returns exactly the flattened visible-row list built by hand today as `_hit_flat`. Depth is capped by the data, not the abstraction. |
| 6b | building that tree | `HierarchicalVMBuilder` + `BatchAttachResult` + `MissingParentPolicy` | Batch attach with an explicit missing-parent policy instead of hand-rolled parent lookup. |
| 7 | `options_provider`, `_provider_cache`, `_provider_done`, `_fetch_generation` | `AsyncResourceVM<list[OptionVM]>` + `AsyncResourceRetention` | The strongest fit in the mapping. `_fetch_generation` is a hand-rolled stale-result suppressor, which VMx owns natively — plus Idle/Loading/Ready/Error, retry, cancel, and `LoadingWithValue` to keep the previous list visible while refetching. Deletes four fields and their coordination. |
| 8 | option search (`/`, Tab-to-search) | `ISearchable` + `FilteredCompositeVM` | **Deliberately not `ScoredFilteredCompositeVM`**: today's behaviour is a substring filter and parity is the invariant. Scoring is a clean follow-up, not a silent behaviour change. |
| 9 | service table rows | `CompositeVM<ServiceRowVM>` (flat) + `FilteredCompositeVM` | Flat, not nested-by-category: the table has **one** cursor spanning all rows; nesting per category would create six competing `Current` slots. Category is a row field; grouping is a render concern. |
| 9b | row storage / lookup by service name | `KeyedServicedObservableCollection[str, ServiceRowVM]` | Ordered + hub-aware + captured-key index. Replaces index-based `self._services[i]` mutation with keyed access. |
| 9c | **locked** rows (`ServiceRow.configurable is False`) | `ReadonlyComponentVMOf[ServiceRow]` | Encodes the lock **in the type** rather than as a runtime `if`. Configurable rows are `ComponentVMOf` (mutable `Model`); always-on infrastructure is structurally unable to change source. |
| 10 | `ServiceRow.is_changed` (source ≠ default) | `DerivedProperty[bool]` | Recomputes on change, distinct-until-changed. |
| 11 | per-row source cycling | `RelayCommandOf[str]` | Parameterized command; the parameter is the target source id. |
| 12 | cloud APIs (3 providers) | `GroupVM<CloudApiVM>`, each `ISelectionTogglable` + nested secret VM | Peers, no current. |
| 13 | command summary | `DerivedProperty` fed by `AggregateChangeStream` over `ObservableMembershipSource` | The summary must recompute when **any** row changes. `AggregateChangeStream` is defined as "dynamic membership and current-member change fan-in" — exactly this. **Rejected**: subscribing to each row VM by hand, which is the wiring code VMx exists to remove. Deletes `_refresh_command_summary` (CC 36). |
| 14 | base-port change → all ports recompute | `NumberStepVM` + `DerivedProperty` per row, emitted inside `TransactionalMessageHubProto.Batch(...)` | Without the transaction this fans out ~60 individual notifications for one edit. `Batch` collapses it to one atomic emission. Deletes `_on_base_port_change`. |
| 15 | dependency-conflict prompt | `ConfirmationDecoratorCommand` over `IDialogService.Confirm` | The command itself carries "ask first". **Rejected bare `ModalVM`**: it would require the screen to sequence dialog→action manually, which is the code-behind being deleted. |
| 16 | bulk row replacement (`set_rows`) | `CompositeVM.BatchUpdate()` | Suppresses intermediate `CollectionChanged`, emits a single `Reset`. |
| 17 | the `_selections` map | `ObservableDictionary` | Multi-key observable dictionary; today a plain dict with manual refresh calls after every write. |

### 4.2 Launch phase

| # | Case | VMx abstraction | Why |
|---|---|---|---|
| 18 | Setup/Logs tabs — `show_tab`, `_active_tab`, 3 tab actions | `DiscriminatorVM[str]` | Its stated purpose verbatim: "one source of truth for an active slot, pane, route, mode". Its LIFO `ModalOpen`/`ModalClose` also layers the conflict dialog above whichever tab is active, free. |
| 19 | `run_pipeline_and_stream` | `AsyncRelayCommand` | Reactive `canExecute` gates re-launch. **Rejected `AsyncResourceVM`**: launch produces a *stream*, not one acquired value. |
| 20 | log lines | `ObservableList<LogLine>` | Granular per-mutation events — the LogPane needs "a line was appended", not "the collection changed". **Rejected `ServicedObservableCollection`**: hub-aware but coarser. |
| 20b | burst appends | `ObservableList.batch_update()` | Compose emits lines in bursts; one notification per drained batch instead of per line. |
| 21 | level chips (all/errors/warns/info) | `CompositeVM<LevelChipVM>` (`Current`) driving an `IFilterable<LogLine>` predicate via `DerivedProperty` | Single-choice → `Current`. The filter is a separate concern from the selection driving it. |
| 22 | source-filter popup (multi-select) | `GroupVM<SourceChipVM>`, `ISelectionTogglable` | Peers, feeding the same `IFilterable`. |
| 23 | `action_stop_stack` / `_cold`, `pending_teardown` | `AsyncRelayCommand` + `ICancelable` + `ConfirmationDecoratorCommand` on the cold variant | Cold teardown destroys volumes; confirmation belongs on the command, not the key handler. |
| 24 | setup→launch transition, `_retire_wizard_widgets` | five-state lifecycle — `LaunchVM` sits `DESTRUCTED` until launch, then `construct()`; `reconstruct()` for re-runs | `DESTRUCTED` is the initial state the original ticket's §3 omitted, and it is the one that models this transition. |
| 25 | multi-effect actions (confirm step → advance; launch → open log tee) | `CompositeCommand`, `precede_with` / `succeed_with` / `wrap_with` | Command composition instead of hand-sequenced calls in a handler. |
| 26 | "a clipboard copy must never make `./start.sh` exit non-zero" | `DecoratorCommand` | A single error-containing decorator applied to commands, rather than repeated try/except in handlers. |
| 27 | footer hints, failure hints, status line | `DerivedProperty` (`from_two` … `from_many`) off tab + phase + launch state | |

### 4.3 Deliberate rejections

Recorded so the spec shows these were considered, not missed:

- **`AggregateVM1..6`** — arity-correct for both the screen composition and the six
  service categories, and tempting for that reason. Rejected twice: `AggregateVM`
  is for *heterogeneous* fixed tuples, and `Component1`…`Component6` accessors are
  strictly less legible than named properties (`service_table`, `command_summary`).
  The six categories are homogeneous, so the arity match is a coincidence.
- **`ForwardingComponentVM` / `ForwardingCompositeVM`** — these are proxy /
  caching / instrumentation decorators. Atlas has no proxying need.
- **`PagedComposition` / `TokenPagedComposition` / `IPageable`** — nothing pages.
- **`ModeledCrudCommands`, `IDeletable` / `IUpdatable` / `ISavable` / `INewCreatable`,
  `ICurrentDeletable` / `ICurrentUpdatable`, `IManagable`** — the wizard edits a
  fixed set; there is no create/delete.
- **`FileFilter` / file-pick dialogs** — the wizard picks no files.
- **`ILocalizer`** — Atlas has `BRAND_*` but no i18n. Wire `NULL_LOCALIZER` so the
  seam exists, unused.
- **`ScoredFilteredCompositeVM`** — see §4.1 case 8; deferred to preserve parity.
- **`vmx.notifications`** — `_flag_logs_alert` would fit, but the subpackage is
  opt-in and orthogonal. Tracked as a follow-up rather than smuggled in.

## 5. Data flow and binding contract

### 5.1 Output — ViewModel to View

One `BoundWidget` base in `wizard/view/binding.py` holds a VM reference,
subscribes via `when_property_changed` / `subscribe_value`, and calls
`refresh()`. Subscriptions dispose on `on_unmount`. Widgets read VM properties
only, never `wizard.model`.

### 5.2 Input — View to ViewModel

Widget events forward to commands and do nothing else. The existing
`post_message` / `FilterChanged` pattern in `multiselect_filter_chips.py` is the
precedent; it becomes the general rule.

### 5.3 Threading

```
docker_manager.stream_compose      [worker thread — unchanged Model]
        |
LaunchVM -> ObservableList<LogLine>
        |  RxDispatcher.asyncio(loop)      foreground = AsyncIOScheduler
MessageHub -> View subscribes -> LogPane.refresh()   [event-loop thread]
```

Rule: **the View subscribes on Foreground only and never touches a VM from a
worker thread.** One dispatcher per VM tree, injected at construction — VMx has
no global dispatcher, which is what makes this testable.

This is the highest-risk seam in the work and the subject of the Pass 2 spike.

### 5.4 Lifecycle ownership

`run_setup_flow` / `run_launch_flow` construct the root VM, hand it to the App,
and `dispose()` depth-first on exit.

### 5.5 Error handling

VMx requires command predicates never raise, so every `canExecute` must be
total. Async failures land as VM *state* (`AsyncResourceVM.Error`, launch error
state), never as exceptions crossing into widgets — preserving today's invariant
that a clipboard copy cannot make `./start.sh` exit non-zero (§4.2 case 26).

## 6. Delivery plan

One long-lived branch; slices land as reviewed commits; a single squash into
`develop` when validated.

- **Pass 0 — Textual upgrade.** `6.2.1` → `8.2.8`, its own PR into `develop`,
  landed *before* the refactor work begins, so no wizard regression is ambiguous
  between "VMx" and "Textual". See §9.
- **Pass 1 — extract rules, no VMx.** Move domain rules into `wizard/model/`;
  repoint `--no-tui`. Includes the file moves of §3.1 as a pure-move commit with
  no logic change. Model-tier tests written here.
- **Pass 2 — adopt VMx.** Pin `vmx==3.23.0`; build the VM tree over the clean
  rules; prove the threading seam (§5.3) on `CommandSummary` before converting
  anything else.
- **Pass 3 — convert surfaces**, risk-ascending: CommandSummary → ServiceTable →
  CloudApis → prompt steps → `prompt_panel.py` / `option_row.py` → launch and
  streaming → teardown.
- **Pass 4 — enforce.** AST lint and import lint (§3.2, §2), allowlist emptied,
  `.maintenance.json` refreshed (§8).

`prompt_panel.py` (1,885 LOC) and `option_row.py` (922 LOC) are where the ZERO
bar is won or lost — five of the twelve worst complexity blocks in the wizard
live in `prompt_panel.py`. They get their own slices in Pass 3.

## 7. Testing strategy

Six tiers. The point of the paradigm switch is that tiers 1–2 become possible at
all.

1. **Model** — pure functions, table-driven. Every rule extracted in Pass 1.
   Written *before* any VMx exists, so a failure has one possible cause.
2. **ViewModel — headless, no Textual App mounted.** Per VM: initial state, each
   command's `canExecute` both ways, each `DerivedProperty` recompute, each
   lifecycle transition. Uses `RxDispatcher.immediate()` and `NULL_MESSAGE_HUB`
   for synchronous determinism. **Rule: every VM gets a test module; every
   command, derived property, and lifecycle transition gets at least one test.**
3. **Binding** — a deliberately small set of Textual `run_test()` pilots proving
   VM change → widget render and widget event → command executed. Slow; kept thin
   on purpose.
4. **Threading** — explicit tests that a worker-thread emission reaches a
   subscriber on the foreground scheduler. This is the bug class the existing
   3,908-test suite cannot catch.
5. **Architecture** — the import-direction lint (§3.2) and the AST no-domain-logic
   lint (§2). Both **mutation-proven**: the suite includes a case that injects
   domain logic into a widget and asserts the lint fails. A lint that has never
   failed is not a lint.
6. **Regression** — the existing 60 TUI-touching test files stay green,
   unmodified where possible.

## 8. Success metrics

### 8.1 Lines of code

Baseline at `bf2e8403`:

| Surface | LOC |
|---|---|
| `ui/textual/` | 10,143 |
| `wizard_screen.py` | 2,883 (21 `action_*`, 71 `self._*` fields, 2 manual refreshes) |
| `integration.py` | 1,448 (`_build_steps_and_rows` 589 + `_selections_to_args` 277 = 60%) |
| `prompt_panel.py` | 1,885 |
| `option_row.py` | 922 |
| `ui/state.py` + `state_builder.py` | 431 |
| `wizard/` (llm_steps 1,064 + comfyui_steps 515 + service_discovery 207 + ray_steps 23) | 1,810 |
| **Total migration surface** | **12,384** |

Reported per pass, split three ways — **relocated** (Pass 1 moves), **eliminated**
(Passes 3–4 deletions), **added** (VM layer + tests). Conflating these would
flatter the result. A committed `scripts/loc_report.py` makes it reproducible.

LOC is a weak proxy and is tracked because it was asked for, not because it is
the primary signal.

### 8.2 Complexity — the primary signal, already CI-gated

`.maintenance.json` + `bootstrapper/tests/test_maintenance_baseline.py` enforce a
complexity ledger. It already lists two of this work's targets as accepted
signals, with rationales inviting exactly this refactor:

```
_build_steps_and_rows  CC 70  "Revisit when prompt construction changes next."
_selections_to_args    CC 63  "Revisit together with _build_steps_and_rows."
```

Current wizard-surface offenders:

| Symbol | Grade |
|---|---|
| `_build_steps_and_rows` | F (70) |
| `_selections_to_args` | F (63) |
| `WizardScreen._run_pipeline_and_stream` | F (49) |
| `PromptPanel.load_step` | F (44) |
| `WizardScreen._refresh_command_summary` | E (36) |
| `PromptPanel._mount_visible_rows` | E (34) |

That is **6 of the repo's 20 E-or-worse symbols — 30% — concentrated in the
wizard**.

**Definition of done, in ledger terms:**

- `radon_grade_e_or_worse` drops from 20 by the six symbols above
- `radon_grade_c_or_worse` drops from 357
- `modules_over_600_logical_lines` drops from 14 as `wizard_screen.py`,
  `integration.py`, `prompt_panel.py`, `option_row.py` are split
- the two `accepted_signals` entries are **deleted** from `.maintenance.json`
  rather than re-accepted
- no new Radon E/F symbol is introduced without a symbol-level rationale

The ledger's own policy permits a snapshot refresh "only during a reviewed
maintenance pass"; this is one, so the refresh lands as a reviewed commit with
each delta explained.

## 9. Pass 0 — Textual upgrade detail

**Premise correction:** arbitrary text selection landed in Textual **2.0.0**;
Atlas runs **6.2.1**. At 6.2.1 `Widget.ALLOW_SELECT` and `RichLog.ALLOW_SELECT`
are both `True`, `Widget.get_selection` exists, `RichLog` does not override it,
and nothing in Atlas disables selection or captures the mouse in the log pane.
**Mouse selection already works.** The upgrade is an ergonomics improvement, not
an enabling one.

What `8.2.8` adds:

| Feature | Version | Relevance |
|---|---|---|
| auto-scroll while selecting | 8.x | the real pain in a streaming log — today a drag cannot pass the viewport edge |
| selection across containers | 8.x | the log pane is nested |
| "select outside of text" | 8.x | looser precision on dense compose output |
| `TextSelected` event | 6.11.0 | makes selection VM-observable rather than widget-private |
| double-width char selection fix | 6.4.0 | compose output contains box-drawing and emoji |

**Risk, named:** `textual-image` (imported as `Image`, drives the splash poster)
declares only `textual>=0.68.0` — a floor with no ceiling — so pip metadata will
not catch a break across two majors, and it hooks terminal image protocols into
Textual internals. It **cannot be bumped to match**: `textual-image>=0.13`
requires Python ≥3.12, which would break Atlas's 3.10 floor. Pass 0 therefore
requires an explicit splash smoke-test; if it breaks, the fallback is the
existing block-art path, not dropping Python 3.10.

Atlas does **not** import `Select`, so the headline 8.x break
(`Select.BLANK` → `Select.NULL`) does not apply. The imported surface is
`App, Binding, ComposeResult, Container, events, get_cell_size, Horizontal,
Image, Input, Message, RichLog, Screen, Selection, SelectionList, Static,
Vertical, VerticalScroll, Widget, Worker, WorkerState`.

## 10. Dependency posture

- Pin `vmx==3.23.0` exactly. Its `__min_spec_version__` equals its `__version__`
  (3.23.0), so the spec floor tracks the current release; there is no slack to
  drift into.
- VMx adds exactly one transitive runtime dependency: `reactivex>=4.0.4`.
- VMx is `requires-python >=3.10` (classifiers through 3.14) — no conflict with
  the bootstrapper floor.
- VMx is a young first-party framework (four PyPI releases: 2.6.0, 2.6.1, 3.1.0,
  3.23.0). The §3.2 import rules are what keep that coupling reversible: the
  Model layer and the `--no-tui` path never depend on it.

## 11. Risks

| Risk | Mitigation |
|---|---|
| Cross-thread widget mutation (§5.3) | Pass 2 spike proves the seam first; tier-4 tests target it specifically |
| `prompt_panel.py` / `option_row.py` interleave presentation and domain deeply | Own slices in Pass 3; the AST lint allowlist shrinks to empty |
| Long-lived branch drifts from `develop` | Periodic merges from `develop`; Pass 0 lands separately and first |
| VMx regression | Exact pin; Model and `--no-tui` structurally VMx-free (§3.2) |
| `textual-image` breaks on Textual 8.x | Explicit splash smoke-test in Pass 0; block-art fallback |
| Parity regression between TUI and `--no-tui` | Shared Model rules make parity structural, not asserted; existing gates stay green |

## 12. Documentation

Per house rule, docs land in the same slice as the change, never deferred:

- `bootstrapper/` architecture notes in `CLAUDE.md` / `AGENTS.md` updated as the
  package structure changes (note: `CLAUDE.md` is gitignored; `AGENTS.md` is the
  tracked file)
- three-surface docs regenerated where the wizard is described
- `.maintenance.json` refresh commit carries its own rationale (§8.2)
