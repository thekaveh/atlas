"""Copy affordances for the log pane.

LogPane subclasses RichLog, a scrolling container. Under Textual 6.x,
`allow_select = ALLOW_SELECT and not is_container` meant a scrolling
container could never drag-select; Textual 8.x dropped that gate, so the
log pane is now selectable by design — the originating request for the
Textual 8.x upgrade. These explicit copy actions remain because they are
filter-aware (they match whichever level/source chips are active, which
a drag-select can't know about), they capture the whole scrollback
rather than only whatever you can physically drag over, and they're
keyboard-driven; the command summary and stack overview are separately
selectable via their inner Static widgets.

Round 2 additions (post-review): ``visible_text()`` must respect the active
level/source filter (Important 2), ``action_copy_session_log`` must offload
its file read off the UI thread and cap it (Important 1), and both new
``WizardScreen`` actions need direct coverage (Important 3).
"""
from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

import pytest  # noqa: E402
from textual.app import App, ComposeResult  # noqa: E402
from ui.session_log import SessionLogConfig, SessionLogTee  # noqa: E402

from ui.textual.screens.wizard_screen import (  # noqa: E402
    SESSION_LOG_COPY_CAP_BYTES,
    WizardScreen,
    read_session_log_tail,
)
from ui.textual.widgets.log_pane import LogPane  # noqa: E402
from ui.textual.widgets.prompt_panel import PromptOption, PromptStep  # noqa: E402


class _App(App):
    def compose(self) -> ComposeResult:
        yield LogPane(title=" logs ")


def test_visible_text_joins_the_buffered_records():
    async def scenario():
        async with _App().run_test(size=(100, 20)) as pilot:
            pane = pilot.app.query_one(LogPane)
            pane.write_log("supabase-db ready", level="info", source="supabase-db")
            pane.write_log("kong started", level="info", source="kong")
            await pilot.pause()
            return pane.visible_text()

    text = asyncio.run(scenario())

    assert "supabase-db ready" in text
    assert "kong started" in text
    assert text.count("\n") >= 1, "records must be newline-joined"


def test_visible_text_respects_the_active_level_filter():
    """Important 2: `y` must copy what's ON SCREEN, not the whole buffer —
    its hint sits directly beside the a/e/w/i/s filter chips."""
    async def scenario():
        async with _App().run_test(size=(100, 20)) as pilot:
            pane = pilot.app.query_one(LogPane)
            pane.write_log("supabase-db ready", level="info", source="supabase-db")
            pane.write_log("kong crashed", level="error", source="kong")
            pane.set_filter("error", set())
            await pilot.pause()
            return pane.visible_text()

    text = asyncio.run(scenario())

    assert "kong crashed" in text
    assert "supabase-db ready" not in text, (
        "filtered-out records must not appear in the copy"
    )


def test_log_pane_is_drag_selectable_since_textual_8():
    """Textual 6.x refused to drag-select scrolling containers
    (`allow_select = ALLOW_SELECT and not is_container`); Textual 8.x
    dropped that gate, so the log pane is drag-selectable like any other
    widget. Enabling drag-select on the live log pane was the originating
    request for the Textual 8.x upgrade, so this is the desired outcome,
    not a regression."""
    async def scenario():
        async with _App().run_test(size=(100, 20)) as pilot:
            return pilot.app.query_one(LogPane).allow_select

    assert asyncio.run(scenario()) is True


def test_reflow_is_a_no_op_when_nothing_was_written_while_hidden():
    """I1: reflow() must not pay for a rerender (and its scroll-to-bottom
    jump) on a reveal where nothing was actually written while hidden —
    only WizardScreen.show_tab() calls this, on every Logs-tab reveal."""
    async def scenario():
        async with _App().run_test(size=(100, 20)) as pilot:
            pane = pilot.app.query_one(LogPane)
            pane.write_log("a", level="info", source="x")
            await pilot.pause()
            rerender_calls = []
            pane._rerender = lambda: rerender_calls.append(1)
            pane.reflow()
            return rerender_calls

    assert asyncio.run(scenario()) == [], "reflow() must be a no-op when _wrap_dirty is False"


# ─── read_session_log_tail (module-level helper backing action_copy_session_log) ──

def _observed_log_path(path, *, before_open=None, before_read=None):
    """Observe real file I/O, with deterministic writer boundary hooks."""
    reads = []
    opens = []

    class Reader:
        def __init__(self, raw):
            self.raw = raw

        def __getattr__(self, name):
            return getattr(self.raw, name)

        def __enter__(self):
            self.raw.__enter__()
            return self

        def __exit__(self, *args):
            return self.raw.__exit__(*args)

        def read(self, size=-1):
            if before_read is not None:
                before_read()
            data = self.raw.read(size)
            reads.append((size, len(data)))
            return data

    class ObservedPath(type(Path())):
        def open(self, *args, **kwargs):
            opens.append(1)
            if before_open is not None:
                before_open()
            return Reader(super().open(*args, **kwargs))

    return ObservedPath(path), reads, opens


def _assert_raw_read_budget(reads, cap):
    assert all(0 <= requested <= cap for requested, _ in reads)
    assert sum(returned for _, returned in reads) <= cap


@pytest.mark.parametrize("case", [
    (b"", 8, "", False),
    (b"one\ntwo\n", 8, "one\ntwo\n", False),
    (b"one\n", 8, "one\n", False),
    (b"head\nlast\n", 6, "last\n", True),
    (b"head\nlast", 7, "last", True),
    (b"abcdefgh", 4, "efgh", True),
    ("界界".encode("utf-8"), 4, "�界", True),
    (b"\xff" * 64, 32, "�" * 32, True),
    (b"", 0, "", False),
    (b"nonempty", 0, "", True),
])
def test_session_log_snapshot_preserves_text_with_a_raw_byte_budget(tmp_path, case):
    data, cap, payload, truncated = case
    path = tmp_path / "session.log"
    path.write_bytes(data)
    observed, reads, opens = _observed_log_path(path)

    text = read_session_log_tail(observed, cap_bytes=cap)

    marker = f"… truncated, full log at {path}\n" if truncated else ""
    assert text == marker + payload
    assert opens == [1]
    _assert_raw_read_budget(reads, cap)


def test_session_log_negative_cap_is_rejected_before_file_io(tmp_path):
    path, reads, opens = _observed_log_path(tmp_path / "does-not-exist.log")

    with pytest.raises(ValueError):
        read_session_log_tail(path, cap_bytes=-1)

    assert opens == reads == []


@pytest.mark.parametrize("initial_size", ["small", "large"])
@pytest.mark.parametrize("timing", ["before_open", "before_read"])
def test_session_log_copy_stays_bounded_while_the_real_tee_appends(
    tmp_path, initial_size, timing,
):
    cap = 256
    tee = SessionLogTee(
        prefix="atlas-launch-copy-", config=SessionLogConfig(directory=str(tmp_path)),
    )
    tee.write("original line\n")
    if initial_size == "large":
        tee.write("old " + "x" * 1024 + "\nold tail\n")
    original = tee.base_path.read_bytes()
    ready, done = threading.Event(), threading.Event()
    errors = []

    def writer():
        try:
            assert ready.wait(5), "owned writer was not signalled"
            tee.write("new " + "y" * 8192 + "\nnew tail\n")
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()

    def append_now():
        ready.set()
        assert done.wait(5), "owned writer did not finish"
        assert not errors

    observed, reads, _ = _observed_log_path(tee.base_path, **{timing: append_now})
    thread = threading.Thread(target=writer, name="owned-session-log-writer")
    thread.start()
    try:
        text = read_session_log_tail(observed, cap_bytes=cap)
    finally:
        ready.set()
        thread.join(5)
        tee.close()
    assert not thread.is_alive()
    assert not errors
    assert done.is_set()
    _assert_raw_read_budget(reads, cap)
    marker = f"… truncated, full log at {tee.base_path}\n"
    original_window = {
        "small": original.decode("utf-8"), "large": marker + "old tail\n",
    }[initial_size]
    expected = marker + "new tail\n" if timing == "before_open" else original_window
    assert text == expected


@pytest.mark.parametrize("timing", ["before_open", "before_read"])
def test_session_log_copy_stays_bounded_when_the_file_is_truncated(tmp_path, timing):
    path = tmp_path / "session.log"
    path.write_bytes(b"old line\n" * 100)
    observed, reads, _ = _observed_log_path(
        path, **{timing: lambda: path.write_bytes(b"replacement\n")},
    )

    text = read_session_log_tail(observed, cap_bytes=32)

    _assert_raw_read_budget(reads, 32)
    if timing == "before_open":
        assert text == "replacement\n"
    else:
        assert text == f"… truncated, full log at {path}\n"

def test_read_session_log_tail_returns_full_text_when_under_the_cap(tmp_path):
    log_file = tmp_path / "session.log"
    log_file.write_text("line one\nline two\n", encoding="utf-8")

    text = read_session_log_tail(log_file, cap_bytes=SESSION_LOG_COPY_CAP_BYTES)

    assert text == "line one\nline two\n"


def test_read_session_log_tail_truncates_and_marks_when_over_the_cap(tmp_path):
    log_file = tmp_path / "session.log"
    line = "x" * 100
    total_lines = 200  # 200 * ~107 bytes ≈ 21 KB, comfortably over a tiny cap
    with log_file.open("w", encoding="utf-8") as fh:
        for i in range(total_lines):
            fh.write(f"{i:05d} {line}\n")

    cap = 2_000  # tiny cap so the test file (~21KB) is well over it
    text = read_session_log_tail(log_file, cap_bytes=cap)

    assert text.startswith(f"… truncated, full log at {log_file}\n")
    assert f"{total_lines - 1:05d}" in text, "must keep the END of the file (the tail)"
    assert "00000 " not in text, "must NOT keep the START of the file"


# ─── WizardScreen.action_copy_logs / action_copy_session_log ──────────────

class _WizardApp(App):
    def __init__(self, screen: WizardScreen) -> None:
        super().__init__()
        self._screen = screen

    def on_mount(self) -> None:
        self.push_screen(self._screen)


# M5: WizardScreen.__init__ opens /tmp/atlas-launch-*.log eagerly via
# NamedTemporaryFile(delete=False) — every screen built via _screen() below
# leaks that fd + file unless closed and unlinked. Mirrors the close+unlink
# pattern in test_tui_launch_log.py. Captured at construction time (not at
# teardown): several tests here reassign scr._launch_log_path mid-test, so
# the ORIGINAL path must be recorded here to still be reachable for cleanup.
_OPEN_SCREEN_LOGS: list[tuple[WizardScreen, Path | None]] = []


@pytest.fixture(autouse=True)
def _cleanup_launch_log_tees():
    yield
    while _OPEN_SCREEN_LOGS:
        scr, path = _OPEN_SCREEN_LOGS.pop()
        scr._close_launch_log_tee()
        if path is not None:
            path.unlink(missing_ok=True)


def _screen() -> WizardScreen:
    # Mirrors tests/test_wizard_tabs.py's _screen() helper: a single dummy
    # step exercises the real (non-auto-launch) setup path.
    step = PromptStep(
        title="Dummy", step_index=1, step_total=1,
        heading="Dummy step", subtitle="",
        options=[PromptOption(value="a", label="A"), PromptOption(value="b", label="B")],
        default_value="a",
    )
    scr = WizardScreen(steps=[step], services=[], no_splash=True)
    _OPEN_SCREEN_LOGS.append((scr, scr._launch_log_path))
    return scr


def test_action_copy_logs_copies_only_the_filtered_records():
    scr = _screen()
    clipboard: list[str] = []

    async def scenario():
        async with _WizardApp(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            # action_copy_logs gates on _phase == "launch" (M3: matches
            # a/e/w/i/s, so a stray `y` mid-wizard can't clobber the
            # clipboard — see the cold-start/hosts steps).
            scr._phase = "launch"
            pilot.app.copy_to_clipboard = clipboard.append
            scr._log_pane.write_log("supabase-db ready", level="info", source="supabase-db")
            scr._log_pane.write_log("kong crashed", level="error", source="kong")
            scr._log_pane.set_filter("error", set())
            scr.action_copy_logs()
            await pilot.pause()

    asyncio.run(scenario())

    assert len(clipboard) == 1
    assert "kong crashed" in clipboard[0]
    assert "supabase-db ready" not in clipboard[0]


def test_action_copy_logs_is_a_no_op_before_launch():
    """M3: `y` mid-wizard must not clobber the clipboard — it's a
    plausible "yes" keystroke on the cold-start/hosts steps."""
    scr = _screen()
    clipboard: list[str] = []

    async def scenario():
        async with _WizardApp(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            assert scr._phase == "setup"
            pilot.app.copy_to_clipboard = clipboard.append
            scr._log_pane.write_log("supabase-db ready", level="info", source="supabase-db")
            scr.action_copy_logs()
            await pilot.pause()

    asyncio.run(scenario())

    assert clipboard == []


def test_action_copy_session_log_is_a_no_op_before_launch():
    """M3: `Y` mid-wizard must not spawn a worker or touch the clipboard."""
    scr = _screen()
    clipboard: list[str] = []

    async def scenario():
        async with _WizardApp(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            assert scr._phase == "setup"
            assert scr._launch_log_path is not None, "tee opens eagerly in __init__"
            pilot.app.copy_to_clipboard = clipboard.append
            scr.action_copy_session_log()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(scenario())

    assert clipboard == []


def test_action_copy_session_log_with_no_log_yet_notifies_and_does_not_raise():
    scr = _screen()
    notifications: list[tuple[str, dict]] = []
    clipboard: list[str] = []

    async def scenario():
        async with _WizardApp(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._phase = "launch"
            scr.notify = lambda msg, **kw: notifications.append((msg, kw))
            pilot.app.copy_to_clipboard = clipboard.append
            # WizardScreen.__init__ opens the tee eagerly (announce_in_pane=
            # False), so a fresh screen already has a real _launch_log_path.
            # Simulate the "tee failed to open" state that leaves it None
            # (see _open_launch_log_tee's own except-OSError branch).
            scr._launch_log_path = None
            scr.action_copy_session_log()
            await pilot.pause()

    asyncio.run(scenario())

    assert clipboard == []
    assert len(notifications) == 1
    msg, kw = notifications[0]
    assert kw.get("severity") == "warning"


def test_action_copy_session_log_oserror_notifies_without_raising_or_leaking(tmp_path):
    scr = _screen()
    notifications: list[tuple[str, dict]] = []
    clipboard: list[str] = []

    async def scenario():
        async with _WizardApp(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._phase = "launch"
            scr.notify = lambda msg, **kw: notifications.append((msg, kw))
            pilot.app.copy_to_clipboard = clipboard.append
            # A directory is not readable as a file: opening it raises
            # IsADirectoryError, a subclass of OSError.
            scr._launch_log_path = tmp_path
            scr.action_copy_session_log()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(scenario())

    assert clipboard == [], "must not copy anything on failure"
    assert len(notifications) == 1
    msg, kw = notifications[0]
    assert kw.get("severity") == "error"
    assert str(tmp_path) not in msg, "must not leak the raw path/exception text"
    assert msg == "Could not read the session log: IsADirectoryError"


def test_action_copy_session_log_clipboard_failure_does_not_fail_the_launch(tmp_path):
    """M4: on_worker_state_changed's ERROR-state handler calls
    _mark_launch_failed() for ANY worker that reaches WorkerState.ERROR —
    a clipboard operation must never be able to make ./start.sh exit
    nonzero. Simulates copy_to_clipboard itself raising (e.g. a driver
    quirk), which must be contained inside the worker, not escape it.
    """
    scr = _screen()
    launch_results: list[int] = []
    scr._on_launch_result = launch_results.append
    notifications: list[tuple[str, dict]] = []
    log_file = tmp_path / "session.log"
    log_file.write_text("line one\n", encoding="utf-8")

    def _boom(_text: str) -> None:
        raise RuntimeError("clipboard driver exploded")

    async def scenario():
        async with _WizardApp(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._phase = "launch"
            scr.notify = lambda msg, **kw: notifications.append((msg, kw))
            pilot.app.copy_to_clipboard = _boom
            scr._launch_log_path = log_file
            scr.action_copy_session_log()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(scenario())

    assert launch_results == [], (
        "a clipboard failure must not call _mark_launch_failed() / "
        "set a nonzero exit code"
    )
    assert len(notifications) == 1
    msg, kw = notifications[0]
    assert kw.get("severity") == "error"
    assert "clipboard driver exploded" not in msg, "must not leak the raw exception text"
    assert msg == "Could not copy the session log: RuntimeError"


def test_action_copy_session_log_copies_full_contents_when_under_the_cap(tmp_path):
    scr = _screen()
    notifications: list[tuple[str, dict]] = []
    clipboard: list[str] = []
    log_file = tmp_path / "session.log"
    log_file.write_text("line one\nline two\n", encoding="utf-8")

    async def scenario():
        async with _WizardApp(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._phase = "launch"
            scr.notify = lambda msg, **kw: notifications.append((msg, kw))
            pilot.app.copy_to_clipboard = clipboard.append
            scr._launch_log_path = log_file
            scr.action_copy_session_log()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(scenario())

    assert clipboard == ["line one\nline two\n"]
    assert len(notifications) == 1
    msg, kw = notifications[0]
    assert str(log_file) in msg
    assert kw.get("severity") not in ("error", "warning")


def test_session_log_worker_reads_off_the_ui_thread_and_keeps_it_responsive(
    tmp_path, monkeypatch,
):
    from ui.textual.screens import wizard_screen

    scr = _screen()
    path = tmp_path / "session.log"
    path.write_text("owned worker line\n", encoding="utf-8")
    started, release = threading.Event(), threading.Event()
    reader_threads = []
    copied = []
    actual_reader = read_session_log_tail

    def slow_reader(value):
        reader_threads.append(threading.get_ident())
        started.set()
        assert release.wait(5), "owned read was not released"
        return actual_reader(value)

    monkeypatch.setattr(wizard_screen, "read_session_log_tail", slow_reader)

    async def scenario():
        async with _WizardApp(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._phase = "launch"
            scr._launch_log_path = path
            scr.notify = lambda *args, **kwargs: None
            pilot.app.copy_to_clipboard = copied.append
            ui_thread = threading.get_ident()
            scr.action_copy_session_log()
            try:
                assert await asyncio.to_thread(started.wait, 5)
                heartbeat = asyncio.get_running_loop().create_future()
                asyncio.get_running_loop().call_soon(heartbeat.set_result, True)
                assert await asyncio.wait_for(heartbeat, 1)
                assert reader_threads and reader_threads[0] != ui_thread
                assert copied == []
            finally:
                release.set()
                await pilot.app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(scenario())
    assert copied == ["owned worker line\n"]


@pytest.mark.parametrize("read_fails", [False, True])
def test_session_log_worker_contains_notification_failure(tmp_path, read_fails):
    scr = _screen()
    path = tmp_path / "session.log"
    path.write_text("owned notification line\n", encoding="utf-8")
    copied, notices, logged, launch_results = [], [], [], []
    scr._on_launch_result = launch_results.append

    def broken_notify(message, **kwargs):
        notices.append((message, kwargs))
        raise RuntimeError("owned notification double failed")

    async def scenario():
        async with _WizardApp(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._phase = "launch"
            scr._launch_log_path = tmp_path if read_fails else path
            scr.notify = broken_notify
            scr._safe_log = lambda message, **kwargs: logged.append((message, kwargs))
            pilot.app.copy_to_clipboard = copied.append
            scr.action_copy_session_log()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(scenario())
    assert launch_results == []
    if read_fails:
        assert copied == []
        assert notices[0][0] == "Could not read the session log: IsADirectoryError"
    else:
        assert copied == ["owned notification line\n"]
        assert notices[-1][0] == "Could not copy the session log: RuntimeError"
        assert logged and "RuntimeError" in logged[0][0]


def test_action_copy_session_log_truncates_when_over_the_cap(tmp_path):
    # ``_copy_session_log_worker`` calls read_session_log_tail(path) with no
    # explicit cap_bytes, so it always uses the real default
    # (SESSION_LOG_COPY_CAP_BYTES) — a default bound at function-definition
    # time, so monkeypatching the module constant would NOT reach it.
    # Write a real fixture comfortably over the real cap instead.
    scr = _screen()
    clipboard: list[str] = []
    log_file = tmp_path / "session.log"
    line = "x" * 1000
    bytes_per_line = len(line) + 7  # 5-digit prefix + space + newline
    total_lines = (SESSION_LOG_COPY_CAP_BYTES // bytes_per_line) * 3
    with log_file.open("w", encoding="utf-8") as fh:
        for i in range(total_lines):
            fh.write(f"{i:05d} {line}\n")
    assert log_file.stat().st_size > SESSION_LOG_COPY_CAP_BYTES

    async def scenario():
        async with _WizardApp(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._phase = "launch"
            scr.notify = lambda *a, **kw: None
            pilot.app.copy_to_clipboard = clipboard.append
            scr._launch_log_path = log_file
            scr.action_copy_session_log()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(scenario())

    assert len(clipboard) == 1
    text = clipboard[0]
    marker = f"… truncated, full log at {log_file}\n"
    assert text.startswith(marker)
    assert f"{total_lines - 1:05d}" in text, "must contain the tail (end of file)"
    assert "00000 " not in text, "must NOT contain the head (start of file)"
    assert len(text.removeprefix(marker).encode("utf-8")) <= SESSION_LOG_COPY_CAP_BYTES, (
        "this ASCII fixture's log payload must stay within the raw byte cap"
    )


def test_quiet_session_path_loss_is_checked_before_clipboard_read(tmp_path):
    scr = _screen()
    launch_results = []
    scr._on_launch_result = launch_results.append
    tee = scr._launch_log_fh
    assert tee is not None
    tee.base_path.unlink()
    notifications = []

    async def scenario():
        async with _WizardApp(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._phase = 'launch'
            scr.notify = lambda message, **kwargs: notifications.append(message)
            scr.action_copy_session_log()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(scenario())
    assert tee.degraded
    assert any('Could not read the session log' in notice for notice in notifications)
    assert launch_results == []
