"""Gate on hand-written anchor numbers in service READMEs (audit D1-24).

Each hand-written ``<a id="N-slug"></a>`` anchor carries a section number that
should match the nearest numbered heading. About 900 pre-existing mismatches
are accepted, because no link uses them and renaming them would be churn. The
baseline is per file and may only go down. A file that gains a mismatch fails.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = Path(__file__).parent / "fixtures" / "service_readme_anchor_baseline.json"
HEADING = re.compile(r"^(#{1,6}) (\d+(?:\.\d+)*)\.? ")
ANCHOR = re.compile(r'<a id="(\d+(?:\.\d+)*)[-a-z0-9.]*"></a>')


def _current_mismatches() -> dict[str, int]:
    out: dict[str, int] = {}
    for path in sorted((ROOT / "services").glob("*/README.md")):
        current = None
        count = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            heading = HEADING.match(line)
            if heading:
                current = heading.group(2)
            for match in ANCHOR.finditer(line):
                if current is not None and match.group(1) != current:
                    count += 1
        if count:
            out[path.relative_to(ROOT).as_posix()] = count
    return out


def test_no_new_anchor_number_mismatches():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["mismatches"]
    current = _current_mismatches()
    grown = {
        path: (baseline.get(path, 0), count)
        for path, count in current.items()
        if count > baseline.get(path, 0)
    }
    assert not grown, f"new anchor-number mismatches (baseline, now): {grown}"


def test_baseline_has_no_stale_entries():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["mismatches"]
    current = _current_mismatches()
    stale = {p: n for p, n in baseline.items() if current.get(p, 0) < n}
    assert not stale, (
        "anchor mismatches were fixed; lower these baseline counts: " f"{stale}"
    )
