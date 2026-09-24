"""The wizard states what is true, not what sounds reassuring (#1032).

Three claims in the wizard's own copy were false against
``bootstrapper/profiles.yml``, the post-start probes were wrapped in a
blanket ``contextlib.suppress(Exception)`` dispatched after the success
message, and the off-track service marking was computed only for a
``--track`` supplied on the CLI.

This module covers the subset of #1032 that does not depend on #1043's
shared launch plan and executor. The criteria that do are recorded on the
ticket and deliberately not asserted here — pinning agreement between two
independent pipelines would pin coincidence, not a contract.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from core.config_parser import ConfigParser
from tracks import remark_off_track_rows
from ui.textual import integration as I
from ui.textual.screens.wizard_screen import WizardScreen
from ui.textual.widgets import PromptStep, ServiceRow

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILES = yaml.safe_load((REPO_ROOT / "bootstrapper" / "profiles.yml").read_text())["profiles"]


class _HostsManager:
    def __getattr__(self, _name):
        return lambda *a, **k: False


def _steps():
    steps, *_ = I._build_steps_and_rows(ConfigParser(), _HostsManager())
    return steps


def _step_titled(prefix: str) -> PromptStep:
    return next(s for s in _steps() if s.title.startswith(prefix))


# ─── The copy matches profiles.yml ────────────────────────────────────


def test_both_profiles_really_do_bind_loopback():
    """The premise for the two bind assertions below."""
    assert PROFILES["default"]["host_bind_ip"] == "127.0.0.1:"
    assert PROFILES["prod"]["host_bind_ip"] == "127.0.0.1:"


def test_no_profile_copy_claims_a_wide_open_bind():
    """The dev hint said "0.0.0.0 ports" while profiles.yml binds loopback —
    a security-relevant claim that was wrong in the alarming direction."""
    step = _step_titled("Profile")
    surfaces = [step.subtitle] + [f"{o.label} {o.hint}" for o in step.options]
    for text in surfaces:
        assert "0.0.0.0" not in text, text


def test_profile_copy_only_claims_resource_limits_if_the_overlay_sets_any():
    """The prod subtitle promised "resource limits". The prod overlay sets
    LOG_MAX_SIZE, LOG_MAX_FILE and two sources — no limit of any kind."""
    prod_env = PROFILES["prod"].get("env", {}) or {}
    sets_limits = any(
        re.search(r"(LIMIT|MEMORY|CPU|RESERVATION)", key.upper())
        for key in prod_env
    )
    step = _step_titled("Profile")
    text = " ".join([step.subtitle] + [o.hint for o in step.options]).lower()
    if not sets_limits:
        assert "resource limit" not in text
        assert "resource limits" not in text


def test_the_prod_copy_names_what_the_overlay_actually_changes():
    prod = PROFILES["prod"]
    step = _step_titled("Profile")
    text = " ".join([step.subtitle] + [o.hint for o in step.options]).lower()
    if (prod.get("env") or {}).get("LOG_MAX_SIZE"):
        assert "log rotation" in text
    if (prod.get("sources") or {}).get("prometheus") == "container":
        assert "observability" in text or "prometheus" in text


def test_the_bind_claim_is_stated_as_common_to_both_profiles():
    """Framing loopback as a prod feature implied dev was exposed."""
    step = _step_titled("Profile")
    assert "127.0.0.1" in step.subtitle
    assert "both profiles" in step.subtitle.lower()


# ─── Prompted is not the same as running ──────────────────────────────


def test_the_track_subtitle_does_not_call_optional_services_always_on():
    """Prometheus and Grafana are always PROMPTED and default to off."""
    step = _step_titled("Track")
    assert "always-on" not in step.subtitle.lower()
    assert "asked in every track" in step.subtitle.lower()


def test_the_track_subtitle_says_the_optional_two_default_to_off():
    step = _step_titled("Track")
    assert "default to off" in step.subtitle.lower()


def test_prometheus_and_grafana_really_do_default_to_disabled():
    """The premise. If either ever ships enabled, the copy above is wrong
    again and this test is where that surfaces."""
    env = ConfigParser().parse_env_file()
    for var in ("PROMETHEUS_SOURCE", "GRAFANA_SOURCE"):
        example = (REPO_ROOT / ".env.example").read_text()
        match = re.search(rf"^{var}=(.*)$", example, re.M)
        assert match, var
        assert match.group(1).strip() == "disabled", (var, match.group(1))


# ─── Post-start verification reports an outcome ───────────────────────


class _Recorder:
    """The slice of WizardScreen the probe reporting touches."""

    def __init__(self):
        self.logs: list[tuple[str, str]] = []
        self.status: list[tuple[str, str]] = []

    def _safe_log(self, msg, source="", level="info"):
        self.logs.append((msg, level))

    def _write_status(self, msg, style="", source=""):
        self.status.append((msg, style))

    async def _run_probe(self, name, probe, on_line):
        return await WizardScreen._run_probe(self, name, probe, on_line)

    def _report_verification(self, outcomes):
        return WizardScreen._report_verification(self, outcomes)


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_a_passing_probe_reports_verified():
    rec = _Recorder()
    calls = []
    result = _run(rec._run_probe("ports", lambda on_line: calls.append(1), None))
    assert result == ("ports", "verified", "")
    assert calls == [1]


def test_a_raising_probe_reports_unverified_and_logs_the_reason():
    """The defect: this used to be swallowed by contextlib.suppress, so a
    failed port check left "All services started" as the last word."""
    rec = _Recorder()

    def _boom(on_line):
        raise RuntimeError("port 63000 already bound")

    name, outcome, detail = _run(rec._run_probe("ports", _boom, None))
    assert (name, outcome) == ("ports", "unverified")
    assert "port 63000 already bound" in detail
    assert any("did not complete" in msg and lvl == "error" for msg, lvl in rec.logs)


def test_an_absent_probe_reports_skipped_with_a_reason():
    rec = _Recorder()
    name, outcome, detail = _run(rec._run_probe("comfyui-models", None, None))
    assert (name, outcome) == ("comfyui-models", "skipped")
    assert detail, "a skip must carry its reason"


def test_an_unverified_probe_qualifies_the_headline():
    rec = _Recorder()
    rec._report_verification([
        ("ports", "unverified", "boom"),
        ("comfyui-models", "verified", ""),
    ])
    headline = " ".join(msg for msg, _ in rec.status)
    assert "not verified" in headline
    assert "ports" in headline
    assert "Logs tab" in headline, "a failure must name a next action"


def test_a_skipped_probe_is_reported_as_skipped_not_as_a_pass():
    rec = _Recorder()
    rec._report_verification([
        ("ports", "verified", ""),
        ("comfyui-models", "skipped", "not available in this configuration"),
    ])
    headline = " ".join(msg for msg, _ in rec.status)
    assert "skipped" in headline
    assert "comfyui-models" in headline


def test_all_probes_passing_reports_a_clean_verification():
    rec = _Recorder()
    rec._report_verification([("ports", "verified", ""), ("m", "verified", "")])
    headline = " ".join(msg for msg, _ in rec.status)
    assert "verification passed" in headline
    assert "not verified" not in headline


def test_every_outcome_reaches_the_log_with_its_severity():
    rec = _Recorder()
    rec._report_verification([
        ("ports", "unverified", "boom"),
        ("comfyui-models", "skipped", "why"),
    ])
    levels = {msg.split("]")[0]: lvl for msg, lvl in rec.logs}
    assert any(lvl == "error" for lvl in levels.values())
    assert len(rec.logs) >= 2, "nothing may be silently dropped"


def test_the_blanket_suppress_is_gone_from_the_post_up_checks():
    """Structural guard: the two probes must not be re-wrapped in a bare
    suppress, which is what made the outcome invisible."""
    source = (
        REPO_ROOT / "bootstrapper" / "ui" / "textual" / "screens" / "wizard_screen.py"
    ).read_text()
    block = source[source.index("async def _post_up_checks"):]
    block = block[: block.index("self.run_worker(_post_up_checks")]
    # Comments explain what was removed and why, so judge the code only.
    code = "\n".join(
        line for line in block.splitlines() if not line.lstrip().startswith("#")
    )
    assert "contextlib.suppress" not in code, code


# ─── Interactive track switching matches the resolver ─────────────────


def _rows_and_info():
    _steps_, rows, services_info, *_ = I._build_steps_and_rows(
        ConfigParser(), _HostsManager()
    )
    return rows, services_info


def test_a_track_pick_marks_exactly_the_services_the_track_excludes():
    rows, services_info = _rows_and_info()
    from tracks import is_in_track, load_tracks, normalize_service_key

    registry = load_tracks()
    for key in ("data-eng", "gen-ai-rag"):
        track = registry.by_key[key]
        marked = remark_off_track_rows(key, rows, services_info=services_info)
        expected = {
            svc.display_name for svc in services_info
            if not is_in_track(track, svc.key, always_on=registry.always_on)
        }
        assert {r.name for r in marked if r.off_track} == expected, key


def test_different_tracks_really_do_exclude_different_sets():
    rows, services_info = _rows_and_info()
    a = {r.name for r in remark_off_track_rows("data-eng", rows, services_info=services_info) if r.off_track}
    b = {r.name for r in remark_off_track_rows("gen-ai-rag", rows, services_info=services_info) if r.off_track}
    assert a and b and a != b


def test_the_all_track_excludes_nothing():
    rows, services_info = _rows_and_info()
    out = remark_off_track_rows("all", rows, services_info=services_info)
    assert not any(r.off_track for r in out)


def test_an_unknown_track_clears_the_marking_rather_than_guessing():
    rows, services_info = _rows_and_info()
    seeded = [ServiceRow(name=r.name, off_track=True) for r in rows]
    out = remark_off_track_rows("no-such-track", seeded, services_info=services_info)
    assert not any(r.off_track for r in out)


def test_an_overridden_service_is_never_marked_off_track():
    rows, services_info = _rows_and_info()
    excluded = [
        r.name for r in remark_off_track_rows("data-eng", rows, services_info=services_info)
        if r.off_track
    ]
    assert excluded, "fixture needs at least one excluded service"
    target = next(s for s in services_info if s.display_name == excluded[0])
    from tracks import normalize_service_key

    out = remark_off_track_rows(
        "data-eng", rows, services_info=services_info,
        overridden=frozenset({normalize_service_key(target.key)}),
    )
    assert target.display_name not in {r.name for r in out if r.off_track}


def test_the_screen_recomputes_rows_on_a_track_commit():
    rows, services_info = _rows_and_info()

    class _Stub:
        def __init__(self):
            self._services = list(rows)
            self._on_track_change = lambda key, current: remark_off_track_rows(
                key, current, services_info=services_info
            )
            self.rendered = []

        class _Table:
            def __init__(self, outer):
                self.outer = outer

            def set_rows(self, rows_):
                self.outer.rendered = list(rows_)

        def _apply_track_change(self, key):
            self._service_table = self._Table(self)
            return WizardScreen._apply_track_change(self, key)

    stub = _Stub()
    stub._apply_track_change("data-eng")
    assert any(r.off_track for r in stub.rendered)
    assert stub.rendered is not rows


def test_a_screen_without_the_callback_is_unaffected():
    class _Stub:
        _on_track_change = None
        _services = []

    WizardScreen._apply_track_change(_Stub(), "data-eng")  # must not raise


# ─── No health indicator is derived from a SOURCE value ───────────────


def test_no_service_row_field_claims_runtime_health():
    """#1032 asks that no health indicator come from a SOURCE value. There
    is no health field at all — this guards against one appearing without
    an observed-state source behind it."""
    fields = set(ServiceRow.__dataclass_fields__)
    for forbidden in ("health", "healthy", "running", "up", "status"):
        assert forbidden not in fields, forbidden


def test_the_service_table_never_words_a_source_as_running_or_healthy():
    table = (
        REPO_ROOT / "bootstrapper" / "ui" / "textual" / "widgets" / "service_table.py"
    ).read_text()
    lowered = table.lower()
    for forbidden in ("healthy", "is running", "running ✓"):
        assert forbidden not in lowered, forbidden
