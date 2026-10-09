"""Regenerates the <!-- TOPOLOGY:BEGIN --> ... <!-- TOPOLOGY:END --> block in README.md.

Run: uv run --project bootstrapper python -m tools.generate_readme_topology
"""

from __future__ import annotations

from pathlib import Path

from services.topology import CATEGORY_LABELS, CATEGORY_ORDER, get_topology


def generate_block(services_root: Path) -> str:
    topology = get_topology(services_root)

    # Bucket rows by category once (O(n)), then iterate the categories in
    # display order. Avoids the prior O(n×m) nested loop and mirrors the
    # by-category pattern shared with the architecture-diagram skill.
    by_category: dict[str, list] = {c: [] for c in CATEGORY_ORDER}
    for r in topology.rows:
        if r.category in by_category:
            by_category[r.category].append(r)

    lines: list[str] = [
        "<!-- TOPOLOGY:BEGIN -->",
        "_Engine-only manifests (speaches, chatterbox) are not listed — they're "
        "selected as source variants of their parent (STT Provider / TTS Provider) "
        "rather than as standalone services._",
        "",
        "| Category | Service | Default port | Alias |",
        "|---|---|---:|---|",
    ]
    from services.topology import unpublished_port_vars

    unpublished = unpublished_port_vars(services_root)
    for cat in CATEGORY_ORDER:
        for r in by_category[cat]:
            port = topology.port_defaults.get(r.port_var or "", "—")
            if port != "—" and r.port_var in unpublished:
                # Reserved in the port block but not bound on the host
                # (pg-meta, Studio): listing the number read as reachable.
                port = "— (Kong only)" if r.alias else "—"
            alias = r.alias or "—"
            lines.append(f"| {CATEGORY_LABELS[cat]} | {r.display_name} | {port} | {alias} |")
    lines.append("<!-- TOPOLOGY:END -->")
    return "\n".join(lines) + "\n"


def _rendered(text: str, block: str) -> str:
    """``text`` with its TOPOLOGY block replaced. Swapped or repeated markers
    are refused: text[:start] + block + text[end:] duplicated everything
    between them (2026-10-08 run, cycle 66)."""
    begin, finish = "<!-- TOPOLOGY:BEGIN -->", "<!-- TOPOLOGY:END -->"
    if text.count(begin) != 1 or text.count(finish) != 1:
        raise RuntimeError("README.md must contain exactly one pair of TOPOLOGY markers")
    start, end = text.find(begin), text.find(finish)
    if end < start:
        raise RuntimeError("README.md TOPOLOGY:END comes before TOPOLOGY:BEGIN")
    return text[:start] + block.rstrip() + text[end + len(finish):]


def update_readme(readme_path: Path, services_root: Path, *, check: bool = False) -> bool:
    """Rewrite the block, or with ``check`` only report whether it is
    current (True) without writing; `--check` used to rewrite it anyway."""
    text = readme_path.read_text(encoding="utf-8")
    new_text = _rendered(text, generate_block(services_root))
    if check:
        return new_text == text
    readme_path.write_text(new_text, encoding="utf-8")
    return True


if __name__ == "__main__":
    import sys

    project_root = Path(__file__).resolve().parent.parent.parent
    check = "--check" in sys.argv[1:]
    current = update_readme(project_root / "README.md", project_root / "services", check=check)
    if check:
        print("README.md TOPOLOGY block is current" if current else
              "README.md TOPOLOGY block is stale; run `python -m tools.generate_readme_topology`")
        sys.exit(0 if current else 1)
    print("Updated README.md TOPOLOGY block")
