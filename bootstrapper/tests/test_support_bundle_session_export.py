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


@pytest.mark.parametrize("assignment", [
    "BASE_PORT=64000\n",
    "export BASE_PORT=64000\n",
    "  export\tBASE_PORT = 64000 # custom port\n",
    "\ufeffBASE_PORT=64000\n",
    "\ufeffBASE_PORT=63000\r\nexport BASE_PORT=64000 # winner\r\n",
])
@pytest.mark.parametrize("origin", [None, "consumer.yml", "override.env"])
def test_doctor_archive_preserves_supported_active_env_origins(tmp_path, monkeypatch, assignment, origin):
    import json
    from click.testing import CliRunner
    from core.config_parser import ConfigParser
    import start

    env_file = tmp_path / "active.env"
    secret = "SyntheticBundleOriginSecret79"
    env_file.write_bytes((assignment + "SYNTHETIC_API_KEY=" + secret + "\n").encode())
    parser = ConfigParser(str(tmp_path))
    parser.env_file_path = env_file
    assert parser.parse_env_file()["BASE_PORT"] == "64000"
    before = env_file.read_bytes()
    origins = {"BASE_PORT": str(tmp_path / origin)} if origin else {}
    starter = SimpleNamespace(config_parser=parser, _env_origins=lambda: origins,
                              materialize_consumer_env_for_preflight=lambda: {})
    build = start.AtlasStarter.build_support_bundle
    starter.build_support_bundle = lambda options, checks=None: build(starter, options, checks)
    monkeypatch.setattr(start, "AtlasStarter", lambda: starter)
    check = lambda _: {"id": "port", "status": "fail", "message": "BASE_PORT conflict", "details": {}}
    monkeypatch.setattr(start, "DOCTOR_CHECKS", [check])
    output = tmp_path / "bundle.tar.gz"
    result = CliRunner().invoke(start.main, ["doctor", "--format", "json", "--bundle", str(output)])
    assert result.exit_code == 1 and json.loads(result.stdout)["ok"] is False
    with tarfile.open(output) as archive:
        body = json.load(archive.extractfile("atlas-support-bundle/bundle.json"))
    expected = str(tmp_path / origin) if origin else str(env_file)
    _assert_bundle_base_port_origin(body, expected)
    assert env_file.read_bytes() == before
    assert secret not in json.dumps(body) + result.output


def _assert_bundle_base_port_origin(body, expected):
    entry = next(entry for entry in body["config"] if entry["key"] == "BASE_PORT")
    assert entry["origin"] == expected and expected in entry["action"]
    assert ".env.example default" not in entry["action"]
    assert body["findings"][0]["keys"] == [entry]


@pytest.mark.parametrize("file_state", ["none", "missing", "other-key"])
def test_bundle_origin_keeps_genuine_default_fallback(tmp_path, file_state):
    from core.support_bundle import BundleRequest, Redactor, config_entries
    env_file = None if file_state == "none" else tmp_path / "active.env"
    if file_state == "other-key":
        env_file.write_text("PROJECT_NAME=synthetic\n")
    request = BundleRequest(env={"BASE_PORT": "63000"}, env_file=env_file)
    entries, omitted = config_entries(request, Redactor())
    assert omitted == 0 and len(entries) == 1
    assert entries[0]["origin"] == "default"
    assert entries[0]["action"] == "Set BASE_PORT in .env; it currently takes the .env.example default."


@pytest.mark.parametrize("case", [
    ("\ufeffexport BASE_PORT=${OTHER}\r\n# IGNORED_SOURCE=disabled\r\nSYNTHETIC_API_KEY=PrivateValue79\n", {"BASE_PORT", "SYNTHETIC_API_KEY"}),
    ("VALUE='one\x85FAKE_SOURCE=disabled'\nPROJECT_NAME=synthetic\n", {"VALUE", "PROJECT_NAME"}),
    ("export\tBASE_PORT = '64=000'\rPROJECT_NAME=synthetic\r", {"BASE_PORT", "PROJECT_NAME"}),
    ("BASE_PORT: 64000\nBAD_SOURCE: disabled=wrong\n# COMMENT_SOURCE=disabled\n", set()),
])
def test_bundle_file_key_discovery_is_quiet_and_uses_reader_lines(tmp_path, capsys, case):
    from core.support_bundle import _file_keys
    text, expected = case
    path = tmp_path / "active.env"
    path.write_bytes(text.encode())
    assert _file_keys(path) == expected
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_bundle_file_key_read_errors_still_propagate(tmp_path, monkeypatch):
    from core.support_bundle import _file_keys
    path = tmp_path / "active.env"
    path.write_text("BASE_PORT=64000\n")

    def unreadable(*args, **kwargs):
        raise OSError("synthetic metadata read error")

    monkeypatch.setattr(Path, "read_text", unreadable)
    with pytest.raises(OSError, match="synthetic metadata read error"):
        _file_keys(path)
