"""ComfyUI model-picker wizard step.

Mirrors bootstrapper/wizard/llm_steps.py's role for ComfyUI's model
selection. Single 'ComfyUI · models' step with filter chips for the 6
display groups (All / Image / Image-edit / Video / Audio / 3D), name
search, and warn-only badges for custom-node requirements + hardware
mismatches.

Data source: `assemble_wizard_catalog()` (HF + civitai live scrape +
curated + fallback). Pulled flags are best-effort: the models live in
the `<project>-comfyui-models` named Docker volume, so the host-side
wizard can only see them when `docker volume inspect`'s Mountpoint is
readable from the host (rootful Linux). On Docker Desktop the path
lives inside the VM and the [pulled] badges are simply omitted. NO DB
query — the wizard writes selections to COMFYUI_USER_MODELS in .env;
the bootstrapper resolves these at start via comfyui_resolver and writes
volumes/comfyui/{selected-models.yaml, active-models.tsv}.
"""
from __future__ import annotations

import functools
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List

from utils.comfyui_library import (
    ComfyUILibraryEntry,
    CATEGORY_DISPLAY_GROUPS,
    _family_root,
)
from ui.textual.widgets.prompt_panel import (
    PromptOption,
    PromptStep,
)


COMFYUI_MODELS_TITLE = "ComfyUI  ·  models"

# Filter chip tags parallel CATEGORY_DISPLAY_GROUPS (minus "All" which is
# always implicit via the [ALL] chip).
_FILTER_TAGS: tuple[str, ...] = ("Image", "Image-edit", "Video", "Audio", "3D")


@dataclass
class _ComfyUIOption:
    """Internal option row produced by _merged_comfyui_options().

    Has the same core shape as PromptOption (value, label, hint, badges)
    plus two extra fields (group, checked) used at registration time to
    seed filter chips and default_values. The tests access these via
    getattr so the real PromptOption (which lacks group/checked) remains
    unmodified.

    For family-parent rows (HF entries sharing a leading-letters root
    like ``TRELLIS``), ``sizes`` and ``leaf_details`` carry the per-
    variant data the panel's variant-tree expansion uses. Flat rows
    (singletons / civitai / curated / sidecar) leave them empty.
    """
    value: str
    label: str
    hint: str
    badges: list[str]
    group: str      # display group for the filter chip system
    checked: bool   # True → pre-selected in the wizard's default_values
    sizes: tuple[str, ...] = ()                          # variant tags for family parents (full catalog names)
    leaf_details: dict = field(default_factory=dict)     # variant tag → (display_label, leaf_badges)


@functools.lru_cache(maxsize=1)
def _detect_gpu_memory_gb() -> float | None:
    """Best-effort GPU memory detection via nvidia-smi.

    Returns None on CPU-only / no nvidia-smi / errors.
    """
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True, timeout=5,
            encoding="utf-8", errors="replace",
        )
        values = [int(v.strip()) for v in out.stdout.splitlines() if v.strip()]
        if not values:
            return None
        return max(values) / 1024  # MiB → GiB
    except Exception:  # noqa: BLE001 — best-effort
        return None


def _scan_pulled(volume_root: Path) -> set[str]:
    """Walk the comfyui-models volume; return set of filenames present
    with non-zero size.
    """
    pulled: set[str] = set()
    if not volume_root.is_dir():
        return pulled
    try:
        for p in volume_root.rglob("*"):
            if p.is_file() and p.stat().st_size > 0:
                pulled.add(p.name)
    except OSError:
        # Permission denied mid-walk (e.g. root-owned docker dirs) —
        # return whatever was collected; badges are best-effort.
        pass
    return pulled


def _resolve_models_volume_root(env_vars: Dict[str, str]) -> Path | None:
    """Host-side mountpoint of the `<project>-comfyui-models` volume.

    The wizard runs on the HOST while the models live in a named Docker
    volume (in-container path /opt/ComfyUI/models), so the only host
    handle is `docker volume inspect`. Returns None when the volume
    doesn't exist yet or its Mountpoint isn't readable from the host
    (Docker Desktop keeps it inside the VM) — callers then skip the
    [pulled] badges instead of scanning a path that can never match.
    """
    project = (env_vars.get("PROJECT_NAME", "atlas") or "atlas").strip()
    volume_name = f"{project}-comfyui-models"
    try:
        out = subprocess.run(
            ["docker", "volume", "inspect", "-f", "{{ .Mountpoint }}",
             volume_name],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    mountpoint = Path(out.stdout.strip())
    return mountpoint if mountpoint.is_dir() else None


def _filename_of(url: str) -> str:
    """Extract the filename from a download URL (before any query params)."""
    return url.rsplit("/", 1)[-1].split("?", 1)[0]


def _display_group_for(category: str) -> str:
    """Map a category string to its CATEGORY_DISPLAY_GROUPS group name."""
    for group, cats in CATEGORY_DISPLAY_GROUPS.items():
        if group == "All":
            continue
        if category in cats:
            return group
    return "All"


def _badges_for_entry(
    entry: ComfyUILibraryEntry,
    is_pulled: bool,
    gpu_mem_gb: float | None,
) -> list[str]:
    """Compose the badge list for one catalog entry.

    Badge order: family, category, size, precision/variant, license,
    [pulled], custom-node warns, memory warns, license restrictions.
    Warn-only — nothing is suppressed from the option list.
    """
    out: list[str] = [
        f"[{entry.family}]",
        entry.category,
        f"{entry.size_gb:.2f}GB",
    ]
    if entry.precision:
        out.append(f"[{entry.precision}]")
    if entry.variant:
        out.append(f"[{entry.variant}]")
    if entry.license_name:
        out.append(f"[{entry.license_name}]")
    if is_pulled:
        out.append("[pulled]")
    if entry.requires_custom_node:
        nodes = " + ".join(entry.requires_custom_node)
        out.append(f"⚠ node: {nodes}")
    if gpu_mem_gb is None and not entry.cpu_supported:
        out.append("⚠ requires GPU")
    elif (
        gpu_mem_gb is not None
        and entry.min_vram_gb is not None
        and entry.min_vram_gb > gpu_mem_gb
    ):
        out.append(f"⚠ requires {entry.min_vram_gb:.0f} GB VRAM")
    if entry.min_ram_gb is not None:
        out.append(f"⚠ requires {entry.min_ram_gb:.0f} GB RAM")
    out.extend(f"⚠ {restriction}" for restriction in entry.license_restrictions)
    return out


def _entry_is_pulled(
    entry: ComfyUILibraryEntry,
    pulled_names: set[str],
) -> bool:
    """Return whether every physical artifact for an entry is present."""
    if entry.files:
        filenames = {
            model_file.filename or _filename_of(model_file.url)
            for model_file in entry.files
        }
        return bool(filenames) and filenames.issubset(pulled_names)
    return (
        entry.name in pulled_names
        or _filename_of(entry.url) in pulled_names
        or (entry.filename is not None and entry.filename in pulled_names)
    )


def _flat_option(
    entry: ComfyUILibraryEntry,
    *,
    pulled_names: set[str],
    default_selected: set[str],
    gpu_mem_gb: float | None,
) -> _ComfyUIOption:
    """Build a single flat (non-family) option row for one entry."""
    # download_models.sh saves under the catalog's filename column. Bundles
    # are complete only when every declared file is present.
    is_pulled = _entry_is_pulled(entry, pulled_names)
    group = (
        "Custom" if entry.source == "custom"
        else _display_group_for(entry.category)
    )
    badges = _badges_for_entry(entry, is_pulled, gpu_mem_gb)
    hint = " ".join(badges)
    full_badges = [group.lower()] + badges
    return _ComfyUIOption(
        value=entry.name, label=entry.name, hint=hint, badges=full_badges,
        group=group, checked=(entry.name in default_selected),
    )


def _family_parent_option(
    family_root: str,
    members: list[ComfyUILibraryEntry],
    *,
    pulled_names: set[str],
    default_selected: set[str],
    gpu_mem_gb: float | None,
) -> _ComfyUIOption:
    """Build a single expandable parent row for ``len(members) >= 2``.

    The parent's value is a synthetic ``family:<root>`` token (no
    catalog entry would ever have a colon in its name, so this is
    collision-free). leaf_details carries per-member display data so
    the panel can render leaves with full repo names + their own
    badges + their own sizes.
    """
    # Aggregate group from members (they should all share one — same
    # family ⇒ same group typically; if mixed, take the most popular).
    member_groups = [_display_group_for(m.category) for m in members]
    group = max(set(member_groups), key=member_groups.count)

    # Parent badges = just the group (for filter-chip matching). The
    # family root + variant count are in the label, so no need to
    # repeat them here. Per-leaf badges live in leaf_details and the
    # panel renders them on each leaf row when expanded.
    parent_badges = [group.lower()]
    parent_label = f"{family_root}  ·  {len(members)} variants"

    # Build leaf_details: variant_tag (= full repo name) → (label, badges)
    leaf_details: dict[str, tuple[str, tuple[str, ...]]] = {}
    for m in sorted(members, key=lambda x: (-x.popularity, x.name)):
        is_pulled = _entry_is_pulled(m, pulled_names)
        leaf_badges = tuple(_badges_for_entry(m, is_pulled, gpu_mem_gb))
        leaf_details[m.name] = (m.name, leaf_badges)

    # The synthetic parent value MUST collide with no real catalog
    # entry name. A colon is safe — HF names use ``--`` not ``:``,
    # civitai uses ``civitai-NNN`` (no colon), curated/sidecar names
    # are kebab-case (no colon).
    return _ComfyUIOption(
        value=f"family:{family_root}",
        label=parent_label,
        hint=" ".join(parent_badges),
        badges=parent_badges,
        group=group,
        checked=any(m.name in default_selected for m in members),
        sizes=tuple(leaf_details.keys()),
        leaf_details=leaf_details,
    )


def _merged_comfyui_options(
    catalog: list[ComfyUILibraryEntry],
    sidecar: list[ComfyUILibraryEntry],
    pulled_names: set[str],
    default_selected: set[str],
    gpu_mem_gb: float | None = None,
    warn: Callable[[str], None] | None = None,
) -> list[_ComfyUIOption]:
    """Build the wizard's option list, grouping HF entries that share
    a leading-letters family root into expandable parent rows.

    civitai numeric IDs, hand-picked curated entries, and user-
    supplied sidecar entries always stay flat — only HF entries
    participate in family grouping. See ``_family_root`` for the
    heuristic.

    Args:
        catalog: from assemble_wizard_catalog()
        sidecar: from load_custom_models(); always rendered in Custom group
        pulled_names: from _scan_pulled() — filenames present on disk
        default_selected: parsed from COMFYUI_USER_MODELS env
        gpu_mem_gb: from _detect_gpu_memory_gb() — None = CPU-only / unknown
        warn: optional callback for warnings; if None, prints to stderr
    """
    # Sidecar last so sidecar wins on name collision via dict insertion-order semantics.
    by_name: dict[str, ComfyUILibraryEntry] = {
        e.name: e for e in list(catalog) + list(sidecar)
    }

    # Warn on unresolved defaults — same pattern as integration.py for Ollama.
    for n in default_selected:
        if n not in by_name:
            msg = (f"COMFYUI_USER_MODELS entry {n!r} not found in catalog "
                   f"or sidecar; ignoring.")
            if warn is not None:
                warn(msg)
            else:
                print(f"⚠️  {msg}", file=sys.stderr)

    # Group HF entries by family root; everyone else is a singleton.
    families: dict[str, list[ComfyUILibraryEntry]] = {}
    singletons: list[ComfyUILibraryEntry] = []
    for entry in by_name.values():
        root = _family_root(entry.name, entry.source)
        if root:
            families.setdefault(root, []).append(entry)
        else:
            singletons.append(entry)
    # Promote singleton-families back to flat (a family of one isn't
    # a family).
    flat_singleton_families = [
        r for r, members in families.items() if len(members) < 2
    ]
    for root in flat_singleton_families:
        singletons.extend(families.pop(root))

    options: list[_ComfyUIOption] = []
    for entry in singletons:
        options.append(_flat_option(
            entry, pulled_names=pulled_names,
            default_selected=default_selected, gpu_mem_gb=gpu_mem_gb,
        ))
    for root, members in families.items():
        options.append(_family_parent_option(
            root, members, pulled_names=pulled_names,
            default_selected=default_selected, gpu_mem_gb=gpu_mem_gb,
        ))

    # Sort by popularity. For parents, popularity = max member popularity
    # (assigned in _family_parent_option); for flats, the entry's own.
    pop_by_value: dict[str, int] = {}
    for o in options:
        if o.sizes:
            pop_by_value[o.value] = max(
                by_name[m].popularity for m in o.sizes
            )
        else:
            pop_by_value[o.value] = by_name[o.value].popularity
    options.sort(key=lambda o: (-pop_by_value.get(o.value, 0), o.value))
    return options


def _to_prompt_option(opt: _ComfyUIOption) -> PromptOption:
    """Convert an internal _ComfyUIOption to a real PromptOption for the
    wizard's PromptStep. The group is already embedded in opt.badges;
    checked is handled via default_values on the step, not per-option.
    Family parents pass through ``sizes`` and ``leaf_details`` so the
    panel's variant-tree code can expand the row in place.
    """
    return PromptOption(
        value=opt.value,
        label=opt.label,
        hint=opt.hint,
        badges=opt.badges,
        sizes=opt.sizes,
        leaf_details=opt.leaf_details,
    )


def build_comfyui_steps(
    env_vars: Dict[str, str],
    warn: Callable[[str], None] | None = None,
    volume_root: Path | None = None,
) -> List[PromptStep]:
    """Build the ComfyUI model multiselect step.

    Parallel to build_ollama_steps(). Activated right after the ComfyUI
    source step via a skip_if_prev guard: fires only when
    COMFYUI_SOURCE is container-cpu or container-gpu.

    Args:
        env_vars: the current .env snapshot (read at wizard build time)
        warn: optional log sink for diagnostic messages
        volume_root: override the comfyui-models scan path (for tests)
    """
    _warn = warn or (lambda _msg: None)

    # ── Resolve existing user selection ──────────────────────────────
    existing_names: set[str] = {
        s.strip()
        for s in (env_vars.get("COMFYUI_USER_MODELS", "") or "").split(",")
        if s.strip()
    }

    # ── Resolve sidecar path ─────────────────────────────────────────
    sidecar_path = (env_vars.get("COMFYUI_CUSTOM_MODELS_FILE", "") or "").strip()

    # ── options_provider closure ─────────────────────────────────────
    def _comfyui_options_provider(selections: dict) -> list[PromptOption]:
        from utils.comfyui_library import assemble_wizard_catalog, load_custom_models

        # Live catalog scrape with curated/fallback fallback inside.
        catalog = assemble_wizard_catalog()
        if not catalog:
            _warn(
                "[warn/comfyui-fetch] assemble_wizard_catalog() returned no entries "
                "— check network access"
            )
            return [PromptOption(
                value="",
                label="(catalog unreachable — HF/civitai scrape failed)",
                hint="check network access; comfyui-init will use the bundled fallback",
                badges=[],
            )]

        sidecar: list[ComfyUILibraryEntry] = []
        if sidecar_path:
            sidecar = load_custom_models(sidecar_path)

        # Filesystem scan for already-downloaded models (best-effort —
        # see _resolve_models_volume_root for when this yields nothing).
        scan_root = (volume_root if volume_root is not None
                     else _resolve_models_volume_root(env_vars))
        pulled = _scan_pulled(scan_root) if scan_root is not None else set()

        # GPU memory detection.
        gpu_mem = _detect_gpu_memory_gb()

        raw = _merged_comfyui_options(
            catalog=catalog,
            sidecar=sidecar,
            pulled_names=pulled,
            default_selected=existing_names,
            gpu_mem_gb=gpu_mem,
            warn=_warn,
        )
        return [_to_prompt_option(o) for o in raw]

    # Pre-checked values seed from env (parallel to ollama_default_values).
    comfyui_default_values = sorted(existing_names) if existing_names else []

    def _skip_when_disabled(sel: dict) -> bool:
        """Skip this step only when ComfyUI is disabled.

        Mirrors the Ollama picker, which shows for container-cpu /
        container-gpu / localhost / external — all non-disabled
        variants. For localhost / external the picker still writes
        COMFYUI_USER_MODELS to .env so the bootstrapper's
        comfyui_manifest_generator writes the manifest at start (consumed
        by the backend /comfyui/db/models endpoint that Open WebUI + n8n
        call). For those sources comfyui-init (the download container)
        scales to 0 per service_config.py — same shape as ollama-pull
        being scale=0 for non-container ollama sources. The user
        populates their host ComfyUI install's models dir themselves,
        same as running `ollama pull <name>` on the host for Ollama
        localhost.
        """
        # The wizard's selections dict is keyed by STEP TITLE (see
        # WizardScreen.action_confirm), so the live user choice must be
        # consulted FIRST. Reading .env first made the predicate act on
        # the pre-wizard value: a user flipping disabled→container-gpu
        # never saw the picker, and one flipping container→disabled was
        # shown a picker for a service they just turned off.
        src = (
            sel.get("ComfyUI  ·  source")
            or sel.get("COMFYUI_SOURCE")
            or env_vars.get("COMFYUI_SOURCE", "")
            or ""
        )
        # Empty source = treat as disabled (defensive: should not occur
        # in practice since the source step always writes a value, but
        # preserves the pre-fix behavior where the picker skipped on
        # any unrecognized / missing value).
        return not src or src == "disabled"

    return [
        PromptStep(
            title=COMFYUI_MODELS_TITLE,
            step_index=0, step_total=0,
            heading="Which ComfyUI models to download?",
            subtitle=(
                "Models are downloaded by comfyui-init at container start. "
                "[pulled] = file already on disk in the comfyui-models volume. "
                "⚠ badges are warnings only — models are NOT hidden for hardware "
                "mismatches. Filter by category using the chips above; "
                "press `f` to cycle, Tab or `/` for name search. "
                "Space toggles, Enter confirms."
            ),
            options=[],
            default_values=comfyui_default_values,
            service_name="",
            kind="multiselect",
            skip_if_prev=_skip_when_disabled,
            options_provider=_comfyui_options_provider,
            filter_tags=_FILTER_TAGS,
        ),
    ]
