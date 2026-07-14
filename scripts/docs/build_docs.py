from __future__ import annotations

import argparse
import hashlib
import shutil
import tempfile
from pathlib import Path

import yaml

from .manifest import Manifest, Page, Section, load_manifest
from .render_diagrams import render_all
from .transforms import build_source_map, rewrite_for_surface


class _QuotedString(str):
    pass


class _DocsDumper(yaml.SafeDumper):
    pass


_DocsDumper.add_representer(
    _QuotedString,
    lambda dumper, value: dumper.represent_scalar("tag:yaml.org,2002:str", value, style='"'),
)


def _number_h1(markdown: str, page: Page) -> str:
    lines = markdown.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("# "):
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = f"# {page.number}. {page.title}{newline}"
            return "".join(lines)
    return f"# {page.number}. {page.title}\n\n{markdown}"


def _diagram_asset_maps(manifest: Manifest, surface: str) -> dict[str, str]:
    extension = "svg" if surface == "site" else "png"
    prefix = "assets/img" if surface == "site" else "img"
    result: dict[str, str] = {}
    diagrams = {diagram.id: diagram for diagram in manifest.diagrams}
    for page in manifest.pages:
        for diagram_id in page.diagrams:
            master = diagrams[diagram_id].master
            source_dir = Path(page.source).parent.as_posix()
            result[f"{source_dir}/architecture.svg"] = f"{prefix}/{diagram_id}.{extension}"
            result[f"{source_dir}/architecture.html"] = f"{prefix}/{diagram_id}.{extension}"
            result[master] = f"{prefix}/{diagram_id}.{extension}"
    return result


def _render_pages(manifest: Manifest, repo_root: Path, destination: Path, surface: str) -> None:
    source_map = build_source_map(manifest, surface)
    asset_map = _diagram_asset_maps(manifest, surface)
    for page in manifest.pages:
        output = page.site_path if surface == "site" else page.wiki_path
        markdown = (repo_root / page.source).read_text(encoding="utf-8")
        rendered = rewrite_for_surface(
            markdown,
            surface=surface,
            source_path=page.source,
            output_path=output.as_posix(),
            source_map=source_map,
            asset_map=asset_map,
        )
        rendered = _number_h1(rendered, page)
        target = destination / output
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered.rstrip() + "\n", encoding="utf-8")


def _sidebar_lines(sections: tuple[Section, ...], page_lookup: dict[str, Page], depth: int = 0) -> list[str]:
    lines: list[str] = []
    indent = "  " * depth
    for section in sections:
        if section.source:
            page = page_lookup[section.id]
            lines.append(f"{indent}- [{page.number}. {page.title}]({page.wiki_path.stem})")
        else:
            lines.append(f"{indent}- **{section.number}. {section.title}**")
            lines.extend(_sidebar_lines(section.children, page_lookup, depth + 1))
    return lines


def render_site(manifest: Manifest, repo_root: Path, destination: Path) -> None:
    _reset_dir(destination)
    _render_pages(manifest, repo_root, destination, "site")
    for name in ("assets", "screenshots"):
        source = repo_root / "docs" / name
        if source.is_dir():
            shutil.copytree(source, destination / name, dirs_exist_ok=True)
    stylesheet = repo_root / "docs" / "assets" / "stylesheets" / "atlas.css"
    if stylesheet.exists():
        target = destination / "stylesheets" / "atlas.css"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(stylesheet, target)
    javascript = repo_root / "docs" / "javascripts" / "mathjax.js"
    if javascript.exists():
        target = destination / "javascripts" / "mathjax.js"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(javascript, target)


def render_wiki(manifest: Manifest, repo_root: Path, destination: Path) -> None:
    _reset_dir(destination)
    _render_pages(manifest, repo_root, destination, "wiki")
    for name in ("assets", "screenshots"):
        source = repo_root / "docs" / name
        if source.is_dir():
            shutil.copytree(source, destination / name, dirs_exist_ok=True)
    page_lookup = {page.id: page for page in manifest.pages}
    sidebar = "# Atlas Documentation\n\n" + "\n".join(
        _sidebar_lines(manifest.sections, page_lookup)
    )
    (destination / "_Sidebar.md").write_text(sidebar.rstrip() + "\n", encoding="utf-8")
    (destination / "_Footer.md").write_text(
        "Atlas documentation generated from the canonical public manifest.\n",
        encoding="utf-8",
    )


def _nav_entries(sections: tuple[Section, ...], pages: dict[str, Page]) -> list[dict]:
    nav: list[dict] = []
    for section in sections:
        label = _QuotedString(f"{section.number}. {section.title}")
        if section.source:
            nav.append({label: pages[section.id].site_path.as_posix()})
        else:
            nav.append({label: _nav_entries(section.children, pages)})
    return nav


def render_mkdocs_yml(manifest: Manifest) -> str:
    config = {
        "site_name": "Atlas Documentation",
        "site_description": "Atlas self-hosted AI, data, and engineering platform documentation",
        "site_url": "https://thekaveh.github.io/atlas/",
        "docs_dir": "generated/site",
        "site_dir": "site",
        "strict": True,
        "theme": {
            "name": "material",
            "language": "en",
            "features": [
                "navigation.sections",
                "navigation.indexes",
                "navigation.top",
                "search.suggest",
                "search.highlight",
                "content.code.copy",
                "toc.follow",
            ],
            "palette": [
                {
                    "scheme": "slate",
                    "primary": "custom",
                    "accent": "custom",
                    "toggle": {"icon": "material/weather-sunny", "name": "Switch to light mode"},
                },
                {
                    "scheme": "default",
                    "primary": "custom",
                    "accent": "custom",
                    "toggle": {"icon": "material/weather-night", "name": "Switch to dark mode"},
                },
            ],
        },
        "extra_css": ["stylesheets/atlas.css"],
        "markdown_extensions": [
            "admonition",
            "attr_list",
            "md_in_html",
            "footnotes",
            "def_list",
            "pymdownx.superfences",
            "pymdownx.highlight",
            "pymdownx.inlinehilite",
            "pymdownx.details",
            {"pymdownx.tabbed": {"alternate_style": True}},
            {"toc": {"permalink": True}},
        ],
        "nav": _nav_entries(manifest.sections, {page.id: page for page in manifest.pages}),
    }
    return yaml.dump(config, Dumper=_DocsDumper, sort_keys=False, allow_unicode=True)


def _reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def _file_hashes(path: Path) -> dict[str, str]:
    return {
        item.relative_to(path).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def _assert_dirs_equal(actual: Path, rerendered: Path) -> None:
    if _file_hashes(actual) != _file_hashes(rerendered):
        raise RuntimeError(f"Documentation rendering is not deterministic: {actual}")


def build(
    manifest_path: Path,
    repo_root: Path,
    *,
    site: bool,
    wiki: bool,
    check: bool,
) -> None:
    manifest = load_manifest(manifest_path, repo_root)
    generated = repo_root / "generated"
    if site:
        render_site(manifest, repo_root, generated / "site")
    if wiki:
        render_wiki(manifest, repo_root, generated / "wiki")
    if manifest.diagrams:
        render_all(
            manifest,
            repo_root,
            generated / "site" / "assets" / "img",
            repo_root / "docs" / "diagrams" / "img",
            generated / "wiki" / "img" if wiki else None,
            check_png=check,
        )
    (repo_root / "mkdocs.yml").write_text(render_mkdocs_yml(manifest), encoding="utf-8")
    if check:
        with tempfile.TemporaryDirectory(prefix="atlas-docs-check-") as temp:
            root = Path(temp)
            if site:
                render_site(manifest, repo_root, root / "site")
            if wiki:
                render_wiki(manifest, repo_root, root / "wiki")
            if manifest.diagrams:
                render_all(
                    manifest,
                    repo_root,
                    root / "site" / "assets" / "img",
                    root / "png",
                    root / "wiki" / "img" if wiki else None,
                )
            if site:
                _assert_dirs_equal(generated / "site", root / "site")
            if wiki:
                _assert_dirs_equal(generated / "wiki", root / "wiki")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Atlas documentation surfaces")
    parser.add_argument("--manifest", default="docs/manifest.yaml")
    parser.add_argument("--site", action="store_true")
    parser.add_argument("--wiki", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path.cwd()
    site = args.site or not args.wiki
    wiki = args.wiki or not args.site
    build(root / args.manifest, root, site=site, wiki=wiki, check=args.check)


if __name__ == "__main__":
    main()
