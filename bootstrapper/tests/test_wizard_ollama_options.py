"""
Regression tests for the wizard's Ollama model-picker options_provider.

Locks down the two bugs we hit when the wizard's option list didn't
match what was actually on the host's Ollama:

  1. **Per-variant pulled/library status** — a library family with
     mixed-status leaves (e.g. ``qwen3.6:custom-quant`` pulled
     but ``qwen3.6:27b`` not) was rendering every variant as the
     parent's status. After the fix, each ``PromptOption`` carries a
     ``pulled_variants: frozenset[str]`` so leaves can render their
     real status independently.

  2. **Family parent status from /api/tags** — the parent's
     ``status="pulled" if entry.name in pulled_set else "library"``
     check failed because /api/tags returns *tagged* names like
     ``qwen3.6:latest``, never the bare family name ``qwen3.6``. After
     the fix, the family parent gets ``[pulled]`` whenever ANY tag
     belonging to it is in the host's /api/tags.

  3. **Bucket-1 fallback for non-library pulls** — a pulled-on-host
     tag whose family isn't in ollama.com/library at all (custom
     local builds, dev versions) still has to appear in the wizard.
     After the fix, those land as flat top-level options with their
     full tagged name.

The tests mock ``list_pulled_models`` and ``list_library_entries`` so
they don't depend on network access; an accompanying integration test
in ``test_live_catalog_sync.py`` exercises the same path against the
live host Ollama when the stack is up.
"""

from __future__ import annotations

import pytest

from utils.ollama_library import OllamaLibraryEntry
from wizard.llm_steps import build_ollama_steps, OLLAMA_MODELS_TITLE


# ────────────────────────────────────────────────────────────────────────────
# Test fixtures
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def library_qwen3_custom_chat():
    """A small library scrape returning two families with shared
    structure: ``qwen3.6`` (multi-variant) and ``custom-chat`` (multi-variant).
    Mirrors the real listing-page output where families have coarse
    sizes (``27b``, ``35b``) but the detail page exposes finer tags
    like ``custom-quant``."""
    return [
        OllamaLibraryEntry(
            name="qwen3.6",
            capabilities=frozenset({"thinking", "tools"}),
            sizes=("27b", "35b"),
            pulls=1_300_000,
            updated="2 weeks ago",
        ),
        OllamaLibraryEntry(
            name="custom-chat",
            capabilities=frozenset({"vision"}),
            sizes=("26b", "31b"),
            pulls=8_700_000,
            updated="1 month ago",
        ),
        OllamaLibraryEntry(
            name="custom-embed-large",
            capabilities=frozenset({"embedding"}),
            sizes=("335m",),
            pulls=10_600_000,
            updated="1 year ago",
        ),
    ]


def _stub_pulled(names):
    """Build a list_pulled_models stub returning ``names`` regardless
    of the upstream URL argument."""
    def _impl(url, timeout=2.0):
        return list(names)
    return _impl


def _select_localhost():
    """The wizard's selections dict that puts the source at ollama-localhost.
    The options_provider reads ``LLM_PROVIDER_SOURCE`` from selections via
    ``_selected_llm_source`` which falls back to env_vars; setting it on
    the dict is the cleanest signal."""
    # _selected_llm_source uses the "LLM provider · source" step title.
    # We pass it through env_vars instead — same effect.
    return {}


def _get_options_provider(env_vars):
    """Build the Ollama steps and return the multiselect step's
    options_provider closure. Caller invokes it with their
    selections dict to get the live PromptOption list."""
    steps = build_ollama_steps(env_vars=env_vars, warn=lambda _msg: None)
    multistep = next(s for s in steps if s.title == OLLAMA_MODELS_TITLE)
    return multistep.options_provider


# ────────────────────────────────────────────────────────────────────────────
# pulled_variants — the new field that enables per-leaf status
# ────────────────────────────────────────────────────────────────────────────


def test_pulled_variants_captures_every_tagged_pull_for_each_family(
    monkeypatch, library_qwen3_custom_chat,
):
    """``pulled_variants`` for the qwen3.6 PromptOption must include
    every host-pulled tag of qwen3.6 — both the bare ``latest`` and
    the custom-quantized ``custom-quant`` that's not in the
    listing-page sizes tuple. This is the screenshot bug: previously
    ``custom-quant`` would inherit ``[library]`` from the
    parent because the listing page only knew about ``27b``/``35b``."""
    host_tags = [
        "qwen3.6:latest",
        "qwen3.6:custom-quant",
        "custom-chat:31b",
    ]
    monkeypatch.setattr(
        "wizard.llm_steps.list_pulled_models", _stub_pulled(host_tags),
    )
    monkeypatch.setattr(
        "wizard.llm_steps.list_library_entries",
        lambda timeout=5.0: library_qwen3_custom_chat,
    )
    env = {"LLM_PROVIDER_SOURCE": "ollama-localhost"}
    opts = _get_options_provider(env)(_select_localhost())

    qwen = next(o for o in opts if o.value == "qwen3.6")
    assert qwen.pulled_variants == frozenset(
        {"latest", "custom-quant"}
    ), (
        f"qwen3.6 should carry both pulled tags as variants; "
        f"got {qwen.pulled_variants}"
    )

    gemma = next(o for o in opts if o.value == "custom-chat")
    assert gemma.pulled_variants == frozenset({"31b"})

    # A family with zero host pulls keeps an empty pulled_variants.
    embed_opt = next(o for o in opts if o.value == "custom-embed-large")
    assert embed_opt.pulled_variants == frozenset()


def test_family_parent_status_is_pulled_when_any_variant_is_pulled(
    monkeypatch, library_qwen3_custom_chat,
):
    """Family parent badges include ``pulled`` when any tag of the
    family appears on the host — even when the bare family name
    itself never does, which is always the case (/api/tags returns
    tagged names like ``qwen3.6:latest`` only). This is the second
    half of the screenshot bug."""
    monkeypatch.setattr(
        "wizard.llm_steps.list_pulled_models",
        _stub_pulled(["qwen3.6:custom-quant"]),
    )
    monkeypatch.setattr(
        "wizard.llm_steps.list_library_entries",
        lambda timeout=5.0: library_qwen3_custom_chat,
    )
    env = {"LLM_PROVIDER_SOURCE": "ollama-localhost"}
    opts = _get_options_provider(env)(_select_localhost())

    qwen = next(o for o in opts if o.value == "qwen3.6")
    assert "pulled" in qwen.badges, (
        f"qwen3.6 parent should be tagged [pulled] (a variant is on "
        f"the host); got badges={qwen.badges}"
    )
    assert "library" not in qwen.badges, (
        f"qwen3.6 parent should NOT also carry [library]; "
        f"got badges={qwen.badges}"
    )

    gemma = next(o for o in opts if o.value == "custom-chat")
    assert "library" in gemma.badges
    assert "pulled" not in gemma.badges


def test_family_parent_status_is_library_when_no_variants_pulled(
    monkeypatch, library_qwen3_custom_chat,
):
    """Sanity check the negation: no host pulls of a family ⇒ parent
    stays ``[library]``."""
    monkeypatch.setattr(
        "wizard.llm_steps.list_pulled_models", _stub_pulled([]),
    )
    monkeypatch.setattr(
        "wizard.llm_steps.list_library_entries",
        lambda timeout=5.0: library_qwen3_custom_chat,
    )
    env = {"LLM_PROVIDER_SOURCE": "ollama-localhost"}
    opts = _get_options_provider(env)(_select_localhost())

    for fam in ("qwen3.6", "custom-chat", "custom-embed-large"):
        parent = next(o for o in opts if o.value == fam)
        assert "library" in parent.badges
        assert "pulled" not in parent.badges
        assert parent.pulled_variants == frozenset()


# ────────────────────────────────────────────────────────────────────────────
# Bucket 1: pulled-but-not-in-library fallback
# ────────────────────────────────────────────────────────────────────────────


def test_pulled_not_in_library_appears_as_flat_top_level_option(
    monkeypatch, library_qwen3_custom_chat,
):
    """A host-pulled tag whose family is missing from
    ``ollama.com/library`` (e.g. a custom local build) appears in the
    wizard's option list as a flat top-level row with its full tagged
    name as the option value and ``[pulled]`` status."""
    host_tags = [
        "custom-local-fine-tune:v3",  # not in the library at all
        "qwen3.6:latest",
    ]
    monkeypatch.setattr(
        "wizard.llm_steps.list_pulled_models", _stub_pulled(host_tags),
    )
    monkeypatch.setattr(
        "wizard.llm_steps.list_library_entries",
        lambda timeout=5.0: library_qwen3_custom_chat,
    )
    env = {"LLM_PROVIDER_SOURCE": "ollama-localhost"}
    opts = _get_options_provider(env)(_select_localhost())

    custom = next(
        (o for o in opts if o.value == "custom-local-fine-tune:v3"), None,
    )
    assert custom is not None, (
        "Bucket-1 entry missing — custom local pulls must surface as "
        "flat options even when the family isn't on ollama.com/library"
    )
    assert "pulled" in custom.badges
    # No variant tree for bucket-1 entries.
    assert custom.sizes == ()


# ────────────────────────────────────────────────────────────────────────────
# Pre-check seeding (the auto-checkmark UX)
# ────────────────────────────────────────────────────────────────────────────


def test_options_carry_enough_info_for_pre_check_seeding(
    monkeypatch, library_qwen3_custom_chat,
):
    """The wizard's PromptPanel seeds ``_checked_values`` from each
    option's ``pulled_variants`` at multiselect entry. This test
    locks the contract: the options_provider must populate
    ``pulled_variants`` so the panel has the information it needs.

    The panel's actual seeding logic lives in
    ``PromptPanel._load_step`` (see ``test_prompt_panel_leaf_badges``);
    this test guarantees the upstream payload it consumes."""
    host_tags = [
        "qwen3.6:latest",
        "qwen3.6:custom-quant",
        "custom-chat:31b",
        "custom-embed-large:latest",
        "nomic-embed-text:latest",
    ]
    monkeypatch.setattr(
        "wizard.llm_steps.list_pulled_models", _stub_pulled(host_tags),
    )
    library = library_qwen3_custom_chat + [
        OllamaLibraryEntry(
            name="nomic-embed-text",
            capabilities=frozenset({"embedding"}),
            sizes=(),
            pulls=14_000_000, updated="2 years ago",
        ),
    ]
    monkeypatch.setattr(
        "wizard.llm_steps.list_library_entries",
        lambda timeout=5.0: library,
    )
    env = {"LLM_PROVIDER_SOURCE": "ollama-localhost"}
    opts = _get_options_provider(env)(_select_localhost())

    # Compute what _load_step would seed into _checked_values from
    # these options' pulled_variants.
    seeded = set()
    for opt in opts:
        for tag in opt.pulled_variants:
            seeded.add(f"{opt.value}:{tag}")
    # Every host-pulled tag must be reachable via family.value + variant.
    expected_seeded = set(host_tags)
    assert expected_seeded.issubset(seeded), (
        f"Pre-check seeding would miss: "
        f"{sorted(expected_seeded - seeded)}"
    )


def test_pulled_library_tag_does_not_duplicate_as_flat_row(
    monkeypatch, library_qwen3_custom_chat,
):
    """A pulled TAG of a library family (qwen3.6:latest vs library key
    qwen3.6) must merge into the family row, not also appear as a bogus
    flat '(local model, not in public library)' row. Regression: the
    bucket-1 filter compared the tagged name against bare family keys,
    so every normal pull duplicated."""
    host_tags = ["qwen3.6:latest"]
    monkeypatch.setattr(
        "wizard.llm_steps.list_pulled_models", _stub_pulled(host_tags),
    )
    monkeypatch.setattr(
        "wizard.llm_steps.list_library_entries",
        lambda timeout=5.0: library_qwen3_custom_chat,
    )
    env = {"LLM_PROVIDER_SOURCE": "ollama-localhost"}
    opts = _get_options_provider(env)(_select_localhost())
    flat_dupes = [o for o in opts if o.value == "qwen3.6:latest"]
    assert flat_dupes == [], (
        f"tagged pull of a library family must not produce a flat row: "
        f"{[o.label for o in flat_dupes]}"
    )
    assert any(o.value == "qwen3.6" for o in opts)


def test_wizard_upstream_honors_ollama_localhost_port(
    monkeypatch, library_qwen3_custom_chat,
):
    """The wizard's /api/tags probe must read OLLAMA_LOCALHOST_PORT —
    the 5th consumer site of the localhost-port symmetry rule. Captures
    the URL the probe actually receives (behavioral, not source-text)."""
    captured = {}

    def _capturing_pulled(upstream_url, timeout=2.0):
        captured["url"] = upstream_url
        return []

    monkeypatch.setattr(
        "wizard.llm_steps.list_pulled_models", _capturing_pulled,
    )
    monkeypatch.setattr(
        "wizard.llm_steps.list_library_entries",
        lambda timeout=5.0: library_qwen3_custom_chat,
    )
    env = {
        "LLM_PROVIDER_SOURCE": "ollama-localhost",
        "OLLAMA_LOCALHOST_PORT": "12345",
    }
    _get_options_provider(env)(_select_localhost())
    assert captured.get("url") == "http://localhost:12345"


def test_variant_summary_regex_accepts_m_suffix_context():
    """ollama.com renders huge-context models as '10M context window' —
    the K-only regex silently dropped every such variant (llama4 class),
    collapsing them to coarse sizes."""
    from utils.ollama_library import _VARIANT_SUMMARY_RE

    html = ('<p class="flex text-neutral-500" x>'
            '244GB · 10M context window · Text · 4 months ago</p>')
    m = _VARIANT_SUMMARY_RE.search(html)
    assert m is not None
    assert m.group(2) == "10M"
    html_k = ('<p class="flex text-neutral-500" x>'
              '4.7GB · 256K context window · Text · 1 year ago</p>')
    mk = _VARIANT_SUMMARY_RE.search(html_k)
    assert mk is not None and mk.group(2) == "256K"
