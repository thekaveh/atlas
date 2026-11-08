"""Both launch front ends run one configuration plan (#1043).

``core/launch_plan.py`` owns the ordered configuration stages. These tests
drive the real ``--no-tui`` flow and the real Textual pipeline with the same
fake starter and compare the stages each one actually executed, instead of
matching source text.

Fix 1 (final review) still holds: ``generate_comfyui_manifest`` runs after
``generate_litellm_configuration`` and before ``docker compose up``. Without
it a fresh interactive install never writes
``volumes/comfyui/active-models.tsv``, so comfyui-init downloads nothing.
"""

from __future__ import annotations

import pytest

from core import launch_plan, linear_startup
from tests.test_wizard_launch_outcomes import _Starter, _options, _run_tui


def _ids(stages=launch_plan.CONFIGURE_STAGES) -> list[str]:
    return [stage.id for stage in stages]


@pytest.fixture
def trace(monkeypatch):
    """Every stage id either front end runs, in order, plus Compose calls."""
    seen: list[str] = []
    real = launch_plan.run_stage

    def recording(stage, *args, **kwargs):
        seen.append(stage.id)
        return real(stage, *args, **kwargs)

    monkeypatch.setattr(launch_plan, "run_stage", recording)
    monkeypatch.setattr(linear_startup, "warn_if_submodule_pin_drifted", lambda *_: None)
    return seen


def _linear_trace(trace) -> list[str]:
    trace.clear()
    assert linear_startup.run_linear_startup(_Starter("success"), _options()) == 0
    return list(trace)


def _tui_trace(trace) -> list[str]:
    trace.clear()

    async def compose(args):
        trace.append(f"compose:{args[0]}")
        return 0

    codes, _statuses, succeeded = _run_tui("success", compose=compose)
    assert codes == [0] and succeeded is True
    return list(trace)


def test_both_front_ends_execute_the_same_stage_ids(trace):
    linear = _linear_trace(trace)
    tui = [stage for stage in _tui_trace(trace) if not stage.startswith("compose:")]
    assert linear == tui == _ids()


def test_a_new_stage_needs_one_registration(trace, monkeypatch):
    extra = launch_plan.LaunchStage(
        "throwaway", "Throwaway", lambda starter, _i: starter.throwaway_stage()
    )
    monkeypatch.setattr(
        launch_plan, "CONFIGURE_STAGES", (*launch_plan.CONFIGURE_STAGES, extra)
    )
    assert _linear_trace(trace)[-1] == "throwaway"
    tui = _tui_trace(trace)
    assert tui[tui.index("compose:up") - 1] == "throwaway"


def test_the_comfyui_manifest_follows_litellm_and_precedes_compose_up(trace):
    tui = _tui_trace(trace)
    litellm = tui.index("generate-litellm-configuration")
    comfyui = tui.index("generate-comfyui-manifest")
    assert litellm < comfyui < tui.index("compose:up")


def test_stage_ids_are_unique():
    assert len(set(_ids())) == len(_ids())


def test_no_front_end_keeps_its_own_step_list():
    """The Textual screen had a second ``steps = [`` list (#1043)."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "ui" / "textual" / "screens" / "wizard_screen.py"
    ).read_text(encoding="utf-8")
    assert "        steps = [\n" not in source
    assert "launch_plan.CONFIGURE_STAGES" in source


# ─── The executor's structured results ────────────────────────────────


def _stage(run):
    return launch_plan.LaunchStage("probe", "Probe", run)


def _run(run, **kwargs):
    return launch_plan.run_stage(
        _stage(run), object(), launch_plan.LaunchInputs(), **kwargs
    )


def test_a_stage_that_returns_true_succeeded():
    result = _run(lambda *_: True)
    assert (result.stage_id, result.status, result.ok) == ("probe", launch_plan.SUCCEEDED, True)


def test_a_stage_that_returns_false_failed_validation():
    result = _run(lambda *_: False)
    assert (result.status, result.ok) == (launch_plan.FAILED, False)


def test_an_explained_refusal_failed_with_its_reason():
    def refuse(*_):
        raise launch_plan.StageFailure("SUPABASE_JWT_SECRET is still a placeholder")

    result = _run(refuse)
    assert (result.status, result.detail) == (
        launch_plan.FAILED, "SUPABASE_JWT_SECRET is still a placeholder",
    )


def test_a_stage_that_raises_crashed_and_keeps_the_exception():
    error = RuntimeError("port 63000 already bound")
    result = _run(lambda *_: (_ for _ in ()).throw(error))
    assert (result.status, result.detail, result.error) == (
        launch_plan.CRASHED, "port 63000 already bound", error,
    )


def test_a_requested_cancel_skips_the_stage():
    ran: list[bool] = []
    result = _run(lambda *_: ran.append(True) or True, should_stop=lambda: True)
    assert result.status == launch_plan.CANCELLED and ran == []


def test_a_failed_stage_stops_both_front_ends_before_compose(trace, monkeypatch):
    """Partial startup: the stages after the failure never run."""

    class Failing(_Starter):
        def generate_kong_configuration(self):
            return False

    trace.clear()
    assert linear_startup.run_linear_startup(Failing("success"), _options()) == 1
    assert trace[-1] == "generate-kong-configuration"

    trace.clear()

    async def compose(args):
        trace.append(f"compose:{args[0]}")
        return 0

    _codes, statuses, succeeded = _run_tui("success", Failing("success"), compose=compose)
    assert trace[-1] == "generate-kong-configuration"
    assert succeeded is False
    assert "  ✗ Generate Kong configuration failed" in statuses
