"""Cloud model discovery reports its own provenance (#1180).

Every failure path used to return a bare ``[]``, so the wizard showed the
curated catalog under a caption that read "Live from /v1/models" whether
the provider had answered, rejected the key, or never been reached. A row
in the picker therefore looked like proof the key worked.

These pin the three things the fix owes: a rejected key, a timeout and an
empty success are distinguishable; catalog rows are never labelled live;
and recovery keeps the models the user already saved without ever putting
the key on screen or in the log.
"""

from __future__ import annotations

import io
import socket
import urllib.error
import urllib.request

import pytest

from utils import cloud_models as cm
from utils.cloud_providers import CLOUD_PROVIDERS
from wizard import llm_steps
from wizard.llm_steps import (
    BADGE_CATALOG,
    BADGE_LIVE,
    BADGE_SAVED,
    _curated_rows,
    build_cloud_steps,
    cloud_models_title,
)

_KEY = "sk-test-DO-NOT-LEAK-0123456789"

_OPENAI = CLOUD_PROVIDERS[0]
_ANTHROPIC = CLOUD_PROVIDERS[1]
_OPENROUTER = CLOUD_PROVIDERS[2]


class _Resp(io.BytesIO):
    """Minimal stand-in for the object ``urlopen`` yields."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _serves(body: str):
    def _open(req, timeout=None):
        return _Resp(body.encode("utf-8"))
    return _open


def _raises(exc: BaseException):
    def _open(req, timeout=None):
        raise exc
    return _open


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.invalid/v1/models", code, f"status {code}", {}, None,
    )


def _discover_openai(monkeypatch, opener, sink):
    monkeypatch.setattr(urllib.request, "urlopen", opener)
    return cm.discover_openai_models(_KEY, timeout=0.01, on_warn=sink.append)


# ─── AC1: the three failures are distinguishable ────────────────────


@pytest.mark.parametrize(
    ("opener", "expected"),
    [
        (_raises(_http_error(401)), cm.UNAUTHORIZED),
        (_raises(_http_error(403)), cm.UNAUTHORIZED),
        (_raises(_http_error(429)), cm.RATE_LIMITED),
        (_raises(_http_error(500)), cm.HTTP_ERROR),
        (_raises(socket.timeout("timed out")), cm.TIMEOUT),
        (_raises(urllib.error.URLError(socket.timeout("timed out"))), cm.TIMEOUT),
        (_raises(urllib.error.URLError("name resolution failed")), cm.UNREACHABLE),
        (_serves("not json at all"), cm.MALFORMED),
        (_serves('{"object": "list"}'), cm.MALFORMED),
        (_serves('{"data": []}'), cm.EMPTY),
        (_serves('{"data": [{"id": "dall-e-3"}]}'), cm.EMPTY),
    ],
)
def test_each_failure_reports_its_own_state(monkeypatch, opener, expected):
    result = _discover_openai(monkeypatch, opener, [])
    assert result.state == expected
    assert result.models == []
    assert not result.is_live


def test_a_401_a_timeout_and_an_empty_success_are_three_distinct_states(monkeypatch):
    """AC1: the states the wizard renders must not collapse into one.

    ``HTTPError`` subclasses ``URLError``; catching them together is what
    made a rejected key indistinguishable from a dropped connection.
    """
    states = {
        _discover_openai(monkeypatch, _raises(_http_error(401)), []).state,
        _discover_openai(monkeypatch, _raises(socket.timeout()), []).state,
        _discover_openai(monkeypatch, _serves('{"data": []}'), []).state,
    }
    assert len(states) == 3


def test_a_successful_listing_is_live(monkeypatch):
    result = _discover_openai(
        monkeypatch, _serves('{"data": [{"id": "gpt-5"}, {"id": "gpt-4o"}]}'), [],
    )
    assert result.state == cm.LIVE
    assert result.is_live
    assert [m.id for m in result.models] == ["gpt-4o", "gpt-5"]
    assert result.detail == ""


def test_no_key_never_reaches_the_network(monkeypatch):
    def _explode(req, timeout=None):
        raise AssertionError("discovery must not call out without a key")

    monkeypatch.setattr(urllib.request, "urlopen", _explode)
    assert cm.discover_openai_models("").state == cm.NO_KEY
    assert cm.discover_anthropic_models("").state == cm.NO_KEY


def test_every_fallback_state_is_accounted_for():
    assert cm.LIVE not in cm.FALLBACK_STATES
    assert set(llm_steps._FALLBACK_CAPTIONS) == set(cm.FALLBACK_STATES)


# ─── The other two providers classify identically ───────────────────


def test_anthropic_classifies_the_same_way(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _raises(_http_error(401)))
    assert cm.discover_anthropic_models(_KEY).state == cm.UNAUTHORIZED
    monkeypatch.setattr(
        urllib.request, "urlopen",
        _serves('{"data": [{"id": "claude-x", "display_name": "Claude X"}]}'),
    )
    live = cm.discover_anthropic_models(_KEY)
    assert live.state == cm.LIVE
    assert [m.label for m in live.models] == ["Claude X"]


def test_openrouter_classifies_the_same_way_without_a_key(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _raises(socket.timeout()))
    assert cm.discover_openrouter_models().state == cm.TIMEOUT
    monkeypatch.setattr(
        urllib.request, "urlopen", _serves('{"data": [{"id": "vendor/m1"}]}'),
    )
    live = cm.discover_openrouter_models()
    assert live.state == cm.LIVE
    assert [m.id for m in live.models] == ["openrouter/vendor/m1"]


def test_openrouter_says_so_when_it_truncates(monkeypatch):
    rows = ",".join(f'{{"id": "v/m{i:03d}"}}' for i in range(12))
    monkeypatch.setattr(urllib.request, "urlopen", _serves(f'{{"data": [{rows}]}}'))
    result = cm.discover_openrouter_models(cap=5)
    assert result.state == cm.LIVE
    assert len(result.models) == 5
    assert "5" in result.detail and "12" in result.detail


def test_a_fallback_detail_only_carries_what_its_state_does_not_say(monkeypatch):
    """A caption that reads "the request timed out (request timed out)" is
    a defect, so the timeout detail is deliberately empty."""
    assert _discover_openai(monkeypatch, _raises(socket.timeout()), []).detail == ""
    assert cm.discover_openai_models("").detail == ""
    unauthorized = _discover_openai(monkeypatch, _raises(_http_error(401)), [])
    assert unauthorized.detail == "HTTP 401"


# ─── AC3 (second half): the key never appears ───────────────────────


@pytest.mark.parametrize(
    "opener",
    [
        _raises(_http_error(401)),
        _raises(urllib.error.URLError(f"connecting with {_KEY} failed")),
        _serves("not json"),
        _serves('{"data": []}'),
    ],
)
def test_the_api_key_is_never_logged_or_returned(monkeypatch, opener):
    sink: list[str] = []
    result = _discover_openai(monkeypatch, opener, sink)
    assert _KEY not in result.detail
    assert not any(_KEY in line for line in sink)
    assert sink, "a fallback must say why in the session log"


def test_a_failure_reason_reaches_the_session_log(monkeypatch):
    sink: list[str] = []
    _discover_openai(monkeypatch, _raises(_http_error(401)), sink)
    assert any("401" in line for line in sink)


# ─── Back-compat wrappers ───────────────────────────────────────────


def test_list_wrappers_still_return_plain_model_lists(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _serves('{"data": [{"id": "gpt-5"}]}'))
    assert [m.id for m in cm.list_openai_models(_KEY)] == ["gpt-5"]
    monkeypatch.setattr(urllib.request, "urlopen", _raises(_http_error(401)))
    assert cm.list_openai_models(_KEY) == []
    assert cm.list_openrouter_models() == []
    assert cm.list_anthropic_models(_KEY) == []


# ─── Wizard captions ────────────────────────────────────────────────


def _subtitle(state, detail=""):
    return llm_steps._provenance_subtitle(state, detail)


def test_only_a_live_listing_is_captioned_live():
    assert "Live from the provider" in _subtitle(cm.LIVE)
    for state in sorted(cm.FALLBACK_STATES):
        caption = _subtitle(state)
        assert "Live" not in caption
        assert "Curated catalog" in caption
        assert "credentials unverified" in caption


def test_the_three_failure_captions_read_differently():
    """AC1 at the surface the user actually sees."""
    captions = {
        _subtitle(cm.UNAUTHORIZED),
        _subtitle(cm.TIMEOUT),
        _subtitle(cm.EMPTY),
    }
    assert len(captions) == 3


def test_a_long_error_string_cannot_push_the_option_list_off_screen():
    short = _subtitle(cm.UNREACHABLE, "x" * 10)
    long = _subtitle(cm.UNREACHABLE, "x" * 500)
    assert "…" in long
    assert len(long) - len(short) <= llm_steps._MAX_DETAIL


def test_a_fallback_caption_names_the_retry():
    assert "Esc" in _subtitle(cm.UNAUTHORIZED)


def test_the_pre_fetch_caption_claims_nothing():
    caption = _subtitle(None)
    assert "Live" not in caption
    assert "Curated catalog" not in caption


# ─── AC2 + AC3: badges and preserved selections ─────────────────────


def _cloud_step(env_vars, warn=None):
    steps = build_cloud_steps(env_vars, warn or (lambda _msg: None))
    wanted = cloud_models_title(_OPENAI.name)
    return next(s for s in steps if s.title == wanted)


def _openai_env(**extra):
    env = {
        _OPENAI.api_key_var: _KEY,
        _OPENAI.source_var: "enabled",
    }
    env.update(extra)
    return env


def test_live_rows_are_badged_live_and_the_caption_agrees(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen", _serves('{"data": [{"id": "gpt-5"}]}'),
    )
    step = _cloud_step(_openai_env())
    options = step.options_provider({})
    assert [o.value for o in options] == ["gpt-5"]
    assert options[0].badges == [BADGE_LIVE]
    assert "Live from the provider" in step.subtitle_provider(options)


def test_fallback_rows_never_carry_a_live_badge(monkeypatch):
    """AC2: a curated row is evidence of nothing about the key."""
    monkeypatch.setattr(urllib.request, "urlopen", _raises(_http_error(401)))
    step = _cloud_step(_openai_env())
    options = step.options_provider({})
    assert options, "the fallback must still offer the curated catalog"
    for opt in options:
        assert BADGE_LIVE not in opt.badges
        assert BADGE_CATALOG in opt.badges
    caption = step.subtitle_provider(options)
    assert "Curated catalog" in caption
    assert "rejected the key" in caption
    assert "HTTP 401" in caption


def test_the_caption_tracks_the_outcome_of_the_last_fetch(monkeypatch):
    step = _cloud_step(_openai_env())
    assert "Live" not in step.subtitle  # the pre-fetch placeholder
    monkeypatch.setattr(urllib.request, "urlopen", _raises(socket.timeout()))
    timed_out = step.options_provider({})
    assert "timed out" in step.subtitle_provider(timed_out)
    monkeypatch.setattr(
        urllib.request, "urlopen", _serves('{"data": [{"id": "gpt-5"}]}'),
    )
    live = step.options_provider({})
    assert "Live from the provider" in step.subtitle_provider(live)


def test_the_caption_follows_the_rows_it_is_shown_not_the_last_fetch(monkeypatch):
    """A discarded worker (Esc mid-fetch) still stamps its outcome. Landing
    late it must not caption a catalog list as live, nor a live list as a
    fallback — so the badges on the rendered rows decide."""
    monkeypatch.setattr(
        urllib.request, "urlopen", _serves('{"data": [{"id": "gpt-5"}]}'),
    )
    step = _cloud_step(_openai_env())
    live = step.options_provider({})
    catalog = _curated_rows(_OPENAI.key)

    assert "Live from the provider" in step.subtitle_provider(live)
    stale_live_caption = step.subtitle_provider(catalog)
    assert "Live" not in stale_live_caption
    assert "Curated catalog" in stale_live_caption

    monkeypatch.setattr(urllib.request, "urlopen", _raises(_http_error(401)))
    step.options_provider({})
    assert "Live from the provider" in step.subtitle_provider(live)


def test_a_failed_lookup_keeps_every_already_saved_model_selectable(monkeypatch):
    """AC3: recovery preserves the user's selections.

    ``PromptPanel.load_step`` drops any ``default_values`` entry with no
    matching row, and the next Enter commits the shortened CSV. So a
    live-only model id saved in .env used to disappear from
    ``OPENAI_USER_MODELS`` the first time discovery failed.
    """
    saved = "gpt-5-live-only-2099"
    monkeypatch.setattr(urllib.request, "urlopen", _raises(_http_error(401)))
    step = _cloud_step(_openai_env(**{_OPENAI.user_models_var: saved}))
    values = {o.value for o in step.options_provider({})}
    assert saved in values
    assert set(step.default_values) <= values

    carried = next(o for o in step.options_provider({}) if o.value == saved)
    assert carried.badges == [BADGE_SAVED]
    assert BADGE_LIVE not in carried.badges


def test_a_saved_model_the_provider_does_list_is_not_duplicated(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen", _serves('{"data": [{"id": "gpt-5"}]}'),
    )
    step = _cloud_step(_openai_env(**{_OPENAI.user_models_var: "gpt-5"}))
    values = [o.value for o in step.options_provider({})]
    assert values == ["gpt-5"]


def test_the_key_never_reaches_the_picker_or_its_caption(monkeypatch):
    """AC3: recovery must not print the key."""
    sink: list[str] = []
    monkeypatch.setattr(urllib.request, "urlopen", _raises(_http_error(401)))
    step = _cloud_step(_openai_env(), warn=sink.append)
    options = step.options_provider({})
    caption = step.subtitle_provider(options)
    assert _KEY not in caption
    assert not any(_KEY in (o.value + o.label + o.hint) for o in options)
    assert not any(_KEY in line for line in sink)


def test_a_typed_key_is_used_and_still_never_printed(monkeypatch):
    from wizard.llm_steps import cloud_secret_title

    sink: list[str] = []
    seen: list[str] = []

    def _open(req, timeout=None):
        seen.append(req.get_header("Authorization") or "")
        raise _http_error(401)

    monkeypatch.setattr(urllib.request, "urlopen", _open)
    step = _cloud_step({_OPENAI.source_var: "enabled"}, warn=sink.append)
    typed = "sk-typed-into-the-wizard-9999"
    options = step.options_provider({cloud_secret_title(_OPENAI.name): typed})
    assert seen == [f"Bearer {typed}"]
    assert not any(typed in line for line in sink)
    assert typed not in step.subtitle_provider(options)


def test_a_provider_with_no_discovery_route_does_not_blame_the_key():
    """A fourth provider added to the registry without a fetcher must not
    caption "no API key was supplied" when the user supplied one."""
    result = llm_steps._discover_cloud_models("together", _KEY, lambda _m: None)
    assert result.state == cm.UNREACHABLE
    assert "together" in result.detail
    assert "no API key" not in _subtitle(result.state, result.detail)


def test_an_exploding_fetcher_becomes_a_fallback_not_an_empty_picker(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError(f"leaks {_KEY}")

    sink: list[str] = []
    monkeypatch.setattr(llm_steps, "discover_openai_models", _boom)
    result = llm_steps._discover_cloud_models("openai", _KEY, sink.append)
    assert result.state == cm.UNREACHABLE
    assert _KEY not in result.detail
    assert not any(_KEY in line for line in sink)


def test_every_cloud_provider_gets_a_provenance_caption(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _raises(socket.timeout()))
    env = {p.api_key_var: _KEY for p in CLOUD_PROVIDERS}
    steps = build_cloud_steps(env, lambda _m: None)
    titles = {cloud_models_title(p.name) for p in CLOUD_PROVIDERS}
    pickers = [s for s in steps if s.title in titles]
    assert len(pickers) == len(CLOUD_PROVIDERS)
    for step in pickers:
        assert step.subtitle_provider is not None
        assert step.wrap_subtitle, "the provenance line needs more than one row"
        options = step.options_provider({})
        assert "timed out" in step.subtitle_provider(options)


# ─── The screen actually renders the resolved caption ───────────────


def test_render_time_resolution_prefers_the_provider_but_not_while_loading():
    from ui.textual.screens.wizard_screen import _resolved_subtitle
    from ui.textual.widgets.prompt_panel import PromptStep

    step = PromptStep(
        title="t", step_index=1, step_total=1, heading="h",
        subtitle="placeholder", kind="multiselect",
        subtitle_provider=lambda _options: "resolved",
    )
    assert _resolved_subtitle(step, [], False) == "resolved"
    assert _resolved_subtitle(step, [], True) == "placeholder"


def test_render_time_resolution_survives_a_broken_provider():
    from ui.textual.screens.wizard_screen import _resolved_subtitle
    from ui.textual.widgets.prompt_panel import PromptStep

    def _boom(_options):
        raise RuntimeError("provider blew up")

    step = PromptStep(
        title="t", step_index=1, step_total=1, heading="h",
        subtitle="placeholder", kind="multiselect", subtitle_provider=_boom,
    )
    assert _resolved_subtitle(step, [], False) == "placeholder"
    empty = PromptStep(
        title="t", step_index=1, step_total=1, heading="h",
        subtitle="placeholder", kind="multiselect",
        subtitle_provider=lambda _options: "",
    )
    assert _resolved_subtitle(empty, [], False) == "placeholder"


def test_a_step_without_a_provider_keeps_its_static_subtitle():
    from ui.textual.screens.wizard_screen import _resolved_subtitle
    from ui.textual.widgets.prompt_panel import PromptStep

    step = PromptStep(title="t", step_index=1, step_total=1,
                      heading="h", subtitle="static")
    assert _resolved_subtitle(step, [], False) == "static"
    assert _resolved_subtitle(step, [], True) == "static"
