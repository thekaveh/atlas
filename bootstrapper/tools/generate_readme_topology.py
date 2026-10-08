"""Regenerates the <!-- TOPOLOGY:BEGIN --> ... <!-- TOPOLOGY:END --> block in README.md.

Run: uv run --project bootstrapper python -m tools.generate_readme_topology
"""

from __future__ import annotations

from pathlib import Path
import re

from services.topology import CATEGORY_LABELS, CATEGORY_ORDER, get_topology


def _published_port_vars(services_root: Path) -> set[str]:
    """Port vars some compose fragment interpolates (i.e. publishes)."""
    text = "\n".join(path.read_text(encoding="utf-8") for path in services_root.glob("*/compose.yml"))
    return set(re.findall(r"\$\{([A-Z0-9_]+_PORT)\b", text))


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
    published = _published_port_vars(services_root)
    for cat in CATEGORY_ORDER:
        for r in by_category[cat]:
            port = topology.port_defaults.get(r.port_var or "", "—")
            own_compose = (services_root / r.manifest / "compose.yml").exists()  # not virtual
            if r.port_var and port != "—" and own_compose and r.port_var not in published:
                # Reserved in the port block but not bound on the host
                # (pg-meta, Studio): listing the number read as reachable.
                port = "— (Kong only)" if r.alias else "—"
            alias = r.alias or "—"
            lines.append(f"| {CATEGORY_LABELS[cat]} | {r.display_name} | {port} | {alias} |")
    lines.append("<!-- TOPOLOGY:END -->")
    return "\n".join(lines) + "\n"


def update_readme(readme_path: Path, services_root: Path) -> None:
    block = generate_block(services_root)
    text = readme_path.read_text(encoding="utf-8")
    start = text.find("<!-- TOPOLOGY:BEGIN -->")
    end = text.find("<!-- TOPOLOGY:END -->")
    if start == -1 or end == -1:
        raise RuntimeError("README.md is missing the TOPOLOGY markers")
    end += len("<!-- TOPOLOGY:END -->")
    new_text = text[:start] + block.rstrip() + text[end:]
    readme_path.write_text(new_text, encoding="utf-8")


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent.parent.parent
    update_readme(project_root / "README.md", project_root / "services")
    print("Updated README.md TOPOLOGY block")
