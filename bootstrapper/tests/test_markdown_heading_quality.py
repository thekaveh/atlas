from __future__ import annotations

import pytest

from scripts.docs.heading_quality import (
    decorative_symbol_findings,
    heading_number_findings,
    renumber_markdown,
)


def test_renumber_markdown_preserves_fences_and_builds_hierarchy():
    source = """# Guide

## Overview
### Existing 9.7. Detail
#### 1.1. 9.7 Legacy repeated prefix
```markdown
## Example heading
```
## 8. Operations
### Troubleshooting
"""

    assert renumber_markdown(source) == """# Guide

## 1. Overview
### 1.1. Existing 9.7. Detail
#### 1.1.1. 9.7 Legacy repeated prefix
```markdown
## Example heading
```
## 2. Operations
### 2.1. Troubleshooting
"""


def test_heading_number_findings_report_wrong_depth_and_sequence():
    source = "## 1. Overview\n### 4. Detail\n## Operations\n"

    findings = heading_number_findings(source)

    assert [line for line, _message in findings] == [2, 3]


def test_decorative_symbol_findings_ignore_fenced_examples():
    source = "Complete ✅\nPopularity: 10★\nTree: └ child\n```text\n✅ tool output\n```\n"

    assert decorative_symbol_findings(source) == [(1, "✅"), (2, "★"), (3, "└")]


@pytest.mark.parametrize("source", ["# Guide\n#### Skipped\n", "## Parent\n#### Skipped\n"])
def test_skipped_heading_levels_are_rejected(source):
    with pytest.raises(ValueError, match="skips required parent"):
        renumber_markdown(source)

    assert any(
        "skips required parent" in message
        for _line, message in heading_number_findings(source)
    )


def test_semantic_numeric_titles_are_preserved():
    source = "## 2026. Roadmap\n## 3.14 API compatibility\n"

    assert renumber_markdown(source) == (
        "## 1. 2026. Roadmap\n## 2. 3.14 API compatibility\n"
    )
