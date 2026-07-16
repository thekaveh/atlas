"""Regenerate committed manifest-derived reference pages."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from bootstrapper.docs.sitegen.model import load_docs_model
from bootstrapper.docs.sitegen.pages import architecture_pages, reference_pages, static_pages
from bootstrapper.docs.sitegen.services import service_pages
from scripts.docs.manifest import load_manifest


_SERVICE_LINK_RE = re.compile(r"\]\(([a-z0-9][a-z0-9-]*)\.md\)")


def _final_newline(text: str) -> str:
    return text.rstrip() + "\n"


def _service_index(text: str) -> str:
    return _SERVICE_LINK_RE.sub(r"](../services/\1/README.md)", text)


def _ports_reference(text: str) -> str:
    return text.replace("../../deployment/", "../deployment/")


def _apply_manifest_h1_numbers(
    rendered: dict[Path, str], repo_root: Path
) -> dict[Path, str]:
    manifest = load_manifest(repo_root / "docs" / "manifest.yaml", repo_root)
    numbers = {repo_root / page.source: page.number for page in manifest.pages}
    for path, number in numbers.items():
        if path not in rendered or path.suffix != ".md":
            continue
        rendered[path] = re.sub(
            r"(?m)^# (?:\d+(?:\.\d+)*\. )?",
            f"# {number}. ",
            rendered[path],
            count=1,
        )
    return rendered


def render_canonical_references(repo_root: Path) -> dict[Path, str]:
    """Render the committed pages whose content comes from manifests and tracks."""
    model = load_docs_model(repo_root)
    static = static_pages(model)
    services = service_pages(model)
    references = reference_pages(model)
    architecture = architecture_pages(model)
    old_site = repo_root / "docs" / "site"
    rendered = {
        repo_root / "docs" / "tracks.md": _final_newline(static[old_site / "tracks.md"]),
        repo_root / "docs" / "services.md": _final_newline(
            _service_index(services[old_site / "services" / "index.md"])
        ),
        repo_root / "docs" / "reference" / "index.md": _final_newline(
            static[old_site / "reference" / "index.md"]
        ),
    }
    for source, content in references.items():
        target = repo_root / "docs" / "reference" / source.name
        transformed = _ports_reference(content) if source.name == "ports-routes.md" else content
        rendered[target] = _final_newline(transformed)
    for source, content in architecture.items():
        rendered[source] = _final_newline(content)
    return _apply_manifest_h1_numbers(rendered, repo_root)


def sync_canonical_references(repo_root: Path, *, check: bool) -> list[Path]:
    """Return drifted paths and optionally rewrite them when ``check`` is false."""
    changed: list[Path] = []
    for path, expected in render_canonical_references(repo_root).items():
        actual = path.read_text(encoding="utf-8") if path.is_file() else None
        if actual == expected:
            continue
        changed.append(path)
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(expected, encoding="utf-8")
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description="Update manifest-derived canonical docs")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path.cwd()
    changed = sync_canonical_references(root, check=args.check)
    if changed:
        for path in changed:
            print(path.relative_to(root).as_posix())
        if args.check:
            raise SystemExit(1)
        print(f"Updated {len(changed)} canonical reference page(s)")
    else:
        print("PASS canonical references are current")


if __name__ == "__main__":
    main()
