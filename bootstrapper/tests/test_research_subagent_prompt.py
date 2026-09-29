"""Tests for bootstrapper.docs.research_subagent_prompt."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))


def test_prompt_includes_target_service_name():
    from docs.research_subagent_prompt import build_research_prompt
    prompt = build_research_prompt("hermes")
    assert "hermes" in prompt.lower()


def test_prompt_lists_other_services():
    """The prompt enumerates the other 20 doc folders so the subagent knows
    what pairs to consider."""
    from docs.research_subagent_prompt import build_research_prompt
    prompt = build_research_prompt("hermes")
    for other in ("litellm", "kong", "neo4j", "weaviate", "n8n"):
        assert other in prompt


def test_prompt_includes_do_not_propose_list():
    """The prompt names services already wired to the target as a 'do not propose' set."""
    from docs.research_subagent_prompt import build_research_prompt
    prompt = build_research_prompt("hermes")
    dnp_section_idx = prompt.lower().find("do not propose")
    assert dnp_section_idx >= 0, "prompt must contain a 'do not propose' section"
    tail = prompt[dnp_section_idx:dnp_section_idx + 1000]
    assert "litellm" in tail.lower()


def test_planned_edges_stay_proposable():
    """#1273: a planned edge is documented, not wired, so it is not in the
    do-not-propose set; current and optional edges are."""
    from docs.research_subagent_prompt import _do_not_propose_set

    backend, neo4j = _do_not_propose_set("backend"), _do_not_propose_set("neo4j")
    assert ("neo4j" in backend, "backend" in neo4j, "litellm" in backend) == (False, False, True)


def test_prompt_includes_schema_rules():
    """The prompt cites the schema (frontmatter keys, 3 sections, 800-word cap)."""
    from docs.research_subagent_prompt import build_research_prompt
    prompt = build_research_prompt("hermes")
    assert "frontmatter" in prompt.lower()
    assert "800" in prompt
    assert "5 candidate" in prompt or "5-candidate" in prompt or "max 5" in prompt.lower()
    assert "Missing-pair integrations" in prompt
    assert "Candidate new services" in prompt
    assert "Per-service feature gaps" in prompt


def test_prompt_specifies_output_path():
    from docs.research_subagent_prompt import build_research_prompt
    prompt = build_research_prompt("hermes")
    assert "docs/research/rows/hermes.md" in prompt


def test_prompt_specifies_webfetch_budget():
    from docs.research_subagent_prompt import build_research_prompt
    prompt = build_research_prompt("hermes")
    assert "8" in prompt


def test_prompt_for_aggregate_doc_folder_handles_membership():
    """For stt-provider (aggregate), the prompt explains the doc folder
    aggregates multiple manifests (parakeet, speaches)."""
    from docs.research_subagent_prompt import build_research_prompt
    prompt = build_research_prompt("stt-provider")
    assert "parakeet" in prompt.lower() or "speaches" in prompt.lower()


def test_prompt_handles_pointer_only_doc_folder():
    """multi2vec-clip has no underlying manifest — prompt should still build (no crash)
    and instruct the subagent how to research a pointer-only doc."""
    from docs.research_subagent_prompt import build_research_prompt
    prompt = build_research_prompt("multi2vec-clip")
    assert "multi2vec-clip" in prompt.lower()
    assert "docs/research/rows/multi2vec-clip.md" in prompt


def test_candidate_template_starts_open_and_skips_closed_records():
    """#1190: the one-pager template carries the now-required lifecycle, and
    agents are told not to re-propose shipped or rejected candidates."""
    from docs.research_subagent_prompt import build_research_prompt
    prompt = build_research_prompt("hermes")
    template = prompt[prompt.index("## Candidate one-pager schema"):prompt.index("## Hard caps")]
    assert "license: <SPDX or \"unknown\">\nlifecycle: proposed\nreferenced-by:" in template
    assert "Do NOT re-propose a candidate whose existing one-pager has `lifecycle: shipped`" in template
    assert "then the same command on each candidate file you wrote" in prompt
