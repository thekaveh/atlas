"""Minimum quality bar for machine-generated review tickets (#1246).

Reads one ticket body on stdin (optionally its title and number) and reports
violations of the bar #1246 sets. The same module serves every enforcement
point: a review run can pipe each drafted body through it before filing, and
``.github/workflows/review-ticket-lint.yml`` runs it on every opened or edited
issue that carries the ``atlas-review:`` marker.

Only the unworkable class blocks (exit status 1): evidence a reader cannot
follow (R1) and internal review IDs that lead nowhere (R3). Everything else
is a warning, because gating on every rule would stop nearly the whole
backlog and make the signal useless. With ``--repo-root`` the linter also
re-resolves each evidence link against that checkout (E1), the check a
scheduled re-verification would run.

    python scripts/lint_review_ticket.py --title "fix(x): y" < body.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_BLOB = re.compile(
    r"https://github\.com/(?i:thekaveh/atlas)/blob/(?P<ref>[^/\s)]+)/(?P<path>[^#?\s)]+)"
    r"(?:\?[^#\s)]*)?(?P<anchor>#L(?P<start>\d+)(?:-L(?P<end>\d+))?)?"
)
MARKER = re.compile(r"<!--\s*atlas-review:[^:>\s]+:(?P<id>[A-Z]+-?\d+)\s*-->")
# Review IDs carry two or more digits (F01, F13, FIX-31), so an "F1 score"
# or an F5 key in prose is not one.
REVIEW_ID = re.compile(r"\b(?:FIX-\d+|F\d{2,})\b")
# An issue reference next to an ID: "#1204" (issue numbers here have three
# or more digits, so "step #3" is not one) or an issues URL.
ISSUE_LINK = re.compile(r"#\d{3,}\b|/issues/\d+")
ISSUE_WINDOW = 60
# A markdown link to an issue: every ID in its text is resolved by it.
ISSUE_LINK_TEXT = re.compile(r"\[[^\]]*\]\((?:https://github\.com/thekaveh/atlas)?/?[^)\s]*issues/\d+[^)]*\)")
# A backticked repo path: no spaces (so not a shell command), with a
# directory separator or a file extension; optionally with :line or :a-b.
# The same, written without backticks: a bare repo path in a location cell.
PLAIN_PATH = re.compile(r"(?<![\w/.`-])(?P<path>[\w.-]+(?:/[\w.*-]+)+)(?P<line>:\d+(?:-\d+)?)?(?![\w/`])")
PATH_TOKEN = re.compile(
    r"`(?P<path>[\w.*-]*/[\w.*/-]*|[\w-]+\.[A-Za-z][A-Za-z0-9]*)(?P<line>:\d+(?:-\d+)?)?`"
)
WHOLE_FILE = "whole-file:"
GENERIC_CAPTIONS = (
    "existing foundation or integration seam; this proposal extends it",
)
BOILERPLATE = (
    "no additional hard prerequisite identified",
    "no explicit dependent review ticket identified",
)
RESTATING_SECTIONS = ("cascading effects", "compatibility, migration")
UNDEFINED_REFERENCE = re.compile(r"\b(?:the documented|the configured|under the declared)\b", re.I)
VERIFIABLE = re.compile(
    r"`|\d|https?://|#\d+|\b(?:check(?:ed|s)?|asserts?|returns?|exits?|fails?|passes|shows?|lists?|"
    r"reports?|contains?|equals?|matches|counts?|runs?|tests?|benchmarks?|measure\w*|records?|"
    r"rejects?|compares?|verif\w*|appears?|never|only|each|every)\b",
    re.I,
)
PLAIN_WORDS = {
    "utilize": "use", "utilise": "use", "leverage": "use", "facilitate": "help",
    "with respect to": "about", "in order to": "to",
}
FILLER = ("it should be noted", "it is important to", "robust", "holistic", "seamless",
          "best-in-class", "comprehensive")
CONTEXT_WORDS, TICKET_WORDS, SENTENCE_AVERAGE, SENTENCE_MAX = 80, 400, 25, 40
BLOCKING = frozenset({"R1", "R3"})


@dataclass(frozen=True)
class Violation:
    rule: str
    line: int
    message: str

    @property
    def blocking(self) -> bool:
        return self.rule in BLOCKING


# ── parsing ─────────────────────────────────────────────────────────────


def _body_lines(body: str):
    """(line number, line, inside a fenced code block) for every body line
    that is not itself a fence."""
    in_code = False
    for number, line in enumerate(body.splitlines(), 1):
        if line.lstrip().startswith(("```", "~~~")):
            in_code = not in_code
            continue
        yield number, line, in_code


def _sections(body: str) -> dict[str, list[tuple[int, str]]]:
    """Prose lines under each heading, keyed by lowercased heading text. A
    sub-heading of Evidence stays in Evidence; any other heading starts its
    own section, whatever the ticket's heading levels are."""
    sections: dict[str, list[tuple[int, str]]] = {"": []}
    current, level = "", 0
    for number, line, in_code in _body_lines(body):
        heading = None if in_code else re.match(r"^(#{1,6})\s+(.*)$", line)
        nested = heading and current.startswith("evidence") and len(heading.group(1)) > level
        if heading and not nested:
            current, level = heading.group(2).strip().lower(), len(heading.group(1))
            sections.setdefault(current, [])
        elif not in_code and not heading:
            sections[current].append((number, line))
        elif nested:
            continue
    return sections


def _section(sections: dict, *names: str) -> list[tuple[int, str]]:
    return [line for key, lines in sections.items() if key.startswith(names) for line in lines]


def _table_rows(lines: list[tuple[int, str]]) -> list[tuple[int, str, str]]:
    rows = []
    for number, line in lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if line.strip().startswith("|") and len(cells) >= 2 and not set(cells[0]) <= set("-: "):
            rows.append((number, cells[0], cells[-1]))
    return rows[1:] if rows and rows[0][1].lower() in {"location", "where"} else rows


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9][\w'’.-]*", text)


# ── rules ───────────────────────────────────────────────────────────────


def _evidence_rows(sections: dict) -> list[tuple[int, str, str]]:
    """(line, location, caption) for each evidence table row or bullet
    (``- location — caption``, the older review-ticket format)."""
    lines = _section(sections, "evidence")
    bullets = [
        (number, *(item.split(" — ", 1) if " — " in item else (item, "")))
        for number, line in lines if (item := line.strip()).startswith("- ")
    ]
    return _table_rows(lines) + [(n, loc[2:].strip(), caption.strip()) for n, loc, caption in bullets]


def _cited_locations(location: str) -> list[bool]:
    """For each repo location a cell cites, whether it names a line."""
    links = [bool(link["anchor"]) for link in REPO_BLOB.finditer(location)]
    if links:
        return links
    tokens = [bool(token["line"]) for token in PATH_TOKEN.finditer(location)]
    if tokens or "`" in location:
        return tokens
    return [bool(token["line"]) for token in PLAIN_PATH.finditer(location)]


def check_anchors(sections: dict) -> list[Violation]:
    """R1: each evidence location is line-anchored, or says why it is whole-file."""
    found = []
    for number, location, caption in _evidence_rows(sections):
        cited = _cited_locations(location)
        explained = caption.lower().startswith(WHOLE_FILE) and caption[len(WHOLE_FILE):].strip()
        if cited and not all(cited) and not explained:
            found.append(Violation("R1", number, f"evidence {location[:80]!r} has no #L line anchor "
                                                 f"and no '{WHOLE_FILE}' caption saying why"))
    return found


def check_captions(sections: dict) -> list[Violation]:
    """R2: each caption says what its location shows."""
    return [
        Violation("R2", number, f"generic evidence caption {caption[:60]!r}")
        for number, _location, caption in _evidence_rows(sections)
        if caption.strip().rstrip(".").lower() in GENERIC_CAPTIONS
    ]


def _prose_without_code(body: str) -> list[tuple[int, str]]:
    """Body lines outside fenced blocks, with inline code spans and the text
    of issue links blanked, so code such as a linter's F401 is never read as
    a review ID and ``[F27 — title](…/issues/1159)`` counts as resolved."""
    lines = []
    for number, line, in_code in _body_lines(body):
        if not in_code and not MARKER.search(line):
            blanked = re.sub(r"`[^`]*`", lambda m: " " * len(m.group()), line)
            lines.append((number, ISSUE_LINK_TEXT.sub(lambda m: " " * len(m.group()), blanked)))
    return lines


def check_review_ids(body: str) -> list[Violation]:
    """R3: an internal review ID other than the ticket's own has an issue
    link right next to it."""
    own = {match["id"] for match in MARKER.finditer(body)}
    found = []
    for number, line in _prose_without_code(body):
        for match in REVIEW_ID.finditer(line):
            window = line[max(0, match.start() - 15):match.end() + ISSUE_WINDOW]
            if match.group() not in own and not ISSUE_LINK.search(window):
                found.append(Violation("R3", number, f"{match.group()} is not the ticket's own ID "
                                                     "and has no issue link next to it"))
    return found


def check_boilerplate(sections: dict) -> list[Violation]:
    """R4 and R5: no filler sections, no sections that restate the spec."""
    found = [
        Violation("R4", number, f"boilerplate {phrase!r}: delete the section instead")
        for lines in sections.values() for number, line in lines
        for phrase in BOILERPLATE if phrase in line.lower()
    ]
    found += [
        Violation("R5", lines[0][0] - 1 if lines else 0, f"section {name!r} restates the specification")
        for name, lines in sections.items() if name.startswith(RESTATING_SECTIONS)
    ]
    return found


def check_basis(body: str, title: str | None) -> list[Violation]:
    """R6: a proposal is not titled as a fix."""
    if title and title.startswith("fix(") and re.search(r"\*\*Basis:\*\*\s*proposal|Basis:\s*proposal", body):
        return [Violation("R6", 1, "Basis: proposal under a fix( title")]
    return []


def check_criteria(sections: dict) -> list[Violation]:
    """R7 and R8: each acceptance criterion is defined and checkable."""
    criteria = [(n, line) for n, line in _section(sections, "acceptance") if re.match(r"\s*- \[[ x]\]", line)]
    found = [
        Violation("R7", number, "criterion refers to a 'documented/configured/declared' artifact "
                                "without naming or linking it")
        for number, line in criteria
        if UNDEFINED_REFERENCE.search(line) and not re.search(r"https?://|#\d+|`", line)
    ]
    found += [
        Violation("R8", number, "criterion names nothing measurable: no command, value, file or check")
        for number, line in criteria if not VERIFIABLE.search(line.split("]", 1)[-1])
    ]
    return found


def _prose_lines(sections: dict) -> list[tuple[int, str]]:
    return [(n, line) for name, lines in sections.items() if not name.startswith("evidence")
            for n, line in lines if line.strip() and not line.lstrip().startswith(("|", "<!--"))]


def check_writing(sections: dict) -> list[Violation]:
    """R9: plain words (W2), no filler (W3) and the length budgets (W4)."""
    prose = _prose_lines(sections)
    found = [
        Violation("R9", number, f"W2: use {plain!r}, not {word!r}")
        for number, line in prose for word, plain in PLAIN_WORDS.items()
        if re.search(rf"\b{word}\w*", line, re.I)
    ]
    found += [
        Violation("R9", number, f"W3: filler {word!r}")
        for number, line in prose for word in FILLER if re.search(rf"\b{re.escape(word)}\b", line, re.I)
    ]
    return found + _budget_findings(sections, prose)


def _budget_findings(sections: dict, prose: list[tuple[int, str]]) -> list[Violation]:
    """W4: each budget as (measured, limit, what)."""
    # A line (a bullet, a criterion) ends a sentence as surely as a full stop.
    sentences = [s for _n, line in prose for s in re.split(r"(?<=[.!?])\s+", line) if _words(s)]
    lengths = [len(_words(s)) for s in sentences] or [0]
    budgets = (
        (sum(len(_words(line)) for _n, line in _section(sections, "context")), CONTEXT_WORDS,
         "Context is {} words"),
        (sum(len(_words(line)) for _n, line in prose), TICKET_WORDS, "{} words outside evidence and code"),
        (sum(lengths) / len(lengths), SENTENCE_AVERAGE, "sentences average {:.1f} words"),
        (max(lengths), SENTENCE_MAX, "a sentence runs {} words"),
    )
    return [
        Violation("R9", 0, f"W4: {what.format(measured)}, over {limit}")
        for measured, limit, what in budgets if measured > limit
    ]


def _resolve(repo_root: Path, link: re.Match) -> Path | None:
    """The file a blob link names. A branch such as ``batch/next`` puts part
    of the ref into the matched path, so up to two leading segments are
    tried off, but never down to a bare file name: a deleted
    ``services/foo/README.md`` must not resolve to the root README."""
    parts = link["path"].split("/")
    candidates = [parts[i:] for i in range(min(3, len(parts))) if i == 0 or len(parts) - i > 1]
    return next((repo_root.joinpath(*c) for c in candidates if repo_root.joinpath(*c).is_file()), None)


def check_evidence_resolves(sections: dict, repo_root: Path) -> list[Violation]:
    """E1: each evidence link still resolves in ``repo_root``: the file
    exists and every cited line is inside it."""
    found, line_counts = [], {}
    for number, location, _caption in _evidence_rows(sections):
        for link in REPO_BLOB.finditer(location):
            path = _resolve(repo_root, link)
            if path is None:
                found.append(Violation("E1", number, f"{link['path']} no longer exists"))
                continue
            if path not in line_counts:
                line_counts[path] = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
            last = int(link["end"] or link["start"] or 0)
            if last > line_counts[path]:
                found.append(Violation("E1", number, f"{link['path']}:{last} is past the end of the file"))
    return found


def lint(body: str, *, title: str | None = None, repo_root: Path | None = None) -> list[Violation]:
    sections = _sections(body)
    found = [
        *check_anchors(sections), *check_captions(sections), *check_review_ids(body),
        *check_boilerplate(sections), *check_basis(body, title), *check_criteria(sections),
        *check_writing(sections),
    ]
    if repo_root is not None:
        found += check_evidence_resolves(sections, repo_root)
    return sorted(found, key=lambda v: (not v.blocking, v.rule, v.line))


# ── output ──────────────────────────────────────────────────────────────


def render_markdown(violations: list[Violation]) -> str:
    """The comment the issue workflow posts."""
    blocking = [v for v in violations if v.blocking]
    head = ("This review ticket does not meet the minimum bar (#1246): a reader cannot follow some "
            "of its evidence or references, so it is labelled `review-quality:needs-revision`."
            if blocking else "This review ticket meets the blocking part of the minimum bar (#1246).")
    lines = [head, ""]
    lines += [f"- **{v.rule}{' (blocking)' if v.blocking else ''}**, line {v.line}: {v.message}" for v in violations]
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    """Exit 0 when nothing blocks, 1 on a blocking violation, and 2 when the
    linter itself fails, so a crash is never mistaken for a bad ticket."""
    try:
        return _run(argv)
    except Exception:  # noqa: BLE001 - reported, then a distinct status
        import traceback

        traceback.print_exc()
        return 2


def _run(argv: list[str] | None) -> int:
    parser = argparse.ArgumentParser(description="Lint a machine-generated review ticket body read on stdin")
    parser.add_argument("--title", help="the issue title, for the basis/title rule")
    parser.add_argument("--repo-root", type=Path, help="re-resolve evidence links against this checkout")
    parser.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    args = parser.parse_args(argv)
    violations = lint(sys.stdin.read(), title=args.title, repo_root=args.repo_root)
    if args.format == "json":
        print(json.dumps([{**asdict(v), "blocking": v.blocking} for v in violations], indent=2))
    elif args.format == "markdown":
        print(render_markdown(violations), end="")
    else:
        for v in violations:
            print(f"{'ERROR' if v.blocking else 'WARN '} {v.rule} line {v.line}: {v.message}")
    return 1 if any(v.blocking for v in violations) else 0


if __name__ == "__main__":
    raise SystemExit(main())
