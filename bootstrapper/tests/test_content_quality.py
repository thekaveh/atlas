import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.docs.content_quality import (  # noqa: E402
    diagram_narration_findings,
    production_style_findings,
    marketing_adjective_findings,
    duplicate_block_findings,
    long_prose_findings,
    manifest_prose_findings,
    prose_baseline_increases,
    prose_counts,
    prose_ratchet_findings,
)


def test_diagram_narration_flagged():
    text = "See the figure.\n\nThe diagram above shows how requests flow.\n"
    findings = diagram_narration_findings(text)
    assert [ln for ln, _ in findings] == [3]


def test_diagram_narration_ignores_code_fence():
    text = "```\nthe diagram above shows x\n```\n"
    assert diagram_narration_findings(text) == []


def test_diagram_narration_suppressed_inline():
    text = "The diagram above shows the flow. <!-- lint-ok -->\n"
    assert diagram_narration_findings(text) == []


def test_production_style_narration_flagged():
    text = "Rendered on a slate-950 background with the same JetBrains Mono.\n"
    findings = production_style_findings(text)
    assert findings and findings[0][0] == 1


def test_marketing_adjectives_flagged_in_service_readme():
    text = "Kong is the intelligent, powerful API gateway.\n"
    findings = marketing_adjective_findings(text, is_service_readme=True)
    flagged = {word for _, word in findings}
    assert "intelligent" in flagged and "powerful" in flagged


def test_marketing_adjectives_allowlisted_phrases_ok():
    # "powerful" inside a quoted CLI example or non-service doc is not flagged here
    text = "The optimizer is powerful.\n"
    assert marketing_adjective_findings(text, is_service_readme=False) == []


def test_duplicate_block_across_pages_flagged():
    block = "- `a.yml`\n- `b.py`\n- `c.md`\n- `d.txt`\n"
    docs = {f"p{i}.md": f"# Page {i}\n\n{block}\ntail {i}\n" for i in range(5)}
    findings = duplicate_block_findings(docs, min_lines=4, min_pages=4)
    assert len(findings) == 5  # every page carrying the shared block


def test_unique_blocks_not_flagged():
    docs = {f"p{i}.md": f"# Page {i}\n\nunique line {i}\nother {i}\n" for i in range(5)}
    assert duplicate_block_findings(docs, min_lines=4, min_pages=4) == []


def test_multiple_duplicated_blocks_on_one_page_yield_one_finding():
    # Page p1 (non-last) contains TWO distinct 4-line blocks, each duplicated across 4+ pages
    block1 = "- `a.yml`\n- `b.py`\n- `c.md`\n- `d.txt`\n"
    block2 = "line1\nline2\nline3\nline4\n"
    docs = {}
    for i in range(7):
        if i == 1:
            # p1 has both blocks
            docs[f"p{i}.md"] = f"# Page {i}\n\n{block1}{block2}tail\n"
        elif i < 4:
            # p0, p2, p3 have block1 (4 pages total with block1)
            docs[f"p{i}.md"] = f"# Page {i}\n\n{block1}tail\n"
        else:
            # p4, p5, p6 have block2 (4 pages total with block2)
            docs[f"p{i}.md"] = f"# Page {i}\n\n{block2}tail\n"
    findings = duplicate_block_findings(docs, min_lines=4, min_pages=4)
    # Only one finding for p1, despite two duplicate blocks
    p1_findings = [f for f in findings if f[0] == "p1.md"]
    assert len(p1_findings) == 1


def test_root_readme_does_not_leak_diagram_generation_mechanics():
    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "data_flow.calls" not in readme


def _words(count: int, word: str = "word") -> str:
    """``count`` words that start a sentence (the splitter needs a capital)."""
    return " ".join([word.capitalize()] + [word] * (count - 1))


def _rules(text: str) -> list[tuple[int, str]]:
    return [(line, rule) for line, rule, _ in long_prose_findings(text)]


def test_long_sentence_is_flagged_and_short_one_is_not():
    text = f"# T\n\n{_words(26)}. {_words(25)}.\n"
    assert _rules(text) == [(3, "sentence")]


def test_numbered_step_uses_the_twenty_word_limit():
    text = f"1. {_words(21)}.\n2. {_words(20)}.\n- {_words(21)}.\n"
    assert _rules(text) == [(1, "step")]


def test_paragraph_word_and_sentence_limits():
    words = " ".join(f"{_words(19)}." for _ in range(4))
    sentences = " ".join("Short one." for _ in range(7))
    assert _rules(f"{words}\n\n{sentences}\n") == [
        (1, "para_words"),
        (3, "para_sentences"),
    ]


def test_paragraph_spans_wrapped_lines_until_a_blank_line():
    text = f"{_words(40)}\n{_words(40)}\n\n{_words(10)}.\n"
    assert (1, "para_words") in _rules(text)
    assert all(line != 4 for line, _ in _rules(text))


def test_fences_generated_ranges_html_and_lint_ok_are_skipped():
    long = _words(30)
    text = (
        f"```\n{long}\n```\n\n"
        f"<!-- BEGIN GENERATED X -->\n{long}.\n<!-- END GENERATED X -->\n\n"
        f"<!-- TOPOLOGY:BEGIN -->\n{long}.\n<!-- TOPOLOGY:END -->\n\n"
        f"<p>{long}.</p>\n\n"
        f"{long}. <!-- lint-ok -->\n"
    )
    assert long_prose_findings(text) == []


def test_table_cells_are_checked_one_by_one():
    text = f"| A | B |\n|---|---|\n| {_words(26)}. | {_words(5)} |\n"
    assert _rules(text) == [(3, "sentence")]


def test_link_targets_do_not_count_as_words():
    link = "[x](" + "/".join(["very-long-path"] * 40) + ")"
    assert long_prose_findings(f"{_words(20)} {link}.\n") == []


def test_blockquote_prose_is_checked():
    assert _rules(f"> {_words(26)}.\n") == [(1, "sentence")]


def test_prose_counts_collapse_findings_per_rule():
    text = f"{_words(26)}. {_words(26)}.\n\n1. {_words(21)}.\n"
    assert prose_counts(long_prose_findings(text)) == {"sentence": 2, "step": 1}


def test_manifest_rule_flags_long_or_issue_citing_descriptions_and_notes():
    manifest = {
        "env": [
            {"name": "LONG", "description": _words(41)},
            {"name": "ISSUE", "description": "Fixed in (#1234)."},
            {"name": "OK", "description": "Host port. See RFC #1 and a&#123; entity."},
            {"name": "NONE"},
        ],
        "capabilities": [
            {"name": "Long", "note": f"{_words(26)}. Short."},
            {"name": "Fine", "note": f"{_words(25)}. {_words(25)}."},
        ],
    }
    assert [(key, rule) for key, rule, _ in manifest_prose_findings(manifest)] == [
        ("LONG", "env_description"),
        ("ISSUE", "env_issue_ref"),
        ("Long", "capability_note"),
    ]


def test_ratchet_rejects_regressions_and_unlowered_gains():
    baseline = {"a.md": {"sentence": 3}, "gone.md": {"step": 1}}
    current = {"a.md": {"sentence": 2, "step": 1}, "new.md": {"para_words": 1}}
    messages = prose_ratchet_findings(current, baseline)
    assert "a.md: sentence 2 < baseline 3; lower the baseline (--write-prose-baseline)" in messages
    assert "a.md: step 1 > baseline 0" in messages
    assert "new.md: para_words 1 > baseline 0" in messages
    assert any(message.startswith("gone.md: step 0 < baseline 1") for message in messages)
    assert prose_ratchet_findings(baseline, baseline) == []


def test_baseline_rewrite_reports_every_increase():
    assert prose_baseline_increases({"a.md": {"sentence": 4}}, {"a.md": {"sentence": 3}}) == [
        "a.md: sentence 4 > 3"
    ]
    assert prose_baseline_increases({"a.md": {"sentence": 2}}, {"a.md": {"sentence": 3}}) == []


def test_committed_prose_baseline_matches_the_current_text():
    """The gate is wired into check-docs-drift; this pins the same contract."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "check_docs_drift", _REPO_ROOT / "scripts" / "check-docs-drift.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.check_prose_length() == []
