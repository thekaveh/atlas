"""The landing page's hand-written claims stay grounded in their sources.

`docs/index.md` and `README.md` are the two hand-authored surfaces (the
landing source is projected to BOTH the `.io` site and the wiki `Home`), and
the opener is the fastest-decaying region in the repo: it asserts a service
count, a track count, track names, and the always-on core, none of which the
generator derives. `docs/ROADMAP.md` already states the policy — counts come
from `services/*/service.yml`, not hand-maintained totals — but nothing
enforced it, and the landing page had drifted to "60 service families"
(it was counting `services/*/` DIRECTORIES, three of which are doc-only
folders that own no manifest) while claiming 7 tracks above only 6 cards.

These tests are the enforcement the policy was missing.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "docs" / "index.md"
README = ROOT / "README.md"
TRACKS = ROOT / "bootstrapper" / "tracks.yml"


def _service_families() -> int:
    """A service family is a `service.yml` owner.

    `bootstrapper/services/manifests.py::_is_service_dir` requires the
    manifest, so doc-only folders (`stt-provider`, `doc-processor`,
    `multi2vec-clip`) are NOT families — counting `services/*/` directories
    overstates the platform by exactly those three.
    """
    return len(list((ROOT / "services").glob("*/service.yml")))


def _track_display_names() -> list[str]:
    raw = yaml.safe_load(TRACKS.read_text(encoding="utf-8"))["tracks"]
    entries = raw if isinstance(raw, list) else list(raw.values())
    return [entry["display_name"] for entry in entries]


def test_the_landing_service_family_count_matches_the_manifests():
    text = INDEX.read_text(encoding="utf-8")
    match = re.search(r"Atlas organizes (\d+) service families", text)
    assert match, "the landing page no longer states a service-family count"
    claimed = int(match.group(1))
    actual = _service_families()
    assert claimed == actual, (
        f"docs/index.md claims {claimed} service families but "
        f"services/*/service.yml has {actual}. Counting services/*/ directories "
        f"instead would include the doc-only folders, which own no manifest."
    )


def test_the_landing_track_count_matches_tracks_yml():
    text = INDEX.read_text(encoding="utf-8")
    match = re.search(r"into (\d+) tracks", text)
    assert match, "the landing page no longer states a track count"
    assert int(match.group(1)) == len(_track_display_names())


def test_the_landing_renders_a_card_for_every_track():
    """The claim and the evidence directly beneath it must agree — the page
    said "7 tracks" above six cards, with All / Custom missing entirely."""
    text = INDEX.read_text(encoding="utf-8")
    section = text.split("## 1. Capabilities", 1)[1].split("\n## ", 1)[0]
    titles = re.findall(r'atlas-card__title">([^<]+)<', section)
    assert titles == _track_display_names(), (
        f"track cards {titles} do not match tracks.yml {_track_display_names()}"
    )


def test_both_hand_authored_surfaces_use_the_canonical_track_names():
    """Display names verbatim, not internal slugs and not re-spaced variants.

    README carried `Trading/Financial Research` and `All/Custom` while
    tracks.yml (and the landing page) say `Trading / Financial Research` and
    `All / Custom` — so the two hand-authored surfaces disagreed with the
    source and with each other.
    """
    readme = README.read_text(encoding="utf-8")
    index = INDEX.read_text(encoding="utf-8")
    missing = [
        (name, surface)
        for name in _track_display_names()
        for surface, text in (("README.md", readme), ("docs/index.md", index))
        if name not in text
    ]
    assert not missing, f"track display names missing verbatim: {missing}"


def test_the_always_on_core_claim_matches_the_locked_tier():
    """The opener names the always-on tier; it must match the documented one."""
    readme = README.read_text(encoding="utf-8")
    match = re.search(r"always-on core is ([^.]+)\.", readme)
    assert match, "README no longer states the always-on core"
    claimed = {
        word.strip().removeprefix("the ").removesuffix(" API").strip().lower()
        for word in re.split(r",| and ", match.group(1))
        if word.strip()
    }
    assert claimed == {"kong", "supabase", "redis", "litellm", "backend"}, claimed
