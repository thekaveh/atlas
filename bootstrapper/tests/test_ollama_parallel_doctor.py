"""#849 part 2: the ollama-localhost parallel-serving doctor lint.

Part 1 (container sources) is covered by test_ollama_parallel_serving.py.
This is the half Atlas cannot *fix*, only report: under the host-prereq
doctrine Atlas does not own a host Ollama daemon's environment, so it can
read the daemon's ``OLLAMA_NUM_PARALLEL`` back and say when it is below
what a consumer declared it needs.

Why the check matters more than a normal misconfiguration warning: Ollama
defaults to ONE parallel slot and silently SERIALIZES concurrent requests
instead of rejecting them. A consumer needing 8 gets correct-but-slow
behaviour with no error anywhere — the expensive failure is the one
nobody notices.

Everything here is stubbed: no subprocess runs and no daemon is contacted.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

from services.ollama_localhost import (  # noqa: E402
    host_parallel_config,
    parallel_shortfall,
)


# ─── the host-config probe ───────────────────────────────────────────


def test_darwin_reads_both_values_from_launchctl() -> None:
    calls: list[list[str]] = []

    def runner(args):
        calls.append(args)
        return {"OLLAMA_NUM_PARALLEL": "8\n", "OLLAMA_MAX_LOADED_MODELS": "2\n"}[args[-1]]

    observed = host_parallel_config(runner=runner, platform_name="Darwin")
    assert observed == {"OLLAMA_NUM_PARALLEL": 8, "OLLAMA_MAX_LOADED_MODELS": 2}
    assert all(c[:2] == ["launchctl", "getenv"] for c in calls), calls


def test_non_darwin_reports_unknown_rather_than_guessing() -> None:
    """On Linux the daemon's env depends on how it was started — systemd
    drop-in, shell export, container — with no single readable source. A
    guess here would produce false warnings."""
    def runner(_args):  # pragma: no cover - must never be called
        raise AssertionError("no probe should run off Darwin")

    observed = host_parallel_config(runner=runner, platform_name="Linux")
    assert observed == {"OLLAMA_NUM_PARALLEL": None, "OLLAMA_MAX_LOADED_MODELS": None}


def test_an_unset_launchctl_variable_is_unknown_not_zero() -> None:
    """launchctl prints an empty line for an unset key; that is 'unknown',
    which must stay distinct from a real low value."""
    observed = host_parallel_config(runner=lambda _a: "\n", platform_name="Darwin")
    assert observed["OLLAMA_NUM_PARALLEL"] is None


def test_a_failed_probe_is_unknown() -> None:
    observed = host_parallel_config(runner=lambda _a: None, platform_name="Darwin")
    assert observed["OLLAMA_NUM_PARALLEL"] is None


def test_a_non_numeric_value_is_unknown() -> None:
    """The daemon would ignore it too, so treat it as unset."""
    observed = host_parallel_config(runner=lambda _a: "lots\n", platform_name="Darwin")
    assert observed["OLLAMA_NUM_PARALLEL"] is None


# ─── the comparison ──────────────────────────────────────────────────


def test_a_host_below_the_declared_minimum_is_a_shortfall() -> None:
    short, why = parallel_shortfall(8, {"OLLAMA_NUM_PARALLEL": 1})
    assert short is True
    assert "1" in why and "8" in why
    assert "launchctl setenv" in why, "the warning must say how to fix it"


def test_a_host_meeting_the_minimum_is_not_a_shortfall() -> None:
    short, why = parallel_shortfall(8, {"OLLAMA_NUM_PARALLEL": 8})
    assert short is False
    assert "meets" in why


def test_a_host_above_the_minimum_is_not_a_shortfall() -> None:
    short, _ = parallel_shortfall(4, {"OLLAMA_NUM_PARALLEL": 16})
    assert short is False


def test_an_unknown_host_value_never_reports_a_shortfall() -> None:
    """Warning about a value we failed to read trains people to ignore the
    check — the one outcome worse than staying silent."""
    short, why = parallel_shortfall(8, {"OLLAMA_NUM_PARALLEL": None})
    assert short is False
    assert "could not read" in why


# ─── the doctor check ────────────────────────────────────────────────


class _Starter:
    def __init__(self, env: dict[str, str]) -> None:
        self.config_parser = type(
            "_CP", (), {"parse_env_file": staticmethod(lambda: dict(env))}
        )()


def _check(env: dict[str, str], observed=None):
    import start as start_mod

    if observed is not None:
        import services.ollama_localhost as mod

        original = mod.host_parallel_config
        mod.host_parallel_config = lambda **_kw: observed
        try:
            return start_mod._doctor_check_ollama_parallel(_Starter(env))
        finally:
            mod.host_parallel_config = original
    return start_mod._doctor_check_ollama_parallel(_Starter(env))


def test_container_sources_pass_without_probing_the_host() -> None:
    res = _check({"LLM_PROVIDER_SOURCE": "ollama-container-gpu",
                  "OLLAMA_PARALLEL_MIN": "8"})
    assert res["status"] == "pass"
    assert "container" in res["message"]


def test_no_declared_minimum_passes() -> None:
    res = _check({"LLM_PROVIDER_SOURCE": "ollama-localhost"})
    assert res["status"] == "pass"


def test_a_non_integer_minimum_fails_loudly() -> None:
    res = _check({"LLM_PROVIDER_SOURCE": "ollama-localhost",
                  "OLLAMA_PARALLEL_MIN": "eight"})
    assert res["status"] == "fail"


def test_a_shortfall_is_reported_as_fail() -> None:
    res = _check(
        {"LLM_PROVIDER_SOURCE": "ollama-localhost", "OLLAMA_PARALLEL_MIN": "8"},
        observed={"OLLAMA_NUM_PARALLEL": 1, "OLLAMA_MAX_LOADED_MODELS": 1},
    )
    assert res["status"] == "fail"
    assert "launchctl setenv" in res["message"]


def test_a_satisfied_minimum_passes() -> None:
    res = _check(
        {"LLM_PROVIDER_SOURCE": "ollama-localhost", "OLLAMA_PARALLEL_MIN": "8"},
        observed={"OLLAMA_NUM_PARALLEL": 8, "OLLAMA_MAX_LOADED_MODELS": 2},
    )
    assert res["status"] == "pass"


def test_an_unreadable_host_is_skipped_not_failed() -> None:
    """Advisory by construction — this is the whole reason the check is
    safe to ship without live validation on every platform."""
    res = _check(
        {"LLM_PROVIDER_SOURCE": "ollama-localhost", "OLLAMA_PARALLEL_MIN": "8"},
        observed={"OLLAMA_NUM_PARALLEL": None, "OLLAMA_MAX_LOADED_MODELS": None},
    )
    assert res["status"] == "skipped"


def test_the_check_is_registered_with_doctor() -> None:
    import start as start_mod

    assert start_mod._doctor_check_ollama_parallel in start_mod.DOCTOR_CHECKS


# ─── #798: residency — will the host evict its own working set? ──────


def test_keep_alive_is_read_as_a_string_not_an_int() -> None:
    """OLLAMA_KEEP_ALIVE is a DURATION ("5m", "1h", "-1"). Routing it through
    the int-coercing parallel probe would report it as unknown forever."""
    from services.ollama_localhost import host_keep_alive

    assert host_keep_alive(runner=lambda _a: "5m\n", platform_name="Darwin") == "5m"
    assert host_keep_alive(runner=lambda _a: "-1\n", platform_name="Darwin") == "-1"


def test_keep_alive_unset_or_unreadable_is_none() -> None:
    from services.ollama_localhost import host_keep_alive

    assert host_keep_alive(runner=lambda _a: "\n", platform_name="Darwin") is None
    assert host_keep_alive(runner=lambda _a: None, platform_name="Darwin") is None


def test_keep_alive_is_not_probed_off_darwin() -> None:
    from services.ollama_localhost import host_keep_alive

    def runner(_args):  # pragma: no cover - must never run
        raise AssertionError("no probe should run off Darwin")

    assert host_keep_alive(runner=runner, platform_name="Linux") is None


def test_too_few_resident_slots_is_a_shortfall() -> None:
    from services.ollama_localhost import residency_shortfall

    short, why = residency_shortfall(3, {"OLLAMA_MAX_LOADED_MODELS": 2}, "5m")
    assert short is True
    assert "evict and reload" in why
    assert "launchctl setenv" in why, "must say how to fix it"
    assert "pins every loaded model" in why.lower(), (
        "the -1 suggestion must carry its RAM cost — the ticket calls this a footgun"
    )


def test_enough_slots_is_not_a_shortfall() -> None:
    from services.ollama_localhost import residency_shortfall

    short, why = residency_shortfall(3, {"OLLAMA_MAX_LOADED_MODELS": 4}, "-1")
    assert short is False
    assert "-1" in why, "keep_alive should be surfaced even when passing"


def test_keep_alive_is_surfaced_when_unset() -> None:
    """Enough slots but a 5m TTL still evicts between calls, so the value is
    reported rather than assumed."""
    from services.ollama_localhost import residency_shortfall

    short, why = residency_shortfall(3, {"OLLAMA_MAX_LOADED_MODELS": 4}, None)
    assert short is False
    assert "not set" in why and "5m" in why


def test_unknown_max_loaded_never_reports_a_shortfall() -> None:
    from services.ollama_localhost import residency_shortfall

    short, why = residency_shortfall(3, {"OLLAMA_MAX_LOADED_MODELS": None}, None)
    assert short is False
    assert "could not read" in why


def _residency(env, observed=None, keep_alive=None):
    import start as start_mod
    import services.ollama_localhost as mod

    orig_p, orig_k = mod.host_parallel_config, mod.host_keep_alive
    if observed is not None:
        mod.host_parallel_config = lambda **_kw: observed
    mod.host_keep_alive = lambda **_kw: keep_alive
    try:
        return start_mod._doctor_check_ollama_residency(_Starter(env))
    finally:
        mod.host_parallel_config, mod.host_keep_alive = orig_p, orig_k


def test_residency_passes_for_container_sources() -> None:
    res = _residency({"LLM_PROVIDER_SOURCE": "ollama-container-gpu",
                      "OLLAMA_MODELS_RESIDENT_MIN": "3"})
    assert res["status"] == "pass"
    assert "container" in res["message"]


def test_residency_needs_a_declared_minimum() -> None:
    assert _residency({"LLM_PROVIDER_SOURCE": "ollama-localhost"})["status"] == "pass"


def test_residency_shortfall_fails() -> None:
    res = _residency(
        {"LLM_PROVIDER_SOURCE": "ollama-localhost", "OLLAMA_MODELS_RESIDENT_MIN": "3"},
        observed={"OLLAMA_NUM_PARALLEL": 8, "OLLAMA_MAX_LOADED_MODELS": 2},
        keep_alive="5m",
    )
    assert res["status"] == "fail"
    assert "launchctl setenv" in res["message"]


def test_residency_satisfied_passes() -> None:
    res = _residency(
        {"LLM_PROVIDER_SOURCE": "ollama-localhost", "OLLAMA_MODELS_RESIDENT_MIN": "3"},
        observed={"OLLAMA_NUM_PARALLEL": 8, "OLLAMA_MAX_LOADED_MODELS": 4},
        keep_alive="-1",
    )
    assert res["status"] == "pass"


def test_residency_unreadable_is_skipped() -> None:
    res = _residency(
        {"LLM_PROVIDER_SOURCE": "ollama-localhost", "OLLAMA_MODELS_RESIDENT_MIN": "3"},
        observed={"OLLAMA_NUM_PARALLEL": None, "OLLAMA_MAX_LOADED_MODELS": None},
    )
    assert res["status"] == "skipped"


def test_residency_check_is_registered() -> None:
    import start as start_mod

    assert start_mod._doctor_check_ollama_residency in start_mod.DOCTOR_CHECKS
