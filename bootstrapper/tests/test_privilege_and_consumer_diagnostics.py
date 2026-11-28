"""Two launch-path guarantees that were unpinned.

* A1-01: the --clean-hosts sudo child imports the bootstrapper tree, so it must
  not inherit the caller's PYTHONPATH and must run with -s -P (no user
  site-packages, no working directory on sys.path).
* A1-09: a consumer manifest that fails to load during a track launch must
  produce a warning, not an empty SOURCE set with no message.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import stop as stop_module
import start as start_module

BOOTSTRAPPER_DIR = Path(stop_module.__file__).resolve().parent


def _capture_sudo_invocation(monkeypatch):
    system_utils = importlib.import_module("utils.system")
    monkeypatch.setattr(system_utils, "is_elevated", lambda: False)
    calls: list[tuple[list, dict]] = []

    class _Result:
        returncode = 0

    monkeypatch.setattr(
        stop_module.subprocess,
        "run",
        lambda args, **kwargs: calls.append((args, kwargs)) or _Result(),
    )
    return calls


def test_sudo_child_gets_the_bootstrapper_path_and_never_the_callers(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/opt/caller-controlled")
    calls = _capture_sudo_invocation(monkeypatch)

    assert stop_module._run_privileged_hosts_cleanup() is True

    args, kwargs = calls[0]
    assert f"PYTHONPATH={BOOTSTRAPPER_DIR}" in args
    assert kwargs["env"]["PYTHONPATH"] == str(BOOTSTRAPPER_DIR)
    assert not any("caller-controlled" in str(part) for part in args)


def test_sudo_child_runs_with_user_site_and_cwd_excluded(monkeypatch):
    calls = _capture_sudo_invocation(monkeypatch)

    stop_module._run_privileged_hosts_cleanup()

    args, _ = calls[0]
    interpreter = args.index(sys.executable)
    assert args[interpreter + 1 : interpreter + 3] == ["-s", "-P"]
    assert args[interpreter + 3] == "-c"


def test_child_imports_the_bootstrapper_tree_under_isolation_flags(tmp_path):
    """Real interpreter, no root: the -s -P child still imports HostsManager, and
    a shadowing utils package in the working directory is NOT picked up."""
    shadow = tmp_path / "utils"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("", encoding="utf-8")
    (shadow / "hosts_manager.py").write_text("print('shadowed')\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable, "-s", "-P", "-c",
            "from utils.hosts_manager import HostsManager; print('imported')",
        ],
        cwd=tmp_path,
        env={"PYTHONPATH": str(BOOTSTRAPPER_DIR), "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "imported"


class _Banner:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def show_status_message(self, message, level):
        self.messages.append((message, level))


def _starter(load_consumer_config):
    return SimpleNamespace(
        banner=_Banner(),
        config_parser=SimpleNamespace(load_consumer_config=load_consumer_config),
    )


def test_failed_consumer_load_warns_and_declares_no_sources():
    def _broken():
        raise ValueError("bad manifest")

    starter = _starter(_broken)

    declared = start_module._consumer_declared_source_keys(
        starter, {"silo_source": "container"}
    )

    assert declared == frozenset()
    assert len(starter.banner.messages) == 1
    message, level = starter.banner.messages[0]
    assert level == "warning"
    assert "bad manifest" in message
    assert "*_SOURCE" in message


def test_declared_sources_are_limited_to_cli_source_args_without_a_warning():
    consumer = SimpleNamespace(
        env_overrides={"SILO_SOURCE": "container", "OLLAMA_SOURCE": "disabled", "FOO": "x"}
    )
    starter = _starter(lambda: consumer)

    declared = start_module._consumer_declared_source_keys(
        starter, {"silo_source": "disabled"}
    )

    assert declared == frozenset({"silo_source"})
    assert starter.banner.messages == []
