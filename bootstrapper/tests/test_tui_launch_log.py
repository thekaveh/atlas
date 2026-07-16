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
