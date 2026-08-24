"""Offline regression tests for ``utils/ollama_library.py``'s
``ollama.com/library`` scraper.

Ollama dropped the ``x-test-*`` test-hook attributes the parser used
to anchor on (confirmed live 2026-08: 0 ``x-test-*`` hits, 235
``href="/library/…"`` links still present). The parser was re-anchored
on the current plain-Tailwind card markup — see the module docstring
in ``utils/ollama_library.py`` for the full rationale.

``tests/fixtures/ollama_library_sample.html`` is a trimmed, byte-exact
slice of six real cards pulled live from ``ollama.com/library`` on
2026-08-23 (not hand-typed), covering: multi-size + single-capability
(llama3.1), multi-capability (deepseek-r1: tools + thinking),
capability-only / zero sizes (nomic-embed-text: embedding), a vision
model (gemma3), a hybrid cloud+local model (qwen3.5: cloud chip *and*
pullable sizes), and a cloud-exclusive model with no pullable sizes
(minimax-m2.7). All network access is mocked — this test is hermetic
and must never touch the network; a future markup change on
ollama.com breaks this test immediately instead of silently degrading
the wizard to the ~5-entry curated fallback again.
"""
from __future__ import annotations

from pathlib import Path

import utils.ollama_library as ol

_FIXTURE = Path(__file__).parent / "fixtures" / "ollama_library_sample.html"


class _FakeResponse:
    """Minimal stand-in for ``http.client.HTTPResponse`` — supports the
    ``with urlopen(...) as resp: resp.read()`` usage in
    ``list_library_entries``. Mirrors the fake used in
    ``test_ollama_localhost_pull.py`` for the same urllib seam.
    """

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _serve_fixture(monkeypatch) -> None:
    html_bytes = _FIXTURE.read_bytes()
    monkeypatch.setattr(
        ol.urllib.request, "urlopen",
        lambda *a, **k: _FakeResponse(html_bytes),
    )


def test_fixture_file_exists_and_is_trimmed():
    assert _FIXTURE.exists()
    # "Trimmed but representative" — nowhere near the ~800KB live page.
    assert _FIXTURE.stat().st_size < 100_000


def test_parses_all_fixture_cards(monkeypatch):
    _serve_fixture(monkeypatch)
    entries = ol.list_library_entries(timeout=1.0)
    names = sorted(e.name for e in entries)
    assert names == sorted([
        "llama3.1", "deepseek-r1", "nomic-embed-text",
        "gemma3", "qwen3.5", "minimax-m2.7",
    ])


def test_parsed_values_match_real_ollama_data_multi_size(monkeypatch):
    """Spot-check real extracted values (name, capability, size, pull
    count) for a multi-size, multi-capability entry — regression guard
    against a regex silently matching zero groups (which would sail
    through a plain len() check) as well as against markup drift.
    """
    _serve_fixture(monkeypatch)
    entries = {e.name: e for e in ol.list_library_entries(timeout=1.0)}

    llama = entries["llama3.1"]
    assert llama.capabilities == frozenset({"tools"})
    assert llama.sizes == ("8b", "70b", "405b")
    assert llama.pulls == 118_700_000
    assert llama.updated == "1 year ago"
    assert llama.cloud_only is False

    deepseek = entries["deepseek-r1"]
    assert deepseek.capabilities == frozenset({"tools", "thinking"})
    assert deepseek.pulls == 91_800_000


def test_parsed_values_match_real_ollama_data_embedding(monkeypatch):
    """Same spot-check as above, for the zero-size embedding entry."""
    _serve_fixture(monkeypatch)
    entries = {e.name: e for e in ol.list_library_entries(timeout=1.0)}

    embed = entries["nomic-embed-text"]
    assert embed.capabilities == frozenset({"embedding"})
    assert embed.sizes == ()
    assert embed.pulls == 83_300_000


def test_cloud_only_vs_hybrid_detection(monkeypatch):
    """Cloud chip + pullable sizes (qwen3.5) stays pullable (hybrid);
    cloud chip + zero sizes (minimax-m2.7) is cloud-exclusive."""
    _serve_fixture(monkeypatch)
    entries = {e.name: e for e in ol.list_library_entries(timeout=1.0)}

    hybrid = entries["qwen3.5"]
    assert hybrid.sizes  # has pullable local variants
    assert hybrid.cloud_only is False

    cloud_only = entries["minimax-m2.7"]
    assert cloud_only.sizes == ()
    assert cloud_only.cloud_only is True


def test_entries_sorted_by_pulls_descending(monkeypatch):
    _serve_fixture(monkeypatch)
    entries = ol.list_library_entries(timeout=1.0)
    pulls = [e.pulls for e in entries]
    assert pulls == sorted(pulls, reverse=True)


def test_network_failure_returns_empty_list(monkeypatch):
    def _boom(*_a, **_k):
        raise OSError("network unreachable")
    monkeypatch.setattr(ol.urllib.request, "urlopen", _boom)
    assert ol.list_library_entries(timeout=1.0) == []


def test_min_plausible_entries_threshold_below_fixture_count():
    """The fixture intentionally has fewer cards than a real page —
    guards that MIN_PLAUSIBLE_ENTRIES (the fallback trigger consumed
    by wizard/llm_steps.py) stays well below both the curated
    fallback's ~5 entries and a real page's 200+, so this test's own
    fixture size doesn't accidentally cross it and mask a future
    regression.
    """
    assert 5 < ol.MIN_PLAUSIBLE_ENTRIES < 200
