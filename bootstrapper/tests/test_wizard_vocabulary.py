"""The word "profile" belongs to deployment hardening, and only there.

Atlas has two distinct, separately-configured concepts:

* **tracks** (``bootstrapper/tracks.yml``) — which workload you are
  building, which decides the set of services the wizard prompts for;
* **profiles** (``bootstrapper/profiles.yml``) — ``default`` (dev) vs
  ``prod`` deployment hardening.

The track picker was titled ``"Track  ·  pick your profile"`` and asked
*"Which profile fits what you're building?"*, spending the word one step
before the real profile step arrived. A live run read as though the
prod/dev feature were missing entirely — it is not; it is step 2 and its
selection is honoured — the vocabulary just hid it.

Also pinned here: both ``--track`` and ``--profile`` reach the command
summary. Neither was emitted before, so a summary advertised as a
copy-pasteable command silently dropped two real flags, and a pasted
command would not reproduce the run it claimed to describe.
"""
from __future__ import annotations

from core.config_parser import ConfigParser
from ui.textual import integration as I


class _HostsManager:
    def __getattr__(self, _name):
        return lambda *a, **k: False


def _steps():
    steps, *_ = I._build_steps_and_rows(ConfigParser(), _HostsManager())
    return steps


def _step_titled(prefix: str):
    return next(s for s in _steps() if s.title.startswith(prefix))


def test_the_track_picker_does_not_call_itself_a_profile() -> None:
    track = _step_titled("Track")
    assert "profile" not in track.title.lower(), track.title
    assert "profile" not in (track.heading or "").lower(), track.heading


def test_the_hardening_step_still_owns_the_word_profile() -> None:
    profile = _step_titled("Profile")
    assert "profile" in profile.title.lower()
    # Its options are what actually distinguish dev from prod.
    values = {o.value for o in profile.options}
    assert values == {"default", "prod"}, values


def test_exactly_one_step_uses_the_word_profile_in_its_title() -> None:
    titled = [s.title for s in _steps() if "profile" in s.title.lower()]
    assert len(titled) == 1, f"'profile' is overloaded across steps: {titled}"


def test_the_track_picker_still_resolves_after_the_rename() -> None:
    """The title is a selections-dict KEY, not just display text.

    If the constant and the step title ever drift apart, the lookup
    returns None and every service silently stops being track-filtered —
    a quiet wrong answer, not a crash. This pins them together.
    """
    from tracks import load_tracks

    reg = load_tracks()
    skip = I._make_track_skip(
        "weaviate", always_on=reg.always_on, overridden=frozenset(), registry=reg,
    )
    # gen-ai-rag includes weaviate, so it must NOT be skipped — which only
    # holds if the lookup key and the live step title still agree.
    assert skip({I.PICKER_STEP_TITLE: "gen-ai-rag"}) is False
    # And the constant must actually be the title the wizard ships.
    assert _step_titled("Track").title == I.PICKER_STEP_TITLE


def test_profile_reaches_the_command_summary() -> None:
    flags = I.meta_flags_for({I.PROFILE_STEP_TITLE: "prod"})
    assert ("--profile", "prod") in flags


def test_the_default_profile_is_not_emitted() -> None:
    """Defaults stay off the command line — it mirrors a bare ./start.sh."""
    flags = I.meta_flags_for({I.PROFILE_STEP_TITLE: "default"})
    assert not any(f == "--profile" for f, _ in flags)


def test_track_reaches_the_command_summary() -> None:
    flags = I.meta_flags_for({I.PICKER_STEP_TITLE: "ml-eng"})
    assert ("--track", "ml-eng") in flags
