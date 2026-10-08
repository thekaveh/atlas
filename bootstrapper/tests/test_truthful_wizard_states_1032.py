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
        self.toasts: list[str] = []

    def notify(self, message, **_kwargs):
        self.toasts.append(message)

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


# ─── Progress counts only the decisions this run will ask (#1182) ────
#
# The counter used to render the raw list position over the whole step
# catalogue, so a narrow track counted prompts the user would never see.
# These live here because they are the same kind of claim #1032 fixed:
# the wizard telling the user something about its state that is not true.

from dataclasses import replace  # noqa: E402

from ui.textual.screens.wizard_screen import _reachable_progress  # noqa: E402
from ui.textual.widgets.prompt_panel import _progress_title  # noqa: E402
from wizard.llm_steps import cloud_models_title, cloud_secret_title  # noqa: E402
from wizard.model.cloud_rules import SECRET_DISABLE, SECRET_ENABLE  # noqa: E402


def _position(steps, selections, index):
    return _reachable_progress(steps, index, dict(selections))


def _reachable(steps, selections, index=None):
    return [
        i for i, step in enumerate(steps)
        if i == index
        or not (step.skip_if_prev is not None and step.skip_if_prev(selections))
    ]


def _plain(title, skip=None):
    return PromptStep(title=title, step_index=0, step_total=0, heading=title,
                      skip_if_prev=skip)


def test_a_narrow_track_counts_only_its_own_prompts():
    """AC1: build the real step list, pick a narrow track, and the total is
    the number of prompts that track leaves -- not the catalogue length."""
    steps = _steps()
    selections = {I.PICKER_STEP_TITLE: "gen-ai-rag"}
    reachable = _reachable(steps, selections)
    assert len(reachable) < len(steps), "the premise: this track hides steps"

    for index in reachable:
        ordinal, total, skipped = _position(steps, selections, index)
        assert total == len(reachable)
        assert skipped == len(steps) - len(reachable)
        assert ordinal == reachable.index(index) + 1


def test_disabling_a_provider_updates_the_count_on_the_next_render():
    """AC2 + AC4: the provider's model picker stops counting as a remaining
    decision the moment its key step says "disable", without restarting."""
    from wizard.llm_steps import build_cloud_steps

    env = {"CLOUD_OPENAI_SOURCE": "enabled", "OPENAI_API_KEY": "sk-saved"}
    cloud = build_cloud_steps(env, lambda _msg: None)[:2]
    steps = [_plain("First"), *cloud, _plain("Last")]
    secret, picker = cloud_secret_title("OpenAI"), cloud_models_title("OpenAI")
    assert [s.title for s in steps[1:3]] == [secret, picker]

    before = _position(steps, {secret: SECRET_ENABLE}, 1)
    after = _position(steps, {secret: SECRET_DISABLE}, 1)
    assert before == (2, 4, 0)
    assert after == (2, 3, 1)
    # Remaining decisions from here (this one included) are exactly the
    # still-reachable steps -- the skipped picker is not among them.
    ordinal, total, _ = after
    selections = {secret: SECRET_DISABLE}
    assert total - ordinal + 1 == len([i for i in _reachable(steps, selections, 1) if i >= 1])


def test_back_and_a_track_change_never_put_the_ordinal_past_the_total():
    """AC3: walk forward, change the track, walk back, and check every
    rendered position -- the ordinal can never exceed the total."""
    steps = _steps()
    picker_at = next(i for i, s in enumerate(steps) if s.title == I.PICKER_STEP_TITLE)
    for track in ("all", "gen-ai-rag", "data-eng", "all"):
        selections = {I.PICKER_STEP_TITLE: track}
        walk = list(range(picker_at, len(steps))) + list(range(len(steps) - 1, -1, -1))
        for index in walk:
            ordinal, total, skipped = _position(steps, selections, index)
            assert 1 <= ordinal <= total, (track, index, ordinal, total)
            assert total + skipped <= len(steps) + 1


def test_the_current_step_always_counts_even_at_a_skipped_boundary():
    always_skip = lambda _sel: True  # noqa: E731
    steps = [_plain("A", always_skip), _plain("B"), _plain("C", always_skip)]
    assert _position(steps, {}, 0) == (1, 2, 1)
    assert _position(steps, {}, 2) == (2, 2, 1)


def test_the_caption_separates_done_remaining_and_skipped():
    """AC6: "4 / 9" means three decisions are done and five follow this one;
    the hidden steps are named separately and never counted as remaining."""
    step = PromptStep(title="Models", step_index=4, step_total=9, heading="h",
                      steps_skipped=2)
    title = _progress_title(step)
    assert title.startswith(" Models  ·  4 / 9  ")
    assert title.rstrip().endswith("·  2 skipped")


def test_the_caption_is_unchanged_when_nothing_is_skipped():
    """Preserves the pre-#1182 look when every step is reachable."""
    step = PromptStep(title="Models", step_index=4, step_total=9, heading="h")
    assert "skipped" not in _progress_title(step)
    assert _progress_title(step).startswith(" Models  ·  4 / 9  ")


@pytest.mark.parametrize("size", [(120, 44), (60, 20)])
def test_the_rendered_border_title_counts_reachable_steps(size):
    """The whole path, through a mounted WizardScreen: the panel's border
    title carries the reachable counter, and at the 60x20 floor the counter
    itself survives -- only the trailing skipped segment may be cut."""
    import asyncio

    from textual.app import App

    always_skip = lambda _sel: True  # noqa: E731
    steps = [_plain("Alpha"), _plain("Hidden", always_skip),
             _plain("Hidden too", always_skip), _plain("Omega")]
    screen = WizardScreen(steps=steps, services=[], no_splash=True)

    class _App(App):
        def on_mount(self) -> None:
            self.push_screen(screen)

    async def scenario():
        async with _App().run_test(size=size) as pilot:
            await pilot.pause()
            first = str(screen._prompt.border_title)
            screen._step_index = 3
            screen._load_current_step()
            await pilot.pause()
            return (first, str(screen._prompt.border_title), screen._prompt._step,
                    screen._prompt.size.width)

    try:
        first, last, step, panel_width = asyncio.run(scenario())
    finally:
        screen._close_launch_log_tee()
        if screen._launch_log_path is not None:
            screen._launch_log_path.unlink(missing_ok=True)

    assert "Alpha  ·  1 / 2" in first
    assert "Omega  ·  2 / 2" in last
    assert (step.step_index, step.step_total, step.steps_skipped) == (2, 2, 2)
    assert "2 skipped" in last
    # The border draws the title between its two corners. Even the longest
    # real step title keeps its two-digit counter inside that span, so any
    # truncation at the floor falls on the bar and the skipped segment.
    longest = max(_steps(), key=lambda s: len(s.title))
    caption = _progress_title(replace(longest, step_index=69, step_total=69))
    counter = caption[: caption.index(" / 69") + len(" / 69")]
    assert len(counter) <= panel_width - 2, (counter, panel_width)


def test_source_step_defaults_follow_the_selected_profile_bundle() -> None:
    """Prod enables Prometheus/Grafana, but the steps defaulted to the .env
    value: Enter answered "disabled", which counted as explicit, so a wizard
    prod launch never turned them on (the --no-tui prod path did)."""
    step = _step_titled("Prometheus")
    provider = step.default_value_provider
    assert provider is not None
    assert provider({I.PROFILE_STEP_TITLE: "prod"}) == PROFILES["prod"]["sources"]["prometheus"]
    assert provider({I.PROFILE_STEP_TITLE: "default"}) == step.default_value


def test_filtered_source_value_falls_back_to_the_manifest_default() -> None:
    from services.manifests import load_manifests, manifest_source_default

    manifests = load_manifests(REPO_ROOT / "services")
    # svc.options[0] is "container" for TIKA, but the declared default is not.
    assert manifest_source_default(manifests, "TIKA_SOURCE") == "disabled"
    assert manifest_source_default(manifests, "NO_SUCH_SOURCE") is None


def test_a_pinned_source_keeps_its_env_default_under_the_prod_profile() -> None:
    """A consumer manifest (or .env.user) that set PROMETHEUS_SOURCE=disabled
    under profile: prod got `container` as the wizard default, so Enter
    started Prometheus; --no-tui kept the declared value."""
    steps, *_ = I._build_steps_and_rows(
        ConfigParser(), _HostsManager(), pinned_source_vars=frozenset({"PROMETHEUS_SOURCE"}),
    )
    step = next(s for s in steps if s.title.startswith("Prometheus"))
    assert step.default_value_provider is None
    assert _step_titled("Grafana").default_value_provider is not None


def test_a_consumer_declared_source_is_neither_skipped_nor_dimmed_by_a_wizard_track() -> None:
    """Picked in the wizard, gen-ai-rag skipped and dimmed a MinIO the
    consumer declared, while the launch kept it running (#783)."""
    from tracks import load_tracks

    declared = frozenset({"minio_source"})
    steps, rows, services_info, *_ = I._build_steps_and_rows(
        ConfigParser(), _HostsManager(), consumer_declared=declared,
    )
    registry = load_tracks()
    if "minio" in registry.by_key["gen-ai-rag"].services:
        pytest.skip("gen-ai-rag now includes MinIO; pick another off-track service")
    minio = next(s for s in steps if s.title.startswith("MinIO"))
    assert minio.skip_if_prev({I.PICKER_STEP_TITLE: "gen-ai-rag"}) is False
    from tracks import consumer_override_keys

    overridden = consumer_override_keys(declared, services_info)
    marked = remark_off_track_rows("gen-ai-rag", rows, services_info=services_info, overridden=overridden)
    assert not next(r for r in marked if r.name.startswith("MinIO")).off_track


def test_the_cli_flag_overview_shows_the_profiles_sources_unless_pinned() -> None:
    """`./start.sh --profile prod <flags>` showed Prometheus/Grafana from .env
    (disabled) while apply_profile_overrides then started them."""
    from types import SimpleNamespace

    _steps_, _rows, services_info, *_ = I._build_steps_and_rows(ConfigParser(), _HostsManager())
    names = {s.env_var_name: s.display_name for s in services_info if getattr(s, "env_var_name", "")}
    prometheus = names["PROMETHEUS_SOURCE"]
    from services.profiles import pinned_source_vars, profile_launch_sources

    root = ConfigParser()
    plain = profile_launch_sources(services_info, "prod", root, set())
    assert plain.get(prometheus) == PROFILES["prod"]["sources"]["prometheus"]
    pins = pinned_source_vars(ConfigParser(), SimpleNamespace(_env_user_keys={"PROMETHEUS_SOURCE"}))
    assert prometheus not in profile_launch_sources(services_info, "prod", root, pins)
    assert profile_launch_sources(services_info, None, root, set()) == {}


def test_the_cli_flag_overview_applies_consumer_profile_overrides() -> None:
    """profile_overrides.prod.sources.prometheus: disabled kept Prometheus off
    at launch while the overview (platform bundle only) showed it on."""
    from types import SimpleNamespace

    from services.profiles import profile_launch_sources

    _steps_, _rows, services_info, *_ = I._build_steps_and_rows(ConfigParser(), _HostsManager())
    prometheus = next(s.display_name for s in services_info if getattr(s, "env_var_name", "") == "PROMETHEUS_SOURCE")
    parser = ConfigParser()
    parser.load_consumer_config = lambda: SimpleNamespace(
        profile_overrides={"prod": {"sources": {"prometheus": "disabled"}}}, env_overrides={})
    assert profile_launch_sources(services_info, "prod", parser, set()).get(prometheus) == "disabled"
