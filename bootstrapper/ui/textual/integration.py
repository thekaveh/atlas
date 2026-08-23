"""
Production bridge between the Textual TUI and ``bootstrapper/start.py``.

Single public entry: ``run_setup_flow(starter, hosts_manager)``.

The whole user-facing flow lives inside ONE Textual ``WizardScreen``:
the wizard prompts run in the lower pane during the setup phase, then
the same pane swaps in log filter chips + a live log surface for the
pipeline (validate / ports / kong / supabase / hosts / encryption /
localhost) and the docker compose build / up / verify / logs.

start.py just calls this and exits when it returns.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App
from textual.binding import Binding


_THEME_PATH = Path(__file__).parent / "theme.css"

# Wizard step title for ComfyUI model multiselect — canonical source of
# truth lives in wizard/comfyui_steps.py; imported here to keep the drain
# loop (selections.get(COMFYUI_MODELS_TITLE)) aligned with what the step
# registers without duplicating the string literal.
from wizard.comfyui_steps import COMFYUI_MODELS_TITLE
from wizard.model.cloud_rules import resolve_cloud_provider
from wizard.model.track_rules import track_force_disabled_sources


# Module-level sink for wizard-time diagnostic warnings (cloud /v1/models
# fetch failures, etc.). The WizardScreen populates this with a thin
# adapter around its ``_safe_log`` once the screen exists; the cloud
# options_provider closures (built BEFORE the screen) read from it.
# ``None`` until the screen wires it; closures must guard against that.
_WIZARD_WARN_SINK = None


def _set_wizard_warn_sink(fn) -> None:
    """Called by WizardScreen.__init__ to register a logger callable
    of shape ``(msg: str) -> None``. Idempotent; reset to None on
    screen teardown.
    """
    global _WIZARD_WARN_SINK
    _WIZARD_WARN_SINK = fn


def _wizard_warn(msg: str) -> None:
    fn = _WIZARD_WARN_SINK
    if fn is None:
        return
    try:
        fn(msg)
    except Exception:
        pass


# ─── helpers ─────────────────────────────────────────────────────────

def _badges_for_option(opt: str, *, recommended: bool = False) -> list[str]:
    s = (opt or "").lower()
    badges: list[str] = []
    if recommended: badges.append("rec.")
    if "container-gpu" in s or s.endswith("-gpu"): badges.append("GPU")
    elif "container-cpu" in s or s.endswith("-cpu"): badges.append("CPU")
    if "localhost" in s: badges.append("local")
    if s == "external": badges.append("external")
    if s == "none": badges.append("cloud-only")
    if s == "disabled": badges.append("disabled")
    return badges


def _option_hint(opt: str) -> str:
    s = (opt or "").lower()
    if "container-gpu" in s or s.endswith("-gpu"):
        return "Run in a container with NVIDIA GPU acceleration"
    if "container-cpu" in s or s.endswith("-cpu"):
        return "Run in a container on CPU only"
    if s == "container":
        return "Run as a Docker container alongside the rest of the stack"
    if "localhost" in s:
        return "Use an instance you already run on this host"
    if "external" in s:
        return "Connect to an instance running elsewhere"
    if s == "disabled":
        return "Skip this service entirely"
    return ""


# Stable title for the new track-picker step. Inserted at index 0 by
# _build_steps_and_rows WHEN the tracks.yml registry loads successfully;
# when the registry is unloadable the picker is suppressed entirely.
# Used as the selections-dict key by every downstream skip predicate.
# NOTE: this string is a selections-dict KEY, not just display text —
# _make_track_skip, _selections_to_args and the prefill paths all look it
# up. It deliberately no longer says "profile": Atlas configures tracks
# (tracks.yml — which workload, hence which services get prompted for)
# and profiles (profiles.yml — dev vs prod hardening) separately, and
# having the track step ask "Which profile fits what you're building?"
# one step before the real profile step made the latter read as missing.
PICKER_STEP_TITLE = "Track  ·  pick your workload"

# Stable title for the deployment-profile picker step. Added right after
# the track-picker step (or at index 0 when the track registry failed).
# Used as the selections-dict key by the profile-based option filter below
# and by the TUI pipeline to set starter.profile before env overrides run.
PROFILE_STEP_TITLE = "Profile  ·  deployment hardening"


def meta_flags_for(selections: dict) -> list[tuple[str, str]]:
    """``--track`` / ``--profile`` flags implied by ``selections``.

    Both are real ``./start.sh`` flags, and neither used to reach the
    command summary: the track and profile steps carry no
    ``service_name``, so they fell through to the meta-step branch and
    matched none of its cold/hosts cases. A summary that advertises
    itself as copy-pasteable was therefore dropping the two flags that
    decide which services run and how they are hardened.

    Defaults are omitted so the summary keeps mirroring a bare
    ``./start.sh`` rather than accreting no-op flags.
    """
    flags: list[tuple[str, str]] = []
    track = (selections.get(PICKER_STEP_TITLE) or "").strip()
    if track:
        flags.append(("--track", track))
    profile = (selections.get(PROFILE_STEP_TITLE) or "").strip()
    if profile and profile != "default":
        flags.append(("--profile", profile))
    return flags


def _resolve_auto_base_port(fallback: int) -> int:
    """Resolve a wizard ``auto`` base-port answer to a concrete free block.

    Delegates to the same ``PortManager.auto_base_port`` the ``--base-port
    auto`` CLI flag uses, so both surfaces pick blocks by identical rules
    (steps by the topology's full port span, scans below the ephemeral
    range, never returns ``DEFAULT_BASE_PORT``).

    Returns ``fallback`` when no free block is found or the probe fails:
    a base port the user cannot see is not worth aborting a launch over,
    and the pipeline's own port checks still run afterwards.
    """
    try:
        from core.port_manager import PortManager

        resolved = PortManager().auto_base_port()
    except Exception:  # noqa: BLE001 - probing is best-effort
        return fallback
    return resolved if resolved else fallback


def _resolve_track_display_name(track: str | None) -> str | None:
    """Look up a track's display_name from the registry; None if no
    track set or lookup fails. Used by both run_setup_flow and
    run_launch_flow to populate the InfoPanel banner."""
    if not track:
        return None
    try:
        from tracks import load_tracks as _lt
        _reg = _lt()
        _t = _reg.by_key.get(track)
        return _t.display_name if _t else None
    except Exception:  # noqa: BLE001
        return None


def _make_track_skip(
    service_key: str,
    *,
    always_on: frozenset[str],
    overridden: frozenset[str],
    registry,
):
    """Build a ``skip_if_prev`` callable for a per-service PromptStep.

    Returns True (skip) when:
        service is NOT in always_on,
        AND the picker-selected track exists and EXCLUDES the service
            (i.e. track.services is a finite set and doesn't list it),
        AND service is NOT in the override set.

    Fail-open semantics: if no picker selection has been made yet, the
    selection doesn't resolve to a known track, or `registry` is None,
    return False. A buggy predicate must never eat user prompts.

    Note: ``overridden`` keys use the folder/normalized form produced by
    ``start.py`` (e.g. ``ray``, ``stt-provider``) — not wizard svc.keys.
    We normalize ``service_key`` here before the check so both forms agree.
    """
    from tracks import is_in_track, normalize_service_key as _norm

    def _skip(selections: dict) -> bool:
        if registry is None:
            return False
        track_key = selections.get(PICKER_STEP_TITLE)
        if not track_key:
            return False
        track = registry.by_key.get(track_key)
        if track is None:
            return False
        if _norm(service_key) in overridden:
            return False
        return not is_in_track(track, service_key, always_on=always_on)

    return _skip


def recompute_ports_for_base(
    new_base: int,
    current_rows,
    config_parser,
    port_offsets: dict,
):
    """Re-derive every ServiceRow's port + alias_port from ``new_base``.

    Single implementation shared by the wizard (``run_setup_flow``) and
    the CLI-driven launch path (``run_launch_flow``). Both call this
    whenever the user re-enters the base-port step or when the launch
    flow recomputes ports after an override.

    Builds a synthetic env dict where each container port_var is
    re-derived from ``new_base``; localhost endpoint vars come straight
    from the live .env so localhost sources still resolve to the host
    machine's port.
    """
    from wizard.model.state_builder import lookup_service_meta, resolve_port as _resolve_port
    from .widgets.service_table import ServiceRow as _SR

    live_env = config_parser.parse_env_file()
    synth_env = dict(live_env)
    for pv, off in port_offsets.items():
        synth_env[pv] = str(new_base + off)
    new_kong = str(new_base + port_offsets.get("KONG_HTTP_PORT", 0))
    new_rows = []
    for r in current_rows:
        meta = lookup_service_meta(r.name)
        port_var = (meta or {}).get("port_var") if meta else None
        new_port = _resolve_port(r.name, r.source, port_var, synth_env) or ""
        new_rows.append(_SR(
            name=r.name, source=r.source, alias=r.alias,
            port=new_port,
            alias_port=(new_kong if r.alias else ""),
            default_source=r.default_source,
            configurable=r.configurable,
            category=r.category,
            pending=r.pending,
            off_track=r.off_track,
            # Preserve the hover-card metadata — a base-port change must not
            # strip a row's S3 endpoints / source options / dependencies.
            tooltip_extra=r.tooltip_extra,
            source_options=r.source_options,
            depends_on=r.depends_on,
        ))
    # Preserve the canonical input order — `current_rows` arrives in
    # category/topology order from `_build_steps_and_rows`, and changing
    # the base port only re-derives port values, not row positions.
    return new_rows


def _build_steps_and_rows(
    config_parser,
    hosts_manager,
    *,
    track_key: str | None = None,
    overridden_services: frozenset[str] | None = None,
    profile: str | None = None,
):
    """Build the wizard steps + service rows from real config."""
    from wizard.model.service_discovery import ServiceDiscovery
    from wizard.model.state_builder import build_app_state
    from core.config_parser import DEFAULT_BASE_PORT, DEFAULT_PROJECT_NAME
    from .widgets.prompt_panel import PromptOption, PromptStep
    from .widgets.service_table import ServiceRow
    from services.manifests import load_manifests as _load_manifests, option_in_profile as _option_in_profile
    from pathlib import Path as _Path
    try:
        _manifests = _load_manifests(_Path(config_parser.root_dir) / "services")
    except Exception:  # noqa: BLE001
        _manifests = []
    # Build SOURCE-var → manifest-name mapping from the manifests.
    # Used by the profile-based option filter so option_in_profile receives
    # the real manifest name (e.g. "ollama") rather than the svc.key from
    # ServiceDiscovery (e.g. "llm_provider"), which doesn't match manifest.name.
    _src_var_to_mname: dict[str, str] = {
        mf.sources.var: mf.name
        for mf in _manifests
        if mf.sources is not None
    }

    services_info = ServiceDiscovery(config_parser).discover()
    env_vars = config_parser.parse_env_file()
    # ``dict.get(key, default)`` returns "" when the key is present-
    # but-blank, not the default. Guard against that.
    _raw = (env_vars.get("BASE_PORT") or "").strip()
    try:
        current_base_port = int(_raw) if _raw else DEFAULT_BASE_PORT
    except ValueError:
        current_base_port = DEFAULT_BASE_PORT

    # Current Docker Compose project name (container-family namespace). The
    # wizard's project-name step pre-fills with this so an existing PROJECT_NAME
    # (e.g. a submodule consumer's) isn't reset to the default on a bare Enter.
    current_project_name = (env_vars.get("PROJECT_NAME") or "").strip() or DEFAULT_PROJECT_NAME

    # Build canonical order index once — shared by both sorts below.
    from services.topology import get_topology
    _topology = get_topology()
    _canonical_index: dict[str, int] = {
        r.display_name: idx for idx, r in enumerate(_topology.rows)
    }

    def _svc_canonical_key(svc) -> tuple:
        return (_canonical_index.get(svc.display_name, 999), svc.display_name)

    services_info = sorted(services_info, key=_svc_canonical_key)

    steps: list = []
    # Inline step_total values below are illustrative only — Ollama and
    # cloud sub-steps are spliced in dynamically below, so the count
    # isn't knowable up-front. The screen recomputes ``step_total`` at
    # display time from the final ``len(self._steps)`` (see
    # WizardScreen._render_step / dataclasses.replace), and we
    # rewrite each step's ``step_total`` once the list is complete
    # below — so the inline values seeded here never reach the UI.
    total = 0  # placeholder; rewritten below after the dynamic splice

    # Load track registry once; reused for the picker step + per-service
    # skip predicates.
    from tracks import load_tracks
    try:
        _track_registry = load_tracks()
    except Exception:  # noqa: BLE001
        _track_registry = None
        _wizard_warn("tracks.yml failed to load; track-picker disabled.")

    # Prefer the registry-cached value; fall back to a hardcoded set so
    # the per-service loop below can still attach predicates if the
    # registry failed to load (predicates short-circuit to False with
    # registry=None anyway, so the always_on value is moot there).
    if _track_registry is not None:
        _always_on = _track_registry.always_on
    else:
        _always_on = frozenset({"llm-provider", "prometheus", "grafana"})
    _overridden = overridden_services or frozenset()

    # Picker step (only shown if the registry loaded). When --track was
    # passed via the CLI (track_key != None), we still add the picker
    # but it auto-skips because skip_if_prev returns True — the
    # selection is already pinned via prefilled_selections (added in
    # Task 11) so the per-service predicates can read it.
    if _track_registry is not None:
        picker_options = []
        for t in _track_registry.tracks:
            if t.services is None:
                svc_hint = "every configurable service"
            else:
                svc_hint = " + ".join(sorted(t.services))
            # Fold the per-track description into the hint so the user
            # sees BOTH the one-liner intent AND the service list per
            # option. PromptOption has no separate `description` field —
            # hint is the only per-option slot beneath the label.
            # description is non-empty per the JSON schema (minLength: 1),
            # so the else branch is belt-and-suspenders. Kept defensive
            # in case a future schema relaxation makes description optional.
            if t.description:
                hint_text = f"{t.description}  ({svc_hint})"
            else:
                hint_text = svc_hint
            picker_options.append(PromptOption(
                value=t.key,
                label=t.display_name,
                hint=hint_text,
                badges=[],
            ))
        # Default highlight: the CLI-passed track if present and valid,
        # else the first entry.
        if track_key and track_key in _track_registry.by_key:
            picker_default = track_key
        else:
            picker_default = _track_registry.tracks[0].key
        steps.append(PromptStep(
            title=PICKER_STEP_TITLE,
            step_index=1, step_total=total,
            heading="Which workload are you building?",
            subtitle=(
                "Always-on for every track: LLM Engine + Prometheus + "
                "Grafana + cloud-provider keys."
            ),
            options=picker_options,
            default_value=picker_default,
            service_name="",
            # Skip the picker entirely when --track was set on the CLI —
            # the selection is already pinned and the user shouldn't
            # have to re-confirm.
            skip_if_prev=(
                (lambda sel, _tk=track_key: bool(_tk))
                if track_key else None
            ),
        ))

    # Profile picker: dev (default) vs production hardening.
    # Placed right after the track picker (or at the top when the registry
    # failed). Auto-skips when --profile was passed on the CLI — prefilled
    # into selections so the per-service option filter below can read it.
    steps.append(PromptStep(
        title=PROFILE_STEP_TITLE,
        step_index=2, step_total=total,
        heading="Dev or production hardening?",
        subtitle=(
            "prod: localhost-only ports, resource limits, log rotation, "
            "observability on — and localhost sources hidden."
        ),
        options=[
            PromptOption(
                value="default",
                label="Default (dev)",
                hint="0.0.0.0 ports; all sources available",
                badges=[],
            ),
            PromptOption(
                value="prod",
                label="Production hardening",
                hint="127.0.0.1 ports; localhost sources hidden",
                badges=[],
            ),
        ],
        default_value=(profile or "default"),
        service_name="",
        skip_if_prev=(
            (lambda sel, _p=profile: bool(_p)) if profile else None
        ),
    ))

    # Base port is asked FIRST so all subsequent service-port displays
    # reflect the chosen base port immediately.
    steps.append(PromptStep(
        title="Base port  ·  range", step_index=1, step_total=total,
        heading="Which base port range do you want?",
        subtitle="Every service port is computed as base_port + offset. "
                 "Type a port (1024–65000), type auto to pick a free block, "
                 "or press Enter to keep the current value.",
        options=[],
        default_value=str(current_base_port),
        service_name="",
        kind="number",
        number_min=1024,
        number_max=65000,
        # "auto" is a real base-port value on the CLI (--base-port auto)
        # and in consumer manifests (BASE_PORT: auto); the wizard was the
        # only surface that couldn't express it.
        accepts_auto=True,
    ))

    # Project name (Docker Compose namespace). Persisted to .env as
    # PROJECT_NAME, so start AND stop target this container family. A submodule
    # consumer sets a unique name here to avoid colliding with a base Atlas
    # stack; pressing Enter keeps the current value.
    steps.append(PromptStep(
        title="Project name  ·  namespace", step_index=2, step_total=total,
        heading="What should the project (container family) be named?",
        subtitle="Every container, volume, and the network are prefixed "
                 "<name>-… (lowercase letters/digits/-/_). Persisted to .env as "
                 "PROJECT_NAME so start and stop agree. Press Enter to keep the "
                 "current value.",
        options=[],
        default_value=current_project_name,
        service_name="",
        kind="text",
    ))

    # LLM cluster steps (Ollama variants + cloud secret/multiselect
    # pairs) live in wizard/llm_steps.py; spliced in below right after
    # the LLM Engine source step so the LLM section reads coherently.
    from wizard.llm_steps import build_ollama_steps, build_cloud_steps, build_default_model_steps
    # Ray follow-up steps (worker count + external address) live in
    # wizard/ray_steps.py; spliced in right after the Ray source step.
    from wizard.ray_steps import build_ray_followup_steps
    # ComfyUI model multiselect lives in wizard/comfyui_steps.py;
    # spliced in right after the ComfyUI source step.
    from wizard.comfyui_steps import build_comfyui_steps
    from .widgets.prompt_panel import SecondaryNumberInput

    # Per-service localhost-port wiring. Each entry maps a service's
    # source-step display name + the option value(s) eligible for the
    # inline-port widget → the matching env var name + the well-known
    # default. PromptOption.secondary_number is attached for any option
    # whose (service, value) appears here. Generic by construction —
    # the widget doesn't know about ports; this table is the only
    # place that does. Adding a new localhost-capable service is one
    # row here + a manifest entry per Task 7.

    from wizard.model.state_builder import LOCALHOST_PORT_WIRING

    def _localhost_port_config(display: str, opt_value: str) -> "SecondaryNumberInput | None":
        """Build the per-option SecondaryNumberInput for a localhost row,
        or None if this (service, option) isn't in the wiring table."""
        wiring = LOCALHOST_PORT_WIRING.get((display, opt_value))
        if wiring is None:
            return None
        env_var, default_port = wiring
        raw = (env_vars.get(env_var) or str(default_port)).strip()
        try:
            current = int(raw) if raw else int(default_port)
        except ValueError:
            current = int(default_port)
        current = max(1024, min(65535, current))
        return SecondaryNumberInput(
            env_var=env_var,
            description=f"Host port for {display.lower()} in localhost mode (1024-65535).",
            default_value=current,
            number_min=1024,
            number_max=65535,
            unit_suffix="port",
        )

    for i, svc in enumerate(services_info):
        # FAL (#517): a paid cloud media provider prompted with a masked
        # API-token step (spliced right after ComfyUI below), NOT a plain
        # enabled/disabled source tile — entering a key enables it, blank keeps
        # it disabled. Skip its normal source step here. FAL stays discovered
        # (so it keeps its grid row + off-track force-disable), and its apply
        # logic lives in _selections_to_args alongside the cloud secrets.
        if svc.key == "fal":
            continue
        # Per-option secondary_number: attach the inline integer input to
        # the specific option rows where it makes sense.
        # • Ray: worker count on the container-cpu / container-gpu rows.
        # • Localhost-port overrides: attached per-localhost-row in
        #   Task 10. None of the localhost-attachments live here today.
        ray_secondary: SecondaryNumberInput | None = None
        if svc.key in ("ray", "ray-head") or svc.display_name == "Ray":
            raw_default = (env_vars.get("RAY_WORKER_COUNT") or "2").strip()
            try:
                worker_default = max(0, min(64, int(raw_default)))
            except ValueError:
                worker_default = 2
            ray_secondary = SecondaryNumberInput(
                env_var="RAY_WORKER_COUNT",
                description=(
                    "Ray worker replicas alongside the head node "
                    "(0 = head-only single-node cluster). 0-64."
                ),
                default_value=worker_default,
                number_min=0,
                number_max=64,
                unit_suffix="workers",
            )
        # Spark mirrors Ray's worker-count widget. Spark's runtime_sc has
        # four containers (master + worker + history + connect); spark-init
        # is filtered out the same way Ray's ray-worker is. ServiceDiscovery
        # anchors on `spark-master` via the source_mapping shim, so the
        # svc.key check accepts both forms defensively.
        spark_secondary: SecondaryNumberInput | None = None
        if svc.key in ("spark", "spark-master") or svc.display_name == "Apache Spark":
            raw_default = (env_vars.get("SPARK_WORKER_COUNT") or "2").strip()
            try:
                spark_worker_default = max(1, min(8, int(raw_default)))
            except ValueError:
                spark_worker_default = 2
            spark_secondary = SecondaryNumberInput(
                env_var="SPARK_WORKER_COUNT",
                description=(
                    "Number of spark-worker replicas alongside the master. 1-8."
                ),
                default_value=spark_worker_default,
                number_min=1,
                number_max=8,
                unit_suffix="workers",
            )
        # Filter options by deployment profile. CLI --profile is known at
        # construction time; an in-wizard profile selection is known later, so
        # service-source steps also carry an options_provider that rebuilds
        # rows from the current selections when the step is loaded.
        _mname = _src_var_to_mname.get(getattr(svc, "env_var_name", ""), svc.key)

        def _visible_source_options(selections: dict, _svc=svc, _mname=_mname,
                                    _ray_secondary=ray_secondary,
                                    _spark_secondary=spark_secondary) -> list[PromptOption]:
            selected_profile = profile or selections.get(PROFILE_STEP_TITLE) or "default"
            visible = [
                o for o in _svc.options
                if _option_in_profile(_manifests, _mname, o, selected_profile)
            ]
            if not visible:
                visible = list(_svc.options)
            return [
                PromptOption(
                    value=opt,
                    label=_svc.option_labels.get(opt, opt),
                    hint=_option_hint(opt),
                    badges=_badges_for_option(opt, recommended=(opt == _svc.current_value)),
                    secondary_number=(
                        _ray_secondary
                        if _ray_secondary is not None
                           and opt in ("ray-container-cpu", "ray-container-gpu")
                        else _spark_secondary
                        if _spark_secondary is not None and opt == "container"
                        else _localhost_port_config(_svc.display_name, opt)
                    ),
                )
                for opt in visible
            ]

        opts = _visible_source_options({})
        visible_opts = [opt.value for opt in opts]
        # If the current .env value was filtered out, fall back to the
        # manifest default (svc.options[0]) if available in visible_opts,
        # else the first visible option.
        _raw_current = svc.current_value
        if _raw_current in visible_opts:
            default = _raw_current
        else:
            # svc.options[0] is the manifest default for this service.
            _manifest_default = svc.options[0] if svc.options else None
            if _manifest_default in visible_opts:
                default = _manifest_default
            else:
                default = visible_opts[0] if visible_opts else None
        steps.append(PromptStep(
            title=f"{svc.display_name}  ·  source",
            step_index=i + 2, step_total=total,
            heading=f"How should {svc.display_name} run?",
            subtitle=svc.description or "",
            options=opts, default_value=default, service_name=svc.display_name,
            options_provider=_visible_source_options,
            service_key=svc.key,
            # secondary_number REMOVED from PromptStep — config is now
            # on individual PromptOption entries above.
            skip_if_prev=(
                _make_track_skip(
                    svc.key,
                    always_on=_always_on,
                    overridden=_overridden,
                    registry=_track_registry,
                )
                if _track_registry is not None else None
            ),
        ))
        # Splice the entire LLM cluster RIGHT AFTER the LLM Engine
        # source step: Ollama variants, then cloud-provider key+model
        # pairs. Keeps engine + local + cloud adjacent in the wizard
        # flow instead of separating them with unrelated service-source
        # steps. Each spliced sub-step has its own skip_if_prev gating.
        if svc.display_name == "LLM Engine":
            steps.extend(build_ollama_steps(env_vars, _wizard_warn))
            steps.extend(build_cloud_steps(env_vars, _wizard_warn))
            steps.extend(build_default_model_steps(env_vars, _wizard_warn))
        # Splice Ray follow-up prompts RIGHT AFTER the Ray source step.
        # Each sub-step carries its own skip_if_prev predicate so only
        # the appropriate prompt fires for the chosen source.
        # Ray's runtime_sc has two containers (`ray-head` + `ray-worker`),
        # so ServiceDiscovery assigns the family key `ray-head` to the
        # discovery anchor. Match both forms defensively.
        if svc.key in ("ray", "ray-head") or svc.display_name == "Ray":
            steps.extend(build_ray_followup_steps(env_vars, {
                "RAY_SOURCE": svc.current_value or "disabled",
            }))
        # Splice ComfyUI model multiselect RIGHT AFTER the ComfyUI source
        # step. The step's skip_if_prev guard fires when COMFYUI_SOURCE is
        # not container-cpu / container-gpu (localhost, external, disabled),
        # so only container users see the model picker.
        # Each sub-step's skip_if_prev is combined (OR) with the track-skip
        # predicate so off-track ComfyUI doesn't surface via .env fallback
        # (regression guard for audit finding R1).
        if svc.display_name == "ComfyUI":
            _comfyui_substeps = build_comfyui_steps(env_vars, _wizard_warn)
            if _track_registry is not None:
                from dataclasses import replace as _dc_replace
                _track_skip_comfyui = _make_track_skip(
                    svc.key,
                    always_on=_always_on,
                    overridden=_overridden,
                    registry=_track_registry,
                )
                for _sub in _comfyui_substeps:
                    _sub_skip = getattr(_sub, "skip_if_prev", None)
                    if _sub_skip is None:
                        _combined = _track_skip_comfyui
                    else:
                        def _combined(_sel, _a=_sub_skip, _b=_track_skip_comfyui):
                            try:
                                return bool(_a(_sel)) or bool(_b(_sel))
                            except Exception:  # noqa: BLE001
                                return False
                    steps.append(_dc_replace(_sub, skip_if_prev=_combined))
            else:
                steps.extend(_comfyui_substeps)
            # Splice the FAL Cloud Media API-token (secret) step RIGHT AFTER the
            # ComfyUI step (#517) — same `media` category, adjacent in the flow.
            # FAL's plain source step was skipped above; here it becomes a masked
            # token step (key → enabled, blank → disabled). Track-skip wrapped for
            # `fal` so it only shows in tracks where FAL is in scope.
            from wizard.llm_steps import build_fal_secret_step
            _fal_steps = build_fal_secret_step(env_vars, _wizard_warn)
            if _track_registry is not None:
                from dataclasses import replace as _dc_replace_fal
                _track_skip_fal = _make_track_skip(
                    "fal",
                    always_on=_always_on,
                    overridden=_overridden,
                    registry=_track_registry,
                )
                steps.extend(
                    _dc_replace_fal(_fs, skip_if_prev=_track_skip_fal)
                    for _fs in _fal_steps
                )
            else:
                steps.extend(_fal_steps)

    steps.append(PromptStep(
        title="Cold start  ·  rebuild", step_index=len(services_info) + 2,
        step_total=total,
        heading="Reset the existing stack state?",
        subtitle=("Cold start removes all containers & volumes, then re-creates "
                  "them — regenerating the volume-baked DB passwords "
                  "(Supabase + Neo4j) so they match the freshly initialized data."),
        options=[
            PromptOption("no", "No — keep existing data",
                         "fast — reuses volumes, current containers & DB passwords"),
            PromptOption("yes", "Yes — rebuild from scratch",
                         "slow — drops volumes; cleanest reset; new DB passwords"),
        ],
        default_value="no", service_name="",
    ))
    steps.append(PromptStep(
        title="Hosts setup  ·  /etc/hosts", step_index=len(services_info) + 3,
        step_total=total,
        heading="Configure the *.localhost host entries?",
        subtitle="Required for Kong's wildcard routing.",
        options=[
            PromptOption("default", "Default — warn if missing", "no sudo needed"),
            PromptOption("setup", "Setup now (requires sudo)",
                         "writes /etc/hosts entries for *.localhost services"),
            PromptOption("skip", "Skip — I'll handle it manually",
                         "no warnings, no changes"),
        ],
        default_value="default", service_name="",
    ))
    steps.append(PromptStep(
        title="Confirm  ·  launch the stack", step_index=0, step_total=0,
        heading="Launch the stack with this configuration?",
        subtitle="Last chance to back out — Yes will start docker compose.",
        options=[
            PromptOption("yes", "Yes — launch now", "starts docker compose"),
            PromptOption("no", "No — exit without starting",
                         "discards wizard selections; nothing written or launched"),
        ],
        default_value="yes", service_name="",
    ))

    # Second pass: now that every dynamically-spliced step is in place,
    # stamp the final 1-based ``step_index`` and total ``step_total``
    # so callers that read the seed values (e.g. one-off renders that
    # bypass WizardScreen._render_step) see correct numbers.
    total = len(steps)
    from dataclasses import replace as _dc_replace
    steps = [
        _dc_replace(s, step_index=i + 1, step_total=total)
        for i, s in enumerate(steps)
    ]

    # Build the off-track display-name set for visual dimming in the
    # right-pane service table.  Only populated when a --track was supplied
    # via the CLI (track_key is not None) AND the registry loaded.
    # Override-enabled services are excluded from off-track marking because
    # the user explicitly re-enabled them.
    _off_track_display_names: frozenset[str] = frozenset()
    if _track_registry is not None and track_key:
        _track_obj = _track_registry.by_key.get(track_key)
        if _track_obj is not None and _track_obj.services is not None:
            from tracks import is_in_track as _iit, normalize_service_key as _norm
            _off_track_display_names = frozenset(
                svc.display_name for svc in services_info
                if (not _iit(_track_obj, svc.key, always_on=_always_on))
                and _norm(svc.key) not in _overridden
            )

    def _in_track_display(display_name: str) -> bool:
        return display_name not in _off_track_display_names

    state = build_app_state(
        config_parser, hosts_manager,
        in_track=(
            _in_track_display
            if (track_key and _track_registry is not None)
            else None
        ),
    )
    # Build the parallel CloudApiSummary list — same data the overview
    # box renders, derived from .env via state_builder.all_cloud_apis().
    from .widgets.info_box import CloudApiSummary as _CloudApiSummary
    cloud_summaries = [
        _CloudApiSummary(name=ca.name, enabled=ca.enabled, key_set=ca.key_set)
        for ca in state.cloud_apis
    ]

    # Kong's listener port — every Kong-routed alias URL uses this port
    # (virtual-host routing on a single listener), not the upstream
    # service's own port. Mirror state_builder.build_app_state()'s
    # `"63000"` fallback so the wizard's first paint matches the linear
    # state-builder path when KONG_HTTP_PORT is blank in .env.
    kong_port = (env_vars.get("KONG_HTTP_PORT", "") or "63000").strip()
    # Set of display names whose source the user CAN configure. Used
    # to drive the lock-icon column in the overview — services not in
    # this set are always-on infrastructure.
    configurable_names = {svc.display_name for svc in services_info}

    def _svc_row_canonical_key(svc) -> tuple:
        """Sort by canonical topology order. Services not in the
        topology (infra-only rows) sort to the bottom."""
        return (_canonical_index.get(svc.name, 999), svc.name)

    sorted_services = sorted(state.services, key=_svc_row_canonical_key)

    # MinIO's wizard row is the admin console; surface its S3 API endpoints
    # (direct port + the s3.minio.localhost Kong alias) in the hover tooltip
    # so external s3 clients can discover them from the services pane.
    _minio_port = (env_vars.get("MINIO_PORT", "") or "63020").strip()

    from wizard.model.state_builder import service_extras

    def _tooltip_extra_for(svc) -> list[tuple[str, str]]:
        if svc.name == "MinIO Console":
            return [
                ("S3 API", f"http://localhost:{_minio_port}"),
                ("S3 (Kong)", f"http://s3.minio.localhost:{kong_port}"),
            ]
        return []

    rows = [
        ServiceRow(
            name=s.name, source=(s.source or "container"),
            alias=(s.alias or ""), port=(s.port or ""),
            alias_port=(kong_port if (s.alias or "") else ""),
            default_source=(s.source or "container"),
            configurable=(s.name in configurable_names),
            category=s.category,
            pending=(s.name in configurable_names),  # locked rows start not-pending
            off_track=s.off_track,
            tooltip_extra=_tooltip_extra_for(s),
            source_options=service_extras(s.name)["options"],
            depends_on=service_extras(s.name)["depends"],
        )
        for s in sorted_services
    ]
    return steps, rows, services_info, current_base_port, state, cloud_summaries


def _selections_to_args(
    selections: dict,
    services_info,
    current_base_port: int,
    env_vars: dict | None = None,
):
    """Map wizard selections back to (source_args, stack_options).

    ``env_vars`` is the resolved .env snapshot at wizard-build time;
    used to auto-promote SECRET_KEEP+disabled+key-already-set into
    ``--cloud-X-source enabled`` so the multiselect picks aren't inert.
    """
    from .widgets.prompt_panel import SECRET_KEEP, SECRET_CLEAR
    from utils.cloud_providers import CLOUD_PROVIDERS
    from wizard.llm_steps import (
        OLLAMA_CUSTOM_TITLE,
        OLLAMA_MODELS_TITLE,
        LLM_DEFAULT_CONTENT_TITLE,
        LLM_DEFAULT_EMBED_TITLE,
        LLM_DEFAULT_VISION_TITLE,
        cloud_models_title,
        cloud_secret_title,
        fal_secret_title,
    )
    env_vars = env_vars or {}

    source_args: dict = {}
    for svc in services_info:
        v = selections.get(f"{svc.display_name}  ·  source")
        if v is None: continue
        source_args[svc.key.replace("-", "_") + "_source"] = v

    # ─── Force-disable off-track services ────────────────────────────
    # Rule lives in wizard/model/track_rules.py (#535 Pass 1); see there
    # for why an 'all' track disables nothing and why a registry load
    # failure degrades silently.
    source_args.update(
        track_force_disabled_sources(
            track_key=selections.get(PICKER_STEP_TITLE),
            services_info=services_info,
            already_set=source_args,
        )
    )

    # ─── Cloud-provider selections ───────────────────────────────────
    # Each provider has up to two wizard outputs:
    #   • Secret step  → API key (or SECRET_KEEP / SECRET_CLEAR sentinel)
    #   • Multiselect  → comma-separated active model names
    #
    # Enable/disable + key resolution is delegated to
    # wizard/model/cloud_rules.resolve_cloud_provider (#535 Pass 1) —
    # see there for why ``existing_source`` is threaded through
    # explicitly and why ``source is None`` must be left unwritten
    # rather than coerced to "disabled". The models-CSV write stays
    # here: it persists the raw multiselect string verbatim (not a
    # re-parsed/rejoined form of the resolution's echoed ``.models``),
    # which is env-var-name bookkeeping, not a promotion rule.
    cloud_api_keys: dict = {}
    cloud_user_models: dict = {}
    for provider in CLOUD_PROVIDERS:
        name, source_var, api_key_var = provider.name, provider.source_var, provider.api_key_var
        cli_arg = source_var.lower()       # CLOUD_OPENAI_SOURCE → cloud_openai_source
        # Use the manifest-declared var, NOT a display-name derivation — a
        # multi-word provider name ("Open Router") would yield a broken
        # "OPEN ROUTER_USER_MODELS" (cloud_providers.py warns against this).
        models_var = provider.user_models_var

        secret_v = selections.get(cloud_secret_title(name))
        existing_source = (env_vars.get(source_var, 'disabled') or '').strip().lower()
        existing_key = (env_vars.get(api_key_var, '') or '').strip()

        # Multiselect intent (renders only when the provider is enabled
        # — otherwise skip_if_prev hides the step). None/SECRET_KEEP
        # means the step produced no real answer this session (never
        # visited, or a degraded fetch) — resolve_cloud_provider treats
        # that as a third "no verdict" state via selected_models=None,
        # so a "never visited" step can't be conflated with the user
        # genuinely unchecking every model (selected_models=[]).
        models_v = selections.get(cloud_models_title(name))
        models_visited = models_v not in (None, SECRET_KEEP)
        selected_models = (
            [m for m in models_v.split(",") if m.strip()] if models_visited else None
        )

        resolution = resolve_cloud_provider(
            provider_key=provider.key,
            secret_value=secret_v,
            selected_models=selected_models,
            existing_key_set=bool(existing_key),
            existing_source=existing_source,
        )
        if resolution.source is not None:
            source_args[cli_arg] = resolution.source
        if resolution.api_key is not None:
            cloud_api_keys[api_key_var] = resolution.api_key

        # Models-CSV bookkeeping — mirrors the original's two write
        # sites in order: a SECRET_CLEAR/"" secret blanks it
        # unconditionally, then a visited multiselect (if any)
        # overwrites with the raw CSV — so a visited-but-empty commit
        # still lands as "" and a visited-non-empty commit wins over an
        # unrelated clear.
        if secret_v in (SECRET_CLEAR, ""):
            cloud_user_models[models_var] = ""
        if models_visited:
            cloud_user_models[models_var] = models_v

    # ─── FAL media-provider secret (#517) ────────────────────────────
    # FAL's plain enabled/disabled source step is replaced by a masked
    # API-token step (spliced after ComfyUI), so its source is derived from
    # the key here rather than the generic source loop above. Mirrors the
    # cloud secret semantics exactly:
    #   None              → step never visited (off-track) → leave .env as-is.
    #   SECRET_KEEP       → Enter past a saved key; auto-promote to enabled
    #                       only when a key exists but the source was disabled.
    #   SECRET_CLEAR / "" → disable + wipe the key (no fal_source=enabled with
    #                       a blank key — the runtime footgun this ticket fixes).
    #   real key string   → enable + persist the key.
    # FAL_API_KEY rides the cloud_api_keys bag (a generic var→value .env
    # writer), so no new plumbing is needed.
    fal_secret_v = selections.get(fal_secret_title())
    if fal_secret_v is None:
        pass
    elif fal_secret_v == SECRET_KEEP:
        existing_source = (env_vars.get("FAL_SOURCE", "disabled") or "").strip().lower()
        existing_key = (env_vars.get("FAL_API_KEY", "") or "").strip()
        if existing_source != "enabled" and existing_key:
            source_args["fal_source"] = "enabled"
    elif fal_secret_v == SECRET_CLEAR or fal_secret_v == "":
        source_args["fal_source"] = "disabled"
        cloud_api_keys["FAL_API_KEY"] = ""
    else:
        source_args["fal_source"] = "enabled"
        cloud_api_keys["FAL_API_KEY"] = fal_secret_v

    # Single unified Ollama models step (replaces the previous
    # pulled+library split). Container modes show library only;
    # localhost/external show a merged [pulled]/[library] list. The
    # CSV is already the user's final selection — no union needed.
    ollama_user_models: dict = {}
    models_v = selections.get(OLLAMA_MODELS_TITLE)
    if models_v is not None and models_v != SECRET_KEEP:
        names = sorted({n.strip() for n in models_v.split(",") if n.strip()})
        ollama_user_models["OLLAMA_USER_MODELS"] = ",".join(names)
    # Free-text custom models — honor SECRET_KEEP/SECRET_CLEAR
    # sentinels so an empty Enter on a re-run doesn't silently wipe
    # an existing OLLAMA_CUSTOM_MODELS value.
    custom = selections.get(OLLAMA_CUSTOM_TITLE)
    if custom is not None and custom != SECRET_KEEP:
        if custom == SECRET_CLEAR:
            ollama_user_models["OLLAMA_CUSTOM_MODELS"] = ""
        else:
            ollama_user_models["OLLAMA_CUSTOM_MODELS"] = custom

    # ComfyUI model multiselect — parallel to the Ollama models block
    # above. The wizard step (declared in T15) writes a set under
    # COMFYUI_MODELS_TITLE; we convert it to a sorted CSV here so
    # apply_user_model_selections can persist it as COMFYUI_USER_MODELS.
    comfyui_user_models: dict = {}
    comfyui_models_v = selections.get(COMFYUI_MODELS_TITLE)
    # None = step never visited (skip-predicate hid it) → no write.
    # SECRET_KEEP = degraded fetch → no write. An explicit empty commit
    # ("0 selected" on a healthy list) WRITES the empty CSV — the user
    # deselected everything, mirroring the Ollama block above (the old
    # truthiness gate made deselect-all silently unpersistable).
    if comfyui_models_v is not None and comfyui_models_v != SECRET_KEEP:
        out_names = sorted(comfyui_models_v) if isinstance(comfyui_models_v, set) else \
            sorted({n.strip() for n in str(comfyui_models_v).split(",") if n.strip()})
        comfyui_user_models["COMFYUI_USER_MODELS"] = ",".join(out_names)

    # Inline secondary integer inputs — any prompt step that mounted a
    # ``SecondaryNumberInput`` writes its value under a synthetic key
    # ``__secondary__:<ENV_VAR>`` in the selections dict (see
    # WizardScreen._action_confirm). Drain all of them into the env-write
    # bag here so the same ``apply_user_model_selections`` pipeline
    # persists them. Generic — works for RAY_WORKER_COUNT today and any
    # future localhost-port override tomorrow.
    for sel_key, sel_val in selections.items():
        if isinstance(sel_key, str) and sel_key.startswith("__secondary__:"):
            env_var = sel_key.split(":", 1)[1]
            if env_var:
                ollama_user_models[env_var] = sel_val

    # Default model selections — the final three wizard steps let the user
    # pick LITELLM_DEFAULT_MODEL, LITELLM_EMBEDDING_MODEL, LITELLM_VISION_MODEL.
    # Draining rules:
    #   content / embedding: omit when None (step skipped) or SECRET_KEEP
    #     (degraded step), and also when "" (empty answer — no models available).
    #   vision: "" is a valid explicit "skip" answer (persist it so the user's
    #     choice to leave vision unset is honoured); SECRET_KEEP is omitted.
    default_model_selections: dict[str, str] = {}
    content_v = selections.get(LLM_DEFAULT_CONTENT_TITLE)
    if content_v not in (None, SECRET_KEEP) and content_v != "":
        default_model_selections["LITELLM_DEFAULT_MODEL"] = content_v
    embed_v = selections.get(LLM_DEFAULT_EMBED_TITLE)
    if embed_v not in (None, SECRET_KEEP) and embed_v != "":
        default_model_selections["LITELLM_EMBEDDING_MODEL"] = embed_v
    vision_v = selections.get(LLM_DEFAULT_VISION_TITLE)
    if vision_v not in (None, SECRET_KEEP):       # "" (skip) is a valid explicit answer
        default_model_selections["LITELLM_VISION_MODEL"] = vision_v

    bp = selections.get("Base port  ·  range")
    # "auto" is resolved to a concrete free block HERE rather than being
    # threaded downstream as a string. Three separate consumers int() this
    # value (run_launch_flow's port preview, _run_pipeline_and_stream, and
    # the .env write), and resolving up front also means the stack overview
    # previews the ports the run will actually bind. The user's intent is
    # kept in base_port_auto so the command summary still shows
    # "--base-port auto", which is what reproduces the choice next time.
    base_port_auto = str(bp or "").strip().lower() == "auto"
    if base_port_auto:
        base_port_val = _resolve_auto_base_port(current_base_port)
    else:
        try:
            base_port_val = int(bp) if bp else current_base_port
        except ValueError:
            base_port_val = current_base_port

    # Project name → PROJECT_NAME. Normalize (lowercase + validate); on an
    # empty or invalid entry, fall back to the current .env value so a stray
    # answer never resets the namespace. Only emit it when it actually differs
    # from the current value (avoids a no-op .env write every launch).
    project_name_val = None
    pn_raw = selections.get("Project name  ·  namespace")
    if pn_raw:
        from core.config_parser import normalize_project_name
        try:
            _pn = normalize_project_name(pn_raw)
        except ValueError:
            _pn = None
        _current_pn = ((env_vars or {}).get("PROJECT_NAME") or "").strip().lower()
        if _pn and _pn != _current_pn:
            project_name_val = _pn
    cold = selections.get("Cold start  ·  rebuild") == "yes"
    hosts = selections.get("Hosts setup  ·  /etc/hosts", "default")
    launch = selections.get("Confirm  ·  launch the stack") == "yes"
    # Resolve the deployment profile from the wizard's profile-step selection.
    # Falls back to "default" when the step was never visited.
    resolved_profile = (selections.get(PROFILE_STEP_TITLE) or "default").strip()
    return source_args, {
        "base_port": base_port_val, "base_port_auto": base_port_auto,
        "project_name": project_name_val, "cold": cold,
        "setup_hosts": (hosts == "setup"), "skip_hosts": (hosts == "skip"),
        "launch_confirmed": launch,
        "cloud_api_keys": cloud_api_keys,
        "cloud_user_models": cloud_user_models,
        "ollama_user_models": ollama_user_models,
        "comfyui_user_models": comfyui_user_models,
        "default_model_selections": default_model_selections,
        "profile": resolved_profile,
    }


# ─── main entry ──────────────────────────────────────────────────────

def _run_app_with_process_cleanup(app) -> None:
    """Own SIGTERM cleanup for bounded commands launched by TUI workers."""
    from core.process_runner import cleanup_active_processes_on_sigterm

    with cleanup_active_processes_on_sigterm():
        app.run()


def run_setup_flow(
    config_parser, hosts_manager, *,
    starter=None,
    no_port_migrate: bool = False,
    track: str | None = None,
    overridden_services: frozenset[str] | None = None,
    no_splash: bool = False,
    profile: str | None = None,
) -> int:
    """Run wizard + pipeline + docker compose all in ONE Textual screen.

    Returns exit code: 0 on success / cancellation.
    """
    from .widgets import BrandInfo
    from .screens.wizard_screen import WizardScreen

    # Port-layout v0 → v1 migration runs BEFORE the wizard reads .env so
    # the user sees post-migration port values in the box. start.py has
    # already called setup_env_file + backfill_missing_env_vars; the
    # helper is idempotent and a no-op once the v1 sentinel is stamped.
    if starter is not None:
        starter.run_port_migration(no_port_migrate)

    steps, rows, services_info, current_base_port, state, cloud_summaries = (
        _build_steps_and_rows(
            config_parser, hosts_manager,
            track_key=track,
            overridden_services=overridden_services or frozenset(),
            profile=profile,
        )
    )
    from .widgets.info_box import ConsumerSummary as _ConsumerSummary
    consumer_summaries = [
        _ConsumerSummary(name=consumer.name)
        for consumer in state.consumers
    ]
    brand = BrandInfo(
        name=getattr(state, "brand_name", None) or "Atlas",
        tagline=getattr(state, "tagline", None) or "Self-hosted Engineering Platform",
        creator=getattr(state, "creator", None) or "",
        creator_email=getattr(state, "creator_email", None) or "",
        license=getattr(state, "license", None) or "",
        repo=getattr(state, "repo_url", None) or "",
        version=getattr(state, "version", None) or "",
    )

    state_holder = {"interrupted": False, "exit_code": 0}

    # Derive track display name for the InfoPanel banner. Only relevant
    # when --track was passed on the CLI; in wizard mode the picker step
    # resolves the selection live, but the banner is populated here from
    # the CLI arg so the panel shows the right label before the wizard
    # starts (and persists through _refresh_info_panel calls).
    _track_display_name = _resolve_track_display_name(track)

    # Snapshot env vars at wizard-build time so the cloud auto-promotion
    # logic in _selections_to_args has the .env state to compare against.
    _env_snapshot = config_parser.parse_env_file()

    def _resolve(selections: dict) -> tuple[dict, dict]:
        return _selections_to_args(
            selections, services_info, current_base_port, _env_snapshot,
        )

    # Single source of truth for "what port should this service show
    # given its current source": delegates to state_builder.resolve_port
    # which already knows about localhost endpoint vars
    # (LITELLM_BASE_URL, COMFYUI_ENDPOINT, etc.) for localhost sources
    # and falls back to the container port var otherwise.
    from core.port_manager import PortManager
    from wizard.model.state_builder import lookup_service_meta, resolve_port as _resolve_port
    port_offsets = PortManager(str(config_parser.root_dir)).port_offsets()

    def _resolve_port_for_service(name: str, source: str) -> str:
        """Live re-derive a service's displayed port for the given
        source. Re-reads ``.env`` so localhost endpoint changes (and
        any other manual edits) are picked up on every confirm —
        effectively the "refresh" the user asked for."""
        meta = lookup_service_meta(name)
        port_var = (meta or {}).get("port_var") if meta else None
        env = config_parser.parse_env_file()
        return _resolve_port(name, source, port_var, env) or ""

    def _recompute_ports(new_base: int, current_rows):
        return recompute_ports_for_base(
            new_base, current_rows, config_parser, port_offsets,
        )

    class _SetupApp(App):
        CSS_PATH = str(_THEME_PATH)
        # TITLE is set dynamically in on_mount so it honors BRAND_NAME
        # overrides (forks that rebrand via BRAND_* env vars get their own
        # window title without code changes).
        TITLE = "Setup"
        BINDINGS = [Binding("ctrl+c", "interrupt", "Quit", priority=True)]

        def on_mount(self) -> None:
            self.title = f"{brand.name or 'Atlas'} — Setup"
            _prefilled: dict = {}
            if track:
                _prefilled[PICKER_STEP_TITLE] = track
            if profile:
                _prefilled[PROFILE_STEP_TITLE] = profile
            self.push_screen(WizardScreen(
                steps=steps, services=rows, brand=brand,
                starter=starter,
                stack_options_resolver=_resolve,
                on_base_port_change=_recompute_ports,
                resolve_port_for_service=_resolve_port_for_service,
                cloud_apis=cloud_summaries,
                consumers=consumer_summaries,
                prefilled_selections=(_prefilled if _prefilled else None),
                track_display_name=_track_display_name,
                no_splash=no_splash,
                on_launch_result=lambda code: state_holder.__setitem__("exit_code", code),
            ))

        def action_interrupt(self) -> None:
            state_holder["interrupted"] = True
            state_holder["exit_code"] = 130
            self.exit()

    _run_app_with_process_cleanup(_SetupApp())
    return state_holder["exit_code"]


# ─── CLI-flag-driven launch (no wizard) ──────────────────────────────

def run_launch_flow(
    config_parser, hosts_manager, *,
    starter,
    source_args: dict,
    stack_options: dict,
    no_port_migrate: bool = False,
    track: str | None = None,
    overridden_services: frozenset[str] | None = None,
    no_splash: bool = False,
    profile: str | None = None,
) -> int:
    """Push the same Textual launch screen the wizard transitions to,
    but pre-loaded with CLI args — no wizard prompts in between.

    Used when ``./start.sh`` is invoked with explicit source / stack
    flags so the user gets the same in-screen pipeline + log streaming
    UX as the wizard path.

    Returns exit code: 0 on detach, non-zero on interrupt.
    """
    from .widgets import BrandInfo
    from .screens.wizard_screen import WizardScreen
    from core.port_manager import PortManager
    from wizard.model.state_builder import lookup_service_meta, resolve_port as _resolve_port

    # Port-layout v0 → v1 migration runs BEFORE the launch overview reads
    # .env so the displayed ports match the post-migration topology.
    # start.py has already called setup_env_file + backfill; the helper
    # is idempotent once the v1 sentinel is stamped.
    starter.run_port_migration(no_port_migrate)

    _pm = PortManager(str(config_parser.root_dir))

    _, rows, services_info, current_base_port, state, cloud_summaries = (
        _build_steps_and_rows(
            config_parser, hosts_manager,
            track_key=track,
            overridden_services=overridden_services or frozenset(),
            profile=profile,
        )
    )
    from .widgets.info_box import ConsumerSummary as _ConsumerSummary
    consumer_summaries = [
        _ConsumerSummary(name=consumer.name)
        for consumer in state.consumers
    ]
    brand = BrandInfo(
        name=getattr(state, "brand_name", None) or "Atlas",
        tagline=getattr(state, "tagline", None) or "Self-hosted Engineering Platform",
        creator=getattr(state, "creator", None) or "",
        creator_email=getattr(state, "creator_email", None) or "",
        license=getattr(state, "license", None) or "",
        repo=getattr(state, "repo_url", None) or "",
        version=getattr(state, "version", None) or "",
    )

    port_offsets = _pm.port_offsets()
    env_vars = config_parser.parse_env_file()

    # Resolve the effective base port. CLI ``--base-port N`` wins; else
    # whatever's in .env today.
    base_port = stack_options.get("base_port")
    if base_port is None:
        base_port = current_base_port
    base_port = int(base_port)

    # Build a synthetic env dict that reflects the effective post-launch
    # configuration: container ports re-derived from the chosen base port,
    # plus the CLI source overrides applied. ``resolve_port`` picks the
    # localhost endpoint port for ``*-localhost`` sources and the
    # synth-env container port otherwise.
    synth_env = dict(env_vars)
    for pv, off in port_offsets.items():
        synth_env[pv] = str(base_port + off)
    kong_port = str(base_port + port_offsets.get("KONG_HTTP_PORT", 0))

    # Map CLI source-arg keys (e.g. "llm_provider_source") onto the
    # corresponding display name from the wizard's services_info list,
    # so we can splice the override into the right ServiceRow.
    # Multi-container families (ray-head, spark-master, airflow-webserver)
    # surface their discovery-anchor key, but the CLI source flag uses the
    # family stem (--ray-source, not --ray-head-source). Remap before lookup
    # or these source overrides never show in the pre-launch overview.
    from .screens.wizard_screen import _FAMILY_FLAG_STEM
    overrides_by_name: dict[str, str] = {}
    for svc in services_info:
        stem = _FAMILY_FLAG_STEM.get(svc.key, svc.key)
        cli_key = stem.replace("-", "_") + "_source"
        v = source_args.get(cli_key)
        if v:
            overrides_by_name[svc.display_name] = v

    # Splice CLI overrides onto the rows + re-derive ports + alias_port.
    new_rows = []
    for r in rows:
        new_source = overrides_by_name.get(r.name, r.source)
        meta = lookup_service_meta(r.name)
        port_var = (meta or {}).get("port_var") if meta else None
        new_port = _resolve_port(r.name, new_source, port_var, synth_env) or ""
        from .widgets.service_table import ServiceRow as _SR
        new_rows.append(_SR(
            name=r.name, source=new_source, alias=r.alias,
            port=new_port,
            alias_port=(kong_port if r.alias else ""),
            default_source=r.default_source,
            configurable=r.configurable,
            category=r.category,
            pending=False,  # launch-flow rows are fully resolved before display
            off_track=r.off_track,
            # Preserve hover-card metadata — CLI-flag launch mode must show the
            # same S3 endpoints / source options / dependencies as wizard mode
            # (mirrors recompute_ports_for_base, which carries the same comment).
            tooltip_extra=r.tooltip_extra,
            source_options=r.source_options,
            depends_on=r.depends_on,
        ))

    # `state.services` already arrives in canonical topology order; the
    # source-resolved `new_rows` list preserves that order (the loop above
    # iterates `state.services` in input order).

    # Same recompute / resolve callbacks as run_setup_flow — harmless
    # in CLI mode where they're never triggered, but keeps WizardScreen
    # construction symmetrical.
    def _resolve_port_for_service(name: str, source: str) -> str:
        meta = lookup_service_meta(name)
        port_var = (meta or {}).get("port_var") if meta else None
        env = config_parser.parse_env_file()
        return _resolve_port(name, source, port_var, env) or ""

    def _recompute_ports(new_base: int, current_rows):
        return recompute_ports_for_base(
            new_base, current_rows, config_parser, port_offsets,
        )

    # Derive track display name for the InfoPanel banner.
    _track_display_name = _resolve_track_display_name(track)

    state_holder = {"interrupted": False, "exit_code": 0}

    class _LaunchApp(App):
        CSS_PATH = str(_THEME_PATH)
        # TITLE is set dynamically in on_mount so it honors BRAND_NAME
        # overrides (forks that rebrand via BRAND_* env vars get their own
        # window title without code changes).
        TITLE = "Launch"
        BINDINGS = [Binding("ctrl+c", "interrupt", "Quit", priority=True)]

        def on_mount(self) -> None:
            self.title = f"{brand.name or 'Atlas'} — Launch"
            # ``steps=[]`` is fine because auto_launch=True bypasses
            # the wizard entirely; the prompt panel is composed but
            # immediately removed by the launch transition.
            _prefilled_launch: dict = {}
            if track:
                _prefilled_launch[PICKER_STEP_TITLE] = track
            if profile:
                _prefilled_launch[PROFILE_STEP_TITLE] = profile
            self.push_screen(WizardScreen(
                steps=[], services=new_rows, brand=brand,
                starter=starter,
                on_base_port_change=_recompute_ports,
                resolve_port_for_service=_resolve_port_for_service,
                cloud_apis=cloud_summaries,
                consumers=consumer_summaries,
                auto_launch=True,
                prefilled_source_args=dict(source_args),
                prefilled_stack_options=dict(stack_options,
                                             base_port=base_port),
                prefilled_selections=(
                    _prefilled_launch if _prefilled_launch else None
                ),
                track_display_name=_track_display_name,
                no_splash=no_splash,
                on_launch_result=lambda code: state_holder.__setitem__("exit_code", code),
            ))

        def action_interrupt(self) -> None:
            state_holder["interrupted"] = True
            state_holder["exit_code"] = 130
            self.exit()

    _run_app_with_process_cleanup(_LaunchApp())
    return state_holder["exit_code"]
