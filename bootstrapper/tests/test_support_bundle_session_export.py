"""Actual failed-screen bundle/preview/archive regressions, using owned logs."""
import asyncio
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest

from ui.session_log import SessionLogConfig, SessionLogTee


@pytest.fixture
def export_screen(tmp_path, monkeypatch):
    from start import AtlasStarter
    from ui.textual.screens.wizard_screen import WizardScreen
    env_file = tmp_path / ".env"
    env_file.write_text("SYNTHETIC_API_KEY=KnownSecretBoundary79\n")
    parser = SimpleNamespace(env_file_path=env_file,
                            parse_env_file=lambda: {"SYNTHETIC_API_KEY": "KnownSecretBoundary79"})
    starter = SimpleNamespace(support_bundle_path=tmp_path / "support.tar.gz",
                              config_parser=parser, _env_origins=lambda: {})
    starter.build_support_bundle = lambda options: AtlasStarter.build_support_bundle(
        starter, options, checks=([], []))
    screen = WizardScreen(steps=[], services=[], starter=starter)
    screen._close_launch_log_tee()
    screen._launch_log_fh = SessionLogTee(prefix="atlas-launch-export-", config=SessionLogConfig(
        directory=str(tmp_path), segment_max_bytes=1024))
    screen._launch_log_path = screen._launch_log_fh.base_path
    workers, preview = [], []

    def log(text, **_kwargs):
        preview.append(text)
        screen._tee_to_log(text)

    monkeypatch.setattr(screen, "_safe_log", log)
    monkeypatch.setattr(screen, "run_worker", lambda coro, **_kwargs: workers.append(coro))
    try:
        yield screen, workers, preview
    finally:
        screen._close_launch_log_tee()
        for worker in workers:
            worker.close()


def exported(screen):
    with tarfile.open(screen._starter.support_bundle_path) as archive:
        return {member.name: archive.extractfile(member).read() for member in archive.getmembers()}


def test_rotated_failed_screen_exports_actual_terminal_canary(export_screen):
    screen, workers, preview = export_screen
    tee = screen._launch_log_fh
    tee.write("EARLIEST_CONTEXT\n" + "failure diagnostic line\n" * 900 + "TERMINAL_FAILURE_CANARY\n")
    screen._start_support_bundle_export()
    screen._start_support_bundle_export()
    assert len(workers) == 1
    asyncio.run(workers[0])
    text = exported(screen)["atlas-support-bundle/logs/session.log"].decode()
    assert "TERMINAL_FAILURE_CANARY" in text
    assert "TERMINAL_FAILURE_CANARY" in "\n".join(preview)
    assert b"EARLIEST_CONTEXT" in tee.base_path.read_bytes()
    assert not screen._support_bundle_running


def test_capture_survives_worker_delay_close_and_real_owned_reclaim(export_screen, tmp_path):
    screen, workers, preview = export_screen
    tee = screen._launch_log_fh
    tee.write("diagnostic line\n" * 900 + "IMMUTABLE_FAILURE_CANARY\n")
    screen._start_support_bundle_export()
    screen._close_launch_log_tee()
    for _ in range(5):
        later = SessionLogTee(prefix="atlas-launch-reclaim-", config=SessionLogConfig(
            directory=str(tmp_path), segment_max_bytes=1024))
        later.close()
    assert not tee.base_path.exists()
    asyncio.run(workers[0])
    assert b"IMMUTABLE_FAILURE_CANARY" in exported(screen)["atlas-support-bundle/logs/session.log"]
    assert "IMMUTABLE_FAILURE_CANARY" in "\n".join(preview)


def test_unrotated_screen_preserves_payload_and_preview_before_write(export_screen):
    screen, workers, preview = export_screen
    screen._launch_log_fh.write("complete context café🙂\nKnownSecretBoundary79\nTERMINAL_CANARY\n")
    screen._start_support_bundle_export()
    asyncio.run(workers[0])
    text = exported(screen)["atlas-support-bundle/logs/session.log"].decode()
    shown = "\n".join(preview)
    assert "complete context café🙂" in text and "TERMINAL_CANARY" in text
    assert "KnownSecretBoundary79" not in text + shown
    assert shown.index("Support bundle preview") < shown.index("Wrote support bundle")
    assert Path(screen._starter.support_bundle_path).stat().st_mode & 0o777 == 0o600


def test_snapshot_env_failure_remains_fail_closed(export_screen, monkeypatch):
    screen, workers, _preview = export_screen
    screen._launch_log_fh.write("KnownSecretBoundary79\n")
    monkeypatch.setattr(screen._starter.config_parser, "parse_env_file",
                        lambda: (_ for _ in ()).throw(OSError("unreadable env")))
    screen._start_support_bundle_export()
    asyncio.run(workers[0])
    members = exported(screen)
    assert set(members) == {"atlas-support-bundle/bundle.json"}
    assert b"log excerpts left out" in members["atlas-support-bundle/bundle.json"]


def test_capture_error_is_contained_and_remaining_bundle_is_available(export_screen, monkeypatch):
    screen, workers, preview = export_screen
    monkeypatch.setattr(screen._launch_log_fh, "snapshot",
                        lambda: (_ for _ in ()).throw(OSError("snapshot unavailable")))
    screen._start_support_bundle_export()
    asyncio.run(workers[0])
    assert "atlas-support-bundle/bundle.json" in exported(screen)
    assert "snapshot" in "\n".join(preview)
    assert not screen._support_bundle_running


@pytest.mark.parametrize("missing", [False, True])
def test_closed_or_missing_logger_records_unavailable_source(export_screen, missing):
    screen, workers, _preview = export_screen
    if missing:
        screen._close_launch_log_tee()
    else:
        screen._launch_log_fh.close()
    screen._start_support_bundle_export()
    asyncio.run(workers[0])
    members = exported(screen)
    assert set(members) == {"atlas-support-bundle/bundle.json"}
    assert b'"available": false' in members["atlas-support-bundle/bundle.json"]


def test_irreducible_bundle_overflow_is_reported_without_archive(export_screen, monkeypatch):
    from dataclasses import replace
    screen, workers, preview = export_screen
    original = screen._starter.build_support_bundle
    monkeypatch.setattr(screen._starter, "build_support_bundle", lambda options: original(
        replace(options, limits=replace(options.limits, max_bundle_bytes=128))))
    screen._start_support_bundle_export()
    asyncio.run(workers[0])
    assert not Path(screen._starter.support_bundle_path).exists()
    assert "support bundle not written" in "\n".join(preview)
    assert "metadata" in "\n".join(preview)
    assert not screen._support_bundle_running
