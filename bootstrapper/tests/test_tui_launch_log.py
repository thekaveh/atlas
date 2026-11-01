from __future__ import annotations

import datetime
import stat
from pathlib import Path

from ui.textual.screens import wizard_screen


class _FrozenDateTime(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 7, 14, 3, 4, 5, tzinfo=tz)


def test_concurrent_wizards_get_distinct_owner_only_launch_logs(monkeypatch) -> None:
    monkeypatch.setattr(datetime, "datetime", _FrozenDateTime)
    screens = [
        wizard_screen.WizardScreen(steps=[], services=[]),
        wizard_screen.WizardScreen(steps=[], services=[]),
    ]
    paths = [screen._launch_log_path for screen in screens]

    try:
        assert all(isinstance(path, Path) for path in paths)
        assert paths[0] != paths[1]
        for path in paths:
            assert path.name.startswith("atlas-launch-20260714T030405-")
            assert path.suffix == ".log"
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
    finally:
        for screen in screens:
            screen._close_launch_log_tee()
        for path in paths:
            path.unlink(missing_ok=True)


def test_a_worker_thread_degradation_notice_reaches_the_pane_via_the_loop(
    monkeypatch, tmp_path
) -> None:
    """The tee notifies in the thread whose write failed, usually a pipeline
    worker. The notice used to write the Textual pane from that thread; it
    must be marshalled with ``call_from_thread`` like every other worker log."""
    import tempfile
    import threading

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    screen = wizard_screen.WizardScreen(steps=[], services=[])
    tee = screen._launch_log_fh
    marshalled: list = []
    pane_writes: list = []

    class _App:
        def call_from_thread(self, callback, *args, **kwargs):
            marshalled.append(callback)

    monkeypatch.setattr(wizard_screen.WizardScreen, "app", property(lambda _self: _App()))
    monkeypatch.setattr(
        screen._log_pane, "write_log",
        lambda msg, **kw: pane_writes.append((threading.current_thread(), msg, kw)),
    )
    try:
        assert tee is not None
        tee.base_path.unlink()
        worker = threading.Thread(target=tee.write, args=("from a worker\n",))
        worker.start()
        worker.join(5)
        assert not worker.is_alive()
        assert tee.degraded
        assert pane_writes == [], "the worker thread wrote the pane directly"
        assert len(marshalled) == 1
        marshalled[0]()
        [(thread, msg, kw)] = pane_writes
        assert thread is threading.main_thread()
        assert msg.startswith("⚠ session-file logging degraded")
        assert kw == {"level": "warn", "source": "pipeline"}
    finally:
        screen._close_launch_log_tee()
