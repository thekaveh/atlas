import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.docs.content_quality import (  # noqa: E402
    diagram_narration_findings,
    production_style_findings,
    marketing_adjective_findings,
    duplicate_block_findings,
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
