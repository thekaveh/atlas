"""
PromptPanel — wizard's main interactive prompt body.

Hosts every interactive step the wizard exposes. The shape changes
per ``PromptStep.kind``:

  * ``options`` / ``multiselect`` (default) — option list with
    cursor-driven navigation. Multiselect adds ``[✓]`` / ``[ ]``
    checkboxes plus optional filter-chip / search-box wiring (see
    below) and a per-row variant tree for Ollama parents.
  * ``number`` — masked free-integer ``Input`` (base port, etc.).
  * ``secret`` — masked password ``Input`` with KEEP / CLEAR sentinels.
  * ``text`` — unmasked free-text ``Input`` with the same sentinels.

Ollama-style multiselect layout (the richest variant) — top-down:

    ┌─ Ollama  ·  models                          12 / 18 ──┐
    │  Which Ollama models do you want registered?            │
    │                                                         │
    │  [ Tab or /  to filter models by name…             ]   │  ← search input
    │  Filter  [ALL]  embedding  thinking  vision  tools …    │  ← chip row
    │                                                         │
    │  ▸ ▼ [✓] qwen3.6  [thinking][vision][tools][mlx]  …M    │  ← parent
    │       latest · 8b (4.8GB) · 14b (8.4GB) · 27b (17GB) …  │
    │     ○ └─ [✓] qwen3.6:8b   (4.8GB · 256K ctx)  [vision]  │  ← leaves
    │     ○ └─ [ ] qwen3.6:27b  (17GB · 256K ctx)             │
    │  ▸ ▶ [ ] llama3.1  [thinking][tools]  [legacy]    114M  │
    │                                                         │
    │  [optional dependency conflict insert here]             │
    └─────────────────────────────────────────────────────────┘

Wiring summary:
  * Search box ↔ ``_search_query`` ↔ substring filter inside
    ``_rebuild_visible``. Focus parked on the option list at mount —
    Tab / `/` / mouse-click hands focus to the Input. See
    ``focus_search``, ``has_search_focus``, ``unfocus_search``,
    ``toggle_search_focus``.
  * Filter chips ↔ ``_filter_tag`` ↔ tag membership in
    ``_rebuild_visible``. Both filters stack (chip AND substring).
  * Variant tree ↔ ``_expanded`` set + ``_variant_cache`` ↔ row
    emission in ``_rebuild_visible`` (parents + their leaves when
    expanded). Detail-page fetch worker populates the cache async.
  * Per-row checkbox ↔ ``_checked_values`` ↔ predicate
    ``_row_is_checked`` (handles bare ↔ tagged mutex for Ollama).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import Input, Static

from .. import palette as P
from wizard.model.cloud_rules import SECRET_CLEAR, SECRET_KEEP
from .dependency_conflict import ConflictAction, DependencyConflict
from .multiselect_filter_chips import (
    ALL_KEY as FILTER_ALL_KEY,
    FilterChanged,
    MultiselectFilterChips,
)
from .option_row import (
    OptionRow,
    OptionRowWithInput,
    SECONDARY_LABEL_PREFIX_WIDTH,
    _approx_size,
    _LEAF_PREFIX_WIDTH,
    _LEAF_TAG_COL,
    _PARENT_PREFIX_WIDTH,
    _PARENT_TAG_COL,
)


# ─── tree-multiselect helpers ───────────────────────────────────────────
#
# The Ollama multiselect supports per-variant selection by letting the
# user expand a multi-variant parent (``qwen3``) in place — the leaves
# (``qwen3:0.6b``, ``qwen3:8b``, …) appear directly below the parent in
# the same scrollable list. No popup, no focus handover: cursor, Space,
# and arrow keys all stay in the prompt panel. Selections persist to
# ``_checked_values`` in ``model[:tag]`` form; the predicate below
# treats a bare entry as "pull :latest" and tagged entries as "pull
# this exact variant".

# Synthetic tag prepended to every multi-variant parent's expansion so
# the user can explicitly pick the model-maker default from the tree
# instead of going hunting for the catalog's "implicit" :latest.
_LATEST_TAG = "latest"


# Capability tags whose value is per-variant (each leaf has its own
# from the detail page's Input column / MLX chip). These are NOT
# inherited from parent → leaf because the parent's listing-level
# capability is a UNION across variants (parent shows ``[vision]`` if
# any variant has Image input, but specific text-only leaves
# shouldn't; same for ``[mlx]`` — only MLX-quantized leaves carry it).
_PER_VARIANT_CAPS = frozenset({"vision", "audio", "mlx"})

# Status / lifecycle tags that describe the row's relationship to the
# user's environment, not the model's capabilities. Shown on the
# parent only; repeating them on every leaf adds visual noise without
# extra information (every leaf of a [library] parent is library; the
# user already sees that on the parent right above).
_STATUS_TAGS = frozenset({"pulled", "library", "legacy", "default"})


def _inherited_leaf_badges(parent_badges: list[str]) -> list[str]:
    """Subset of parent badges that propagate to each leaf.

    Drop status tags (parent-only) and input-modality-derived tags
    (per-variant, supplied by the detail-page Input column). Everything
    else (``thinking``, ``tools``, ``embedding``, any future capability
    that's family-level) is inherited so the leaf shows the full
    capability picture it actually supports.
    """
    return [
        b for b in parent_badges
        if b not in _PER_VARIANT_CAPS and b not in _STATUS_TAGS
    ]


@dataclass(frozen=True)
class _VisibleRow:
    """One row in PromptPanel._visible — either a parent model card or
    one of its variant leaves when the parent is expanded.

    Encoding:
      • parent: ``variant=None``, ``abs_idx`` points into step.options.
      • leaf:   ``variant="latest"`` or one of opt.sizes; ``abs_idx``
                still points to the PARENT's index in step.options.
    """
    kind: str               # "parent" or "leaf"
    abs_idx: int            # parent's index in step.options
    parent_value: str       # parent's model name
    variant: str | None     # None for parent; tag string for leaf

    def identity(self) -> str:
        """Stable cursor-identity string for save/restore across
        filter or expand/collapse re-renders."""
        if self.variant is None:
            return self.parent_value
        return f"{self.parent_value}:{self.variant}"


def _row_is_checked(row_value: str, checked: set[str]) -> bool:
    """Row-checked predicate accommodating both bare and tagged entries.

    A row is "checked" if ``_checked_values`` contains either the
    bare name (``qwen3`` → pulls :latest) or any ``model:tag`` entry
    that starts with the row's value followed by ``:``.
    """
    if row_value in checked:
        return True
    prefix = row_value + ":"
    return any(v.startswith(prefix) for v in checked)


def _row_is_checked_comfyui(opt_sizes: tuple[str, ...], checked: set[str]) -> bool:
    """ComfyUI variant-tree parent-checked aggregate.

    Each tag in ``opt_sizes`` is a full catalog name (e.g.
    ``microsoft--TRELLIS-image-large``). The parent is considered
    checked if ANY of its variants is in the selection set. The
    Ollama ``_row_is_checked`` prefix-match doesn't fit because
    ComfyUI parents use a synthetic family-root value (e.g.
    ``TRELLIS``) that isn't a substring of the leaf values.
    """
    return any(v in checked for v in opt_sizes)


def _row_variants(row_value: str, checked: set[str]) -> frozenset[str]:
    """Tags currently selected for ``row_value`` in ``checked``.

    Bare-entry case (``qwen3`` ∈ checked) maps to ``{"latest"}`` so
    the line-2 highlight on the row points at the synthetic
    :latest variant rather than silently going dark. Tagged entries
    contribute their ``:tag`` suffix as-is.
    """
    out: set[str] = set()
    if row_value in checked:
        out.add(_LATEST_TAG)
    prefix = row_value + ":"
    for v in checked:
        if v.startswith(prefix):
            out.add(v[len(prefix):])
    return frozenset(out)


def _mask_secret(value: str) -> str:
    """Render a secret as ``••••••…XXXX`` — bullets + last 4 chars."""
    s = (value or "").strip()
    if not s:
        return ""
    if len(s) <= 4:
        return "•" * len(s)
    return "•" * 8 + "…" + s[-4:]


@dataclass
class SecondaryNumberInput:
    """An inline integer textbox attached to a ``PromptOption``. The
    option renders the input directly between its label and its badges
    (see ``OptionRowWithInput``).

    On confirm, the integer value is captured alongside the tile
    selection and written to ``env_var`` via the wizard's selection
    pipeline.

    Eligibility for the inline input is now per-option: a
    ``PromptOption`` either carries one of these (eligible — input
    rendered) or doesn't (no input on that row). The earlier
    ``show_when`` filter on this dataclass is gone — equivalent
    behaviour comes from attaching the config to the matching options.

    ``unit_suffix`` is the short label rendered immediately to the
    right of the textbox on eligible rows ("workers", "port", "MB", …)
    so the user reads the row as ``ollama-localhost [ 11434 ] port
    [rec.]``. Empty string hides the suffix entirely.

    Sibling-sync semantics (when two rows on the same step both carry
    a config): inputs writing the SAME ``env_var`` mirror keystrokes
    so the user only types once; inputs writing DIFFERENT ``env_var``s
    are independent (e.g. STT step's parakeet vs. whisper-cpp ports).
    """
    env_var: str
    description: str = ""
    default_value: int = 0
    number_min: int = 0
    number_max: int = 1_000_000
    unit_suffix: str = ""


@dataclass
class PromptOption:
    value: str
    label: str
    hint: str = ""
    badges: list[str] = field(default_factory=list)
    # Display-only enrichments populated by the Ollama models step from
    # ``utils.ollama_library.OllamaLibraryEntry``. Default empty so every
    # other multiselect step (cloud /v1/models, source pickers, …) is
    # unaffected and OptionRow simply skips the columns.
    pulls: int = 0                  # 0 ⇒ unknown ⇒ not rendered
    sizes: tuple[str, ...] = ()     # () ⇒ unknown ⇒ not rendered
    # Set of variant tags (bare suffix after the ``parent:``) that the
    # upstream Ollama has actually pulled, sourced from /api/tags at
    # options-build time. Used by ``_leaf_render_data`` so each
    # individual variant row can independently render its [pulled] vs
    # [library] status — without this, a family with one pulled tag
    # and others unpulled would inherit a single status from the
    # parent, masking the per-tag reality. Empty ⇒ unknown ⇒ leaf
    # falls back to the parent's status (correct for container modes
    # where /api/tags wasn't consulted).
    pulled_variants: frozenset[str] = field(default_factory=frozenset)
    # Inline secondary integer input attached to THIS row. When set, the
    # row renders via OptionRowWithInput with an editable textbox between
    # the label and the badges. When None (the default), the row renders
    # via the plain OptionRow. See SecondaryNumberInput for semantics.
    secondary_number: "SecondaryNumberInput | None" = None
    # ComfyUI variant-tree extension: when present, each tag in
    # ``sizes`` MUST appear here as a (display_label, leaf_badges)
    # tuple. The panel uses these for leaf rendering instead of the
    # Ollama parent:tag format, treats the tag as the full selection
    # identity (not prefixed with parent.value), and suppresses the
    # synthetic ``_LATEST_TAG`` row at the head of every expansion.
    # Empty dict ⇒ Ollama-style tree (default).
    leaf_details: dict = field(default_factory=dict)


@dataclass
class PromptStep:
    title: str
    step_index: int
    step_total: int
    heading: str
    subtitle: str = ""
    options: list[PromptOption] = field(default_factory=list)
    default_value: str | None = None
    # Initial checked values for ``kind="multiselect"`` steps. Each
    # entry must match an ``options[i].value``.
    default_values: list[str] = field(default_factory=list)
    # Optional callable that returns ``options`` lazily based on the
    # in-progress wizard selections — used by the Ollama-upstream
    # multiselect, where the option list comes from a live ``/api/tags``
    # query that depends on the user's just-picked LLM source.
    options_provider: "Callable[[dict], list[PromptOption]] | None" = None
    # The exact service-table row name this step controls. Used by the
    # wizard screen to highlight the matching row when this step loads.
    service_name: str = ""
    # Canonical service key from ServiceInfo (e.g. ``llm_provider``,
    # ``doc_processor``). Used by the wizard screen's command preview
    # to emit the correct ``--<key>-source`` flag — deriving the flag
    # from ``service_name`` (display name) silently breaks when display
    # name and key diverge (``LLM Engine`` vs ``llm_provider``).
    service_key: str = ""
    # "options" (default — multiple-choice list), "number" (free
    # integer input, e.g. base port), "secret" (masked free-text
    # input for an API key, with empty/clear/keep semantics),
    # "multiselect" (checkbox list — Space toggles, Enter confirms),
    # or "text" (free-text Input, no masking).
    kind: str = "options"
    number_min: int = 1024
    number_max: int = 65000
    # ``kind="number"`` opt-in: also accept the literal string "auto"
    # alongside the numeric range. Only the base-port step sets this —
    # "auto" is a real base-port value everywhere else in Atlas
    # (``--base-port auto``, manifest ``BASE_PORT: auto``), and without
    # this flag the numeric coercion below silently replaces it with
    # ``default_value``. Every other number step stays strictly numeric.
    accepts_auto: bool = False
    # Optional predicate the wizard screen calls before loading this
    # step. Receives the in-progress ``selections`` dict and returns
    # True if this step should be skipped. Used to skip cloud
    # multi-select steps when the user disabled the provider.
    skip_if_prev: "Callable[[dict], bool] | None" = None
    # Optional override for the hint rendered below a ``kind="secret"``
    # input when an existing key is present. Cloud provider steps use
    # this to distinguish "existing key + provider already enabled"
    # from "existing key + provider currently disabled; Enter enables".
    secret_keep_hint: str | None = None
    # Tags shown as single-select filter chips above a multiselect's
    # option list. Empty ⇒ no filter row is mounted (default for every
    # step that isn't Ollama models). When non-empty, the wizard renders
    # ``[ALL] tag1 tag2 …`` and filters visible options by membership
    # in ``PromptOption.badges``.
    filter_tags: tuple[str, ...] = ()
    # secondary_number REMOVED 2026-05-25 — config now lives on each
    # PromptOption (see PromptOption.secondary_number). Eligibility is
    # "option carries a config" instead of "step-level show_when filter."


def _progress_braille(step: int, total: int, width: int = 10) -> str:
    """Tiny inline progress bar for the border subtitle."""
    if total <= 0:
        return P.PROGRESS_EMPTY * width
    ratio = max(0.0, min(1.0, step / total))
    full = int(ratio * width)
    return P.PROGRESS_FILLED * full + P.PROGRESS_EMPTY * (width - full)


AUTO_PORT = "auto"


def normalize_number_entry(raw: str, step: "PromptStep") -> str:
    """Validate a ``kind="number"`` entry into the value to commit.

    Numbers clamp into ``[number_min, number_max]``; anything unparseable
    falls back to the step's ``default_value``. When the step sets
    ``accepts_auto``, the literal ``"auto"`` (any case, surrounding
    whitespace ignored) is a valid answer and passes through unchanged —
    without that opt-in the ``int()`` below would quietly swap it for the
    default, which is exactly why the base-port step could not express a
    value the CLI and consumer manifests both accept.

    Extracted from ``PromptPanel.selected_option`` so the rule is
    testable without mounting a widget.
    """
    text = (raw or "").strip()
    if step.accepts_auto and text.lower() == AUTO_PORT:
        return AUTO_PORT
    try:
        value = int(text) if text else int(step.default_value or 0)
    except ValueError:
        try:
            value = int(step.default_value or 0)
        except ValueError:
            return str(step.default_value or "")
    return str(max(step.number_min, min(step.number_max, value)))


class PromptPanel(Container):
    """Wizard prompt body. Title + step counter live on the border."""

    DEFAULT_CSS = """
    PromptPanel {
        border: round #2b2f4a;
        background: #0e0f18;
        padding: 1 2;
        height: auto;
    }
    PromptPanel > .prompt-heading { height: 1; color: #e0e6f2; text-style: bold; }
    PromptPanel > .prompt-subtitle { height: 1; color: #8992b5; }
    PromptPanel > .prompt-spacer-2 { height: 1; }
    /* Scrollable option list — capped so a 230-entry library scrape
       doesn't blow past the viewport. The cursor is kept in view by
       PromptPanel.move() calling scroll_visible() on the focused row. */
    PromptPanel > #option-list {
        height: auto;
        max-height: 18;
        scrollbar-size-vertical: 1;
    }
    PromptPanel > #number-slot { height: auto; }
    PromptPanel #number-input {
        height: 1;
        width: 14;
        background: #1c1d30;
        color: #c0caf5;
        border: none;
        padding: 0 1;
    }
    PromptPanel #number-input:focus {
        color: #7dcfff;
        background: #1c2034;
    }
    PromptPanel #secret-input {
        height: 1;
        width: 1fr;
        background: #1c1d30;
        color: #c0caf5;
        border: none;
        padding: 0 1;
    }
    PromptPanel #secret-input:focus {
        color: #7dcfff;
        background: #1c2034;
    }
    PromptPanel #number-hint {
        height: 1;
        color: #8992b5;
        padding-top: 1;
    }
    PromptPanel #secret-hint {
        height: auto;
        color: #8992b5;
        padding-top: 1;
    }
    PromptPanel > #conflict-slot { height: auto; }
    PromptPanel > #filter-slot { height: auto; }
    PromptPanel > #search-slot { height: auto; }
    PromptPanel #search-input {
        height: 1;
        width: 1fr;
        background: #1a1b2c;
        color: #c0caf5;
        border: none;
        padding: 0 1;
    }
    /* Focused state — strongly tinted bg + cyan text so the user can
       tell at a glance their keystrokes are going to the search box.
       Without this, an Input that has focus but no text reads almost
       identical to one that doesn't, and the user can accidentally
       type into it thinking they're driving the option list. We stay
       on a 1-cell height (no border-tall) so the layout doesn't
       reflow when focus shifts. */
    PromptPanel #search-input:focus {
        background: #2c3e54;
        color: #7dcfff;
        text-style: bold;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._heading = Static("", classes="prompt-heading")
        self._subtitle = Static("", classes="prompt-subtitle")
        self._spacer2 = Static("", classes="prompt-spacer-2")
        # Container for the (optional) MultiselectFilterChips widget;
        # populated in load_step() only when the step carries
        # ``filter_tags``. Empty for every other step.
        self._filter_slot = Container(id="filter-slot")
        # Container for the (optional) search Input — appears above the
        # filter-chip row on the Ollama multiselect step. Lets the user
        # narrow the option list by substring against the model name.
        # Empty for every other step.
        self._search_slot = Container(id="search-slot")
        self._search_input: Input | None = None
        # Case-folded substring used to filter visible rows. ``""``
        # disables the substring filter. Persists across filter-chip
        # changes within the step but clears on step re-entry.
        self._search_query: str = ""
        # VerticalScroll (not plain Vertical) so long option lists
        # (Ollama library scrape can be ~230 entries) get a scrollbar
        # and a clipped viewport; otherwise the cursor moves down past
        # the bottom of the screen and the user can't see what they're
        # selecting. PromptPanel.move() calls scroll_visible() on the
        # focused row so the viewport always follows the selection.
        self._option_list = VerticalScroll(id="option-list")
        self._number_slot = Container(id="number-slot")
        # Persistent input/hint widgets — created lazily on first use,
        # reused (display toggled) on every subsequent step. Re-mounting
        # was causing DuplicateIds because Container.remove_children()
        # is asynchronous: the previous step's widget was still in the
        # node list when the next step's mount tried to register an id
        # of the same name.
        self._number_input: Input | None = None
        self._number_hint: Static | None = None
        # Per-eligible-row inline secondary inputs — one ``Input`` per
        # row whose option.value is in ``step.secondary_number.show_when``.
        # All sibling inputs share one logical value: keystrokes mirror
        # across via ``on_input_changed`` (sync block keyed off this
        # list). Empty when the active step has no ``secondary_number``
        # or no option qualifies. Rebuilt on every ``load_step`` /
        # options re-mount.
        self._secondary_inputs: list[Input] = []
        self._secret_input: Input | None = None
        self._secret_hint: Static | None = None
        self._conflict_slot = Container(id="conflict-slot")
        self._step: PromptStep | None = None
        self._selected_index = 0
        # Set of option.value strings currently checked when the
        # active step is a multi-select. Filter-tag-agnostic — switching
        # filters never mutates this set, so a row that was checked
        # under one filter stays checked when re-revealed by another.
        self._checked_values: set[str] = set()
        # Multi-select filter state. ``_filter_tag`` is the chip key
        # currently active ("all" or one of step.filter_tags).
        self._filter_tag: str = "all"
        self._filter_chips: "MultiselectFilterChips | None" = None
        # ``_visible`` is the canonical ordered list of rows currently
        # mounted in ``_option_list``: parents in step.options order
        # (filtered by ``_filter_tag``), with their variant leaves
        # spliced in immediately after each expanded parent. The
        # cursor ``_selected_index`` is a display-position index into
        # this list — works uniformly for parents and leaves. Empty
        # for non-multiselect steps (which keep absolute-index
        # semantics in ``_selected_index``).
        self._visible: list[_VisibleRow] = []
        # Parents the user has expanded — kept across re-renders
        # (filter changes, leaf toggles) so the tree state survives.
        # Cleared on step (re-)entry so revisits start collapsed.
        self._expanded: set[str] = set()
        # Per-session cache of detail-page variant data. Lazily
        # populated on first expand of each multi-variant parent —
        # see ``_ensure_variants_loaded``. ``_variant_loading`` is the
        # in-flight set so a re-expand while the worker is running
        # doesn't fire a second fetch.
        self._variant_cache: dict[str, list] = {}
        self._variant_loading: set[str] = set()
        self._on_change: Callable[[int, PromptOption], None] | None = None

    def compose(self) -> ComposeResult:
        yield self._heading
        yield self._subtitle
        yield self._spacer2
        yield self._search_slot
        yield self._filter_slot
        yield self._option_list
        yield self._number_slot
        yield self._conflict_slot

    def set_on_change(self, callback: Callable[[int, PromptOption], None]) -> None:
        self._on_change = callback

    def load_step(self, step: PromptStep) -> None:
        self._step = step
        # Drop the per-row secondary-input registry: the options-branch
        # below repopulates it when (and only when) at least one
        # ``opt.secondary_number`` is set on the step. Any other step
        # kind (number / secret / text / multiselect) finishes with an
        # empty list, which is exactly the "no secondary input on this
        # step" signal that ``secondary_values()`` uses.
        self._secondary_inputs = []
        # All caption info on the top border title — service name +
        # step counter + a small progress bar.
        bar = _progress_braille(step.step_index, step.step_total)
        self.border_title = (
            f" {step.title}  ·  {step.step_index} / {step.step_total}  {bar} "
        )
        self.border_subtitle = ""
        self._heading.update(step.heading)
        self._subtitle.update(step.subtitle)
        # Hide the persistent search input by default — the multiselect
        # branch below re-shows it when ``filter_tags`` is set. Without
        # this the Input would linger across step changes (number /
        # secret / text / non-filter multiselect) and appear above
        # forms it doesn't belong to.
        if self._search_input is not None:
            self._search_input.display = False

        if step.kind == "number":
            # Free integer input — reuse the persistent Input/Static
            # pair so we never trigger a DuplicateIds race on re-entry.
            self._option_list.remove_children()
            self._hide_secret_widgets()
            default = step.default_value or ""
            # Only the opted-in step advertises "auto" — every other
            # number step stays strictly numeric, so its hint must not
            # promise a value it will reject.
            auto_hint = " or auto" if step.accepts_auto else ""
            if default:
                hint_text = (
                    f"type a value in {step.number_min}–{step.number_max}"
                    f"{auto_hint}, or press Enter to keep {default}"
                )
            else:
                hint_text = (
                    f"type a value in {step.number_min}–{step.number_max}"
                    f"{auto_hint}"
                )
            if self._number_input is None:
                self._number_input = Input(
                    value="",
                    placeholder=default,
                    id="number-input",
                )
                self._number_slot.mount(self._number_input)
            else:
                self._number_input.value = ""
                self._number_input.placeholder = default
                self._number_input.display = True
            if self._number_hint is None:
                self._number_hint = Static(hint_text, id="number-hint")
                self._number_slot.mount(self._number_hint)
            else:
                self._number_hint.update(hint_text)
                self._number_hint.display = True
            # Focus the input so the user can type immediately.
            self._number_input.focus()
            return

        if step.kind == "secret":
            # Masked free-text input for an API key. Reuses the
            # persistent Input/Static pair across providers.
            #   empty + no existing key  → leave provider disabled
            #   empty + existing key set → keep current key (no change)
            #   "clear"                  → wipe key, set provider disabled
            #   any other text           → enable provider, store as key
            self._option_list.remove_children()
            self._hide_number_widgets()
            existing = (step.default_value or "").strip()
            placeholder = _mask_secret(existing) if existing else "paste API key here…"
            if existing:
                hint_text = step.secret_keep_hint or (
                    f"key currently set ({placeholder})  ·  Enter to keep  ·  "
                    "type a new key to replace  ·  type \"clear\" + Enter to remove"
                )
            else:
                hint_text = (
                    "paste a key + Enter to enable  ·  Enter (empty) to leave disabled"
                )
            if self._secret_input is None:
                self._secret_input = Input(
                    value="",
                    placeholder=placeholder,
                    password=True,
                    id="secret-input",
                )
                self._number_slot.mount(self._secret_input)
            else:
                self._secret_input.value = ""
                # Reset horizontal scroll — without this, a previously
                # pasted long key leaves the cursor parked at the end
                # and the masked dots scroll out of view.
                try:
                    self._secret_input.cursor_position = 0
                except Exception:  # noqa: BLE001
                    pass
                self._secret_input.placeholder = placeholder
                self._secret_input.display = True
            if self._secret_hint is None:
                self._secret_hint = Static(hint_text, id="secret-hint")
                self._number_slot.mount(self._secret_hint)
            else:
                self._secret_hint.update(hint_text)
                self._secret_hint.display = True
            self._secret_input.focus()
            return

        if step.kind == "text":
            # Free-text input (NOT masked). Reuses the same persistent
            # number-input slot — text is just "number minus the
            # numeric coercion." For Ollama "additional models to pull"
            # and similar comma-separated free-form fields. Honors
            # the same keep-current/clear sentinels as ``kind="secret"``.
            self._option_list.remove_children()
            self._hide_secret_widgets()
            default = (step.default_value or "").strip()
            placeholder = default if default else "type a value"
            if self._number_input is None:
                self._number_input = Input(
                    value="",
                    placeholder=placeholder,
                    id="number-input",
                )
                self._number_slot.mount(self._number_input)
            else:
                self._number_input.value = ""
                self._number_input.placeholder = placeholder
                self._number_input.display = True
            if default:
                hint_text = (
                    f"currently set ({default})  ·  Enter to keep  ·  "
                    "type new text to replace  ·  type \"clear\" + Enter to remove"
                )
            else:
                hint_text = "type a value + Enter, or press Enter (empty) to skip"
            if self._number_hint is None:
                self._number_hint = Static(hint_text, id="number-hint")
                self._number_slot.mount(self._number_hint)
            else:
                self._number_hint.update(hint_text)
                self._number_hint.display = True
            self._number_input.focus()
            return

        if step.kind == "multiselect":
            # Checkbox list — Space toggles the focused row, Enter
            # confirms the entire selection. Uses the existing
            # _option_list container with OptionRow widgets in
            # multi=True mode. When the step declares ``filter_tags``,
            # a MultiselectFilterChips row is mounted above the list
            # and ``_selected_index`` becomes a display-position index
            # into ``self._visible`` (the ordered list of currently-
            # mounted rows, parents + expanded leaves, post-filter)
            # instead of an absolute index into ``step.options``.
            self._hide_number_widgets()
            self._hide_secret_widgets()
            # Drop default_values that don't map to any visible row.
            # A default ``qwen3.6:latest`` maps to the row ``qwen3.6``
            # via the bare/tagged predicate; a default with no matching
            # row at all (cloud account lost access; Ollama upstream
            # doesn't have a default-active model pulled) is silently
            # dropped so it doesn't leak into the saved CSV.
            visible_row_names = {opt.value for opt in step.options}
            # Pre-build a set of ComfyUI leaf names so defaults like
            # ``microsoft--TRELLIS-image-large`` (a leaf inside the
            # synthetic ``TRELLIS`` family parent) still pre-check.
            comfyui_leaf_names: set[str] = set()
            for opt in step.options:
                if opt.leaf_details:
                    comfyui_leaf_names.update(opt.sizes)

            def _maps_to_visible_row(v: str) -> bool:
                if v in visible_row_names:
                    return True
                if v in comfyui_leaf_names:
                    return True
                if ":" in v:
                    return v.split(":", 1)[0] in visible_row_names
                return False

            self._checked_values = {
                v for v in (step.default_values or []) if _maps_to_visible_row(v)
            }
            # Auto-pre-check every variant the upstream Ollama has
            # already pulled, in addition to explicit defaults. This
            # mirrors the runtime litellm-init auto-import behaviour
            # (``OLLAMA_AUTO_IMPORT_LOCAL_MODELS``) so the wizard's UI
            # tells the same story as the rendered config will after
            # confirmation: "if it's on your host, it's in the stack".
            # Operators who want a model hidden can still uncheck it
            # — the post-confirm CSV is the final word.
            #
            # ``pulled_variants`` is populated from /api/tags by the
            # wizard's options-provider for ollama-localhost / -external
            # modes; container modes leave it empty so this loop is a
            # no-op there.
            for opt in step.options:
                for tag in opt.pulled_variants:
                    self._checked_values.add(f"{opt.value}:{tag}")
            # Reset the filter + expansion + search state on every
            # (re-)entry into a multiselect step. The default
            # "show everything, all collapsed, empty query" is the
            # least surprising entry state.
            #
            # ``_variant_cache`` is DELIBERATELY NOT reset here.
            # Model names on ollama.com are globally unique and the
            # Ollama multiselect is a singleton step, so cached
            # detail-page data from a previous visit is still valid;
            # preserving it means re-expanding a parent doesn't pay
            # another HTTP round-trip. If a future feature adds a
            # second variant-tree-style multiselect against a
            # different namespace, this cache should become keyed on
            # ``(step_index, model_name)`` instead.
            self._filter_tag = FILTER_ALL_KEY
            self._expanded = set()
            self._search_query = ""
            self._mount_filter_chips(step.filter_tags)
            self._mount_search_input(step.filter_tags)
            self._selected_index = 0
            self._mount_visible_rows()
            return

        # Default options-list step
        self._hide_number_widgets()
        self._hide_secret_widgets()
        self._selected_index = 0
        if step.default_value is not None:
            for i, opt in enumerate(step.options):
                if opt.value == step.default_value:
                    self._selected_index = i
                    break

        # Options-list mounting.
        #
        # When ANY option on the step carries a ``secondary_number``
        # config, EVERY row uses the ``OptionRowWithInput`` composite
        # so the textbox column and the badges column align across the
        # screen — eligible rows mount a real ``Input`` tagged with the
        # env_var it writes, ineligible rows mount a spacer Static of
        # the same width.
        #
        # When NO option on the step has a secondary_number, every row
        # falls back to the plain ``OptionRow`` — the existing baseline.
        self._secondary_inputs = []
        any_secondary = any(opt.secondary_number is not None for opt in step.options)

        # Fixed label column width for the composite layout (cells):
        # cursor + dot indicator (3) + inter-column gap (4) + the
        # longest option label in this step. Every row uses the same
        # value so the textbox column lands at the same x on every row.
        label_col_width = 0
        if any_secondary and step.options:
            label_col_width = SECONDARY_LABEL_PREFIX_WIDTH + max(
                len(opt.label) for opt in step.options
            )

        # Compute the longest unit_suffix in the step so every row's
        # suffix cell reserves the same width (eligible rows render the
        # text, ineligible rows render a space-padded equivalent).
        unit_suffix_for_row = ""
        if any_secondary:
            unit_suffix_for_row = max(
                (opt.secondary_number.unit_suffix
                 for opt in step.options if opt.secondary_number is not None),
                key=len,
                default="",
            )

        self._option_list.remove_children()
        for i, opt in enumerate(step.options):
            if not any_secondary:
                self._option_list.mount(OptionRow(
                    opt.label,
                    hint=opt.hint,
                    badges=opt.badges,
                    selected=(i == self._selected_index),
                ))
                continue
            cfg = opt.secondary_number
            inp: "Input | None" = None
            if cfg is not None:
                inp = Input(
                    value=str(cfg.default_value),
                    placeholder=str(cfg.default_value),
                )
                # Tag the input with its env_var so _sync_secondary_inputs
                # can mirror only between siblings sharing one. The
                # attribute is consumed in T3's sync refactor.
                inp.associated_env_var = cfg.env_var  # type: ignore[attr-defined]
                self._secondary_inputs.append(inp)
            self._option_list.mount(OptionRowWithInput(
                opt.label,
                hint=opt.hint,
                badges=opt.badges,
                selected=(i == self._selected_index),
                input_widget=inp,
                label_width=label_col_width,
                unit_suffix=unit_suffix_for_row,
            ))

    def _sync_secondary_inputs(self, source: "Input") -> None:
        """Mirror ``source.value`` across every sibling secondary input
        that writes the SAME ``env_var``.

        Called from ``on_input_changed`` whenever any eligible-row Input
        emits a Changed event. Inputs writing the same env_var share
        one logical value (e.g. Ray's two container-source rows both
        write RAY_WORKER_COUNT and stay synced); inputs writing
        different env_vars are independent (e.g. STT step's parakeet
        vs. whisper-cpp ports).

        The associated env_var was stamped on each Input as
        ``associated_env_var`` when it was mounted (see ``load_step``).
        """
        source_env_var = getattr(source, "associated_env_var", "")
        new_value = source.value or ""
        for other in self._secondary_inputs:
            if other is source:
                continue
            if getattr(other, "associated_env_var", "") != source_env_var:
                continue  # different env_var — independent
            if other.value == new_value:
                continue
            other.value = new_value

    def _hide_number_widgets(self) -> None:
        """Hide (don't remove) the persistent number-step widgets."""
        if self._number_input is not None:
            self._number_input.display = False
        if self._number_hint is not None:
            self._number_hint.display = False

    def _hide_secret_widgets(self) -> None:
        """Hide (don't remove) the persistent secret-step widgets.
        Also clear any value still in the masked input so the focus
        on the next visit starts from an empty buffer.
        """
        if self._secret_input is not None:
            self._secret_input.value = ""
            self._secret_input.display = False
        if self._secret_hint is not None:
            self._secret_hint.display = False

    # ─── filter chips + visible-index helpers ───────────────────────────

    def _mount_filter_chips(self, tags: tuple[str, ...]) -> None:
        """Show (or hide) the filter-chip row above the option list."""
        self._filter_slot.remove_children()
        if not tags:
            self._filter_chips = None
            return
        self._filter_chips = MultiselectFilterChips(
            tags, active=self._filter_tag,
        )
        self._filter_slot.mount(self._filter_chips)

    def _mount_search_input(self, filter_tags: tuple[str, ...]) -> None:
        """Show (or hide) the search-by-name input above the chip row.

        The search box only makes sense on multiselect steps with a
        material option list — gated on ``filter_tags`` for now since
        the Ollama models step is the only one that fits that profile.
        Cloud-models splash steps don't have filter_tags and skip the
        search box; they're short enough lists not to need one.

        Uses the same create-once-then-toggle pattern as the number
        and secret inputs: an Input with a fixed ``id`` mounted twice
        in quick succession (splash render → real-data render for
        ``options_provider`` steps) blows up with ``DuplicateIds``
        because ``remove_children`` is asynchronous. The persistent
        widget sidesteps that race.

        Focus is deliberately parked on the option list right after
        mount so the Input doesn't accidentally swallow the user's
        first ``space`` or ``j``/``k`` keystrokes. Tab, mouse click,
        or ``/`` are the explicit affordances that hand focus to the
        Input.
        """
        if not filter_tags:
            if self._search_input is not None:
                self._search_input.display = False
            return
        if self._search_input is None:
            self._search_input = Input(
                value="",
                placeholder="Tab or /  to filter models by name…",
                id="search-input",
            )
            self._search_slot.mount(self._search_input)
        else:
            # Reuse — reset value to match the freshly-cleared
            # ``_search_query`` on step (re-)entry and bring it back
            # on screen.
            self._search_input.value = self._search_query
            self._search_input.display = True
        # Park focus on the option list so the Input never auto-grabs
        # focus when mounted (Textual sometimes focuses the first
        # focusable widget despite ``AUTO_FOCUS = None``). Without this
        # the user can press Space expecting to expand the currently-
        # focused row and instead type a literal space into a search
        # box they can't tell is focused.
        try:
            self._option_list.focus()
        except Exception:  # noqa: BLE001
            pass

    def focus_search(self) -> None:
        """Move keyboard focus to the search input. Called from the
        wizard screen's ``/`` keybinding. No-op when the search box
        isn't mounted on the current step.
        """
        if self._search_input is not None:
            self._search_input.focus()

    def has_search_focus(self) -> bool:
        """True iff the search input is mounted AND currently focused.

        The wizard screen consults this from ``action_back`` so Esc
        returns focus to the option list (instead of rewinding the
        step) when the user is actively typing in the search box.
        """
        return (
            self._search_input is not None
            and self._search_input.has_focus
        )

    def unfocus_search(self) -> None:
        """Move focus away from the search input so keyboard
        navigation (up/down, Space, etc.) returns to the option list.
        Used by the wizard screen on Esc when the search box has focus.
        """
        if self._search_input is not None and self._search_input.has_focus:
            self._option_list.focus()

    def toggle_search_focus(self) -> None:
        """Tab-toggle: if the search box has focus, hand it back to
        the option list; otherwise focus the search box. Symmetric so
        Tab feels like the standard "next field" affordance even
        though there are only two stops in the rotation.
        """
        if self._search_input is None:
            return
        if self._search_input.has_focus:
            self.unfocus_search()
        else:
            self.focus_search()

    def on_input_submitted(self, event) -> None:
        """Enter inside the search box returns focus to the option
        list instead of confirming the whole step.

        Without this the user would have to press Esc (which feels
        like "cancel") to leave search mode after typing a query.
        ``check_action`` already suppresses the screen-level
        ``confirm`` binding while search has focus, so Textual
        Input's default Submitted message reaches us here.
        """
        if (
            self._search_input is not None
            and event.input is self._search_input
        ):
            self.unfocus_search()
            event.stop()

    def _rebuild_visible(self) -> list[_VisibleRow]:
        """Compute the visible-rows list from ``step.options``, the
        active filter tag, the expanded-parents set, and any cached
        detail-page variant data.

        Each multi-variant parent in ``_expanded`` contributes its
        leaves immediately after the parent. Source of those leaves
        in priority order:
          1. Cached ``OllamaVariant`` list from ollama.com detail-page
             fetch (full tag list including non-param variants like
             ``27b-coding-mxfp8``, with real sizes / contexts /
             per-variant capabilities).
          2. A single loading-splash leaf while the worker is in
             flight (``__loading__`` sentinel).
          3. Fallback: synthetic ``:latest`` + each scraped param-
             count size from the listing page.
        """
        out: list[_VisibleRow] = []
        if not self._step or not self._step.options:
            return out
        tag = (self._filter_tag or FILTER_ALL_KEY).strip().lower()
        query = (self._search_query or "").strip().lower()
        for i, opt in enumerate(self._step.options):
            # Case-insensitive badge match: the chip widget already
            # lowercases ``tag`` (above), but ``opt.badges`` may carry
            # Title-Case display strings emitted by upstream picker code
            # (e.g. ComfyUI's "Image" / "Image-edit" / "3D" groups).
            # Defense-in-depth so a future picker emitting mixed-case
            # badges doesn't silently filter every row out — see
            # test_filter_chip_match_is_case_insensitive.
            if tag != FILTER_ALL_KEY and tag not in {b.lower() for b in opt.badges}:
                continue
            # Substring filter against the model name (case-insensitive).
            # Empty query matches everything. Matched against
            # ``opt.value`` (the canonical Ollama model id) which is
            # what users type when they go looking for ``qwen3.6`` or
            # ``llama3.1``.
            if query and query not in opt.value.lower():
                continue
            out.append(_VisibleRow(
                kind="parent", abs_idx=i,
                parent_value=opt.value, variant=None,
            ))
            # A family is expandable when ollama.com offers multiple sizes
            # OR when the host has pulled any variant (so a custom tag like
            # ``qwen3.6:35b-a3b-coding-mxfp8`` — not in the listing or detail
            # page — still gets a visible leaf row).
            is_expandable = len(opt.sizes) >= 2 or bool(opt.pulled_variants)
            if opt.value in self._expanded and is_expandable:
                detail = self._variant_cache.get(opt.value)
                if detail is not None:
                    # Real variant list from the detail page. Track tags
                    # so we can splice in any pulled-but-not-on-the-page
                    # variants right after.
                    seen: set[str] = set()
                    for v in detail:
                        seen.add(v.tag)
                        out.append(_VisibleRow(
                            kind="leaf", abs_idx=i,
                            parent_value=opt.value, variant=v.tag,
                        ))
                    # Pulled tags missing from the detail page (e.g.
                    # community / custom builds the model-maker hasn't
                    # listed) — surface them so the user can see what's
                    # on their host, not just what's on ollama.com.
                    # NOT `tag` — that name holds the active filter chip, read
                    # by the badge test at the top of this loop for EVERY later
                    # option. Rebinding it here meant the first expanded family
                    # with host-pulled variants left `tag` holding a variant
                    # string (e.g. "8b"), so every remaining option failed the
                    # badge match and vanished — while the chip row still read
                    # ALL, giving no cue anything had been filtered.
                    for pulled_tag in sorted(opt.pulled_variants):
                        if pulled_tag in seen:
                            continue
                        out.append(_VisibleRow(
                            kind="leaf", abs_idx=i,
                            parent_value=opt.value, variant=pulled_tag,
                        ))
                elif opt.value in self._variant_loading:
                    # Fetch in flight — splash placeholder.
                    out.append(_VisibleRow(
                        kind="leaf", abs_idx=i,
                        parent_value=opt.value, variant="__loading__",
                    ))
                else:
                    # Fallback: listing-page param-count sizes + any
                    # pulled variants not in that set (so the host's
                    # real inventory is always represented even before
                    # the detail-page worker lands).
                    #
                    # For ComfyUI parents (leaf_details present) skip
                    # the synthetic _LATEST_TAG row — there's no
                    # "model-maker default" concept; every variant tag
                    # in ``opt.sizes`` is already a full catalog name.
                    seen = set()
                    iter_tags = (
                        opt.sizes if opt.leaf_details
                        else (_LATEST_TAG, *opt.sizes)
                    )
                    for v in iter_tags:
                        seen.add(v)
                        out.append(_VisibleRow(
                            kind="leaf", abs_idx=i,
                            parent_value=opt.value, variant=v,
                        ))
                    # NOT `tag` — that name holds the active filter chip, read
                    # by the badge test at the top of this loop for EVERY later
                    # option. Rebinding it here meant the first expanded family
                    # with host-pulled variants left `tag` holding a variant
                    # string (e.g. "8b"), so every remaining option failed the
                    # badge match and vanished — while the chip row still read
                    # ALL, giving no cue anything had been filtered.
                    for pulled_tag in sorted(opt.pulled_variants):
                        if pulled_tag in seen:
                            continue
                        out.append(_VisibleRow(
                            kind="leaf", abs_idx=i,
                            parent_value=opt.value, variant=pulled_tag,
                        ))
        return out

    def _leaf_render_data(
        self, vrow: _VisibleRow, opt: PromptOption,
    ) -> tuple[str, str, list[str]]:
        """Compute the leaf's display data — ``(full_name, size_label,
        leaf_badges)`` — from the detail-page cache + listing-page
        fallback. Extracted from ``_mount_visible_rows`` so the
        two-pass tag-alignment computation can reuse it without
        duplicating the cache-lookup / fallback logic.

        Per-variant ``[pulled]`` / ``[library]`` status is rendered
        independently of the parent: a family like ``qwen3.6`` whose
        host has only ``qwen3.6:35b-a3b-coding-mxfp8`` pulled will
        correctly show that ONE leaf as ``[pulled]`` while the
        ``27b``, ``35b``, etc. siblings render as ``[library]``.
        Sourced from ``opt.pulled_variants`` which the wizard's
        options-provider populates from /api/tags. Empty (e.g.
        container modes, or no upstream reachable) ⇒ no per-leaf
        status badge is added; the parent's aggregate badge is the
        only signal.
        """
        tag = vrow.variant or ""
        # ComfyUI variant-tree: leaf_details supplies a pre-computed
        # (label, badges) tuple per tag. Bypass Ollama's parent:tag
        # format + detail-cache machinery entirely.
        if opt.leaf_details and tag in opt.leaf_details:
            label, leaf_badges = opt.leaf_details[tag]
            # Empty size_label — badges (including size) are already
            # in leaf_badges; the size column on line 2 stays blank.
            return label, "", list(leaf_badges)
        full_name = f"{vrow.parent_value}:{tag}"
        detail_entry = None
        cached = self._variant_cache.get(vrow.parent_value)
        if cached is not None:
            detail_entry = next(
                (v for v in cached if v.tag == tag), None,
            )
        inherited = _inherited_leaf_badges(opt.badges)
        # Per-leaf status: present in the parent's pulled_variants set
        # ⇒ ``pulled``; otherwise ⇒ ``library`` (only when we know the
        # parent IS in the library, i.e. opt.sizes is non-empty —
        # bucket-1 pulled-but-not-in-library leaves never get here).
        leaf_status: list[str] = []
        if opt.pulled_variants:
            leaf_status = (
                ["pulled"] if tag in opt.pulled_variants else ["library"]
            )
        if detail_entry is not None:
            size_label = (
                f"({detail_entry.size_label} · "
                f"{detail_entry.context_label} ctx)"
            )
            leaf_badges = (
                leaf_status + inherited + sorted(detail_entry.capabilities)
            )
        elif tag == _LATEST_TAG:
            size_label = "(model-maker default)"
            leaf_badges = leaf_status + list(inherited)
        else:
            approx = _approx_size(tag)
            if approx and approx != tag:
                size_label = f"({tag} · {approx})"
            else:
                size_label = f"({tag})"
            leaf_badges = leaf_status + list(inherited)
        return full_name, size_label, leaf_badges

    def _mount_visible_rows(self, *, restore_identity: str | None = None) -> None:
        """Re-render the option list. Called on initial step load,
        every ``FilterChanged``/``cycle_filter``, and every
        expand/collapse/leaf-toggle gesture.

        Runs in two passes. Pass 1 walks ``self._visible`` to collect
        the maximum content width across parents and leaves separately;
        pass 2 mounts the rows passing each one a ``tag_start_col`` so
        every row's capability/status tag block lands at the same
        horizontal column — even when one outlier leaf has a much
        longer label than its siblings (e.g. ``qwen3.6:35b-a3b-coding-
        mxfp8`` next to ``qwen3.6:8b``).

        ``restore_identity`` is the cursor's ``_VisibleRow.identity()``
        from before the rebuild. When provided, the cursor is parked
        on the matching row in the new visible list; when the row no
        longer exists (e.g. collapse hid the leaf, filter dropped the
        parent) the cursor falls back to position 0.
        """
        if self._step is None:
            return
        self._visible = self._rebuild_visible()
        # OptionRow has no ``id=`` so the sync remove/mount pair is
        # safe — Textual only raises DuplicateIds when two widgets in
        # the same tree share an explicit id.
        self._option_list.remove_children()
        if not self._visible:
            # Empty filter — show a non-toggleable placeholder.
            self._option_list.mount(OptionRow(
                "(no models match this filter)",
                selected=False,
                multi=False,
            ))
            self._selected_index = 0
            return
        # Resolve cursor: explicit restore wins; otherwise clamp.
        if restore_identity is not None:
            self._selected_index = next(
                (i for i, vr in enumerate(self._visible)
                 if vr.identity() == restore_identity),
                0,
            )
        elif not (0 <= self._selected_index < len(self._visible)):
            self._selected_index = 0

        # ── Pass 1: collect per-row metadata + width metrics ──
        parent_label_max = 0
        leaf_content_max = 0
        prepared: list[dict] = []
        for disp_i, vrow in enumerate(self._visible):
            opt = self._step.options[vrow.abs_idx]
            is_focus = (disp_i == self._selected_index)
            if vrow.kind == "parent":
                if len(opt.label) > parent_label_max:
                    parent_label_max = len(opt.label)
                prepared.append({
                    "kind": "parent", "opt": opt,
                    "is_focus": is_focus, "vrow": vrow,
                })
                continue
            tag = vrow.variant or ""
            if tag == "__loading__":
                prepared.append({"kind": "loading", "is_focus": is_focus})
                continue
            full_name, size_label, leaf_badges = self._leaf_render_data(vrow, opt)
            # Content width = label + 2-cell gap + size_label (when
            # present). Mirrors what ``OptionRow._render_leaf`` emits
            # between the prefix and the tag block.
            content = len(full_name) + (2 + len(size_label) if size_label else 0)
            if content > leaf_content_max:
                leaf_content_max = content
            prepared.append({
                "kind": "leaf", "opt": opt, "is_focus": is_focus,
                "vrow": vrow, "full_name": full_name,
                "size_label": size_label, "leaf_badges": leaf_badges,
            })

        # Tag-block start columns: at least the static defaults, but
        # bumped up to clear the longest visible content + a 2-cell
        # minimum gap. Parents and leaves get separate columns because
        # their prefix widths differ.
        parent_tag_col = max(
            _PARENT_TAG_COL, _PARENT_PREFIX_WIDTH + parent_label_max + 2,
        )
        leaf_tag_col = max(
            _LEAF_TAG_COL, _LEAF_PREFIX_WIDTH + leaf_content_max + 2,
        )

        # ── Pass 2: mount rows with the computed alignment columns ──
        for entry in prepared:
            kind = entry["kind"]
            if kind == "loading":
                self._option_list.mount(OptionRow(
                    "⏳ Fetching variants from ollama.com/library …",
                    selected=entry["is_focus"],
                    multi=False,
                    is_leaf=True,
                ))
                continue
            opt = entry["opt"]
            is_focus = entry["is_focus"]
            vrow = entry["vrow"]
            if kind == "parent":
                if opt.leaf_details:
                    # ComfyUI: aggregate over the full variant names
                    # in opt.sizes (parent value is a synthetic family
                    # root that doesn't appear in _checked_values).
                    row_checked = _row_is_checked_comfyui(
                        opt.sizes, self._checked_values,
                    )
                    row_variants = (
                        frozenset(v for v in opt.sizes
                                  if v in self._checked_values)
                        if row_checked else frozenset()
                    )
                else:
                    row_checked = _row_is_checked(opt.value, self._checked_values)
                    row_variants = (
                        _row_variants(opt.value, self._checked_values)
                        if row_checked else frozenset()
                    )
                # Expand-indicator state: parents are expandable when
                # ollama.com lists multiple sizes OR when the host has
                # pulled any variant (custom builds appear as a leaf
                # row even without listing/detail-page coverage).
                if len(opt.sizes) >= 2 or opt.pulled_variants:
                    expand_state = "expanded" if opt.value in self._expanded else "collapsed"
                else:
                    expand_state = "none"
                # Line-2 size column source — prefer the detail-page
                # tag list when we have it (so detail-only tags like
                # ``27b-coding-mxfp8`` get listed AND highlighted when
                # the user selects them). Falls back to the listing-
                # page param-count sizes otherwise. The synthetic
                # ``latest`` is prepended in OptionRow._render_size_variants;
                # we strip it from the input list to avoid a duplicate.
                detail = self._variant_cache.get(opt.value)
                if detail is not None:
                    line2_sizes = tuple(
                        v.tag for v in detail if v.tag != _LATEST_TAG
                    )
                    line2_labels = {
                        v.tag: v.size_label
                        for v in detail
                        if v.tag != _LATEST_TAG
                    }
                else:
                    line2_sizes = opt.sizes
                    line2_labels = None
                self._option_list.mount(OptionRow(
                    opt.label,
                    hint=opt.hint,
                    badges=opt.badges,
                    selected=is_focus,
                    multi=True,
                    checked=row_checked,
                    sizes=line2_sizes,
                    pulls=opt.pulls,
                    checked_variants=row_variants,
                    expand_state=expand_state,
                    size_labels=line2_labels,
                    tag_start_col_override=parent_tag_col,
                ))
            else:
                full_name = entry["full_name"]
                size_label = entry["size_label"]
                leaf_badges = entry["leaf_badges"]
                # ComfyUI leaves use the variant tag directly as the
                # selection identity (the variant IS a full catalog
                # name like ``microsoft--TRELLIS-image-large``);
                # Ollama leaves use the parent:tag identity.
                opt_for_leaf = self._step.options[vrow.abs_idx]
                if opt_for_leaf.leaf_details:
                    leaf_value = vrow.variant or ""
                else:
                    leaf_value = vrow.identity()
                leaf_checked = (
                    leaf_value in self._checked_values
                    or (vrow.variant == _LATEST_TAG
                        and vrow.parent_value in self._checked_values)
                )
                self._option_list.mount(OptionRow(
                    full_name,
                    selected=is_focus,
                    multi=True,
                    checked=leaf_checked,
                    is_leaf=True,
                    leaf_size_label=size_label,
                    badges=leaf_badges,
                    tag_start_col_override=leaf_tag_col,
                ))

    def _apply_filter_tag(self, tag: str) -> None:
        """Switch the active filter chip and re-mount visible rows.

        Shared implementation for chip-click (``on_filter_changed``)
        and keyboard cycle (``cycle_filter``). Preserves the cursor on
        the same option value when that row survives the filter
        change; falls back to position 0 otherwise. The checked-set
        is filter-agnostic, so rows hidden by the new filter stay
        checked and reappear when the user switches back.
        """
        if (
            self._step is None
            or self._step.kind != "multiselect"
            or not self._step.filter_tags
        ):
            return
        # Normalize once: chips lowercase their keys at construction, but
        # PromptStep.filter_tags may be Title-Case (ComfyUI's are). Without
        # this, `f`-cycling filtered the rows correctly while highlighting
        # the ALL chip (set_active fell back on the unknown key).
        tag = (tag or FILTER_ALL_KEY).strip().lower()
        if tag == self._filter_tag:
            return
        prev_identity: str | None = None
        if self._visible and 0 <= self._selected_index < len(self._visible):
            prev_identity = self._visible[self._selected_index].identity()
        self._filter_tag = tag
        if self._filter_chips is not None:
            self._filter_chips.set_active(tag)
        self._mount_visible_rows(restore_identity=prev_identity)

    def on_filter_changed(self, event: FilterChanged) -> None:
        """Handle a chip click bubbling up from MultiselectFilterChips."""
        self._apply_filter_tag(event.tag)
        event.stop()

    def cycle_filter(self, direction: int = 1) -> None:
        """Advance the active filter chip by ``direction`` positions.

        Order is ``[ALL, *step.filter_tags]``; wraps at both ends.
        No-op on steps without filter_tags. Used by the wizard's
        ``f`` keyboard binding so users on terminals without mouse
        passthrough can still drive the filter chip row.
        """
        if not self._step or not self._step.filter_tags:
            return
        # Lowercase to match the normalized self._filter_tag — a raw
        # Title-Case order made order.index() raise after a chip click
        # (which posts lowercase keys), silently restarting the cycle
        # from ALL every time.
        order = [FILTER_ALL_KEY,
                 *(t.strip().lower() for t in self._step.filter_tags)]
        try:
            idx = order.index(self._filter_tag)
        except ValueError:
            idx = 0
        new_tag = order[(idx + direction) % len(order)]
        if new_tag == self._filter_tag:
            return
        self._apply_filter_tag(new_tag)

    def clear_conflict(self) -> None:
        self._conflict_slot.remove_children()

    def show_conflict(
        self,
        *,
        title: str,
        body: str,
        actions: list[ConflictAction],
    ) -> None:
        self._conflict_slot.remove_children()
        self._conflict_slot.mount(DependencyConflict(title=title, body=body, actions=actions))

    @property
    def selected_index(self) -> int:
        return self._selected_index

    def secondary_values(self) -> list[tuple[str, str]]:
        """Return ``[(env_var, value)]`` for the currently-selected
        option's ``secondary_number`` config, or ``[]`` when:
          - the step isn't ``kind="options"``, OR
          - no option is selected, OR
          - the selected option has no ``secondary_number`` config.

        Returning a list (rather than a single Optional tuple) keeps
        the wizard's drain loop generic — a future step could expose
        multiple env vars per selected option without a signature
        change. Today the list always has 0 or 1 entries.

        Per spec §7.5: writes are gated by the CURRENTLY-SELECTED
        option. Typing a value in an eligible row and then navigating
        away to a non-eligible row produces ``[]`` here, so ``.env``
        is not written with the abandoned row's value. This mirrors
        the pre-refactor ``show_when`` filter behaviour.

        Per-input clamping into the configured ``[number_min,
        number_max]`` range; non-numeric input falls back to
        ``default_value``.
        """
        if self._step is None or self._step.kind != "options":
            return []
        sel = self.selected_option
        if sel is None or sel.secondary_number is None:
            return []
        cfg = sel.secondary_number
        # Find the mounted Input for this option in mount order. The
        # mount-order pairing convention from load_step: each eligible
        # option contributes one Input to ``_secondary_inputs`` in the
        # same order as ``step.options``. Walk both lists in lockstep
        # to find the input matching the selected option.
        input_iter = iter(self._secondary_inputs)
        for opt in self._step.options:
            if opt.secondary_number is None:
                continue
            try:
                inp = next(input_iter)
            except StopIteration:
                return []
            if opt is sel:
                raw = (inp.value or "").strip()
                try:
                    value = int(raw) if raw else int(cfg.default_value)
                except ValueError:
                    value = int(cfg.default_value)
                value = max(cfg.number_min, min(cfg.number_max, value))
                return [(cfg.env_var, str(value))]
        return []

    @property
    def selected_option(self) -> PromptOption | None:
        if self._step is None:
            return None
        if self._step.kind == "number":
            # Build a synthetic option with the validated number value
            # (or the literal "auto" when the step opts in).
            raw = self._number_input.value if self._number_input else ""
            value = normalize_number_entry(raw, self._step)
            return PromptOption(value=value, label=value)
        if self._step.kind == "text":
            # Same keep-current/clear sentinels as ``kind="secret"`` so an
            # empty Enter on a step with an existing default_value doesn't
            # silently wipe the user's previously-saved value (e.g.
            # OLLAMA_CUSTOM_MODELS).
            #   empty + no existing → ""
            #   empty + existing    → SECRET_KEEP (keep current)
            #   "clear"             → SECRET_CLEAR
            #   any other text      → typed value
            raw = (self._number_input.value if self._number_input else "").strip()
            has_existing = bool((self._step.default_value or "").strip())
            if raw == "":
                if has_existing:
                    return PromptOption(value=SECRET_KEEP, label="kept current")
                return PromptOption(value="", label="(empty)")
            if raw.lower() == "clear":
                return PromptOption(value=SECRET_CLEAR, label="cleared")
            return PromptOption(value=raw, label=raw)
        if self._step.kind == "multiselect":
            # Degraded step: no real (non-empty-value) options means the
            # provider fetch failed or returned only a placeholder row.
            # Commit "keep current" so an Enter here can't wipe the
            # user's previously-saved CSV (e.g. OLLAMA_USER_MODELS)
            # after a transient network failure.
            real = [o for o in (self._step.options or [])
                    if (o.value or "").strip()]
            if not real:
                return PromptOption(value=SECRET_KEEP,
                                    label="kept current (options unavailable)")
            checked = sorted(self._checked_values)
            return PromptOption(
                value=",".join(checked),
                label=f"{len(checked)} selected" if checked else "(none)",
            )
        if self._step.kind == "secret":
            # Encode the secret-step result with sentinel values so
            # downstream code can distinguish keep-current vs. clear vs.
            # type-a-new-one without leaking the actual key into option
            # state any longer than necessary.
            raw = (self._secret_input.value if self._secret_input else "").strip()
            has_existing = bool((self._step.default_value or "").strip())
            if raw == "":
                if has_existing:
                    return PromptOption(value=SECRET_KEEP, label="kept current")
                return PromptOption(value="", label="(disabled)")
            if raw.lower() == "clear":
                return PromptOption(value=SECRET_CLEAR, label="cleared")
            return PromptOption(value=raw, label="enabled")
        if not self._step.options:
            return None
        idx = max(0, min(self._selected_index, len(self._step.options) - 1))
        return self._step.options[idx]

    def move(self, delta: int) -> None:
        if not self._step or not self._step.options:
            return
        # For a multiselect with filter_tags the cursor is a
        # display-position index into ``_visible`` (which can include
        # tree leaves when parents are expanded). Branch on
        # ``filter_tags`` alone so an empty-filter placeholder returns
        # early instead of silently advancing the cursor through
        # hidden options.
        if self._step.kind == "multiselect" and self._step.filter_tags:
            n = len(self._visible)
        else:
            n = len(self._step.options)
        if n == 0:
            return
        new = (self._selected_index + delta) % n
        if new == self._selected_index:
            return
        self._selected_index = new
        # ``OptionRow`` is the normal row widget; ``OptionRowWithInput``
        # is the composite used for eligible rows on prompts with a
        # ``secondary_number``. A bare ``query(OptionRow)`` would skip
        # the composites (they're Container, not Widget subclasses) and
        # leave their highlight stuck — iterate the children directly
        # and forward to whichever ``set_selected`` is exposed.
        rows = [
            r for r in self._option_list.children
            if isinstance(r, (OptionRow, OptionRowWithInput))
        ]
        for i, row in enumerate(rows):
            row.set_selected(i == new)
        # Keep the focused row in the scrollable viewport. Without this,
        # moving past the bottom of the visible area scrolls the index
        # but not the viewport — the row is selected but invisible.
        if 0 <= new < len(rows):
            try:
                rows[new].scroll_visible(animate=False)
            except Exception:  # noqa: BLE001
                # Best-effort: pre-mount or test contexts may not have a
                # parent scroller; selection still updates correctly.
                pass
        if self._on_change and self.selected_option is not None:
            self._on_change(new, self.selected_option)

    def on_input_changed(self, event: "Input.Changed") -> None:
        """Live updates from the Input widgets mounted in the panel.

        Three consumers:

        * **Search input** (multiselect with filter_tags): re-render
          the visible row set on every keystroke so the user sees
          results narrow live as they type.
        * **Secret input** (cloud API key step): show a char-count
          confirmation hint since the masked dots can scroll out of
          view on long keys.
        * **Secondary inputs** (options step with ``secondary_number``):
          mirror the typed value across every sibling input so the
          inline textboxes on each eligible row stay consistent (they
          share one logical value).
        """
        if self._step is None:
            return
        # Secondary-inputs sync — fires for any eligible-row Input on an
        # options step that has ``secondary_number`` set. Checked first
        # because it's the cheapest discriminator (membership test).
        if event.input in self._secondary_inputs:
            self._sync_secondary_inputs(event.input)
            return
        # Search-input branch — fires for the Ollama multiselect.
        if (
            self._search_input is not None
            and event.input is self._search_input
            and self._step.kind == "multiselect"
        ):
            prev_identity: str | None = None
            if self._visible and 0 <= self._selected_index < len(self._visible):
                prev_identity = self._visible[self._selected_index].identity()
            self._search_query = event.value or ""
            self._mount_visible_rows(restore_identity=prev_identity)
            return
        if (
            self._step.kind != "secret"
            or self._secret_input is None
            or self._secret_hint is None
            or event.input is not self._secret_input
        ):
            return
        n = len(event.value or "")
        if n == 0:
            # Restore the static hint when the field is cleared.
            existing = (self._step.default_value or "").strip()
            placeholder = _mask_secret(existing) if existing else "paste API key here…"
            if existing:
                self._secret_hint.update(
                    self._step.secret_keep_hint or (
                        f"key currently set ({placeholder})  ·  Enter to keep  ·  "
                        "type a new key to replace  ·  type \"clear\" + Enter to remove"
                    )
                )
            else:
                self._secret_hint.update(
                    "paste a key + Enter to enable  ·  Enter (empty) to leave disabled"
                )
        else:
            self._secret_hint.update(f"✓ {n} char{'s' if n != 1 else ''} entered  ·  Enter to confirm")

    def toggle_focused(self) -> None:
        """Multi-select: Space on the focused row.

        Behaviour depends on what's under the cursor:

        * **Multi-variant parent** (Ollama row with sizes ≥ 2 on a
          filter-enabled step): expand or collapse the parent in
          place. Selection state is unchanged — Space is purely a UI
          gesture here. Use a leaf below to toggle a specific tag.
        * **Single-variant parent** (embedding-only models, custom
          local builds, non-Ollama multiselect rows): toggle the bare
          row entry — preserves existing behaviour from before the
          tree feature was added.
        * **Leaf row** (a variant exposed under an expanded parent):
          toggle the ``model:tag`` entry. The bare↔tagged mutex is
          enforced: adding a tagged entry clears any bare entry for
          the same parent.
        """
        if not self._step or self._step.kind != "multiselect":
            return
        if not self._step.options:
            return
        if not self._visible and self._step.filter_tags:
            # Empty-filter placeholder visible — bail so Space doesn't
            # silently mutate hidden rows.
            return
        if self._step.filter_tags:
            disp_idx = max(0, min(self._selected_index, len(self._visible) - 1))
            vrow = self._visible[disp_idx]
            opt = self._step.options[vrow.abs_idx]
        else:
            # Non-filter multiselect (cloud /v1/models): no tree, no
            # _visible list; keep absolute-index semantics.
            disp_idx = max(0, min(self._selected_index, len(self._step.options) - 1))
            opt = self._step.options[disp_idx]
            vrow = None
        # Don't toggle the temporary "⏳ Fetching…" splash row (cloud-
        # models step / detail-page worker in flight) or an empty-value
        # informational placeholder row (e.g. llm_steps.py's degraded-
        # catalog notice — mirrors the ``real = [...]`` filter below).
        if opt.value == "__loading__" or not opt.value:
            return
        if vrow is not None and vrow.variant == "__loading__":
            return

        # Branch on row kind.
        if vrow is not None and vrow.kind == "leaf":
            # ComfyUI leaf: variant IS the full selection identity, so
            # skip the Ollama parent:tag composition. Toggle the
            # variant directly in _checked_values.
            opt_for_leaf = self._step.options[vrow.abs_idx]
            if opt_for_leaf.leaf_details:
                variant = vrow.variant or ""
                if variant in self._checked_values:
                    self._checked_values.discard(variant)
                else:
                    self._checked_values.add(variant)
            else:
                self._toggle_leaf(vrow.parent_value, vrow.variant or "")
            # Re-render: parent's aggregate checkbox + line-2 colour
            # may have changed too, so refresh the whole visible list.
            self._mount_visible_rows(restore_identity=vrow.identity())
            return

        # Parent (or non-tree multiselect option). Expand on activation
        # when ollama.com lists multiple sizes OR the host has pulled a
        # variant — so families with a single listing-page size but a
        # custom pulled tag still open into a leaf row.
        if (
            self._step.filter_tags
            and (len(opt.sizes) >= 2 or opt.pulled_variants)
        ):
            # Multi-variant parent → expand / collapse, no selection
            # change. Cursor stays on the parent.
            if opt.value in self._expanded:
                self._expanded.discard(opt.value)
            else:
                self._expanded.add(opt.value)
                if not opt.leaf_details:
                    # Ollama: trigger an async fetch of the detail page
                    # so we can surface real variants (incl. non-param
                    # ones like ``27b-coding-mxfp8``) with their real
                    # sizes and per-variant capabilities. ComfyUI
                    # parents already carry full per-leaf data in
                    # ``leaf_details`` — no network fetch needed.
                    self._ensure_variants_loaded(opt.value)
            self._mount_visible_rows(restore_identity=opt.value)
            return

        # Single-variant / non-filter path — bare toggle, same as
        # before the tree feature shipped.
        if opt.value in self._checked_values:
            self._checked_values.discard(opt.value)
            new_state = False
        else:
            self._checked_values.add(opt.value)
            new_state = True
        rows = list(self._option_list.query(OptionRow))
        if 0 <= disp_idx < len(rows):
            rows[disp_idx].set_checked(new_state)

    # ─── tree-multiselect helpers ────────────────────────────────────

    def _ensure_variants_loaded(self, model: str) -> None:
        """Kick off the detail-page fetch for ``model`` if not already
        cached or in flight. Idempotent.

        The fetch runs in a Textual worker so the wizard stays
        responsive (the HTTP request is offloaded via
        ``asyncio.to_thread``). On completion the worker updates the
        cache and re-renders the visible rows so the loading-splash
        leaf is replaced with the real per-variant list.
        """
        if model in self._variant_cache or model in self._variant_loading:
            return
        self._variant_loading.add(model)
        try:
            self.run_worker(
                self._fetch_variants_worker(model),
                exclusive=False, exit_on_error=False,
            )
        except Exception:  # noqa: BLE001
            # No app / worker manager (e.g. headless smoke test);
            # leave the loading state so the splash stays visible.
            pass

    async def _fetch_variants_worker(self, model: str) -> None:
        """Worker body: blocking HTTP fetch + parse, then re-render."""
        import asyncio
        from utils.ollama_library import fetch_model_variants
        try:
            variants = await asyncio.to_thread(
                fetch_model_variants, model, 5.0,
            )
        except Exception:  # noqa: BLE001
            variants = None
        self._variant_loading.discard(model)
        # Only commit + re-render if this model is still expanded on
        # a multiselect step. If the user collapsed, navigated to a
        # different step kind, or unmounted the panel entirely while
        # the fetch was in flight, skip the cache write so a future
        # feature that mounts a second variant-tree multiselect against
        # a different model namespace won't see stale data leak in.
        # (Today's wizard has only one Ollama step so model names are
        # globally unique — this guard is defensive.)
        still_relevant = (
            self._step is not None
            and self._step.kind == "multiselect"
            and model in self._expanded
        )
        if not still_relevant:
            return
        if variants is not None:
            self._variant_cache[model] = variants
        self._mount_visible_rows()

    def _toggle_leaf(self, parent: str, variant: str) -> None:
        """Toggle the per-variant selection state for one leaf.

        Enforces the bare↔tagged invariant: adding a tagged entry
        clears any pre-existing bare entry for the same parent. Also
        handles the implicit-:latest case where a bare entry from
        legacy .env values appears "checked" against the synthetic
        :latest leaf.
        """
        if not variant:
            return
        tagged = f"{parent}:{variant}"
        is_latest = variant == _LATEST_TAG
        # "checked" from the user's perspective: tagged entry present,
        # OR (for :latest only) the bare entry stands in.
        bare_implicit_latest = is_latest and parent in self._checked_values
        currently_checked = tagged in self._checked_values or bare_implicit_latest

        if currently_checked:
            # Uncheck: drop both the explicit tagged entry and (if
            # we're on :latest) the implicit bare entry that mirrored it.
            self._checked_values.discard(tagged)
            if is_latest:
                self._checked_values.discard(parent)
        else:
            # Check: ensure the bare entry is gone (mutex with tagged),
            # then add the tagged form.
            self._checked_values.discard(parent)
            self._checked_values.add(tagged)
