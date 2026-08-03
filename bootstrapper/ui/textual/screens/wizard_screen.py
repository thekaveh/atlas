"""
WizardScreen — single screen for the entire setup → launch → logs flow.

Layout (top to bottom):
    BrandPanel (contains BlockLogo)     7 cells
    Vertical#wizard-body                1fr
        Vertical#info-section: InfoPanel        (always visible, auto)
        Vertical#lower-pane:
            during setup:  PromptPanel + CommandSummary
            during launch: LogFilterChips + LogPane
    FooterBar                           3 cells (docked bottom)

No tab bar, no status ribbon — the prompt panel's border carries its
own title and step counter, so the chrome stays minimal. The service
status box stays pinned at top, and the lower pane swaps from
prompt+command-summary to filter-chips+log-pane when the user
confirms launch.
"""

from __future__ import annotations

import asyncio
from collections import deque
import contextlib
import os
import signal
import sys
import tempfile
from pathlib import Path
from typing import Callable

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.worker import Worker, WorkerState

from ..widgets import (
    BrandInfo,
    BrandPanel,
    CloudApiSummary,
    CloudApisRow,
    CommandSummary,
    ConsumerSummary,
    FooterBar,
    InfoBoxState,
    InfoPanel,
    LogFilterChips,
    LogPane,
    PromptOption,
    PromptPanel,
    PromptStep,
    ServiceRow,
    ServiceSummary,
    ServiceTable,
)


# Multi-container service families surface their discovery-anchor container
# key as ``PromptStep.service_key`` (e.g. ``ray-head``), but the real CLI flag
# uses the family stem (``--ray-source``, not ``--ray-head-source``). These are
# the discovery shims documented in ``utils/source_override_manager.py``; the
# command-summary panel must remap them or it renders flags Click rejects.
_FAMILY_FLAG_STEM = {
    "ray-head": "ray",
    "spark-master": "spark",
    "airflow-webserver": "airflow",
    "langfuse-web": "langfuse",
    "llm-graph-builder-frontend": "llm-graph-builder",
    "celery-worker": "celery",
}

_FAILURE_LOG_TIMEOUT_SECONDS = 60.0
_FAILURE_HINT_BUFFER_BYTES = 64 * 1024
_PROCESS_TERMINATION_GRACE_SECONDS = 2.0
_COMPOSE_BUILD_TIMEOUT_SECONDS = 90 * 60.0
_COMPOSE_UP_TIMEOUT_SECONDS = 30 * 60.0


def _compose_timeout_seconds(args: list[str]) -> float | None:
    """Return a deadline for finite Compose work; follow logs until cancelled."""
    if args[:1] == ["build"]:
        return _COMPOSE_BUILD_TIMEOUT_SECONDS
    if args[:1] == ["logs"] and "-f" in args:
        return None
    return _COMPOSE_UP_TIMEOUT_SECONDS


def _append_bounded_hint(
    captured: deque[tuple[str, int]], line: str, captured_bytes: int
) -> int:
    """Append a recent diagnostic line without exceeding the byte ceiling."""
    encoded = line.encode("utf-8", errors="replace")
    if len(encoded) > _FAILURE_HINT_BUFFER_BYTES:
        encoded = encoded[-_FAILURE_HINT_BUFFER_BYTES :]
        line = encoded.decode("utf-8", errors="ignore")
        encoded = line.encode("utf-8")
        captured.clear()
        captured_bytes = 0
    line_size = max(1, len(encoded))
    while captured and (
        captured_bytes + line_size > _FAILURE_HINT_BUFFER_BYTES
    ):
        _old_line, old_size = captured.popleft()
        captured_bytes -= old_size
    captured.append((line, line_size))
    return captured_bytes + line_size


async def _stop_process_tree(
    proc: asyncio.subprocess.Process,
    *,
    termination_grace_seconds: float = _PROCESS_TERMINATION_GRACE_SECONDS,
) -> None:
    """Stop an async subprocess and descendants, then reap the leader."""
    if termination_grace_seconds <= 0:
        raise ValueError("termination_grace_seconds must be positive")
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:  # pragma: no cover - native Windows is not exercised in CI
            proc.terminate()
    except ProcessLookupError:
        pass
    # The leader can exit on TERM while a resistant child survives. Preserve
    # the full grace period, even through cancellation, before escalation.
    grace_task = asyncio.create_task(asyncio.sleep(termination_grace_seconds))
    await _await_uncancellable(grace_task)
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:  # pragma: no cover
            proc.kill()
    except ProcessLookupError:
        pass
    wait_task = asyncio.create_task(proc.wait())
    await _await_uncancellable(wait_task)


async def _await_uncancellable(task: asyncio.Task):
    """Wait for a thread-backed task despite repeated cancellation requests."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


def _kill_remaining_process_group(proc: asyncio.subprocess.Process) -> None:
    if os.name != "posix":
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


async def _launch_process(
    command: list[str],
    *,
    termination_grace_seconds: float = _PROCESS_TERMINATION_GRACE_SECONDS,
    **kwargs,
) -> asyncio.subprocess.Process:
    """Launch without losing the process-group handle to caller cancellation."""
    launch_task = asyncio.create_task(
        asyncio.create_subprocess_exec(*command, **kwargs)
    )
    try:
        return await asyncio.shield(launch_task)
    except asyncio.CancelledError as cancellation:
        try:
            proc = await _await_uncancellable(launch_task)
        except Exception:
            raise cancellation
        try:
            await _stop_process_tree(
                proc, termination_grace_seconds=termination_grace_seconds
            )
        finally:
            raise cancellation


async def _cancel_stream_and_stop(
    stream_task: asyncio.Task[int],
    proc: asyncio.subprocess.Process,
    termination_grace_seconds: float,
) -> None:
    """Drain a failed stream task and stop its entire process group."""
    stream_task.cancel()
    with contextlib.suppress(BaseException):
        await stream_task
    await _stop_process_tree(
        proc, termination_grace_seconds=termination_grace_seconds
    )


async def _run_streamed_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    on_line: Callable[[str], None],
    timeout_seconds: float | None,
    termination_grace_seconds: float = _PROCESS_TERMINATION_GRACE_SECONDS,
) -> int:
    """Stream one isolated command with a total deadline and safe cleanup."""
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    proc = await _launch_process(
        command,
        cwd=str(cwd),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
        start_new_session=os.name == "posix",
        termination_grace_seconds=termination_grace_seconds,
    )

    async def consume() -> int:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            on_line(raw.decode(errors="replace").rstrip("\r\n"))
        return await proc.wait()

    stream_task = asyncio.create_task(consume())
    try:
        if timeout_seconds is None:
            returncode = await stream_task
        else:
            returncode = await asyncio.wait_for(
                stream_task, timeout=timeout_seconds
            )
        _kill_remaining_process_group(proc)
        return returncode
    except asyncio.TimeoutError:
        assert timeout_seconds is not None
        await _stop_process_tree(
            proc, termination_grace_seconds=termination_grace_seconds
        )
        on_line(f"Command timed out after {timeout_seconds:.0f}s")
        return 124
    except asyncio.CancelledError:
        await _cancel_stream_and_stop(
            stream_task, proc, termination_grace_seconds
        )
        raise
    except BaseException:
        await _cancel_stream_and_stop(
            stream_task, proc, termination_grace_seconds
        )
        raise


async def _capture_bounded_process_output(
    command: list[str],
    *,
    cwd: Path,
    sink: Callable[[str], object],
    timeout_seconds: float,
) -> tuple[int, list[str]]:
    """Capture process output to a sink and a bounded recent-hint buffer."""
    captured: deque[tuple[str, int]] = deque()
    captured_bytes = 0

    def capture_line(line: str) -> None:
        nonlocal captured_bytes
        sink(line + "\n")
        captured_bytes = _append_bounded_hint(captured, line, captured_bytes)

    returncode = await _run_streamed_command(
        command,
        cwd=cwd,
        env=os.environ.copy(),
        on_line=capture_line,
        timeout_seconds=timeout_seconds,
    )
    return returncode, [line for line, _size in captured]


_SETUP_HINTS = [
    (("↑", "↓"), "navigate"),
    (("space",), "toggle"),
    (("↵",), "confirm"),
    # `Tab` / `/` both focus the model-name search input on steps that
    # mount one (today: only ``Ollama  ·  models``). No-op on other
    # steps but the hint stays visible so users know the search box is
    # keyboard-reachable. Tab again (or Esc) returns focus to the
    # option list.
    (("tab", "/"), "search"),
    # `f` cycles capability filter chips when the active step exposes
    # them (today: only ``Ollama  ·  models``). No-op on other steps;
    # hint stays visible so users on terminals without mouse passthrough
    # know the chip row is reachable from the keyboard.
    (("f",), "filter"),
    (("esc",), "back"),
    (("ctrl+q",), "quit"),
]

_LAUNCH_HINTS = [
    (("a",), "all"),
    (("e",), "errors"),
    (("w",), "warns"),
    (("i",), "info"),
    (("s",), "sources"),
    (("ctrl+q",), "detach"),
]

_STARTUP_HINTS = [
    (("a",), "all"),
    (("e",), "errors"),
    (("w",), "warns"),
    (("i",), "info"),
    (("s",), "sources"),
    (("ctrl+c",), "cancel"),
]



def prune_skip_hidden_selections(steps, selections: dict) -> dict:
    """Return a copy of ``selections`` without commits from steps whose
    skip-predicate is true.

    A user can visit a step (e.g. the ComfyUI picker), commit, then go
    Back and disable the owning service — the stale commit would
    otherwise persist (an empty CSV wiping COMFYUI_USER_MODELS for a
    now-disabled service). Predicate exceptions mean "don't skip" (keep
    the commit), mirroring WizardScreen._step_should_skip. Synthetic
    ``__secondary__:`` keys are never step titles, so they always
    survive. Module-level so tests bind to the production logic.
    """
    pruned = dict(selections)
    for step in steps:
        skip = getattr(step, "skip_if_prev", None)
        if skip is None:
            continue
        try:
            hidden = bool(skip(pruned))
        except Exception:  # noqa: BLE001 — buggy predicate must not crash launch
            hidden = False
        if hidden:
            pruned.pop(step.title, None)
    return pruned


class WizardScreen(Screen):
    """Setup wizard + in-place log streaming."""

    AUTO_FOCUS = None

    BINDINGS = [
        Binding("up", "move(-1)", "Up", priority=True),
        Binding("down", "move(1)", "Down", priority=True),
        # Vim-style ``k``/``j`` are kept as a convenience but split
        # into a separate action so ``check_action`` can disable them
        # while the search input has focus (otherwise typing
        # ``"kimi"`` or ``"jinja"`` would walk the cursor instead of
        # appearing as text in the search box).
        Binding("k", "vim_move(-1)", "Up", priority=True),
        Binding("j", "vim_move(1)", "Down", priority=True),
        Binding("space", "toggle", "Toggle", priority=True),
        Binding("enter", "confirm", "Confirm", priority=True),
        Binding("escape", "back", "Back", priority=True),
        Binding("ctrl+q", "quit_wizard", "Quit", priority=True),
        # Multiselect filter cycle — no-op on steps without filter_tags.
        # show=False because the footer hints are explicitly listed in
        # _SETUP_HINTS rather than auto-derived from the BINDINGS list.
        Binding("f", "cycle_filter", "Filter", show=False, priority=True),
        # `/` focuses the model-name search input on the Ollama
        # multiselect step (only step that mounts one). Priority lets
        # us steal `/` even when the option list has focus.
        Binding("slash", "focus_search", "Search", show=False, priority=True),
        # Tab toggles focus between the search input and the option
        # list. Symmetric: pressing Tab from either side flips. This
        # gives the user a discoverable, keyboard-only affordance —
        # the implicit "first focusable widget" Tab behaviour Textual
        # provides isn't enough because we explicitly park focus on
        # the option list to keep Space from being eaten by an
        # unfocused-looking Input.
        Binding("tab", "toggle_search_focus", "Search", show=False, priority=True),
        Binding("a", "filter_all", "All logs", show=False, priority=True),
        Binding("e", "filter_errors", "Errors only", show=False, priority=True),
        Binding("w", "filter_warns", "Warns only", show=False, priority=True),
        Binding("i", "filter_info", "Info only", show=False, priority=True),
        Binding("s", "filter_sources", "Sources", show=False, priority=True),
    ]

    DEFAULT_CSS = """
    WizardScreen {
        background: #12131e;
        layers: base popup overlay;
    }
    WizardScreen > #wizard-body {
        height: 1fr;
        padding: 0 2;
    }
    WizardScreen #info-section { width: 100%; height: auto; }
    WizardScreen #lower-pane {
        width: 100%;
        height: 1fr;
    }
    WizardScreen AtlasSplash { layer: overlay; width: 100%; height: 100%; }
    """

    def __init__(
        self,
        *,
        steps: list[PromptStep],
        services: list[ServiceRow],
        brand: BrandInfo | None = None,
        starter=None,
        stack_options_resolver: Callable[[dict[str, str]], tuple[dict, dict]] | None = None,
        on_base_port_change: Callable[[int, list[ServiceRow]], list[ServiceRow]] | None = None,
        resolve_port_for_service: Callable[[str, str], str] | None = None,
        cloud_apis: list[CloudApiSummary] | None = None,
        consumers: list[ConsumerSummary] | None = None,
        auto_launch: bool = False,
        prefilled_source_args: dict | None = None,
        prefilled_stack_options: dict | None = None,
        prefilled_selections: dict | None = None,
        track_display_name: str | None = None,
        no_splash: bool = False,
        on_launch_result: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__()
        self._no_splash = no_splash
        self._steps = steps
        self._services = services
        self._brand = brand or BrandInfo()
        self._starter = starter
        self._on_launch_result = on_launch_result
        self._stack_options_resolver = stack_options_resolver
        # Called when the user confirms a new base port; should return
        # an updated list of ServiceRows with recomputed ports.
        self._on_base_port_change = on_base_port_change
        # Called when the user picks a new SOURCE for a service. Given
        # (service_name, new_source) returns the port string to display
        # — typically the localhost port for "localhost" sources and
        # the container port otherwise. Returns "" when no port should
        # be shown (e.g., disabled).
        self._resolve_port_for_service = resolve_port_for_service
        # Auto-launch mode: skip the wizard prompts entirely and jump
        # straight to the launch phase. Used when start.py is invoked
        # with explicit CLI flags so the user doesn't have to walk
        # through the wizard for choices they've already made.
        self._auto_launch = auto_launch
        if prefilled_source_args is not None:
            self._source_args = dict(prefilled_source_args)
        else:
            self._source_args = None
        if prefilled_stack_options is not None:
            self._stack_options = dict(prefilled_stack_options)
        else:
            self._stack_options = None
        # Track display name — shown as "Track: <name>" banner in the
        # InfoPanel footer when a track was selected via --track or the
        # picker step.
        self._track_display_name: str | None = track_display_name

        self._step_index = 0
        self._selections: dict[str, str] = dict(prefilled_selections or {})
        # Frozen defaults snapshot — used to compute "N changed from
        # defaults" correctly (only count selections that DIFFER from
        # their step's default_value).
        self._defaults: dict[str, str] = {
            s.title: (s.default_value or "") for s in steps
        }
        # Provider-options cache for ``options_provider``-driven steps.
        # Keyed by step_index → list[PromptOption]. ``_provider_done``
        # tracks whether the worker has populated the cache, so the
        # splash branch in ``_load_current_step`` doesn't fire twice.
        # Cleared on action_back so revisits re-fetch with the (possibly
        # changed) upstream key.
        self._provider_cache: dict[int, list] = {}
        self._provider_done: dict[int, bool] = {}
        # Generation token incremented every time we invalidate the
        # cache (action_back, etc.). Each ``_fetch_provider_options``
        # captures the generation at dispatch and refuses to write its
        # result if the token has bumped — protects against a slow
        # fetch (slow API + 5s timeout) writing stale options into a
        # cache the user has already invalidated by going back and
        # changing the upstream key.
        self._fetch_generation: int = 0

        self._phase: str = "setup"   # "setup" | "launch"
        self._launch_detach_ready = False

        self._command_summary = CommandSummary()
        self._service_table = ServiceTable(services)
        self._cloud_apis: list[CloudApiSummary] = list(cloud_apis or [])
        self._consumers: list[ConsumerSummary] = list(consumers or [])
        # Pass the service table so the row can align its category legend
        # to the actual 2nd-slot start (cached by ServiceTable on each render).
        self._cloud_apis_row = CloudApisRow(
            self._cloud_apis, service_table=self._service_table,
        )
        summaries = [
            ServiceSummary(name=r.name, source=r.source, port=r.port,
                           alias=r.alias, pending=r.pending)
            for r in services
        ]
        self._info_panel = InfoPanel(
            InfoBoxState(
                brand=self._brand,
                services=summaries,
                cloud_apis=self._cloud_apis,
                consumers=self._consumers,
                track_label=self._track_display_name,
            ),
            # CloudApisRow renders the category legend on its right half.
            body_widgets=[self._service_table, self._cloud_apis_row],
            title=f" Stack overview · {len(services)} services ",
        )
        self._prompt = PromptPanel()
        self._footer = FooterBar(hints=_SETUP_HINTS)

        # Launch-phase widgets created lazily on transition.
        # NOTE: ``_source_args`` and ``_stack_options`` may have been
        # seeded above from prefilled_* arguments — DON'T overwrite
        # them here.
        self._log_chips: LogFilterChips | None = None
        self._log_pane: LogPane | None = None

        # Register a wizard-time warning sink so cloud /v1/models fetch
        # failures (and similar) land in the launch log + log pane.
        # The sink is module-level state on integration.py so the
        # closures in _build_steps_and_rows (built BEFORE this screen
        # exists) can reach it.
        try:
            from .. import integration as _integration_mod
            _integration_mod._set_wizard_warn_sink(
                lambda msg: self._safe_log(msg, source="wizard", level="warn")
            )
        except Exception:  # noqa: BLE001
            pass

        # Open the session-log tee NOW so wizard-time warnings (cloud
        # /v1/models fetch failures, etc.) get persisted from the
        # moment the screen exists. Previously the tee was opened only
        # in the launch transition, so any setup-phase warning was
        # silently dropped — contradicting docs that promised the file
        # captured ``everything the log pane showed plus a few sources
        # the pane filters out``. The path is announced in the log
        # pane at launch time when _log_pane finally exists.
        self._launch_log_fh = None
        self._launch_log_path = None
        self._open_launch_log_tee(announce_in_pane=False)

    def compose(self) -> ComposeResult:
        with Vertical(id="wizard-body"):
            yield BrandPanel(
                tagline=self._brand.tagline or "Self-hosted Engineering Platform",
                author=self._brand.creator,
                author_email=self._brand.creator_email,
                license=self._brand.license,
                version=self._brand.version,
                repo=self._brand.repo,
            )
            with Vertical(id="info-section"):
                yield self._info_panel
            with Vertical(id="lower-pane"):
                yield self._prompt
                yield self._command_summary
            yield self._footer

    def on_mount(self) -> None:
        # Set the compose-line project prefix as EARLY as possible so
        # any source name that flows into the filter dropdown gets
        # stripped to its bare form (``supabase-db`` instead of
        # ``<project>-supabase-db``). Doing it here, before any worker
        # runs, eliminates the timing window in which the default
        # ``"atlas-"`` prefix could leak through and produce both
        # stripped + unstripped chips.
        if self._starter is not None:
            try:
                set_project_prefix(self._starter.config_parser.get_project_name())
            except Exception:  # noqa: BLE001
                pass
        if self._auto_launch:
            # CLI-flag mode: skip the wizard and jump straight to the
            # launch phase. The prompt panel and command summary are
            # composed in the tree but get removed by the transition's
            # ``await lower.remove_children()`` step.
            #
            # The opening splash is intentionally NOT shown on this path:
            # a scripted ``--<svc>-source`` launch is non-interactive and
            # should start immediately rather than hold for the splash over
            # an already-streaming launch screen. ``no_splash`` is still
            # accepted by ``run_launch_flow`` for signature symmetry with
            # ``run_setup_flow`` and so a future launch-mode splash could
            # honor it without re-plumbing.
            self.run_worker(
                self._transition_to_launch(),
                exclusive=True, exit_on_error=False,
            )
            return
        self._load_current_step()
        # Pin --base-port into the command summary from the start so it's
        # ALWAYS visible regardless of whether the user has reached the
        # base-port step yet.
        self._refresh_command_summary()
        # Mount the opening splash: a full-screen dim overlay with the poster
        # centered (smooth on image-capable terminals, block-art fallback
        # otherwise). It holds, then removes itself to reveal the wizard, which
        # is mounted beneath and shows dimly through the overlay meanwhile.
        from ui.textual.widgets.atlas_splash import AtlasSplash, should_show_splash
        if should_show_splash(self._no_splash):
            self.mount(AtlasSplash())

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Surface failures from exit_on_error=False workers.

        The auto-launch transition (CLI-flag mode) runs with
        exit_on_error=False; without this, any exception inside it left
        the worker in ERROR state and the user staring at a permanently
        empty lower pane with no message anywhere.
        """
        if event.state is not WorkerState.ERROR:
            return
        self._mark_launch_failed()
        err = event.worker.error
        with contextlib.suppress(Exception):
            if self._log_pane is not None:
                self._write_status(
                    f"❌ {event.worker.name or 'background task'} failed: {err}",
                    style="bold red", source="pipeline",
                )
            else:
                # Setup phase: no log pane exists yet, so _write_status
                # would no-op and the failure vanished silently. Surface
                # it as a toast instead.
                self.notify(
                    str(err),
                    title=f"{event.worker.name or 'Background task'} failed",
                    severity="error",
                    timeout=10,
                )

    def _mark_launch_failed(self) -> None:
        """Record a nonzero CLI result while leaving the error visible in the TUI."""
        if self._on_launch_result is not None:
            self._on_launch_result(1)

    # ─── setup phase ─────────────────────────────────────────────────

    def _step_should_skip(self, idx: int) -> bool:
        """Run a step's ``skip_if_prev`` predicate. Exceptions are caught
        and treated as "don't skip" so a buggy predicate can't crash the
        wizard. Used by both the forward (``_load_current_step``) and
        backward (``action_back``) navigation walks.
        """
        if not (0 <= idx < len(self._steps)):
            return False
        skip = getattr(self._steps[idx], "skip_if_prev", None)
        if skip is None:
            return False
        try:
            return bool(skip(self._selections))
        except Exception:  # noqa: BLE001
            return False

    def _advance_past_skipped(self, direction: int) -> None:
        """Walk ``self._step_index`` in ``direction`` (+1 forward, -1 backward)
        past any consecutive skip_if_prev=True steps. Stops at the
        respective boundary (last step on forward, first step on
        backward) even if that step is itself skipped — the boundary
        steps in this wizard never set skip_if_prev, so this is safe.
        ``len(self._steps)`` bounds the loop so a pathological skip
        chain can't loop forever.
        """
        guard = 0
        max_iter = len(self._steps)
        while guard < max_iter and self._step_should_skip(self._step_index):
            next_idx = self._step_index + direction
            if direction > 0 and next_idx >= len(self._steps):
                break
            if direction < 0 and next_idx < 0:
                break
            self._step_index = next_idx
            guard += 1

    def _load_current_step(self) -> None:
        # Honor skip_if_prev — used by cloud multi-select steps to
        # bypass themselves when the previous secret step disabled
        # the provider.
        self._advance_past_skipped(direction=1)

        original = self._steps[self._step_index]
        provider = getattr(original, "options_provider", None)

        # Live options-provider path — render a "⏳ Fetching…" splash
        # first, then dispatch a worker to run the (potentially HTTP)
        # options_provider, then re-render with the real options when
        # it returns. Keeps the UI responsive (Esc still works) instead
        # of freezing the whole wizard for 5s.
        provider_pending = (
            provider is not None
            and original.kind == "multiselect"
            and not self._provider_done.get(self._step_index, False)
        )
        if provider_pending:
            # Strip a redundant " Cloud" suffix so the splash reads
            # "⏳ Fetching OpenAI models…" not "OpenAI Cloud models".
            provider_name = original.title.split('  ·  ')[0]
            if provider_name.endswith(" Cloud"):
                provider_name = provider_name[: -len(" Cloud")]
            splash_options = [PromptOption(
                value="__loading__",
                label=f"⏳ Fetching {provider_name} models…",
                hint="(usually <2s)",
            )]
            self._render_step(original, options=splash_options, is_loading=True)
            self.run_worker(
                self._fetch_provider_options(self._step_index, original, provider),
                exclusive=False, exit_on_error=False,
            )
            return

        # kind="options" steps with an options_provider run the provider
        # synchronously (these are cheap, local computations — no HTTP),
        # so the step renders with real options on first visit instead of
        # an empty list (the PromptStep is built with options=[] as a
        # placeholder because the options depend on upstream selections).
        if (
            provider is not None
            and original.kind == "options"
            and not self._provider_done.get(self._step_index, False)
        ):
            try:
                opts = provider(dict(self._selections))
            except Exception:  # noqa: BLE001
                opts = []
            self._provider_cache[self._step_index] = opts
            self._provider_done[self._step_index] = True

        # Provider already ran (cache hit) OR this step has no provider —
        # use the cached/static options directly.
        live_options = self._provider_cache.get(self._step_index, original.options)
        self._render_step(original, options=live_options)

    def _render_step(self, original: PromptStep, *, options=None, is_loading: bool = False) -> None:
        """Build a PromptStep instance with display-time-resolved fields
        and load it into the prompt panel.

        ``is_loading`` is set when this is the splash render between
        the user reaching a provider-driven step and the worker's
        options fetch returning. It prefixes the subtitle with an
        hourglass to match the splash option row.
        """
        # Preserve user state across revisits: if this is a multi-select
        # the user has already answered (e.g. they hit back then forward),
        # restore their checkbox state from self._selections rather than
        # reverting to the step's original default_values.
        live_default_values = list(original.default_values)
        if original.kind == "multiselect":
            prior = self._selections.get(original.title)
            if isinstance(prior, str):
                live_default_values = [s for s in prior.split(",") if s]
        live_options = options if options is not None else original.options
        # ``dataclasses.replace`` carries every field on the dataclass
        # forward by default — new fields added to PromptStep show up
        # at display time automatically, no need to update this method
        # in lock-step. Only the fields that change at render time get
        # an explicit override.
        from dataclasses import replace
        step = replace(
            original,
            step_index=self._step_index + 1,
            step_total=len(self._steps),
            subtitle=("⏳  " + original.subtitle.lstrip()) if is_loading else original.subtitle,
            options=live_options,
            default_values=live_default_values,
        )
        self._prompt.load_step(step)
        if step.service_name:
            target = step.service_name.lower()
            for i, row in enumerate(self._services):
                if row.name.lower() == target:
                    self._service_table.set_cursor(i)
                    break
        else:
            self._service_table.set_cursor(None)
        self._prompt.clear_conflict()

    async def _fetch_provider_options(
        self, step_index: int, original: PromptStep, provider,
    ) -> None:
        """Run the step's options_provider in a worker, then re-render.

        ``step_index`` is captured at dispatch time so we can detect
        the user navigating away mid-fetch and avoid stomping the
        currently-displayed step. ``generation`` is also captured so a
        late-arriving worker whose cache slot has since been
        invalidated (action_back) silently drops its result instead of
        polluting the new cache entry.
        """
        sel_snapshot = dict(self._selections)
        generation = self._fetch_generation
        try:
            options = await asyncio.to_thread(provider, sel_snapshot)
        except Exception as exc:  # noqa: BLE001
            # Surface instead of swallowing: a silent [] here looked like
            # an empty catalog and (pre-SECRET_KEEP guard) let a single
            # Enter wipe the user's saved model CSV after a network blip.
            self._safe_log(
                f"[warn/options-fetch] {original.title}: {type(exc).__name__}: {exc}",
                source="wizard", level="warn",
            )
            options = []
        # Generation drift means action_back ran while we were waiting
        # — the user may have changed the upstream key, so writing our
        # results would be wrong. Drop silently. The cache is empty so
        # on revisit the splash will dispatch a fresh worker.
        if generation != self._fetch_generation:
            return
        # Cache the result so going back-and-forward doesn't re-fetch
        # within the same wizard run. Cleared on action_back so the
        # user's revisit always sees a fresh fetch when they return.
        self._provider_cache[step_index] = options or []
        self._provider_done[step_index] = True
        # If the user has navigated away mid-fetch, don't disturb their
        # current step — but the cache will be ready on revisit.
        if self._step_index != step_index or self._phase != "setup":
            return
        # Re-render with real options on the main thread.
        self._load_current_step()

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """Suppress screen-level priority bindings while the search
        input has focus, so typed keys land in the Input as text.

        The wizard's keyboard model binds bare letters (``f``, ``a``,
        ``e``, ``w``, ``i``, ``j``, ``k``) and ``space`` with
        ``priority=True``. That's the right default while focus sits
        on the non-focusable option list, but it makes the search box
        useless: typing ``"qwen"`` would hijack the ``"w"`` to fire
        ``filter_warns``. The whitelist below names the actions that
        STAY enabled while search is focused — every other action
        returns ``False`` so the key flows through to the Input.

        Whitelist:
        * ``back`` — Esc unfocuses the search (see ``action_back``).
        * ``quit_wizard`` — Ctrl+Q stays a universal escape hatch.
        * ``move`` — arrow keys still walk the option list. Textual
          ``Input`` uses left/right for cursor movement, leaving
          up/down free to live-preview the narrowed results while
          typing.
        * ``toggle_search_focus`` — Tab toggles focus back to the
          option list (symmetric with the Tab-to-enter-search path).

        Deliberately NOT in the whitelist:
        * ``confirm`` — Enter inside the focused Input emits Textual's
          ``Input.Submitted`` message, which ``PromptPanel.on_input_
          submitted`` catches and routes to ``unfocus_search()``. The
          step's commit-Enter is reachable as soon as focus is back
          on the option list. The user picks the explicit "I'm done
          searching" moment instead of accidentally committing the
          whole multiselect while mid-keystroke.
        * ``vim_move`` (``j``/``k``), ``cycle_filter`` (``f``),
          ``filter_*`` (``a``/``e``/``w``/``i``), ``toggle`` (Space),
          ``focus_search`` (``/``) — all suppressed so the bound
          letters / Space / slash land in the Input as plain text.
        """
        if (
            self._phase == "setup"
            and self._prompt.has_search_focus()
        ):
            _SEARCH_ALLOWED = {
                "back", "quit_wizard", "move", "toggle_search_focus",
            }
            if action not in _SEARCH_ALLOWED:
                return False
        return True

    def action_vim_move(self, delta: int) -> None:
        """``j``/``k`` movement — delegates to :meth:`action_move` but
        named separately so ``check_action`` can suppress it while
        the search input has focus (without also suppressing the
        arrow-key navigation users expect to keep working there).
        """
        self.action_move(delta)

    def action_move(self, delta: int) -> None:
        if self._phase != "setup":
            return
        self._prompt.move(delta)

    def action_toggle(self) -> None:
        """Space: toggle / expand / collapse the focused multi-select row.

        Delegated to ``PromptPanel.toggle_focused``, which branches on
        the row kind (parent vs leaf) and whether the parent is
        expandable. No popup ⇒ no focus handover ⇒ no priority-binding
        conflict; the cursor and arrows stay in the prompt panel
        throughout.
        """
        if self._phase != "setup":
            return
        self._prompt.toggle_focused()

    def action_cycle_filter(self) -> None:
        """``f``: cycle the multiselect filter chip forward.

        Order is ``[ALL, *step.filter_tags]``; wraps. No-op on steps
        without filter_tags (so `f` is safe to leave bound globally —
        it just does nothing on non-filter steps).
        """
        if self._phase != "setup":
            return
        self._prompt.cycle_filter(direction=+1)

    def action_focus_search(self) -> None:
        """``/``: focus the model-name search input on multiselect
        steps that mount one (today: only ``Ollama  ·  models``).

        No-op on every other step. When focused, typing into the input
        live-narrows the option list; Esc returns focus to the option
        list (handled by ``action_back`` below).
        """
        if self._phase != "setup":
            return
        self._prompt.focus_search()

    def action_toggle_search_focus(self) -> None:
        """``Tab``: flip focus between the search input and the option
        list. No-op on steps without a search input.

        Symmetric on purpose — pressing Tab from either side returns
        to the other. Combined with the explicit focus park on step
        load (see ``PromptPanel._mount_search_input``), this is the
        only way the search box gets focus by accident.
        """
        if self._phase != "setup":
            return
        self._prompt.toggle_search_focus()

    def action_confirm(self) -> None:
        if self._phase != "setup":
            return
        # Defensive: in CLI-flag auto-launch mode the screen mounts with
        # steps=[] and flips to the "launch" phase from a worker scheduled in
        # on_mount. An Enter processed in the tiny window before that worker
        # runs (phase still "setup", _steps empty) would IndexError below.
        if not self._steps:
            return
        # Suppress confirmation while the splash row is showing for a
        # provider-driven multiselect — committing now would silently
        # commit only the step's static default_values (the live
        # options haven't arrived yet). The splash row's value is the
        # ``__loading__`` sentinel, which is harmless to commit but
        # confusing UX-wise. Wait for the worker to populate the cache.
        step = self._steps[self._step_index]
        if (
            step.kind == "multiselect"
            and getattr(step, "options_provider", None) is not None
            and not self._provider_done.get(self._step_index, False)
        ):
            return
        opt = self._prompt.selected_option
        if opt is None:
            return
        self._selections[step.title] = opt.value
        # Inline secondary integer inputs (kind="options" + per-option
        # secondary_number): capture each visible eligible input's value
        # under a synthetic ``__secondary__:<ENV_VAR>`` key so
        # _selections_to_args can route them to the env-write bag.
        # Empty list when the step has no eligible rows.
        for env_var, value in self._prompt.secondary_values():
            self._selections[f"__secondary__:{env_var}"] = value
        # Track picker: when the user commits the track-picker step,
        # resolve the picked key to a display_name and update the
        # InfoPanel banner. Without this, the banner only appears in
        # CLI mode (--track gen-ai-rag); picker-mode selections never
        # refresh self._track_display_name from None.
        try:
            from .. import integration as _int_mod
            if step.title == _int_mod.PICKER_STEP_TITLE:
                from tracks import load_tracks as _lt
                _reg = _lt()
                _t = _reg.by_key.get(opt.value)
                self._track_display_name = (
                    _t.display_name if _t is not None else None
                )
                self._refresh_info_panel()
        except Exception:  # noqa: BLE001
            # A bad track lookup must not block the wizard from advancing.
            pass
        # Cloud secret step: live-update the Cloud APIs row in the
        # overview to reflect the user's choice.
        if step.kind == "secret" and self._cloud_apis:
            self._apply_secret_step_to_cloud_apis(step, opt.value)
        # Cloud multiselect step: an empty CSV ("0 selected") means
        # the user explicitly de-selected every model. Match the
        # _selections_to_args policy ("disable provider + wipe key")
        # by reflecting the disabled state in the overview now,
        # instead of waiting until launch to surprise the user.
        if step.kind == "multiselect" and self._cloud_apis:
            self._apply_models_step_to_cloud_apis(step, opt.value)
        # Service-source step: update that row's source and refresh.
        for row in self._services:
            if step.service_name and row.name == step.service_name:
                row.source = opt.value
                row.pending = False
                # Re-derive the port for the new source — localhost
                # sources should show the host machine's port, container
                # sources the assigned container port, disabled none.
                if self._resolve_port_for_service is not None:
                    try:
                        new_port = self._resolve_port_for_service(row.name, row.source)
                        row.port = new_port or ""
                    except Exception:  # noqa: BLE001
                        pass
                # Row position is fixed by canonical topology order — a
                # source change only updates this row's port/source/alias
                # values, not its place in the list. (Earlier versions
                # re-sorted by ascending port on every confirm, which
                # silently broke same-category adjacency the moment a
                # localhost source surfaced a small port number.)
                self._service_table.set_rows(self._services)
                self._refresh_info_panel()
                break
        # Base-port step: recompute every row's port from the new base.
        if "base port" in step.title.lower() and self._on_base_port_change is not None:
            try:
                new_base = int(opt.value)
            except ValueError:
                new_base = None
            if new_base is not None:
                self._services = self._on_base_port_change(new_base, self._services)
                self._service_table.set_rows(self._services)
                self._refresh_info_panel()
        self._refresh_command_summary()
        if self._step_index + 1 < len(self._steps):
            self._step_index += 1
            self._load_current_step()
        else:
            # Last step is the launch confirm. opt.value is "yes" / "no".
            if opt.value == "yes":
                self.run_worker(
                    self._transition_to_launch(),
                    exclusive=True, exit_on_error=False,
                )
            else:
                # "No — exit without starting" must actually exit
                # (previously a silent no-op: Enter did nothing and only
                # Esc/Ctrl+Q could leave the screen).
                self._close_launch_log_tee()
                self.app.exit()

    def _refresh_info_panel(self) -> None:
        """Rebuild the service summaries from self._services and re-emit
        them + the current cloud_apis state through the InfoPanel.

        Extracted from the four sites that previously inlined the same
        4-line "summaries = [...]; self._info_panel.update_state(...)"
        block: action_confirm (service-source branch + base-port branch)
        and the two cloud overview update helpers below.
        """
        summaries = [
            ServiceSummary(name=r.name, source=r.source, port=r.port,
                           alias=r.alias, pending=r.pending)
            for r in self._services
        ]
        self._info_panel.update_state(
            InfoBoxState(
                brand=self._brand,
                services=summaries,
                cloud_apis=self._cloud_apis,
                consumers=self._consumers,
                track_label=self._track_display_name,
            )
        )

    def _apply_models_step_to_cloud_apis(self, step: PromptStep, value: str) -> None:
        """Live-update the Cloud APIs overview block after a cloud
        multiselect step.

        Two cases mirror ``_selections_to_args``:
          • Empty CSV ("0 selected") → user wants this provider OFF.
          • Non-empty CSV on a previously-disabled provider whose key
            is set → auto-promote (same path as the secret step's
            SECRET_KEEP+disabled+key path; without this, the overview
            would lag the launch state).
        """
        from ..widgets.prompt_panel import SECRET_KEEP
        from wizard.llm_steps import cloud_models_title
        title = step.title or ""
        target: CloudApiSummary | None = None
        for entry in self._cloud_apis:
            if title == cloud_models_title(entry.name):
                target = entry
                break
        if target is None:
            return
        if value == SECRET_KEEP:
            # Degraded multiselect commit (options never loaded) — leave
            # the overview untouched; _selections_to_args skips the
            # bucket too, so promoting here would make the overview lie.
            return
        csv = (value or "").strip()
        if csv == "":
            # Empty selection ⇒ provider gets disabled at launch.
            target.enabled = False
            target.key_set = False
        elif target.key_set and not target.enabled:
            # Non-empty selection on a previously-disabled provider with
            # a saved key. _selections_to_args auto-promotes the source
            # to ``enabled``; mirror that in the overview now.
            target.enabled = True
        else:
            return  # no overview change required
        self._cloud_apis_row.set_cloud_apis(self._cloud_apis)
        self._refresh_info_panel()

    def _apply_secret_step_to_cloud_apis(self, step: PromptStep, value: str) -> None:
        """Live-update the Cloud APIs overview block after a secret step.

        Maps the step title (e.g. ``OpenAI Cloud  ·  API key``) to the
        matching CloudApiSummary, applies the wizard's sentinel-encoded
        value, and refreshes the row + footer count line.
        """
        # Local imports avoid a hard dependency at module load time.
        from ..widgets.prompt_panel import SECRET_KEEP, SECRET_CLEAR
        from wizard.llm_steps import cloud_secret_title

        # Exact title match via the same helper that built the step
        # title. Keeps the two sides in lockstep — if cloud_secret_title
        # ever changes format, both producer and consumer move together.
        title = step.title or ""
        target: CloudApiSummary | None = None
        for entry in self._cloud_apis:
            if title == cloud_secret_title(entry.name):
                target = entry
                break
        if target is None:
            return
        if value == SECRET_KEEP:
            # Auto-promote case: .env had source=disabled but a key is
            # present. The skip predicate let the multiselect through;
            # _selections_to_args will flip source to enabled. Mirror
            # that in the overview so the user sees the correct state
            # immediately instead of waiting until launch.
            if target.key_set and not target.enabled:
                target.enabled = True
            else:
                return  # truly nothing changed
        elif value == SECRET_CLEAR or value == "":
            target.enabled = False
            target.key_set = False
        else:
            target.enabled = True
            target.key_set = True
        self._cloud_apis_row.set_cloud_apis(self._cloud_apis)
        self._refresh_info_panel()

    def _refresh_command_summary(self) -> None:
        from ..widgets.prompt_panel import SECRET_KEEP, SECRET_CLEAR
        from wizard.llm_steps import (
            OLLAMA_CUSTOM_TITLE,
            OLLAMA_MODELS_TITLE,
            cloud_models_title,
            cloud_secret_title,
        )

        flags: list[tuple[str, str]] = []
        # Base-port flag is ALWAYS shown — pinned at the top so the user
        # can see the chosen base port at a glance regardless of whether
        # it differs from the default. Falls back to the step's default
        # value when the user hasn't reached the base-port step yet.
        for step in self._steps:
            if "base port" in step.title.lower() and not step.service_name:
                value = self._selections.get(
                    step.title, self._defaults.get(step.title, ""),
                )
                if value:
                    flags.append(("--base-port", value))
                break

        # Build per-cloud title indexes once so we can match cloud
        # secret + multiselect steps without scanning the cloud list
        # for every step. Iterate the canonical CLOUD_PROVIDERS list so
        # adding a 4th provider doesn't silently miss this site.
        from utils.cloud_providers import CLOUD_PROVIDERS
        cloud_secret_titles = {
            cloud_secret_title(p.name): p.key for p in CLOUD_PROVIDERS
        }
        cloud_models_titles = {
            cloud_models_title(p.name): p.key for p in CLOUD_PROVIDERS
        }

        # All other steps follow. Skip-hidden steps are excluded so the
        # summary matches what the launch-time prune will actually
        # persist (a stale commit from a step the user later hid via
        # Back+disable used to render a flag that would never apply).
        for idx, step in enumerate(self._steps):
            if (getattr(step, "skip_if_prev", None) is not None
                    and self._step_should_skip(idx)):
                continue
            value = self._selections.get(step.title)
            if value is None:
                continue
            default = self._defaults.get(step.title, "")

            if step.service_name:
                # Service source flag — always show once picked. Derive
                # from the canonical ``ServiceInfo.key`` carried on the
                # PromptStep (e.g. ``llm_provider`` → ``--llm-provider-source``).
                # Falling back to the display-name slug is a bug:
                # ``LLM Engine`` → ``--llm-engine-source`` is not a real
                # Click flag and Click rejects it.
                key = step.service_key or step.service_name.lower().replace(" ", "-")
                key = _FAMILY_FLAG_STEM.get(key, key)
                flag = "--" + key.replace("_", "-") + "-source"
                flags.append((flag, value))
                continue

            title_low = step.title.lower()
            if "base port" in title_low:
                continue

            # Cloud secret step → equivalent --cloud-X-source +
            # sanitized --X-api-key. Never emit the raw key string.
            if step.title in cloud_secret_titles:
                provider = cloud_secret_titles[step.title]
                if value == SECRET_KEEP:
                    pass  # no flag — keeping current state
                elif value == SECRET_CLEAR or value == "":
                    flags.append((f"--cloud-{provider}-source", "disabled"))
                else:
                    flags.append((f"--cloud-{provider}-source", "enabled"))
                    flags.append((f"--{provider}-api-key", "<set>"))
                continue

            # Cloud multiselect → --X-models with truncated CSV /
            # selection count.
            if step.title in cloud_models_titles:
                provider = cloud_models_titles[step.title]
                if value == SECRET_KEEP:
                    continue  # degraded fetch — selection kept, no flag
                csv = (value or "").strip()
                if csv == "":
                    flags.append((f"--{provider}-models", "(none — provider disabled)"))
                else:
                    n = csv.count(",") + 1
                    short = csv if len(csv) <= 60 else csv[:57] + "..."
                    flags.append((f"--{provider}-models", f"{n} selected ({short})"))
                continue

            # Ollama models step (single unified [pulled]/[library] view).
            # The custom free-text step has its own flag below.
            if step.title == OLLAMA_MODELS_TITLE:
                if value == SECRET_KEEP:
                    continue  # degraded fetch — selection kept, no flag
                csv = (value or "").strip()
                if csv == "":
                    continue
                n = csv.count(",") + 1
                short = csv if len(csv) <= 60 else csv[:57] + "..."
                flags.append(("--ollama-models", f"{n} selected ({short})"))
                continue
            if step.title == OLLAMA_CUSTOM_TITLE:
                if value in (SECRET_KEEP, "", None):
                    continue
                if value == SECRET_CLEAR:
                    flags.append(("--ollama-custom-models", "(cleared)"))
                else:
                    short = value if len(value) <= 60 else value[:57] + "..."
                    flags.append(("--ollama-custom-models", short))
                continue

            # Meta steps (cold, hosts) — only show when non-default.
            if value == default:
                continue
            if "cold" in title_low and value == "yes":
                flags.append(("--cold", ""))
            elif "hosts" in title_low and value == "setup":
                flags.append(("--setup-hosts", ""))
            elif "hosts" in title_low and value == "skip":
                flags.append(("--skip-hosts", ""))
            # launch confirm itself never appears as a flag

        self._command_summary.set_flags(flags)

    def action_back(self) -> None:
        # If the search box has focus, Esc returns focus to the option
        # list — same UX pattern as :input in vim. Without this Esc
        # would rewind a wizard step while the user is actively typing
        # a search query.
        if (
            self._phase == "setup"
            and self._prompt.has_search_focus()
        ):
            self._prompt.unfocus_search()
            return
        # If a chip popup is open, escape closes it first instead of
        # rewinding the wizard step.
        if (
            self._log_chips is not None
            and getattr(self._log_chips, "_open_popup", None) is not None
        ):
            self._log_chips._open_popup.action_dismiss()
            return
        if self._phase != "setup":
            return
        if self._step_index > 0:
            # Walk backwards over any skip_if_prev steps so the user
            # doesn't land on an auto-skipped page when going back.
            self._step_index -= 1
            # Invalidate cached options_provider results from the new
            # step onward — earlier steps' caches still hold (the user's
            # upstream choices for them haven't changed). This means
            # going back from step 12 to step 10 doesn't blow away a
            # cached fetch from step 6.
            for k in list(self._provider_cache):
                if k >= self._step_index:
                    self._provider_cache.pop(k, None)
                    self._provider_done.pop(k, None)
            # Bump the generation so any in-flight fetch worker for the
            # cleared range (or beyond) drops its result instead of
            # writing back into the now-empty cache.
            self._fetch_generation += 1
            self._advance_past_skipped(direction=-1)
            self._load_current_step()
        else:
            self.app.exit()

    def action_quit_wizard(self) -> None:
        if self._phase == "launch" and not self._launch_detach_ready:
            self.notify(
                "Startup is still running; Ctrl+C cancels it.",
                severity="warning",
                timeout=6,
            )
            return
        # Close the tee on a setup-phase quit so the wizard-time
        # warnings flushed earlier aren't left in a still-open fh
        # past process exit. The launch-phase ``finally`` block
        # already does this; this branch covers the setup-quit path.
        self._close_launch_log_tee()
        self.app.exit()

    # ─── transition ──────────────────────────────────────────────────

    async def _transition_to_launch(self) -> None:
        # If args were prefilled at construction time (auto-launch mode),
        # honor them. Otherwise resolve from the wizard's selections.
        if self._source_args is None or self._stack_options is None:
            if self._stack_options_resolver is not None:
                # Drop commits from steps whose skip-predicate is true at
                # LAUNCH time (see prune_skip_hidden_selections).
                _launch_selections = prune_skip_hidden_selections(
                    self._steps, self._selections
                )
                self._source_args, self._stack_options = self._stack_options_resolver(
                    _launch_selections
                )
                if self._starter is not None:
                    try:
                        from .. import integration as _int_mod
                        self._starter.active_track = _launch_selections.get(
                            _int_mod.PICKER_STEP_TITLE
                        )
                        self._starter.active_track_overrides = frozenset()
                    except Exception:  # noqa: BLE001
                        pass
            else:
                self._source_args, self._stack_options = {}, {}

        self._phase = "launch"
        self.set_focus(None)

        lower = self.query_one("#lower-pane", Vertical)
        await lower.remove_children()

        self._log_chips = LogFilterChips(on_change=self._on_log_filter_change)
        self._log_pane = LogPane(
            title=" Stack startup · pipeline ",
            subtitle=" ctrl+c to cancel ",
        )
        self._log_pane.set_on_new_source(self._log_chips.add_source)
        await lower.mount(self._log_chips)
        await lower.mount(self._log_pane)

        self._footer.update_hints(_STARTUP_HINTS)

        # The session log was opened in __init__ so it could capture
        # wizard-time warnings. Now that the log pane exists, surface
        # the path as the first user-visible line so the operator can
        # find it on disk.
        if self._launch_log_path is not None and self._log_pane is not None:
            self._log_pane.write_log(
                f"📝 session log: {self._launch_log_path}",
                level="info", source="pipeline",
            )

        if self._starter is None:
            return  # wizard-only mode (tests)

        self.run_worker(
            self._run_pipeline_and_stream(),
            exclusive=False, exit_on_error=False,
        )

    # ─── launch-log tee ──────────────────────────────────────────────

    def _open_launch_log_tee(self, *, announce_in_pane: bool = True) -> None:
        """Open ``/tmp/atlas-launch-<ts>-<unique>.log`` for the duration
        of the wizard. _write_status / _safe_log mirror their output
        into this file so a user who quits the wizard still has a
        record of what happened (cloud /v1/models fetch failures during
        setup, plus everything compose printed during launch).

        ``announce_in_pane=False`` is used for the early call from
        ``__init__`` — the log pane doesn't exist yet there. The
        launch transition does the announce later when the pane is up.
        """
        import datetime
        ts = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
        fh = None
        path = None
        try:
            fh = tempfile.NamedTemporaryFile(
                mode="w",
                buffering=1,
                encoding="utf-8",
                prefix=f"atlas-launch-{ts}-",
                suffix=".log",
                dir=tempfile.gettempdir(),
                delete=False,
            )
            path = Path(fh.name)
            self._launch_log_path = path
            self._launch_log_fh = fh
            fh.write(f"# atlas session log — started {ts}\n")
            fh.flush()
            if announce_in_pane and self._log_pane is not None:
                self._log_pane.write_log(
                    f"📝 session log: {path}",
                    level="info", source="pipeline",
                )
        except OSError as exc:  # noqa: BLE001
            if fh is not None:
                with contextlib.suppress(OSError):
                    fh.close()
            if path is not None:
                with contextlib.suppress(OSError):
                    path.unlink()
            self._launch_log_fh = None
            self._launch_log_path = None
            if self._log_pane is not None:
                self._log_pane.write_log(
                    f"⚠ could not open launch log file: {exc}",
                    level="warn", source="pipeline",
                )

    def _close_launch_log_tee(self) -> None:
        fh = getattr(self, "_launch_log_fh", None)
        if fh is not None:
            try:
                fh.close()
            except Exception:  # noqa: BLE001
                pass
            self._launch_log_fh = None
        # Drop the wizard warn sink so a stale screen reference can't
        # leak into a future invocation.
        try:
            from .. import integration as _integration_mod
            _integration_mod._set_wizard_warn_sink(None)
        except Exception:  # noqa: BLE001
            pass

    def _tee_to_log(self, msg: str, *, source: str = "", level: str = "") -> None:
        """Append a line to the launch-log file. Best-effort, no raise."""
        fh = getattr(self, "_launch_log_fh", None)
        if fh is None:
            return
        try:
            prefix = ""
            if source or level:
                prefix = f"[{level or '·'}/{source or '·'}] "
            fh.write(prefix + (msg or "") + "\n")
        except Exception:  # noqa: BLE001
            pass

    async def _capture_failure_compose_logs(self) -> None:
        """On compose-up failure, dump ``docker compose logs --tail=200``
        for every service into the launch-log file. The output is NOT
        echoed to the live log pane (too noisy); only the tee file gets it.
        """
        fh = getattr(self, "_launch_log_fh", None)
        if fh is None or self._starter is None:
            return
        try:
            cmd = self._starter.docker_manager._build_compose_command(
                ["logs", "--no-color", "--tail=200"],
                top_level_flags=[],
            )
        except Exception:  # noqa: BLE001
            return
        fh.write("\n# ─── docker compose logs --tail=200 (post-failure) ───\n")
        fh.write(f"# {' '.join(cmd)}\n\n")
        fh.flush()
        try:
            returncode, captured = await _capture_bounded_process_output(
                cmd,
                cwd=Path(self._starter.docker_manager.root_dir),
                sink=fh.write,
                timeout_seconds=_FAILURE_LOG_TIMEOUT_SECONDS,
            )
            if returncode == 124:
                fh.write(
                    f"# capture timed out after {_FAILURE_LOG_TIMEOUT_SECONDS:.0f}s\n"
                )
            fh.flush()
            self._emit_failure_hints(captured)
        except Exception as exc:  # noqa: BLE001
            try:
                fh.write(f"# capture failed: {exc}\n")
                fh.flush()
            except Exception:  # noqa: BLE001
                pass

    def _emit_failure_hints(self, log_lines: list[str]) -> None:
        """Surface a recovery hint in the live log pane when the
        captured docker-compose output reveals a well-known failure
        mode (stale volume, port conflict, etc.).

        Pattern → hint mapping kept intentionally narrow: only mention
        recoveries the user can act on directly. We never silently
        wipe state; we tell them exactly which command to run.
        """
        joined = "\n".join(log_lines).lower()

        # Stale supabase-db volume: pgdata initialized with an old
        # password, current .env now has a different one. Postgres
        # won't re-init existing pgdata, so the stored creds and the
        # .env creds diverge until the volume is wiped.
        if (
            "password authentication failed" in joined
            and "supabase_admin" in joined
        ):
            self._write_status(
                "🔧 Stale supabase-db volume detected.",
                style="bold yellow", source="pipeline",
            )
            self._write_status(
                "   The DB was initialized with a different password "
                "than the current .env (likely from an earlier failed run).",
                style="yellow", source="pipeline",
            )
            self._write_status(
                "   Recovery: ./start.sh --cold "
                "(wipes volumes + re-initializes from scratch).",
                style="bold cyan", source="pipeline",
            )
            return

        # Bind-mount permission denied. Containers (especially init
        # ones running as root) can leave host dirs root-owned, then
        # subsequent runs fail because the new container can't write
        # to its own bind mount. ``_ensure_volume_dir_writable`` covers
        # the litellm/kong cases proactively; this hint catches the
        # generic case where some other volume dir is locked.
        if (
            "permission denied" in joined
            and (
                "config.yaml.tmp" in joined
                or "/litellm-config/" in joined
                or "/kong-config/" in joined
                or "errno 13" in joined
            )
        ):
            self._write_status(
                "🔧 Bind-mount permission denied — a prior container left "
                "a host directory root-owned. Recovery: "
                "`sudo chmod -R 777 volumes/` and re-run ./start.sh, "
                "or `./start.sh --cold` to wipe state entirely.",
                style="bold yellow", source="pipeline",
            )
            return

        # Generic auth failure on a non-supabase service (less common).
        if "authentication failed" in joined or "password authentication" in joined:
            self._write_status(
                "🔧 Authentication failed for one or more services — "
                "likely a stale volume from a prior run. "
                "Try ./start.sh --cold to wipe and re-initialize.",
                style="bold yellow", source="pipeline",
            )

    def _on_log_filter_change(self, level: str, disabled: set[str]) -> None:
        if self._log_pane is not None:
            self._log_pane.set_filter(level, disabled)

    def action_filter_all(self) -> None:
        if self._phase == "launch" and self._log_chips is not None:
            self._log_chips.clear_filters()

    def action_filter_errors(self) -> None:
        if self._phase == "launch" and self._log_chips is not None:
            self._log_chips.set_level("error")

    def action_filter_warns(self) -> None:
        if self._phase == "launch" and self._log_chips is not None:
            self._log_chips.set_level("warn")

    def action_filter_info(self) -> None:
        if self._phase == "launch" and self._log_chips is not None:
            self._log_chips.set_level("info")

    def action_filter_sources(self) -> None:
        if self._phase == "launch" and self._log_chips is not None:
            self._log_chips.toggle_source_picker()

    # ─── pipeline + docker compose runner ────────────────────────────

    def _write_status(self, msg: str, *, style: str = "", source: str = "pipeline") -> None:
        if self._log_pane is None:
            return
        text = Text(msg)
        if style:
            text.stylize(style)
        level = "info"
        if "red" in style: level = "error"
        elif "yellow" in style: level = "warn"
        elif "green" in style: level = "ok"
        elif "cyan" in style: level = "info"
        elif "dim" in style: level = "dim"
        self._log_pane.write_styled(text, level=level, source=source)
        self._tee_to_log(msg, source=source, level=level)

    def _safe_log(self, msg: str, *, source: str = "pipeline", level: str = "info") -> None:
        """Write a log line to the pane + tee file, from any thread.

        Two contexts call this:
          • Pipeline-step callbacks invoked via ``asyncio.to_thread`` —
            they run in a worker thread; UI writes must marshal back
            to the main loop with ``call_from_thread``.
          • ``_run_compose`` — this is a coroutine
            on the main event loop; ``call_from_thread`` from the
            same thread silently mis-routes (no UI update). Use a
            direct call instead.

        Always tee to the launch-log file regardless of thread.
        """
        import threading
        self._tee_to_log(msg, source=source, level=level)
        if self._log_pane is None:
            return
        on_main = threading.current_thread() is threading.main_thread()
        try:
            if on_main:
                self._log_pane.write_log(msg, level=level, source=source)
            else:
                self.app.call_from_thread(
                    self._log_pane.write_log, msg, level=level, source=source,
                )
        except Exception:  # noqa: BLE001
            pass

    async def _run_pipeline_and_stream(self) -> None:
        starter = self._starter
        cold = bool((self._stack_options or {}).get("cold", False))
        from core.config_parser import DEFAULT_BASE_PORT
        base_port = int(
            (self._stack_options or {}).get("base_port") or DEFAULT_BASE_PORT
        )
        setup_hosts = bool((self._stack_options or {}).get("setup_hosts", False))
        skip_hosts = bool((self._stack_options or {}).get("skip_hosts", False))

        original_banner = getattr(starter, "banner", None)
        starter.banner = _NullBanner()

        # Persist the wizard's chosen PROJECT_NAME (from the project-name step)
        # BEFORE anything reads get_project_name() or runs compose, so the whole
        # launch — and a later bare ./stop.sh — target this container family.
        # (Banner is the NullBanner here, so the persist's status line is
        # suppressed and doesn't corrupt the Textual chrome.)
        _proj = (self._stack_options or {}).get("project_name")
        if _proj:
            if not starter._persist_project_name(_proj):
                self._write_status(
                    "  ✗ Persist project name failed",
                    style="bold red",
                    source="pipeline",
                )
                return

        # Tell the compose-line classifier the actual project name so
        # container names like ``<project>-supabase-db-1`` collapse to
        # ``supabase-db`` and don't appear as duplicate sources in the
        # filter dropdown next to the bare names.
        try:
            set_project_prefix(starter.config_parser.get_project_name())
        except Exception:  # noqa: BLE001
            pass

        # Route any subprocess output the docker_manager / hosts_manager
        # would otherwise print to the terminal directly into the LogPane,
        # so the Textual chrome doesn't get corrupted by stray writes.
        try:
            starter.docker_manager.set_command_echo_callback(
                lambda msg: self._safe_log(msg, source="docker", level="info")
            )
        except Exception:  # noqa: BLE001
            pass
        try:
            starter.hosts_manager.set_logger(
                lambda msg, level="info": self._safe_log(
                    msg, source="hosts",
                    level={"warning": "warn", "success": "ok"}.get(level, level),
                )
            )
        except Exception:  # noqa: BLE001
            pass

        # Capture any stray ``print()`` calls from starter helpers
        # (port_manager, key_generator, etc.) and route them to the LogPane.
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout = _LogPaneWriter(self, source="pipeline", level="info")
        sys.stderr = _LogPaneWriter(self, source="pipeline", level="warn")

        # CRITICAL: ``docker_manager.execute_compose_command`` runs
        # ``subprocess.run`` WITHOUT redirecting stdout — its docker compose
        # output goes straight to the terminal fd and overwrites the
        # Textual screen. Wrap it for the duration of the pipeline so its
        # output gets piped into the LogPane like every other compose call.
        original_execute = starter.docker_manager.execute_compose_command

        def _patched_execute(args, use_env_file=True, project_name=None):
            # ``--ansi=never`` — stdout here is a Popen pipe; with
            # ``--ansi=always`` compose tries to attach a TTY-based
            # progress console and fails with "failed to get console:
            # provided file is not a console". Same fix as _run_compose.
            previous_project = starter.docker_manager.project_name_override
            if project_name:
                starter.docker_manager.project_name_override = project_name
            full_cmd = starter.docker_manager._build_compose_command(
                args, top_level_flags=["--ansi=never"],
            )
            starter.docker_manager.project_name_override = previous_project
            self._safe_log("$ " + " ".join(full_cmd), source="docker", level="dim")
            try:
                import subprocess as _sp
                proc = _sp.Popen(
                    full_cmd,
                    cwd=str(starter.docker_manager.root_dir),
                    stdin=_sp.DEVNULL, stdout=_sp.PIPE, stderr=_sp.STDOUT,
                    text=True, bufsize=1, encoding="utf-8", errors="replace",
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    line = line.rstrip("\r\n")
                    if line:
                        src, lvl = _classify_compose_line(line)
                        self._safe_log(line, source=(src or "docker"), level=lvl)
                return proc.wait()
            except Exception as exc:  # noqa: BLE001
                self._safe_log(f"❌ {exc}", source="docker", level="error")
                return 1

        starter.docker_manager.execute_compose_command = _patched_execute

        # Resolve the deployment profile from stack_options (set by
        # _selections_to_args from the wizard's PROFILE_STEP_TITLE selection,
        # or by run_launch_flow from the CLI --profile flag). Defaults to
        # "default" so the no-profile path is a no-op.
        _resolved_profile = ((self._stack_options or {}).get("profile") or "default")
        starter.profile = _resolved_profile

        def _validate_sources_for_profile() -> bool:
            """Profile-source compatibility check for the TUI pipeline.

            Mirrors the same guard in the linear path (start.py) so that
            a ``--profile prod`` TUI launch with a dev-only source (e.g.
            ollama-localhost) that was left in .env by a track-skipped
            wizard step fails fast with a clear message instead of
            proceeding silently.  No-ops for non-prod profiles.
            """
            if _resolved_profile != "prod":
                return True
            svc_srcs = starter.config_parser.parse_service_sources()
            starter.source_validator.validation_errors = []
            ok = starter.source_validator.validate_sources_for_profile(
                svc_srcs, _resolved_profile
            )
            if not ok:
                starter.source_validator.print_validation_results()
            return ok

        def _assert_no_placeholder_secrets() -> bool:
            """Prod-launch gate for the TUI pipeline: refuse to start if any
            managed secret still equals its shipped placeholder. Mirrors the
            linear path (start.py). Runs after key generation, so a normal
            first-run auto-rotation happens first and does not trip it.
            No-ops for non-prod profiles."""
            if _resolved_profile != "prod":
                return True
            try:
                starter.key_generator.assert_no_placeholders_remaining()
                return True
            except RuntimeError as exc:
                self._safe_log(str(exc), source="pipeline", level="warn")
                return False

        steps = [
            ("Apply source overrides",
             lambda: starter.apply_source_overrides(**(self._source_args or {}))),
            # Apply prod-profile env overrides after source overrides so the
            # observability default-ON only fires when the user didn't set
            # prometheus/grafana sources explicitly. The explicit_prometheus/
            # explicit_grafana guards replicate start.py's linear-path logic:
            # if the user already set them via source_args, those args were
            # already applied above, so we pass None here (helper skips the
            # default-ON write). The TUI wizard merges user choices into
            # source_args so we can safely read from there.
            ("Apply profile overrides",
             lambda _p=_resolved_profile: starter.apply_profile_overrides(
                 _p,
                 explicit_prometheus=(self._source_args or {}).get("prometheus_source"),
                 explicit_grafana=(self._source_args or {}).get("grafana_source"),
             )),
            # Validate that no dev-only source (e.g. ollama-localhost) is active
            # under --profile prod. This mirrors the linear-path check in start.py
            # but was absent from the TUI pipeline: a track-skipped service step
            # could leave a localhost source in .env and proceed silently.
            # Runs only when profile=prod; default profile allows all sources.
            # Runs AFTER Apply profile overrides so the .env already reflects any
            # prod-default source changes before we read it back.
            ("Validate sources for profile",
             _validate_sources_for_profile),
            ("Apply cloud API keys",
             lambda: starter.apply_cloud_api_keys(
                 (self._stack_options or {}).get("cloud_api_keys", {})
             )),
            ("Apply user model selections",
             lambda: starter.apply_user_model_selections({
                 **((self._stack_options or {}).get("cloud_user_models", {}) or {}),
                 **((self._stack_options or {}).get("ollama_user_models", {}) or {}),
                 **((self._stack_options or {}).get("comfyui_user_models", {}) or {}),
                 # Default-model picker (B3): LITELLM_DEFAULT_MODEL,
                 # LITELLM_EMBEDDING_MODEL, LITELLM_VISION_MODEL.
                 **((self._stack_options or {}).get("default_model_selections", {}) or {}),
                 # CLI-launch catch-all: COMFYUI_CUSTOM_MODELS_FILE,
                 # RAY_WORKER_COUNT, PROMETHEUS_RETENTION_DAYS,
                 # SPARK_WORKER_COUNT — flags that don't match the wizard's
                 # cosmetic *_USER_MODELS / OLLAMA_* bucket patterns.
                 # Without this bucket they'd be silently dropped on the
                 # ./start.sh --flag <value> path while TUI is active.
                 **((self._stack_options or {}).get("user_env_writes", {}) or {}),
             })),
            ("Validate source configurations",
             starter.validate_source_configurations),
            # Always clear any port env vars left over from a previous
            # session BEFORE port configuration runs, so the new
            # base-port assignments aren't shadowed by stale exports.
            ("Clear stale port environment",
             lambda: (starter.unset_port_environment_variables() or True)),
            ("Configure ports",
             lambda: starter.handle_port_configuration(base_port)),
            # Secrets MUST be generated/rotated BEFORE any step that bakes a
            # secret into a derived config value. generate_service_configuration
            # embeds SUPABASE_DB_PASSWORD / GRAPH_DB_PASSWORD / REDIS_PASSWORD into
            # LIGHTRAG_PG_URI / LIGHTRAG_NEO4J_PASSWORD / LIGHTRAG_REDIS_URI, and
            # generate_kong/litellm configuration embed their own keys. If those
            # ran first and a password then rotated (cold start, or a first-run
            # placeholder→real upgrade), the derived value would carry the STALE
            # secret while the DB volume is initdb'd with the NEW one — the
            # client (e.g. lightrag-init) then hits "password authentication
            # failed for user supabase_admin" against a perfectly fresh volume.
            ("Validate Supabase keys",
             lambda: starter.validate_supabase_keys(cold_start=cold)),
            ("Generate encryption keys",
             lambda: starter.generate_encryption_keys(cold_start=cold)),
            ("Generate service configuration",
             starter.generate_service_configuration),
            ("Check service dependencies",
             starter.check_service_dependencies),
            ("Generate Kong configuration",
             starter.generate_kong_configuration),
            ("Generate LiteLLM configuration",
             starter.generate_litellm_configuration),
            ("Generate ComfyUI manifest",
             starter.generate_comfyui_manifest),
            ("Configure hosts",
             lambda: starter.handle_hosts_configuration(setup_hosts, skip_hosts)),
            # Prod-launch gate: refuse to start under --profile prod if any
            # managed secret still equals its placeholder. After key gen, so
            # first-run auto-rotation passes cleanly. No-op for non-prod.
            ("Assert no placeholder secrets",
             _assert_no_placeholder_secrets),
            ("Validate localhost services",
             starter.validate_localhost_services),
            # Defensive final backfill — runs after every other
            # pipeline step has touched .env. Catches the edge case
            # where a user merged new entries into .env.example after
            # their .env was last fully written; if any earlier step
            # somehow regenerated .env from a parsed snapshot rather
            # than regex-replacing, the new vars (MINIO_IMAGE,
            # MINIO_PORT, etc.) would be missing at compose time and
            # ``docker compose up`` would fail with ``variable X not
            # set, defaulting to blank string``. Cheap when there's
            # nothing to add (a no-op tail append).
            ("Backfill .env from .env.example",
             starter.backfill_missing_env_vars),
        ]

        self._write_status("⚙ Running setup pipeline", style="bold cyan",
                           source="pipeline")
        managed_hosts_pending = False
        managed_host_start_task: asyncio.Task | None = None
        try:
            for i, (label, fn) in enumerate(steps, start=1):
                self._write_status(f"  · {label}…", style="dim",
                                   source="pipeline")
                try:
                    ok = await asyncio.to_thread(fn)
                except Exception as exc:  # noqa: BLE001
                    self._write_status(f"  ✗ {label} crashed: {exc}",
                                       style="bold red", source="pipeline")
                    self._mark_launch_failed()
                    return
                if not ok:
                    self._write_status(f"  ✗ {label} failed",
                                       style="bold red", source="pipeline")
                    self._mark_launch_failed()
                    return
                self._write_status(f"  ✓ {label}", style="bold green",
                                   source="pipeline")

            self._write_status("", style="", source="pipeline")

            self._write_status("  · Start managed host services…", style="dim",
                               source="pipeline")
            managed_hosts_pending = True
            managed_host_start_task = asyncio.create_task(
                asyncio.to_thread(starter.start_managed_host_processes)
            )
            if not await asyncio.shield(managed_host_start_task):
                self._write_status("  ✗ Start managed host services failed",
                                   style="bold red", source="pipeline")
                self._mark_launch_failed()
                return
            self._write_status("  ✓ Start managed host services", style="bold green",
                               source="pipeline")

            # Enabled-service target set from the rendered projection (#504):
            # Compose plans builds for the WHOLE graph when `up` has no
            # service list, so a broken build for a disabled service would
            # abort the launch. None (projection failure) → full graph.
            import asyncio as _asyncio
            targets = await _asyncio.to_thread(
                self._starter.docker_manager.enabled_service_targets
            )

            if cold:
                self._write_status("📦 Building images (cold start)…",
                                   style="bold cyan", source="pipeline")
                rc = await self._run_compose(["build", "--no-cache", *(targets or [])])
                if rc != 0:
                    self._write_status("❌ Build failed", style="bold red",
                                       source="pipeline")
                    self._mark_launch_failed()
                    return

            self._write_status("🚀 Starting containers…",
                               style="bold cyan", source="pipeline")
            # #506: rebuild stale local images after an in-place source upgrade
            # on the warm TUI launch too (the cold path already builds above);
            # `source_build_args()` is empty when the source commit is unchanged.
            # Kept on one line — the pipeline-step guards locate this call by the
            # literal `self._run_compose(["up"` substring.
            rc = await self._run_compose(["up", "-d", "--force-recreate", *self._starter.docker_manager.source_build_args(), *(targets or [])])
            if rc != 0:
                self._write_status("❌ Start failed — capturing per-service logs to launch log",
                                   style="bold red", source="pipeline")
                await self._capture_failure_compose_logs()
                self._write_status(
                    f"📝 see {self._launch_log_path or '/tmp/atlas-launch-*.log'} "
                    "for full output",
                    style="bold yellow", source="pipeline",
                )
                self._mark_launch_failed()
                return
            self._starter.docker_manager.mark_source_built()
            ok = await asyncio.to_thread(
                starter.verify_one_shot_init_containers,
                lambda msg, level="info": self._safe_log(
                    msg, source="pipeline", level=level,
                ),
            )
            if not ok:
                self._write_status(
                    "❌ Required init container failed — see launch log",
                    style="bold red",
                    source="pipeline",
                )
                self._mark_launch_failed()
                return
            starter.commit_managed_host_processes()
            managed_hosts_pending = False
            self._write_status("✅ All services started",
                               style="bold green", source="pipeline")
            self._launch_detach_ready = True

            if self._log_pane is not None:
                self._log_pane.set_title(
                    " Live docker logs ",
                    subtitle=" ctrl+q to detach ",
                )
            self._footer.update_hints(_LAUNCH_HINTS)

            # Kick off port verification + ComfyUI model check in the
            # background so the live log stream starts IMMEDIATELY rather
            # than waiting for ~18 sequential subprocess calls.
            def _on_verify_line(msg: str, level: str) -> None:
                self._safe_log(msg, source="verify", level=level)

            async def _post_up_checks() -> None:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(
                        starter.show_container_status_and_verify_ports,
                        on_line=_on_verify_line,
                    )
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(
                        starter.check_comfyui_models, on_line=_on_verify_line
                    )

            self.run_worker(_post_up_checks(), exclusive=False, exit_on_error=False)

            self._write_status(
                "📋 Streaming docker logs · ctrl+q to detach",
                style="bold cyan", source="pipeline",
            )
            await self._run_compose(["logs", "-f"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            # The pipeline runs in an exit_on_error=False worker: without
            # this catch, a crash died silently and the UI froze at the
            # last status line with nothing in the log pane or tee file.
            import traceback
            self._write_status(
                f"❌ launch crashed: {exc}", style="bold red", source="pipeline",
            )
            for tb_line in traceback.format_exc().splitlines():
                self._safe_log(tb_line, source="pipeline", level="error")
            self._mark_launch_failed()
        finally:
            # asyncio.to_thread cannot cancel its worker. If the Textual worker
            # is cancelled during native startup, wait for that thread to
            # settle before touching the ownership ledger and rolling back.
            if managed_host_start_task is not None:
                with contextlib.suppress(Exception):
                    await _await_uncancellable(managed_host_start_task)
            if managed_hosts_pending:
                rollback_task = asyncio.create_task(
                    asyncio.to_thread(starter.rollback_managed_host_processes)
                )
                rollback_ok = await _await_uncancellable(rollback_task)
                if not rollback_ok:
                    self._write_status(
                        "❌ Managed host rollback incomplete; run ./stop.sh and "
                        "inspect the managed-host logs.",
                        style="bold red",
                        source="pipeline",
                    )
            starter.banner = original_banner
            try:
                starter.docker_manager.set_command_echo_callback(print)
            except Exception:  # noqa: BLE001
                pass
            try:
                starter.hosts_manager.set_logger(None)
            except Exception:  # noqa: BLE001
                pass
            try:
                starter.docker_manager.execute_compose_command = original_execute
            except Exception:  # noqa: BLE001
                pass
            sys.stdout, sys.stderr = old_stdout, old_stderr
            self._close_launch_log_tee()

    async def _run_compose(self, args: list[str]) -> int:
        # IMPORTANT: stdout is a pipe (no TTY). Don't pass
        # --ansi=always — it tells compose "I have a TTY, use the
        # animated progress display". Buildkit then tries to attach
        # a console to the pipe and fails with
        #   "failed to get console: provided file is not a console"
        # → compose up exits 1 with no containers created.
        # Default ``--ansi=auto`` correctly picks no-ANSI when piped,
        # which also picks plain progress automatically. We lose
        # Docker's own coloring in the log pane, but our level-based
        # coloring still works via _classify_compose_line.
        full_cmd = self._starter.docker_manager._build_compose_command(
            args, top_level_flags=["--ansi=never"],
        )
        env = {**os.environ, "BUILDKIT_PROGRESS": "plain"}
        # Route through _safe_log so every compose line lands in the
        # launch-log tee (/tmp/atlas-launch-*.log). Direct
        # _log_pane.write_* calls bypassed the tee — image-pull errors
        # from compose up never reached the file.
        self._safe_log("$ " + " ".join(full_cmd), source="docker", level="dim")

        def log_line(line: str) -> None:
            source, level = _classify_compose_line(line)
            self._safe_log(line, source=source, level=level)

        timeout_seconds = _compose_timeout_seconds(args)
        try:
            return await _run_streamed_command(
                full_cmd,
                cwd=Path(self._starter.docker_manager.root_dir),
                env=env,
                on_line=log_line,
                timeout_seconds=timeout_seconds,
            )
        except (OSError, RuntimeError) as exc:
            # OSError covers PermissionError etc., not just a missing
            # binary. (Compose-detection RuntimeErrors raise BEFORE this
            # try — they surface via the pipeline-level "launch crashed"
            # handler instead.)
            self._write_status(f"❌ {exc}", style="bold red", source="docker")
            return 1


# Module-level project prefix — set at launch transition via
# ``set_project_prefix()`` from the actual ConfigParser.get_project_name()
# value, so containers like ``<project_name>-supabase-db-1`` are stripped
# down to ``supabase-db`` regardless of how the user named their stack.
_PROJECT_PREFIX = "atlas-"


def set_project_prefix(project_name: str) -> None:
    """Update the project-name prefix used by ``_strip_project``."""
    global _PROJECT_PREFIX
    if project_name:
        _PROJECT_PREFIX = f"{project_name.strip().rstrip('-')}-"


_ANSI_RE = __import__("re").compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(s: str) -> str:
    """Drop ANSI color/style escape sequences. ``compose --ansi=always``
    wraps service names in color codes that would otherwise defeat the
    project-prefix string match below."""
    return _ANSI_RE.sub("", s)


def _strip_project(name: str) -> str:
    """Strip project prefix + trailing replica suffix from a compose container name.

    Handles both Compose v2 (``<project>-<service>-1``, hyphen-joined)
    and v1 (``<project>_<service>_1``, underscore-joined). Also strips
    any embedded ANSI color escape sequences before matching.

    ``<project>-supabase-db-1`` → ``supabase-db``
    ``<project>_supabase-db_1`` → ``supabase-db``
    ``<project>-ollama-pull``   → ``ollama-pull``
    """
    n = _strip_ansi(name).strip()
    # Try the active project prefix in both -/_ separators.
    base = _PROJECT_PREFIX.rstrip("-_")
    for prefix in (f"{base}-", f"{base}_"):
        if n.startswith(prefix):
            n = n[len(prefix):]
            break
    # Drop trailing -<digit>+ or _<digit>+ replica suffix.
    for sep in ("-", "_"):
        if sep in n:
            head, _, tail = n.rpartition(sep)
            if tail.isdigit() and head:
                n = head
                break
    return n


def _classify_compose_line(line: str) -> tuple[str, str]:
    """Best-effort (source, level) classification for a docker compose line.

    Three common shapes:
      * ``service-1  | message`` — from ``compose logs -f``
      * ``Container atlas-svc-1  Created`` — from ``compose up/down``
      * ``Network atlas-network  Created`` — from ``compose up/down``

    Level is sniffed from common keywords (ERROR/WARN/etc).
    """
    src = ""
    rest = line
    if "|" in line:
        head, _, rest = line.partition("|")
        head_word = head.strip().split()[0] if head.strip() else ""
        src = _strip_project(head_word)
        rest = rest.strip()
    else:
        words = line.strip().split()
        if len(words) >= 2 and words[0] in ("Container", "Network", "Volume"):
            src = _strip_project(words[1])
    upper = rest.upper()
    if "ERROR" in upper or " ERR " in upper or upper.startswith("ERR"):
        level = "error"
    elif "WARN" in upper or "WARNING" in upper:
        level = "warn"
    else:
        level = "info"
    return src, level


class _LogPaneWriter:
    """File-like sink that flushes whole lines into a WizardScreen's LogPane.

    Captures stray ``print()`` calls from pipeline helpers so they don't
    corrupt Textual's screen buffer.
    """

    def __init__(self, screen, *, source: str = "stdout", level: str = "info") -> None:
        self._screen = screen
        self._source = source
        self._level = level
        self._buf = ""

    def write(self, s: str) -> int:
        if not s:
            return 0
        self._buf += s
        while "\n" in self._buf:
            line, _, self._buf = self._buf.partition("\n")
            line = line.rstrip("\r")
            if line.strip():
                self._screen._safe_log(line, source=self._source, level=self._level)
        return len(s)

    def flush(self) -> None:
        if self._buf.strip():
            self._screen._safe_log(self._buf, source=self._source, level=self._level)
            self._buf = ""

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:  # pragma: no cover — required for some libs
        raise OSError("LogPaneWriter has no fileno")


class _NullSink:
    """A no-op stand-in returned for any attribute the NullBanner does not
    define (notably ``banner.console``). It swallows arbitrary attribute access
    AND calls, so chained expressions like ``banner.console.print(...)`` are
    safe no-ops while the real banner is swapped out inside the Textual app.

    Returning a bare ``lambda`` from ``__getattr__`` (the previous behavior)
    handled ``banner.foo()`` but crashed on ``banner.console.print(...)`` with
    ``'function' object has no attribute 'print'`` — the pipeline reaches the
    latter via ``starter.banner.console.print(...)`` (e.g. the embedding
    dimension warning in ``apply_user_model_selections``).
    """

    def __call__(self, *args, **kwargs): return None
    def __getattr__(self, name): return self


_NULL_SINK = _NullSink()


class _NullBanner:
    """Drop-in for ``starter.banner`` that swallows pipeline status messages
    so they don't print to stdout while we're inside the Textual app."""

    def show_status_message(self, *args, **kwargs) -> None: ...
    def show_section_header(self, *args, **kwargs) -> None: ...
    def show_subsection_header(self, *args, **kwargs) -> None: ...
    def log(self, *args, **kwargs) -> None: ...
    def status(self, *args, **kwargs) -> None: ...
    def section(self, *args, **kwargs) -> None: ...
    # Undefined attributes (e.g. ``console``) resolve to a sink that swallows
    # both further attribute access and calls — see _NullSink.
    def __getattr__(self, name): return _NULL_SINK
