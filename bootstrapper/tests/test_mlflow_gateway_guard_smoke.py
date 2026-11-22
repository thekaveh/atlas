from __future__ import annotations

import subprocess

import pytest

from tests.test_mlflow_gateway_guard import (
    ROOT,
    _load_smoke_module,
)


def test_mlflow_image_smoke_bounds_every_docker_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()

    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(smoke.subprocess, "run", hang)

    with pytest.raises(RuntimeError, match="timed out after"):
        smoke._run("docker", "port", "atlas-smoke", "5000/tcp")


@pytest.mark.parametrize("error", (FileNotFoundError("missing"), PermissionError("denied")))
def test_mlflow_image_smoke_normalizes_docker_launch_errors(
    monkeypatch: pytest.MonkeyPatch, error: OSError
) -> None:
    smoke = _load_smoke_module()
    monkeypatch.setattr(
        smoke.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(error)
    )

    with pytest.raises(RuntimeError, match="Docker command could not start") as caught:
        smoke._run("docker", "port", "atlas-smoke", "5000/tcp")

    assert caught.value.__cause__ is error


def test_mlflow_image_smoke_cleanup_preserves_primary_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    smoke = _load_smoke_module()
    monkeypatch.setattr(
        smoke,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("hung cleanup")),
    )

    smoke._cleanup_container("atlas-smoke", suppress_error=True)

    assert "hung cleanup" in capsys.readouterr().err


def test_mlflow_image_smoke_cleanup_failure_is_not_hidden_without_primary_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    monkeypatch.setattr(
        smoke,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("hung cleanup")),
    )

    with pytest.raises(RuntimeError, match="hung cleanup"):
        smoke._cleanup_container("atlas-smoke", suppress_error=False)


@pytest.mark.parametrize("error", (FileNotFoundError("missing"), PermissionError("denied")))
def test_mlflow_image_smoke_cleanup_suppresses_ordinary_error_during_primary_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: OSError,
) -> None:
    smoke = _load_smoke_module()
    monkeypatch.setattr(
        smoke, "_run", lambda *_args, **_kwargs: (_ for _ in ()).throw(error)
    )

    smoke._cleanup_container("atlas-smoke", suppress_error=True)

    assert str(error) in capsys.readouterr().err


@pytest.mark.parametrize("error", (FileNotFoundError("missing"), PermissionError("denied")))
def test_mlflow_image_smoke_cleanup_propagates_ordinary_error_without_primary_failure(
    monkeypatch: pytest.MonkeyPatch, error: OSError
) -> None:
    smoke = _load_smoke_module()
    monkeypatch.setattr(
        smoke, "_run", lambda *_args, **_kwargs: (_ for _ in ()).throw(error)
    )

    with pytest.raises(type(error), match=str(error)):
        smoke._cleanup_container("atlas-smoke", suppress_error=False)


def test_mlflow_driver_probe_timeout_cleans_its_verified_container_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    events: list[tuple[str, ...]] = []
    container_id = "a" * 64
    owner_token = "1" * 32
    removed = [False]

    def fake_run(*args, **_kwargs):
        events.append(args)
        if args[:2] == ("docker", "create"):
            return subprocess.CompletedProcess(args, 0, f"{container_id}\n", "")
        if args[:2] == ("docker", "inspect"):
            return subprocess.CompletedProcess(
                args, 0, f"{container_id} {owner_token}\n", ""
            )
        if args[:2] == ("docker", "wait"):
            raise RuntimeError("driver probe timed out")
        if args[:3] == ("docker", "ps", "--all"):
            output = "" if removed[0] else f"{container_id}\n"
            return subprocess.CompletedProcess(args, 0, output, "")
        if args[:3] == ("docker", "rm", "--force"):
            removed[0] = True
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(smoke, "_run", fake_run)

    with pytest.raises(RuntimeError, match="driver probe timed out"):
        smoke._assert_runtime_drivers(
            "atlas-mlflow:test", "atlas-driver-probe", owner_token
        )

    launch = next(args for args in events if args[:2] == ("docker", "create"))
    assert launch[launch.index("--name") + 1] == "atlas-driver-probe"
    assert launch[launch.index("--label") + 1] == (
        f"{smoke.OWNER_LABEL}={owner_token}"
    )
    assert ("docker", "rm", "--force", container_id) in events


@pytest.mark.parametrize("surface", ("driver", "server"))
def test_mlflow_name_collision_never_removes_an_unowned_container(
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
) -> None:
    smoke = _load_smoke_module()
    events: list[tuple[str, ...]] = []
    owner_token = "2" * 32
    name = f"preexisting-{surface}"

    def fake_run(*args, **_kwargs):
        events.append(args)
        if args[:2] == ("docker", "create"):
            raise subprocess.CalledProcessError(
                125,
                args,
                stderr=(
                    'Conflict. The container name "/occupied" is already in use '
                    "by another container."
                ),
            )
        if args[:3] == ("docker", "ps", "--all"):
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    monkeypatch.setattr(smoke, "_run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        if surface == "driver":
            smoke._assert_runtime_drivers("atlas-mlflow:test", name, owner_token)
        else:
            smoke._start_container("atlas-mlflow:test", name, owner_token)

    assert (
        [args for args in events if args[:3] == ("docker", "rm", "--force")],
        all(name not in args for args in events if args[:2] == ("docker", "rm")),
        owner_token in smoke._INFLIGHT_OWNER_TOKENS,
        owner_token in smoke._OBSERVED_OWNER_TOKENS,
    ) == ([], True, False, False)


def test_mlflow_ambiguous_create_failure_recovers_only_by_owner_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    events: list[tuple[str, ...]] = []
    owner_token = "4" * 32
    container_id = "c" * 64
    removed = [False]

    def fake_run(*args, **_kwargs):
        events.append(args)
        if args[:2] == ("docker", "create"):
            raise RuntimeError("create response timed out")
        if args[:3] == ("docker", "ps", "--all"):
            output = "" if removed[0] else f"{container_id}\n"
            return subprocess.CompletedProcess(args, 0, output, "")
        if args[:3] == ("docker", "rm", "--force"):
            removed[0] = True
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    monkeypatch.setattr(smoke, "_run", fake_run)

    with pytest.raises(RuntimeError, match="create response timed out"):
        smoke._start_container("atlas-mlflow:test", "random-name", owner_token)

    discovery = next(args for args in events if args[:3] == ("docker", "ps", "--all"))
    assert discovery[discovery.index("--filter") + 1] == (
        f"label={smoke.OWNER_LABEL}={owner_token}"
    )
    assert ("docker", "rm", "--force", container_id) in events


@pytest.mark.parametrize(
    "create_error",
    (
        RuntimeError("ambiguous create timeout"),
        subprocess.CalledProcessError(
            125, ("docker", "create"), stderr="daemon response lost"
        ),
    ),
    ids=("client-timeout", "daemon-create-error"),
)
def test_mlflow_ambiguous_create_reconciles_delayed_container_visibility(
    monkeypatch: pytest.MonkeyPatch,
    create_error: BaseException,
) -> None:
    smoke = _load_smoke_module()
    owner_token = "6" * 32
    container_id = "d" * 64
    discoveries = iter(("", f"{container_id}\n", ""))
    removed: list[str] = []
    now = [0.0]

    def fake_run(*args, **_kwargs):
        if args[:2] == ("docker", "create"):
            raise create_error
        if args[:3] == ("docker", "ps", "--all"):
            return subprocess.CompletedProcess(args, 0, next(discoveries), "")
        if args[:3] == ("docker", "rm", "--force"):
            removed.append(args[3])
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    monkeypatch.setattr(smoke, "_run", fake_run)
    monkeypatch.setattr(smoke.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(smoke.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))

    with pytest.raises(type(create_error)):
        smoke._start_container("atlas-mlflow:test", "random-name", owner_token)

    assert (
        removed,
        owner_token in smoke._INFLIGHT_OWNER_TOKENS,
        owner_token in smoke._OBSERVED_OWNER_TOKENS,
    ) == (
        [container_id],
        False,
        False,
    )


def test_mlflow_ambiguous_no_show_retains_token_for_outer_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    owner_token = "a" * 32
    container_id = "f" * 64
    visible = [False]
    removed: list[str] = []
    now = [0.0]

    def fake_run(*args, **_kwargs):
        if args[:2] == ("docker", "create"):
            raise RuntimeError("ambiguous create timeout")
        if args[:3] == ("docker", "ps", "--all"):
            output = f"{container_id}\n" if visible[0] and not removed else ""
            return subprocess.CompletedProcess(args, 0, output, "")
        if args[:3] == ("docker", "rm", "--force"):
            removed.append(args[3])
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    monkeypatch.setattr(smoke, "_run", fake_run)
    monkeypatch.setattr(smoke, "CLEANUP_RECONCILE_SECONDS", 0.5)
    monkeypatch.setattr(smoke.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        smoke.time,
        "sleep",
        lambda seconds: now.__setitem__(0, now[0] + seconds),
    )

    with pytest.raises(RuntimeError, match="ambiguous create timeout"):
        smoke._start_container("atlas-mlflow:test", "random-name", owner_token)

    assert (owner_token in smoke._INFLIGHT_OWNER_TOKENS, removed) == (True, [])

    visible[0] = True
    smoke._cleanup_all_owned(suppress_error=False)

    assert (
        removed,
        owner_token in smoke._INFLIGHT_OWNER_TOKENS,
        bool(smoke._OWNED_CONTAINERS),
    ) == ([container_id], False, False)


def test_mlflow_post_create_inspect_failure_waits_for_label_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    owner_token = "b" * 32
    container_id = "a" * 64
    discoveries = iter(("", f"{container_id}\n", ""))
    removed: list[str] = []
    now = [0.0]

    def fake_run(*args, **_kwargs):
        if args[:2] == ("docker", "create"):
            return subprocess.CompletedProcess(args, 0, f"{container_id}\n", "")
        if args[:2] == ("docker", "inspect"):
            raise subprocess.CalledProcessError(1, args, stderr="daemon restarting")
        if args[:3] == ("docker", "ps", "--all"):
            return subprocess.CompletedProcess(args, 0, next(discoveries), "")
        if args[:3] == ("docker", "rm", "--force"):
            removed.append(args[3])
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    monkeypatch.setattr(smoke, "_run", fake_run)
    monkeypatch.setattr(smoke, "CLEANUP_RECONCILE_SECONDS", 0.5)
    monkeypatch.setattr(smoke.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        smoke.time,
        "sleep",
        lambda seconds: now.__setitem__(0, now[0] + seconds),
    )

    with pytest.raises(subprocess.CalledProcessError):
        smoke._start_container("atlas-mlflow:test", "random-name", owner_token)

    assert (
        removed,
        owner_token in smoke._INFLIGHT_OWNER_TOKENS,
        bool(smoke._OWNED_CONTAINERS),
    ) == ([container_id], False, False)


def test_mlflow_failed_owner_cleanup_retains_retry_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    owner_token = "7" * 32
    container_id = "e" * 64
    now = [0.0]
    removal_attempts = [0]
    removed = [False]

    def fake_run(*args, **_kwargs):
        if args[:3] == ("docker", "ps", "--all"):
            output = "" if removed[0] else f"{container_id}\n"
            return subprocess.CompletedProcess(args, 0, output, "")
        if args[:3] == ("docker", "rm", "--force"):
            removal_attempts[0] += 1
            if removal_attempts[0] == 5:
                removed[0] = True
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 1, "", "daemon restarting")
        raise AssertionError(args)

    monkeypatch.setattr(smoke, "_run", fake_run)
    monkeypatch.setattr(smoke, "CLEANUP_RECONCILE_SECONDS", 0.25)
    monkeypatch.setattr(smoke.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(smoke.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))

    smoke._INFLIGHT_OWNER_TOKENS.add(owner_token)
    smoke._cleanup_owner_token(
        owner_token, suppress_error=True, wait_for_late_container=True
    )

    assert (
        owner_token in smoke._INFLIGHT_OWNER_TOKENS,
        container_id in smoke._OWNED_CONTAINERS,
        removal_attempts,
    ) == (True, True, [2])

    smoke._cleanup_all_owned(suppress_error=False)

    assert (
        owner_token in smoke._INFLIGHT_OWNER_TOKENS,
        bool(smoke._OWNED_CONTAINERS),
        owner_token in smoke._OBSERVED_OWNER_TOKENS,
        removal_attempts,
    ) == (False, False, False, [5])


def test_mlflow_interrupted_owner_discovery_retains_retry_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    owner_token = "8" * 32
    smoke._INFLIGHT_OWNER_TOKENS.add(owner_token)
    monkeypatch.setattr(
        smoke,
        "_container_ids_for_owner",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    with pytest.raises(KeyboardInterrupt):
        smoke._cleanup_owner_token(
            owner_token,
            suppress_error=True,
            wait_for_late_container=True,
        )

    assert owner_token in smoke._INFLIGHT_OWNER_TOKENS


def test_mlflow_driver_preverification_interrupt_retries_late_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    owner_token = "9" * 32
    cleanup_modes: list[bool] = []

    def interrupt_before_verification(*_args) -> str:
        smoke._INFLIGHT_OWNER_TOKENS.add(owner_token)
        raise KeyboardInterrupt

    def cleanup(
        token: str, *, suppress_error: bool, wait_for_late_container: bool
    ) -> None:
        assert token == owner_token
        assert suppress_error is True
        cleanup_modes.append(wait_for_late_container)
        smoke._INFLIGHT_OWNER_TOKENS.discard(token)

    monkeypatch.setattr(smoke, "_create_and_start_container", interrupt_before_verification)
    monkeypatch.setattr(smoke, "_cleanup_owner_token", cleanup)

    with pytest.raises(KeyboardInterrupt):
        smoke._assert_runtime_drivers("atlas-mlflow:test", "driver-name", owner_token)

    assert cleanup_modes == [True]


def test_mlflow_health_request_has_a_total_slow_response_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    release = smoke.threading.Event()

    def slow_response(*_args, **_kwargs):
        release.wait(1)
        return 200, b"", {}

    monkeypatch.setattr(smoke, "_request", slow_response)
    started = smoke.time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="HTTP request exceeded"):
            smoke._request_with_total_timeout("http://localhost", "/health", 0.01)
    finally:
        release.set()

    assert smoke.time.monotonic() - started < 0.5


def test_mlflow_readiness_loop_honors_one_overall_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    now = [0.0]
    attempts: list[float] = []

    def unavailable(*_args, **_kwargs):
        attempts.append(_kwargs.get("timeout_seconds", _args[-1]))
        raise ConnectionRefusedError("starting")

    monkeypatch.setattr(smoke, "_request_with_total_timeout", unavailable)
    monkeypatch.setattr(smoke.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(smoke.time, "sleep", lambda _seconds: now.__setitem__(0, 61.0))

    assert smoke._wait_for_health("http://localhost") is False
    assert attempts == [smoke.READINESS_REQUEST_TIMEOUT_SECONDS]


def test_mlflow_readiness_does_not_retry_a_slow_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    attempts: list[None] = []

    def slow(*_args, **_kwargs):
        attempts.append(None)
        raise TimeoutError("slow response")

    monkeypatch.setattr(smoke, "_request_with_total_timeout", slow)

    with pytest.raises(TimeoutError, match="slow response"):
        smoke._wait_for_health("http://localhost")

    assert attempts == [None]


def test_mlflow_termination_signal_cleans_every_owned_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    cleaned: list[str] = []
    smoke._OWNED_CONTAINERS.update(("driver-probe", "server-probe"))

    def cleanup(
        name: str, *, suppress_error: bool, timeout_seconds: float = 30
    ) -> None:
        assert suppress_error is False
        assert timeout_seconds == smoke.CLEANUP_TIMEOUT_SECONDS
        cleaned.append(name)
        smoke._OWNED_CONTAINERS.discard(name)

    monkeypatch.setattr(smoke, "_cleanup_container", cleanup)

    with pytest.raises(SystemExit) as caught:
        smoke._handle_termination(smoke.signal.SIGTERM, None)

    assert (caught.value.code, set(cleaned)) == (
        128 + smoke.signal.SIGTERM,
        {"driver-probe", "server-probe"},
    )


def test_mlflow_termination_signal_recovers_an_inflight_labeled_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    owner_token = "5" * 32
    recovered: list[tuple[str, bool]] = []
    smoke._INFLIGHT_OWNER_TOKENS.add(owner_token)

    def cleanup(
        token: str, *, suppress_error: bool, wait_for_late_container: bool
    ) -> None:
        recovered.append((token, suppress_error))
        assert wait_for_late_container is True
        smoke._INFLIGHT_OWNER_TOKENS.discard(token)

    monkeypatch.setattr(smoke, "_cleanup_owner_token", cleanup)

    with pytest.raises(SystemExit):
        smoke._handle_termination(smoke.signal.SIGTERM, None)

    assert recovered == [(owner_token, False)]


def test_mlflow_repeated_termination_does_not_interrupt_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    events: list[str] = []

    def cleanup(*, suppress_error: bool) -> None:
        assert suppress_error is True
        events.append("cleanup-start")
        smoke._handle_termination(smoke.signal.SIGTERM, None)
        events.append("cleanup-finished")

    monkeypatch.setattr(smoke, "_cleanup_all_owned", cleanup)

    with pytest.raises(SystemExit) as caught:
        smoke._handle_termination(smoke.signal.SIGTERM, None)

    assert caught.value.code == 128 + smoke.signal.SIGTERM
    assert events == ["cleanup-start", "cleanup-finished"]


def test_mlflow_repeated_signal_during_outer_cleanup_preserves_first_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    cleanup_calls = [0]

    def cleanup(*, suppress_error: bool) -> None:
        assert suppress_error is True
        cleanup_calls[0] += 1
        if cleanup_calls[0] == 2:
            smoke._handle_termination(smoke.signal.SIGINT, None)

    monkeypatch.setattr(smoke, "_cleanup_all_owned", cleanup)

    with pytest.raises(SystemExit) as caught:
        try:
            smoke._handle_termination(smoke.signal.SIGTERM, None)
        finally:
            smoke._cleanup_all_owned(suppress_error=True)

    assert (caught.value.code, cleanup_calls) == (
        128 + smoke.signal.SIGTERM,
        [2],
    )


def test_mlflow_installs_hup_and_term_cleanup_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    installed: dict[int, object] = {}
    monkeypatch.setattr(
        smoke.signal, "signal", lambda signum, handler: installed.update({signum: handler})
    )

    smoke._install_signal_handlers()

    assert (
        installed[smoke.signal.SIGHUP],
        installed[smoke.signal.SIGINT],
        installed[smoke.signal.SIGTERM],
    ) == (
        smoke._handle_termination,
        smoke._handle_termination,
        smoke._handle_termination,
    )


def test_mlflow_image_smoke_launch_interrupt_still_attempts_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke_module()
    cleanup: list[str] = []
    container_id = "b" * 64
    owner_token = "3" * 32
    removed = [False]
    monkeypatch.setattr(smoke, "_image_argument", lambda: "atlas-mlflow:test")
    monkeypatch.setattr(smoke, "_assert_pinned_base", lambda: None)
    monkeypatch.setattr(
        smoke, "_assert_runtime_drivers", lambda _image, _name, _token: None
    )
    monkeypatch.setattr(smoke, "_install_signal_handlers", lambda: None)
    monkeypatch.setattr(smoke.secrets, "token_hex", lambda _size: owner_token)

    def interrupt_launch(*args, **kwargs):
        if args[:2] == ("docker", "create"):
            return subprocess.CompletedProcess(args, 0, f"{container_id}\n", "")
        if args[:2] == ("docker", "inspect"):
            return subprocess.CompletedProcess(
                args, 0, f"{container_id} {owner_token}\n", ""
            )
        if args[:2] == ("docker", "start"):
            return subprocess.CompletedProcess(args, 0, f"{container_id}\n", "")
        if args[:2] == ("docker", "port"):
            raise KeyboardInterrupt
        if args[:3] == ("docker", "ps", "--all"):
            output = "" if removed[0] else f"{container_id}\n"
            return subprocess.CompletedProcess(args, 0, output, "")
        if args[:3] == ("docker", "rm", "--force"):
            removed[0] = True
            cleanup.append(args[3])
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    monkeypatch.setattr(smoke, "_run", interrupt_launch)

    with pytest.raises(KeyboardInterrupt):
        smoke.main()

    assert cleanup == [container_id]


def test_jupyter_mlflow_exception_scans_every_shipped_execution_surface() -> None:
    build = ROOT / "services/jupyterhub/build"
    tracked = subprocess.run(
        ["git", "ls-files", "services/jupyterhub/build"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    paths = [ROOT / "services/jupyterhub/compose.yml", *(ROOT / path for path in tracked)]

    forbidden = (
        "mlflow.server",
        "creategatewaysecret",
        "create_gateway_secret",
        "create_gateway(",
        "auth_config",
        "/gateway/proxy/",
        "/mlflow/gateway/",
        "mlflow ui",
        "mlflow server",
    )
    violations: list[str] = []
    for path in paths:
        content = path.read_bytes().decode("utf-8", errors="replace")
        content = "\n".join(
            line for line in content.splitlines() if not line.lstrip().startswith("#")
        ).lower()
        for token in forbidden:
            if token in content:
                violations.append(f"{path.relative_to(ROOT)}: {token}")

    assert not violations, "Jupyter's reviewed client-only exception became reachable:\n" + "\n".join(
        violations
    )
