#!/usr/bin/env python3
"""Local documentation drift checks for Atlas.

Default scope excludes:
  * historical audit/plan files
  * bootstrapper/ — pytest fixtures under bootstrapper/tests/fixtures/
    contain intentionally-malformed sample Markdown that would generate
    false positives; user-facing bootstrapper docs are surfaced via the
    main README.md hub instead
  * local virtualenvs + generated dependency directories

Zero-arg checker. Invoke as ``python scripts/check-docs-drift.py``.

Exit codes:
    0  — every probe (links / architecture_refs / source_matrix /
         required_files / placeholder_urls) reports PASS
    1  — at least one probe reported FAIL with details on stderr
"""
from pathlib import Path
import re
import sys

try:
    from scripts.docs.heading_quality import (
        decorative_symbol_findings,
        documentation_paths,
        heading_number_findings,
    )
except ModuleNotFoundError:  # Direct ``python scripts/...`` invocation.
    from docs.heading_quality import (
        decorative_symbol_findings,
        documentation_paths,
        heading_number_findings,
    )

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {
    '.git', 'bootstrapper', 'textual', '__pycache__', '.venv', 'venv',
    'tts-venv', 'site-packages', 'plans', '.mypy_cache', '.superpowers', '.kilo',
    '.claude',  # Claude Code's worktrees / scratch dirs are ephemeral, not source
    '.Codex',  # Codex worktrees / scratch dirs are ephemeral, not source
}
EXCLUDED_FILES = {'repo-issues-report.md'}

LINK_RE = re.compile(r'\[[^\]]*\]\(([^)]+)\)')
URL_RE = re.compile(r'^[a-z][a-z0-9+.-]*:', re.I)


def included(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if path.name in EXCLUDED_FILES:
        return False
    return not any(part in EXCLUDED_PARTS for part in rel.parts)


def markdown_files():
    for p in ROOT.rglob('*.md'):
        if included(p):
            yield p


def _strip_fenced_code_blocks(text: str) -> str:
    """Replace fenced ``` code blocks with same-length blanks so line
    numbers and match offsets stay aligned but `[text](url)` inside
    code samples doesn't false-flag as a real link."""
    out = []
    in_fence = False
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith('```'):
            in_fence = not in_fence
            out.append('\n' if line.endswith('\n') else '')
            continue
        if in_fence:
            out.append('\n' if line.endswith('\n') else '')
        else:
            out.append(line)
    return ''.join(out)


def _resolve_link_target(source: Path, target: str) -> Path:
    resolved = (source.parent / target).resolve()
    if resolved.exists():
        return resolved

    wiki_roots = (
        (ROOT / "docs" / "wiki").resolve(),
        (ROOT / "generated" / "wiki").resolve(),
    )
    is_wiki_page = any(source.resolve().is_relative_to(root) for root in wiki_roots)
    if is_wiki_page and not target.lower().endswith(".md"):
        wiki_target = Path(f"{resolved}.md")
        if wiki_target.exists():
            return wiki_target

    return resolved


def check_links():
    broken = []
    for p in markdown_files():
        raw = p.read_text(encoding="utf-8", errors='ignore')
        text = _strip_fenced_code_blocks(raw)
        for match in LINK_RE.finditer(text):
            url = match.group(1).split()[0].strip('<>')
            if URL_RE.match(url) or url.startswith('#') or url.startswith('mailto:'):
                continue
            target = url.split('#', 1)[0]
            if not target:
                continue
            if not _resolve_link_target(p, target).exists():
                line = text[:match.start()].count('\n') + 1
                broken.append(f'{p.relative_to(ROOT)}:{line}: broken link {url}')
    return broken


def check_stale_architecture_refs():
    stale_terms = ['architecture.mermaid', 'generate_diagram.sh', '@mermaid-js/mermaid-cli', 'regenerate with Mermaid', '```mermaid']
    hits = []
    for p in markdown_files():
        text = p.read_text(encoding="utf-8", errors='ignore')
        for line_no, line in enumerate(text.splitlines(), 1):
            for term in stale_terms:
                if term in line:
                    hits.append(f'{p.relative_to(ROOT)}:{line_no}: stale architecture term {term}')
    return hits


def check_source_matrix():
    env = (ROOT / '.env.example').read_text(encoding="utf-8", errors='ignore')
    guide = (ROOT / 'docs/deployment/source-configuration.md').read_text(encoding="utf-8", errors='ignore')
    missing = []
    source_vars = sorted(set(re.findall(r'^([A-Z0-9_]+_SOURCE)=', env, re.M)))
    if not source_vars:
        # Empty-match guard: zero *_SOURCE vars means the pattern or the
        # file is broken, not that the matrix is in sync.
        return ['.env.example: no *_SOURCE= variables matched — check pattern/file']
    for var in source_vars:
        if var not in guide:
            missing.append(f'docs/deployment/source-configuration.md: missing {var}')
    return missing


def check_required_files():
    required = [
        'docs/deployment/ports-and-routes.md',
        'docs/diagrams/architecture.svg',
        'docs/diagrams/architecture.html',
        'docs/diagrams/README.md',
    ]
    return [f'missing required docs artifact {path}' for path in required if not (ROOT / path).exists()]


def check_placeholder_urls():
    hits = []
    for p in markdown_files():
        text = p.read_text(encoding="utf-8", errors='ignore')
        for line_no, line in enumerate(text.splitlines(), 1):
            if 'github.com/your-repo' in line:
                hits.append(f'{p.relative_to(ROOT)}:{line_no}: placeholder GitHub URL')
    return hits


def check_numbered_headings():
    hits = []
    for path in documentation_paths(ROOT):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_number, message in heading_number_findings(text):
            hits.append(f"{path.relative_to(ROOT)}:{line_number}: {message}")
    return hits


def check_professional_symbols():
    hits = []
    for path in documentation_paths(ROOT):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_number, symbol in decorative_symbol_findings(text):
            hits.append(
                f"{path.relative_to(ROOT)}:{line_number}: decorative symbol {symbol}"
            )
    return hits


def main():
    checks = {
        'links': check_links(),
        'architecture_refs': check_stale_architecture_refs(),
        'source_matrix': check_source_matrix(),
        'required_files': check_required_files(),
        'placeholder_urls': check_placeholder_urls(),
        'numbered_headings': check_numbered_headings(),
        'professional_symbols': check_professional_symbols(),
    }
    failed = False
    for name, issues in checks.items():
        if issues:
            failed = True
            print(f'FAIL {name}')
            for issue in issues:
                print(f'  {issue}')
        else:
            print(f'PASS {name}')
    if failed:
        sys.exit(1)

if __name__ == '__main__':
    main()
