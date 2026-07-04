# Atlas Docs Site And Wiki Revamp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a polished, synchronized Atlas documentation product: Material for MkDocs `.io` site, expanded GitHub Wiki, rich generated service pages, architecture coverage, and CI drift checks.

**Architecture:** Keep the existing command surface (`scripts/generate-docs-site.py`, `scripts/export-docs-wiki.py`, `scripts/check-docs-site.py`) but move generation logic into a focused `bootstrapper/docs/sitegen/` package. Generate MkDocs pages and wiki pages from one normalized `DocsModel` built from service manifests, topology, tracks, routes, assets, service READMEs, and diagrams.

**Tech Stack:** Python 3.10+, MkDocs 1.6, Material for MkDocs, PyYAML, pytest, GitHub Actions, GitHub Pages, GitHub Wiki, existing Atlas manifest/topology/docs modules.

## Global Constraints

- Use a separate branch/worktree; do not push directly to `main`.
- Preserve service-local READMEs as source-owned documentation under `services/<name>/README.md`.
- Do not hand-copy service READMEs into static MkDocs pages.
- Use Material for MkDocs with Atlas dark mode by default and a light-mode toggle.
- Use blue/cyan/electric accents that match the Atlas visual identity.
- Reuse `assets/atlas-poster.png`, `assets/atlas-source.png`, `docs/assets/images/atlas-source.png`, `docs/screenshots/wizard-running.png`, and `docs/diagrams/architecture.svg`.
- Keep generated documentation hierarchically numbered.
- Use the `architecture-diagram` design system for high-level or newly regenerated diagrams.
- Keep Pages deployment through GitHub Actions.
- Wiki pages are generated companion pages, not the canonical source of truth.
- CI must fail on generated site drift, wiki drift, broken internal site links, missing service docs entries, missing SOURCE references, and missing track references.

---

## File Structure

- Create `bootstrapper/docs/sitegen/__init__.py`: package marker and public exports.
- Create `bootstrapper/docs/sitegen/model.py`: `DocsModel`, `ServicePage`, `TrackPage`, route/access helpers, and data loading from manifests, topology, tracks, READMEs, assets, and diagrams.
- Create `bootstrapper/docs/sitegen/rendering.py`: markdown helpers, table rendering, slug/path helpers, numbered heading helpers, and safe text normalization.
- Create `bootstrapper/docs/sitegen/mkdocs_config.py`: Material for MkDocs config generation and numbered navigation.
- Create `bootstrapper/docs/sitegen/theme.py`: Atlas Material CSS generation and asset copy targets.
- Create `bootstrapper/docs/sitegen/pages.py`: generated MkDocs home, overview, quick start, concepts, tracks, architecture, configuration, operations, development, and reference pages.
- Create `bootstrapper/docs/sitegen/services.py`: generated service catalog and service profile pages.
- Create `bootstrapper/docs/sitegen/wiki.py`: generated GitHub Wiki page set.
- Modify `scripts/generate-docs-site.py`: thin CLI wrapper over `bootstrapper.docs.sitegen`.
- Modify `scripts/export-docs-wiki.py`: keep live wiki push, use generated wiki artifacts from `sitegen`.
- Modify `scripts/check-docs-site.py`: preserve strict build and built-site link validation.
- Modify `bootstrapper/pyproject.toml`: add `mkdocs-material` to the dev dependency group with a bounded range.
- Modify `bootstrapper/tests/test_docs_site_platform.py`: update current docs-site tests for Material, richer pages, wiki expansion, and service coverage.
- Create `bootstrapper/tests/test_docs_sitegen_model.py`: focused model loader tests.
- Create `bootstrapper/tests/test_docs_sitegen_rendering.py`: focused renderer/page tests.
- Modify `.github/workflows/docs-pages.yml`: keep Pages workflow, update dependency expectations only if needed.
- Modify `.github/workflows/services-lint.yml`: keep docs drift and audit gates aligned with new commands.
- Modify `docs/README.md`, `docs/CONTRIBUTING-services.md`, and `AGENTS.md`: update docs maintainer commands and site/wiki guidance.
- Create or modify generated outputs under `docs/index.md`, `docs/site/**`, `docs/wiki/**`, `docs/assets/stylesheets/atlas.css`, and `mkdocs.yml`.

---

### Task 1: Add Material Dependency And Failing Theme/Config Tests

**Files:**
- Modify: `bootstrapper/pyproject.toml`
- Modify: `bootstrapper/tests/test_docs_site_platform.py`
- Test: `bootstrapper/tests/test_docs_site_platform.py`

**Interfaces:**
- Consumes: existing `mkdocs.yml`, `docs/assets/stylesheets/atlas.css`, and `docs/index.md`.
- Produces: failing tests that require Material for MkDocs, Atlas palette config, dark default, light toggle, richer wiki page set, and hero/screenshot reuse.

- [ ] **Step 1: Write failing tests for Material config and required wiki pages**

Replace `test_atlas_theme_uses_dark_atlas_system_with_local_assets` in `bootstrapper/tests/test_docs_site_platform.py` with this stricter version:

```python
def test_atlas_theme_uses_material_dark_default_with_light_toggle() -> None:
    config = _mkdocs()
    css = THEME_CSS.read_text(encoding="utf-8")
    home = (ROOT / "docs" / "index.md").read_text(encoding="utf-8")

    assert config["theme"]["name"] == "material"
    assert config["theme"]["features"] >= [
        "navigation.sections",
        "navigation.indexes",
        "navigation.top",
        "search.suggest",
        "search.highlight",
    ]
    palettes = config["theme"]["palette"]
    assert palettes[0]["scheme"] == "slate"
    assert palettes[0]["primary"] == "custom"
    assert palettes[0]["accent"] == "custom"
    assert palettes[0]["toggle"]["name"] == "Switch to light mode"
    assert palettes[1]["scheme"] == "default"
    assert palettes[1]["toggle"]["name"] == "Switch to dark mode"

    for color in ("#020617", "#07111f", "#0ea5e9", "#38bdf8", "#60a5fa", "#7dd3fc"):
        assert color in css
    assert ":root" in css
    assert "[data-md-color-scheme=\"slate\"]" in css
    assert "[data-md-color-scheme=\"default\"]" in css
    assert "@import url(" not in css
    assert "fonts.googleapis.com" not in css
    assert "assets/images/atlas-source.png" in home
    assert THEME_HERO_IMAGE.exists()
```

Add this test near `test_wiki_export_and_ci_hooks_are_present`:

```python
def test_wiki_export_contains_full_companion_page_set() -> None:
    expected_pages = {
        "Home.md",
        "_Sidebar.md",
        "Overview.md",
        "Quick-Start.md",
        "Core-Concepts.md",
        "Tracks.md",
        "Services.md",
        "Architecture.md",
        "Configuration.md",
        "Operations.md",
        "Development.md",
        "Reference.md",
    }
    actual_pages = {path.name for path in WIKI_DIR.glob("*.md")}
    assert expected_pages <= actual_pages

    sidebar = (WIKI_DIR / "_Sidebar.md").read_text(encoding="utf-8")
    for page in [
        "Overview",
        "Quick-Start",
        "Core-Concepts",
        "Tracks",
        "Services",
        "Architecture",
        "Configuration",
        "Operations",
        "Development",
        "Reference",
    ]:
        assert f"]({page})" in sidebar

    services = (WIKI_DIR / "Services.md").read_text(encoding="utf-8")
    assert "## 1. Service Catalog" in services
    assert "| Service | Category | Tracks | SOURCE | Values | Dependencies |" in services
```

- [ ] **Step 2: Run the targeted tests and verify they fail**

Run:

```bash
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_site_platform.py::test_atlas_theme_uses_material_dark_default_with_light_toggle bootstrapper/tests/test_docs_site_platform.py::test_wiki_export_contains_full_companion_page_set -q
```

Expected: FAIL because the current theme is `mkdocs`, the wiki page set is too small, and the home page does not include the wizard screenshot.

- [ ] **Step 3: Add Material for MkDocs dependency**

Edit `bootstrapper/pyproject.toml` dev dependencies to:

```toml
dev = [
    "mkdocs>=1.6,<2",
    "mkdocs-material>=9.6,<10",
    "pytest>=7.0.0",
]
```

- [ ] **Step 4: Sync the lockfile**

Run:

```bash
uv lock --project bootstrapper
```

Expected: `bootstrapper/uv.lock` changes to include `mkdocs-material` and its transitive dependencies.

- [ ] **Step 5: Commit failing tests and dependency**

Run:

```bash
git add bootstrapper/pyproject.toml bootstrapper/uv.lock bootstrapper/tests/test_docs_site_platform.py
git commit -m "test: require Material docs site and expanded wiki"
```

---

### Task 2: Introduce Shared Docs Model

**Files:**
- Create: `bootstrapper/docs/sitegen/__init__.py`
- Create: `bootstrapper/docs/sitegen/model.py`
- Test: `bootstrapper/tests/test_docs_sitegen_model.py`

**Interfaces:**
- Consumes: `services.manifests.load_manifests`, `bootstrapper/tracks.yml`, `services/topology.py`, service READMEs, service architecture assets.
- Produces: `load_docs_model(root: Path) -> DocsModel`, `DocsModel.services`, `DocsModel.tracks`, `ServicePage.track_keys`, `ServicePage.diagram_svg`, and `ServicePage.readme`.

- [ ] **Step 1: Write failing model tests**

Create `bootstrapper/tests/test_docs_sitegen_model.py`:

```python
from __future__ import annotations

from pathlib import Path

from docs.sitegen.model import load_docs_model


ROOT = Path(__file__).resolve().parents[2]


def test_docs_model_indexes_services_tracks_and_assets() -> None:
    model = load_docs_model(ROOT)

    assert model.public_url == "https://thekaveh.github.io/atlas/"
    assert model.hero_image == Path("assets/images/atlas-source.png")
    assert model.wizard_screenshot == Path("screenshots/wizard-running.png")
    assert "data-eng" in model.tracks_by_key
    assert "gen-ai-rag" in model.tracks_by_key

    services = model.services_by_name
    assert "supabase" in services
    assert "open-webui" in services
    assert "cloud-providers" in services
    assert "stt-provider" in services

    supabase = services["supabase"]
    assert supabase.title
    assert supabase.category in {"infra", "data", "llm", "media", "agents", "apps", "aggregate"}
    assert supabase.kind in {"container", "virtual", "doc-only"}
    assert supabase.readme == ROOT / "services" / "supabase" / "README.md"
    assert supabase.diagram_svg == ROOT / "services" / "supabase" / "architecture.svg"
    assert supabase.track_keys


def test_docs_model_service_access_and_dependencies_are_normalized() -> None:
    model = load_docs_model(ROOT)
    litellm = model.services_by_name["litellm"]

    assert litellm.source_var == "LITELLM_SOURCE"
    assert litellm.source_values
    assert isinstance(litellm.required_dependencies, list)
    assert isinstance(litellm.optional_dependencies, list)
    assert isinstance(litellm.runtime_calls, list)
    assert isinstance(litellm.kong_aliases, list)
    assert isinstance(litellm.port_vars, list)
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_sitegen_model.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'docs.sitegen'`.

- [ ] **Step 3: Create package exports**

Create `bootstrapper/docs/sitegen/__init__.py`:

```python
"""Generated MkDocs and GitHub Wiki publishing layer for Atlas."""

from .model import DocsModel, ServicePage, TrackPage, load_docs_model

__all__ = ["DocsModel", "ServicePage", "TrackPage", "load_docs_model"]
```

- [ ] **Step 4: Implement model loader**

Create `bootstrapper/docs/sitegen/model.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from services.manifests import load_manifests
from services.topology import get_topology


PUBLIC_URL = "https://thekaveh.github.io/atlas/"


@dataclass(frozen=True)
class TrackPage:
    key: str
    label: str
    description: str
    services: list[str]


@dataclass(frozen=True)
class ServicePage:
    name: str
    title: str
    category: str
    kind: str
    readme: Path
    source_var: str
    source_default: str
    source_values: list[str]
    track_keys: list[str]
    required_dependencies: list[str]
    optional_dependencies: list[str]
    runtime_calls: list[str]
    kong_aliases: list[str]
    port_vars: list[str]
    diagram_svg: Path | None
    diagram_html: Path | None


@dataclass(frozen=True)
class DocsModel:
    root: Path
    public_url: str
    hero_image: Path
    poster_image: Path
    wizard_screenshot: Path
    top_level_diagram: Path
    services: list[ServicePage]
    tracks: list[TrackPage]

    @property
    def services_by_name(self) -> dict[str, ServicePage]:
        return {service.name: service for service in self.services}

    @property
    def tracks_by_key(self) -> dict[str, TrackPage]:
        return {track.key: track for track in self.tracks}


def _load_tracks(root: Path) -> list[TrackPage]:
    data = yaml.safe_load((root / "bootstrapper" / "tracks.yml").read_text(encoding="utf-8"))
    tracks: list[TrackPage] = []
    for row in data["tracks"]:
        tracks.append(
            TrackPage(
                key=row["key"],
                label=row.get("label", row["key"]),
                description=row.get("description", ""),
                services=list(row.get("services", [])),
            )
        )
    return tracks


def _track_membership(tracks: list[TrackPage]) -> dict[str, list[str]]:
    membership: dict[str, list[str]] = {}
    for track in tracks:
        for service in track.services:
            membership.setdefault(service, []).append(track.key)
    return {name: sorted(keys) for name, keys in membership.items()}


def _topology_lookup() -> dict[str, Any]:
    return {row.name: row for row in get_topology()}


def _manifest_docs(root: Path, tracks: list[TrackPage]) -> list[ServicePage]:
    services_dir = root / "services"
    manifests = {manifest.name: manifest for manifest in load_manifests(services_dir)}
    readme_dirs = {
        path.name: path
        for path in services_dir.iterdir()
        if path.is_dir()
        and not path.name.startswith(("_", "."))
        and (path / "README.md").exists()
    }
    membership = _track_membership(tracks)
    topology = _topology_lookup()
    docs: list[ServicePage] = []

    for name in sorted(set(manifests) | set(readme_dirs)):
        manifest = manifests.get(name)
        topological = topology.get(name)
        readme = services_dir / name / "README.md"
        source_var = manifest.sources.var if manifest and manifest.sources else ""
        source_default = manifest.sources.default if manifest and manifest.sources else ""
        source_values = [option.id for option in manifest.sources.options] if manifest and manifest.sources else []
        required = list(manifest.depends_on.required) if manifest else []
        optional = list(manifest.depends_on.optional) if manifest else []
        runtime_calls = list(manifest.data_flow.get("calls", [])) if manifest else []
        aliases = list(getattr(topological, "aliases", []) or [])
        port_vars = [env.name for env in manifest.env if getattr(env, "port", None)] if manifest else []
        diagram_svg = services_dir / name / "architecture.svg"
        diagram_html = services_dir / name / "architecture.html"

        docs.append(
            ServicePage(
                name=name,
                title=manifest.label if manifest else name,
                category=manifest.category if manifest else "aggregate",
                kind="virtual" if manifest and manifest.virtual else "container" if manifest else "doc-only",
                readme=readme,
                source_var=source_var,
                source_default=source_default,
                source_values=source_values,
                track_keys=membership.get(name, []),
                required_dependencies=required,
                optional_dependencies=optional,
                runtime_calls=runtime_calls,
                kong_aliases=aliases,
                port_vars=port_vars,
                diagram_svg=diagram_svg if diagram_svg.exists() else None,
                diagram_html=diagram_html if diagram_html.exists() else None,
            )
        )
    return docs


def load_docs_model(root: Path) -> DocsModel:
    tracks = _load_tracks(root)
    return DocsModel(
        root=root,
        public_url=PUBLIC_URL,
        hero_image=Path("assets/images/atlas-source.png"),
        poster_image=Path("../assets/atlas-poster.png"),
        wizard_screenshot=Path("screenshots/wizard-running.png"),
        top_level_diagram=Path("diagrams/architecture.svg"),
        services=_manifest_docs(root, tracks),
        tracks=tracks,
    )
```

- [ ] **Step 5: Run model tests**

Run:

```bash
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_sitegen_model.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit model package**

Run:

```bash
git add bootstrapper/docs/sitegen bootstrapper/tests/test_docs_sitegen_model.py
git commit -m "feat: add shared docs site model"
```

---

### Task 3: Generate Material MkDocs Config And Atlas Theme

**Files:**
- Create: `bootstrapper/docs/sitegen/rendering.py`
- Create: `bootstrapper/docs/sitegen/mkdocs_config.py`
- Create: `bootstrapper/docs/sitegen/theme.py`
- Modify: `scripts/generate-docs-site.py`
- Test: `bootstrapper/tests/test_docs_site_platform.py`

**Interfaces:**
- Consumes: `DocsModel`.
- Produces: `build_mkdocs_config(model: DocsModel) -> dict`, `theme_artifacts(root: Path) -> dict[Path, str]`, `copy_artifacts(root: Path) -> list[tuple[Path, Path]]`.

- [ ] **Step 1: Write rendering helpers**

Create `bootstrapper/docs/sitegen/rendering.py`:

```python
from __future__ import annotations

from collections.abc import Iterable


def csv_or_dash(values: Iterable[str]) -> str:
    clean = [str(value) for value in values if str(value)]
    return ", ".join(clean) if clean else "-"


def table(headers: list[str], rows: Iterable[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(cell.replace("|", "/") for cell in row) + " |")
    return "\n".join(lines)


def numbered_nav(items: list[dict], prefix: str = "") -> list[dict]:
    numbered: list[dict] = []
    for index, item in enumerate(items, start=1):
        for label, value in item.items():
            numbered_label = f"{prefix}{index}. {label}"
            if isinstance(value, list):
                numbered.append({numbered_label: numbered_nav(value, f"{prefix}{index}.")})
            else:
                numbered.append({numbered_label: value})
    return numbered
```

- [ ] **Step 2: Write Material config generator**

Create `bootstrapper/docs/sitegen/mkdocs_config.py`:

```python
from __future__ import annotations

from .model import DocsModel
from .rendering import numbered_nav


def build_mkdocs_config(model: DocsModel) -> dict:
    service_pages = [{service.name: f"site/services/{service.name}.md"} for service in model.services]
    nav_items = [
        {"Overview": "index.md"},
        {"Quick Start": "site/quick-start.md"},
        {"Core Concepts": "site/core-concepts.md"},
        {"Tracks": "site/tracks.md"},
        {"Service Catalog": [{"Index": "site/services/index.md"}, {"Services": service_pages}]},
        {
            "Architecture": [
                {"Overview": "site/architecture/index.md"},
                {"Diagram Catalog": "architecture/README.md"},
                {"Platform Overview": "architecture/platform-overview.md"},
                {"Bootstrapper Lifecycle": "architecture/bootstrapper-lifecycle.md"},
                {"SOURCE Model": "architecture/source-configuration-model.md"},
                {"Track Matrix": "architecture/track-selection-matrix.md"},
                {"Network Routing": "architecture/network-routing-topology.md"},
                {"RAG Flow": "architecture/data-rag-flow.md"},
                {"LLM Flow": "architecture/llm-provider-flow.md"},
                {"Lakehouse Flow": "architecture/data-engineering-lakehouse-flow.md"},
                {"Observability Flow": "architecture/observability-flow.md"},
                {"Security Boundary": "architecture/security-auth-secrets-boundary.md"},
                {"Service Admission": "architecture/service-admission-workflow.md"},
            ]
        },
        {"Configuration": "site/configuration.md"},
        {"Operations": "site/operations.md"},
        {"Development": "site/development.md"},
        {
            "Reference": [
                {"Index": "site/reference/index.md"},
                {"SOURCE Values": "site/reference/source-values.md"},
                {"Environment Variables": "site/reference/env-vars.md"},
                {"Ports And Routes": "site/reference/ports-routes.md"},
                {"Tracks": "site/reference/tracks.md"},
                {"Service Dependencies": "site/reference/service-dependencies.md"},
                {"Manifest Fields": "site/reference/manifest-fields.md"},
            ]
        },
    ]
    return {
        "site_name": "Atlas Documentation",
        "site_description": "Atlas self-hosted AI, data, and engineering platform documentation",
        "site_url": model.public_url,
        "repo_url": "https://github.com/thekaveh/atlas",
        "repo_name": "thekaveh/atlas",
        "edit_uri": "edit/main/docs/",
        "docs_dir": "docs",
        "site_dir": "site",
        "strict": True,
        "exclude_docs": "README.md\nwiki/*.md",
        "extra_css": ["assets/stylesheets/atlas.css"],
        "validation": {
            "links": {
                "anchors": "ignore",
                "not_found": "warn",
                "unrecognized_links": "ignore",
                "absolute_links": "ignore",
            },
            "nav": {"omitted_files": "ignore"},
        },
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
        "nav": numbered_nav(nav_items),
    }
```

- [ ] **Step 3: Write Atlas theme artifact generator**

Create `bootstrapper/docs/sitegen/theme.py`:

```python
from __future__ import annotations

from pathlib import Path


def atlas_css() -> str:
    return """:root {
  --atlas-void: #020617;
  --atlas-panel: #07111f;
  --atlas-panel-strong: #0b1728;
  --atlas-ink: #e5f4ff;
  --atlas-ink-strong: #f8fbff;
  --atlas-muted: #9fb7cc;
  --atlas-blue: #60a5fa;
  --atlas-sky: #38bdf8;
  --atlas-cyan: #0ea5e9;
  --atlas-electric: #7dd3fc;
}

[data-md-color-scheme="slate"] {
  --md-primary-fg-color: #07111f;
  --md-primary-bg-color: #f8fbff;
  --md-accent-fg-color: #38bdf8;
  --md-default-bg-color: #020617;
  --md-default-fg-color: #e5f4ff;
  --md-typeset-a-color: #38bdf8;
}

[data-md-color-scheme="default"] {
  --md-primary-fg-color: #f8fbff;
  --md-primary-bg-color: #020617;
  --md-accent-fg-color: #0ea5e9;
  --md-typeset-a-color: #0369a1;
}

.md-header {
  background: rgba(2, 6, 23, 0.94);
  border-bottom: 1px solid rgba(96, 165, 250, 0.22);
}

.md-main {
  background:
    linear-gradient(115deg, rgba(14, 165, 233, 0.13), transparent 30rem),
    linear-gradient(180deg, #07111f 0, #020617 32rem, #020617 100%);
}

[data-md-color-scheme="default"] .md-main {
  background:
    linear-gradient(115deg, rgba(14, 165, 233, 0.08), transparent 30rem),
    linear-gradient(180deg, #f8fbff 0, #eef7ff 32rem, #ffffff 100%);
}

.md-typeset h1,
.md-typeset h2,
.md-typeset h3 {
  letter-spacing: 0;
}

.md-typeset h1 {
  color: var(--atlas-ink-strong);
  font-weight: 760;
}

[data-md-color-scheme="default"] .md-typeset h1 {
  color: #020617;
}

.atlas-hero {
  display: grid;
  gap: 1.2rem;
  margin: 1rem 0 2rem;
}

.atlas-hero img,
.atlas-screenshot img,
.atlas-diagram img {
  border: 1px solid rgba(96, 165, 250, 0.24);
  border-radius: 8px;
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.34);
}

.atlas-service-grid {
  display: grid;
  gap: 0.8rem;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
}

.atlas-service-card {
  border: 1px solid rgba(96, 165, 250, 0.22);
  border-radius: 8px;
  padding: 0.85rem;
  background: rgba(7, 17, 31, 0.66);
}

[data-md-color-scheme="default"] .atlas-service-card {
  background: rgba(248, 251, 255, 0.86);
}

.atlas-kicker {
  color: var(--atlas-sky);
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
"""


def theme_artifacts(root: Path) -> dict[Path, str]:
    return {root / "docs" / "assets" / "stylesheets" / "atlas.css": atlas_css()}


def binary_copy_artifacts(root: Path) -> list[tuple[Path, Path]]:
    return [
        (root / "assets" / "atlas-source.png", root / "docs" / "assets" / "images" / "atlas-source.png"),
    ]
```

- [ ] **Step 4: Wire thin generator wrapper for config and theme only**

Temporarily update `scripts/generate-docs-site.py` so the existing page functions still run, but `mkdocs.yml` and CSS come from the new package. Import:

```python
from bootstrapper.docs.sitegen.mkdocs_config import build_mkdocs_config
from bootstrapper.docs.sitegen.model import load_docs_model
from bootstrapper.docs.sitegen.theme import binary_copy_artifacts, theme_artifacts
```

In `build_artifacts()`, replace the `mkdocs.yml` and CSS artifact entries with:

```python
    model = load_docs_model(ROOT)
    artifacts[ROOT / "mkdocs.yml"] = yaml.safe_dump(build_mkdocs_config(model), sort_keys=False)
    artifacts.update(theme_artifacts(ROOT))
```

In `main()`, replace the explicit hero copy call with:

```python
    for source, target in binary_copy_artifacts(ROOT):
        drift += _copy_or_check_binary(source, target, args.check)
```

- [ ] **Step 5: Regenerate docs and run targeted theme test**

Run:

```bash
uv run --project bootstrapper python scripts/generate-docs-site.py
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_site_platform.py::test_atlas_theme_uses_material_dark_default_with_light_toggle -q
```

Expected: PASS.

- [ ] **Step 6: Commit config/theme work after Task 4 satisfies the screenshot assertion**

Run after Task 4:

```bash
git add bootstrapper/docs/sitegen scripts/generate-docs-site.py mkdocs.yml docs/assets/stylesheets/atlas.css docs/assets/images/atlas-source.png
git commit -m "feat: generate Material docs theme"
```

---

### Task 4: Generate Full Site Information Architecture

**Files:**
- Create: `bootstrapper/docs/sitegen/pages.py`
- Modify: `scripts/generate-docs-site.py`
- Generated: `docs/index.md`, `docs/site/overview.md`, `docs/site/quick-start.md`, `docs/site/core-concepts.md`, `docs/site/tracks.md`, `docs/site/architecture/index.md`, `docs/site/configuration.md`, `docs/site/operations.md`, `docs/site/development.md`, `docs/site/reference/index.md`
- Test: `bootstrapper/tests/test_docs_site_platform.py`

**Interfaces:**
- Consumes: `DocsModel`, `rendering.table`, `rendering.csv_or_dash`.
- Produces: `static_pages(model: DocsModel) -> dict[Path, str]`.

- [ ] **Step 1: Add failing IA test**

Add to `bootstrapper/tests/test_docs_site_platform.py`:

```python
def test_generated_site_has_full_information_architecture() -> None:
    required_pages = [
        ROOT / "docs" / "index.md",
        DOCS_SITE / "quick-start.md",
        DOCS_SITE / "core-concepts.md",
        DOCS_SITE / "tracks.md",
        DOCS_SITE / "architecture" / "index.md",
        DOCS_SITE / "configuration.md",
        DOCS_SITE / "operations.md",
        DOCS_SITE / "development.md",
        DOCS_SITE / "reference" / "index.md",
    ]
    for path in required_pages:
        text = path.read_text(encoding="utf-8")
        assert text.startswith("# ")
        assert "## 1. " in text

    home = (ROOT / "docs" / "index.md").read_text(encoding="utf-8")
    assert '<div class="atlas-hero">' in home
    assert "assets/images/atlas-source.png" in home
    assert "screenshots/wizard-running.png" in home
    assert "Atlas is a self-hosted" in home
```

- [ ] **Step 2: Run IA test and verify it fails**

Run:

```bash
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_site_platform.py::test_generated_site_has_full_information_architecture -q
```

Expected: FAIL because `core-concepts.md` is missing and the home page lacks the new structure.

- [ ] **Step 3: Implement static page renderer**

Create `bootstrapper/docs/sitegen/pages.py`:

```python
from __future__ import annotations

from pathlib import Path

from .model import DocsModel
from .rendering import csv_or_dash, table


def static_pages(model: DocsModel) -> dict[Path, str]:
    docs = model.root / "docs"
    service_count = len(model.services)
    source_count = sum(1 for service in model.services if service.source_var)
    track_rows = [
        [track.key, track.description, csv_or_dash(track.services)]
        for track in model.tracks
    ]
    category_rows = []
    categories = sorted({service.category for service in model.services})
    for category in categories:
        services = [service.name for service in model.services if service.category == category]
        category_rows.append([category, str(len(services)), csv_or_dash(services)])

    return {
        docs / "index.md": f"""# Atlas Documentation

<div class="atlas-hero">
  <img src="assets/images/atlas-source.png" alt="Atlas platform source map">
</div>

Atlas is a self-hosted, source-configurable engineering platform for generative AI, RAG, creative AI, ML engineering, and data engineering workloads. The stack is composed through Docker Compose, a Python bootstrapper, service manifests, SOURCE values, tracks, and a Kong-fronted access model.

<div class="atlas-screenshot">
  <img src="screenshots/wizard-running.png" alt="Atlas setup wizard running the launch phase">
</div>

## 1. Start Here

- [Quick Start](site/quick-start.md)
- [Core Concepts](site/core-concepts.md)
- [Service Catalog](site/services/index.md)
- [Architecture](site/architecture/index.md)
- [Reference](site/reference/index.md)

## 2. Documentation Scope

This site indexes {service_count} service families, {len(model.tracks)} tracks, and {source_count} SOURCE-configurable surfaces from repository-owned source files.

## 3. Publication Surfaces

- Public site: [{model.public_url}]({model.public_url})
- GitHub Wiki export source: [docs/wiki/Home.md](https://github.com/thekaveh/atlas/blob/main/docs/wiki/Home.md)
- Source repository: [thekaveh/atlas](https://github.com/thekaveh/atlas)
""",
        docs / "site" / "quick-start.md": """# Quick Start

## 1. Launch Atlas

Run `./start.sh` from the repository root. The setup wizard walks through track selection, service SOURCE choices, base-port selection, host aliases, and the launch summary.

## 2. Common Paths

```bash
./start.sh
./start.sh --track gen-ai-rag
./start.sh --track data-eng
./start.sh --base-port 64000
./start.sh --setup-hosts
```

## 3. First Services To Visit

Use the Atlas root dashboard at `http://localhost:63000` after launch. Direct service URLs and Kong aliases are listed in the generated service catalog and ports reference.
""",
        docs / "site" / "core-concepts.md": """# Core Concepts

## 1. SOURCE Values

Each configurable service has a SOURCE variable that controls whether Atlas runs it in Docker, connects to a localhost instance, disables it, or uses a service-specific mode.

## 2. Tracks

Tracks select the subset of services needed for a workflow and force-disable out-of-track services unless the user explicitly overrides them.

## 3. Manifests

Each manifest owns service metadata, env vars, source options, dependencies, runtime slices, and data-flow calls.

## 4. Gateway Access

Kong provides the main local entrypoint and generated aliases. Direct ports remain available for services that expose their own UI or API.
""",
        docs / "site" / "overview.md": """# Overview

## 1. Platform Model

Atlas combines local-first infrastructure, AI services, data services, workflow automation, notebooks, and observability behind a generated runtime configuration layer.

## 2. Service Families

Service families live under `services/<name>/` and own their manifest, compose fragment, README, initialization scaffolding, and generated diagrams.

## 3. Generated Documentation

The `.io` site and GitHub Wiki are generated from service manifests, tracks, topology, README files, and diagram assets.
""",
        docs / "site" / "tracks.md": "# Tracks\n\n## 1. Track Matrix\n\n" + table(["Track", "Description", "Services"], track_rows),
        docs / "site" / "architecture" / "index.md": """# Architecture

## 1. System Shape

Atlas is organized around a bootstrapper, service manifests, generated Kong routes, Docker Compose fragments, and SOURCE-aware adaptive services.

## 2. Diagram Catalog

Start with the platform overview, then use the focused architecture pages for bootstrapper lifecycle, SOURCE behavior, tracks, routing, RAG, LLMs, lakehouse, observability, security, and service admission.

## 3. Per-Service Diagrams

Generated service diagrams live beside each service README under `services/<name>/architecture.svg` and `services/<name>/architecture.html`.
""",
        docs / "site" / "configuration.md": """# Configuration

## 1. Environment Files

`.env.example` is generated from service manifests and topology defaults. `.env` stores the local runtime choices.

## 2. SOURCE Overrides

Every SOURCE value can be selected through the wizard or passed as a CLI flag such as `--weaviate-source localhost`.

## 3. Ports

Ports are derived from `BASE_PORT` and service-specific slots. Change the base with `./start.sh --base-port 64000`.
""",
        docs / "site" / "operations.md": """# Operations

## 1. Runtime Commands

```bash
./start.sh
./stop.sh
./stop.sh --cold
./stop.sh --clean-hosts
```

## 2. Health And Logs

The launch phase streams Docker Compose output through the Textual UI. The same command path works without the TUI in non-interactive environments.
""",
        docs / "site" / "development.md": """# Development

## 1. Service Admission

Adding a service requires a manifest, compose fragment when applicable, topology row, docs regeneration, route checks, and CI validation.

## 2. Required Docs Checks

```bash
uv run --project bootstrapper python scripts/check-docs-site.py
uv run --project bootstrapper python scripts/export-docs-wiki.py --check
```
""",
        docs / "site" / "reference" / "index.md": """# Reference

## 1. Generated References

- [SOURCE values](source-values.md)
- [Environment variables](env-vars.md)
- [Ports and routes](ports-routes.md)
- [Tracks](tracks.md)
- [Service dependencies](service-dependencies.md)
- [Manifest fields](manifest-fields.md)

## 2. Category Summary

""" + table(["Category", "Count", "Services"], category_rows),
    }
```

- [ ] **Step 4: Wire static pages into generator**

In `scripts/generate-docs-site.py`, import:

```python
from bootstrapper.docs.sitegen.pages import static_pages
```

Replace the existing `_static_pages(services)` artifact update with:

```python
    artifacts.update(static_pages(model))
```

- [ ] **Step 5: Regenerate and test IA plus theme**

Run:

```bash
uv run --project bootstrapper python scripts/generate-docs-site.py
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_site_platform.py::test_generated_site_has_full_information_architecture bootstrapper/tests/test_docs_site_platform.py::test_atlas_theme_uses_material_dark_default_with_light_toggle -q
```

Expected: PASS.

- [ ] **Step 6: Commit site IA**

Run:

```bash
git add bootstrapper/docs/sitegen/pages.py scripts/generate-docs-site.py docs/index.md docs/site mkdocs.yml docs/assets/stylesheets/atlas.css
git commit -m "feat: generate full docs site information architecture"
```

---

### Task 5: Generate Rich Service Catalog And Service Profiles

**Files:**
- Create: `bootstrapper/docs/sitegen/services.py`
- Modify: `scripts/generate-docs-site.py`
- Generated: `docs/site/services/index.md`, `docs/site/services/*.md`
- Test: `bootstrapper/tests/test_docs_site_platform.py`

**Interfaces:**
- Consumes: `DocsModel.services`.
- Produces: `service_pages(model: DocsModel) -> dict[Path, str]`.

- [ ] **Step 1: Add failing service profile tests**

Add to `bootstrapper/tests/test_docs_site_platform.py`:

```python
def test_service_profiles_are_substantial_and_generated_from_model() -> None:
    for name in ["supabase", "open-webui", "litellm", "airflow", "spark"]:
        page = DOCS_SITE / "services" / f"{name}.md"
        text = page.read_text(encoding="utf-8")
        for heading in [
            "## 1. Overview",
            "## 2. Role In Atlas",
            "## 3. Tracks And Category",
            "## 4. Access",
            "## 5. Configuration",
            "## 6. Dependencies And Topology",
            "## 7. Source Values",
            "## 8. Runtime Integration",
            "## 9. Architecture",
            "## 10. Operations",
            "## 11. Source Documentation",
        ]:
            assert heading in text
        assert "Generated service-site entry" not in text
        assert "Source README remains the source of truth" not in text
        assert f"services/{name}/README.md" in text


def test_service_catalog_groups_services_by_category_with_tracks_and_sources() -> None:
    index = (DOCS_SITE / "services" / "index.md").read_text(encoding="utf-8")
    assert "## 1. Service Catalog" in index
    assert "### 1." in index
    assert "| Service | Title | Tracks | SOURCE | Default | Values | Dependencies |" in index
    assert "supabase" in index
    assert "open-webui" in index
    assert "cloud-providers" in index
    assert "stt-provider" in index
```

- [ ] **Step 2: Run service tests and verify they fail**

Run:

```bash
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_site_platform.py::test_service_profiles_are_substantial_and_generated_from_model bootstrapper/tests/test_docs_site_platform.py::test_service_catalog_groups_services_by_category_with_tracks_and_sources -q
```

Expected: FAIL because service pages are shallow.

- [ ] **Step 3: Implement service page renderer**

Create `bootstrapper/docs/sitegen/services.py`:

```python
from __future__ import annotations

from pathlib import Path

from .model import DocsModel, ServicePage
from .rendering import csv_or_dash, table


def _service_link(service: ServicePage) -> str:
    return f"[{service.name}]({service.name}.md)"


def _readme_link(service: ServicePage) -> str:
    return f"[services/{service.name}/README.md](https://github.com/thekaveh/atlas/blob/main/services/{service.name}/README.md)"


def _diagram_line(service: ServicePage) -> str:
    if service.diagram_svg and service.diagram_html:
        return f"- Diagram SVG: [`services/{service.name}/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/{service.name}/architecture.svg)\n- Diagram HTML: [`services/{service.name}/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/{service.name}/architecture.html)"
    return "- Diagram: not generated for this service family."


def _profile(model: DocsModel, service: ServicePage) -> str:
    source_values = csv_or_dash(service.source_values)
    required = csv_or_dash(service.required_dependencies)
    optional = csv_or_dash(service.optional_dependencies)
    runtime_calls = csv_or_dash(service.runtime_calls)
    aliases = csv_or_dash(service.kong_aliases)
    ports = csv_or_dash(service.port_vars)
    tracks = csv_or_dash(service.track_keys)

    return f"""# {service.title}

## 1. Overview

`{service.name}` is an Atlas service family in the `{service.category}` category. Its implementation and service-owned documentation live under `services/{service.name}/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `{service.category}`
- Kind: `{service.kind}`
- Tracks: `{tracks}`

## 4. Access

- Kong aliases: `{aliases}`
- Port variables: `{ports}`

## 5. Configuration

- SOURCE variable: `{service.source_var or 'none'}`
- Default SOURCE: `{service.source_default or 'none'}`
- Available SOURCE values: `{source_values}`

## 6. Dependencies And Topology

- Required dependencies: `{required}`
- Optional dependencies: `{optional}`
- Runtime calls: `{runtime_calls}`

## 7. Source Values

{table(["SOURCE Variable", "Default", "Values"], [[service.source_var or "none", service.source_default or "none", source_values]])}

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `{runtime_calls}`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

{_diagram_line(service)}

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: {_readme_link(service)}
- Public docs home: [{model.public_url}]({model.public_url})
"""


def service_pages(model: DocsModel) -> dict[Path, str]:
    pages: dict[Path, str] = {}
    docs = model.root / "docs" / "site" / "services"
    categories = sorted({service.category for service in model.services})
    sections: list[str] = ["# Service Catalog", "", "## 1. Service Catalog", ""]

    for index, category in enumerate(categories, start=1):
        rows = []
        for service in [svc for svc in model.services if svc.category == category]:
            rows.append([
                _service_link(service),
                service.title,
                csv_or_dash(service.track_keys),
                service.source_var or "none",
                service.source_default or "none",
                csv_or_dash(service.source_values),
                csv_or_dash(service.required_dependencies),
            ])
        sections.extend([
            f"### 1.{index}. {category}",
            "",
            table(["Service", "Title", "Tracks", "SOURCE", "Default", "Values", "Dependencies"], rows),
            "",
        ])

    pages[docs / "index.md"] = "\n".join(sections).rstrip() + "\n"
    for service in model.services:
        pages[docs / f"{service.name}.md"] = _profile(model, service)
    return pages
```

- [ ] **Step 4: Wire service renderer**

In `scripts/generate-docs-site.py`, import:

```python
from bootstrapper.docs.sitegen.services import service_pages
```

Replace the existing `_service_pages(services)` artifact update with:

```python
    artifacts.update(service_pages(model))
```

- [ ] **Step 5: Regenerate and run service tests**

Run:

```bash
uv run --project bootstrapper python scripts/generate-docs-site.py
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_site_platform.py::test_service_profiles_are_substantial_and_generated_from_model bootstrapper/tests/test_docs_site_platform.py::test_service_catalog_groups_services_by_category_with_tracks_and_sources -q
```

Expected: PASS.

- [ ] **Step 6: Commit service docs generation**

Run:

```bash
git add bootstrapper/docs/sitegen/services.py scripts/generate-docs-site.py docs/site/services
git commit -m "feat: generate rich docs service profiles"
```

---

### Task 6: Generate References And Expanded Wiki From The Shared Model

**Files:**
- Create: `bootstrapper/docs/sitegen/wiki.py`
- Modify: `bootstrapper/docs/sitegen/pages.py`
- Modify: `scripts/generate-docs-site.py`
- Modify: `scripts/export-docs-wiki.py`
- Generated: `docs/site/reference/*.md`, `docs/wiki/*.md`
- Test: `bootstrapper/tests/test_docs_site_platform.py`

**Interfaces:**
- Consumes: `DocsModel`.
- Produces: `reference_pages(model: DocsModel) -> dict[Path, str]`, `wiki_pages(model: DocsModel) -> dict[Path, str]`.

- [ ] **Step 1: Add focused reference/wiki assertions**

Update `test_generated_reference_pages_cover_core_sources` to assert:

```python
    deps = (DOCS_SITE / "reference" / "service-dependencies.md").read_text(encoding="utf-8")
    assert "| Service | Required | Optional | Runtime Calls |" in deps
    assert "litellm" in deps
    assert "open-webui" in deps
```

Keep the wiki page-set test from Task 1.

- [ ] **Step 2: Implement wiki renderer**

Create `bootstrapper/docs/sitegen/wiki.py`:

```python
from __future__ import annotations

from pathlib import Path

from .model import DocsModel
from .rendering import csv_or_dash, table


def wiki_pages(model: DocsModel) -> dict[Path, str]:
    wiki = model.root / "docs" / "wiki"
    service_rows = [
        [
            service.name,
            service.category,
            csv_or_dash(service.track_keys),
            service.source_var or "none",
            csv_or_dash(service.source_values),
            csv_or_dash(service.required_dependencies),
        ]
        for service in model.services
    ]
    track_rows = [
        [track.key, track.description, csv_or_dash(track.services)]
        for track in model.tracks
    ]
    return {
        wiki / "Home.md": f"""# Atlas Documentation

Generated from the MkDocs source model. Do not hand-edit the live wiki; run `uv run --project bootstrapper python scripts/export-docs-wiki.py --check` to verify drift.

## 1. Start Here

- [Overview](Overview)
- [Quick Start](Quick-Start)
- [Core Concepts](Core-Concepts)
- [Tracks](Tracks)
- [Services](Services)
- [Architecture](Architecture)
- [Configuration](Configuration)
- [Operations](Operations)
- [Development](Development)
- [Reference](Reference)

## 2. Public Site

The full documentation site is published at [{model.public_url}]({model.public_url}).
""",
        wiki / "_Sidebar.md": "# Atlas Documentation\n\n- [1. Home](Home)\n- [2. Overview](Overview)\n- [3. Quick Start](Quick-Start)\n- [4. Core Concepts](Core-Concepts)\n- [5. Tracks](Tracks)\n- [6. Services](Services)\n- [7. Architecture](Architecture)\n- [8. Configuration](Configuration)\n- [9. Operations](Operations)\n- [10. Development](Development)\n- [11. Reference](Reference)\n",
        wiki / "Overview.md": "# Overview\n\n## 1. Platform Model\n\nAtlas is a self-hosted, source-configurable platform for AI, data, automation, notebooks, and observability.\n\n## 2. Source Model\n\nThe site and wiki are generated from service manifests, tracks, topology, README files, and diagram assets.\n",
        wiki / "Quick-Start.md": "# Quick Start\n\n## 1. Launch\n\nRun `./start.sh` and choose a track in the setup wizard.\n\n## 2. Hosts\n\nRun `./start.sh --setup-hosts` when you want Kong `*.localhost` aliases.\n",
        wiki / "Core-Concepts.md": "# Core Concepts\n\n## 1. SOURCE Values\n\nSOURCE variables choose container, localhost, disabled, or service-specific modes.\n\n## 2. Tracks\n\nTracks select workflow-oriented groups of services.\n\n## 3. Manifests\n\nManifests define service metadata, environment variables, dependencies, and runtime wiring.\n",
        wiki / "Tracks.md": "# Tracks\n\n## 1. Track Matrix\n\n" + table(["Track", "Description", "Services"], track_rows),
        wiki / "Services.md": "# Services\n\n## 1. Service Catalog\n\n" + table(["Service", "Category", "Tracks", "SOURCE", "Values", "Dependencies"], service_rows),
        wiki / "Architecture.md": "# Architecture\n\n## 1. Diagram Catalog\n\nThe public site contains the full architecture catalog and per-service diagram links.\n\n## 2. Stack Shape\n\nAtlas routes browser and API traffic through Kong, composes services through Docker Compose fragments, and adapts application services based on enabled upstreams.\n",
        wiki / "Configuration.md": "# Configuration\n\n## 1. Environment\n\n`.env.example` is generated from manifests and topology defaults. `.env` stores local choices.\n\n## 2. SOURCE Flags\n\nWizard selections can also be passed as `./start.sh --<service>-source <value>` flags.\n",
        wiki / "Operations.md": "# Operations\n\n## 1. Commands\n\nUse `./start.sh`, `./stop.sh`, `./stop.sh --cold`, and `./stop.sh --clean-hosts` from the repository root.\n",
        wiki / "Development.md": "# Development\n\n## 1. Service Admission\n\nAdd or change services through manifests, compose fragments, topology, docs regeneration, and CI checks.\n",
        wiki / "Reference.md": "# Reference\n\n## 1. Generated References\n\nThe public site publishes SOURCE values, environment variables, ports, routes, tracks, dependencies, and manifest-field references.\n",
    }
```

- [ ] **Step 3: Implement generated reference pages**

In `bootstrapper/docs/sitegen/pages.py`, add:

```python
def reference_pages(model: DocsModel) -> dict[Path, str]:
    ref = model.root / "docs" / "site" / "reference"
    source_rows = [
        [service.source_var, service.name, service.source_default, csv_or_dash(service.source_values)]
        for service in model.services
        if service.source_var
    ]
    deps_rows = [
        [
            service.name,
            csv_or_dash(service.required_dependencies),
            csv_or_dash(service.optional_dependencies),
            csv_or_dash(service.runtime_calls),
        ]
        for service in model.services
    ]
    track_rows = [
        [track.key, track.description, csv_or_dash(track.services)]
        for track in model.tracks
    ]
    env_rows = []
    for service in model.services:
        for port_var in service.port_vars:
            env_rows.append([port_var, service.name, "port", "Port-related variable from service manifest"])
    return {
        ref / "source-values.md": "# SOURCE Values\n\n## 1. Generated Source Matrix\n\n" + table(["SOURCE", "Service", "Default", "Values"], source_rows),
        ref / "env-vars.md": "# Environment Variables\n\n## 1. Generated Environment Matrix\n\n" + table(["Variable", "Service", "Kind", "Description"], env_rows),
        ref / "ports-routes.md": "# Ports And Routes\n\n## 1. Canonical Route Reference\n\nSee [ports-and-routes.md](../../deployment/ports-and-routes.md).\n",
        ref / "tracks.md": "# Track Reference\n\n## 1. Generated Track Matrix\n\n" + table(["Track", "Description", "Services"], track_rows),
        ref / "service-dependencies.md": "# Service Dependencies\n\n## 1. Generated Dependency Matrix\n\n" + table(["Service", "Required", "Optional", "Runtime Calls"], deps_rows),
        ref / "manifest-fields.md": "# Manifest Fields\n\n## 1. Manifest Schema Quick Reference\n\n" + table(["Field", "Purpose"], [
            ["containers", "Container names in the service family"],
            ["env", "Environment variables owned by the manifest"],
            ["sources", "SOURCE var, default, and allowed values"],
            ["category", "Topology category and wizard grouping"],
            ["depends_on", "Required and optional logical dependencies"],
            ["runtime_sc", "Per-source runtime scale/env/deploy slices"],
            ["data_flow.calls", "Runtime call graph used by docs and diagrams"],
        ]),
    }
```

- [ ] **Step 4: Wire wiki and references into generator**

In `scripts/generate-docs-site.py`, import:

```python
from bootstrapper.docs.sitegen.pages import reference_pages, static_pages
from bootstrapper.docs.sitegen.wiki import wiki_pages
```

Replace existing `_reference_pages(services)` and `_wiki_pages(services)` updates with:

```python
    artifacts.update(reference_pages(model))
    artifacts.update(wiki_pages(model))
```

- [ ] **Step 5: Regenerate and run wiki/reference tests**

Run:

```bash
uv run --project bootstrapper python scripts/generate-docs-site.py
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_site_platform.py::test_wiki_export_contains_full_companion_page_set bootstrapper/tests/test_docs_site_platform.py::test_generated_reference_pages_cover_core_sources -q
uv run --project bootstrapper python scripts/export-docs-wiki.py --check
```

Expected: PASS.

- [ ] **Step 6: Commit wiki/reference generation**

Run:

```bash
git add bootstrapper/docs/sitegen/pages.py bootstrapper/docs/sitegen/wiki.py scripts/generate-docs-site.py scripts/export-docs-wiki.py docs/site/reference docs/wiki
git commit -m "feat: expand generated docs wiki and references"
```

---

### Task 7: Architecture Catalog And Diagram Sync Checks

**Files:**
- Modify: `bootstrapper/docs/sitegen/pages.py`
- Modify: `bootstrapper/tests/test_docs_site_platform.py`
- Generated: `docs/architecture/*.md`, `docs/architecture/*.html`, `docs/architecture/README.md`

**Interfaces:**
- Consumes: existing high-level `DIAGRAMS` definitions or a new `ARCHITECTURE_PERSPECTIVES` constant in `pages.py`.
- Produces: `architecture_pages(model: DocsModel) -> dict[Path, str]`.

- [ ] **Step 1: Add diagram coverage assertions**

Update `test_required_diagram_catalog_is_linked_and_non_empty` to also assert:

```python
        assert "Generated for Atlas documentation using the architecture-diagram design system." in html_text
        assert "## 1. Diagram" in page_text
        assert "## 2. Source Files" in page_text
        assert "## 3. Update Rule" in page_text
```

Add:

```python
def test_service_profiles_link_available_architecture_assets() -> None:
    for name in ["supabase", "open-webui", "litellm"]:
        text = (DOCS_SITE / "services" / f"{name}.md").read_text(encoding="utf-8")
        assert f"services/{name}/architecture.svg" in text
        assert f"services/{name}/architecture.html" in text
```

- [ ] **Step 2: Run diagram tests and verify current state**

Run:

```bash
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_site_platform.py::test_required_diagram_catalog_is_linked_and_non_empty bootstrapper/tests/test_docs_site_platform.py::test_service_profiles_link_available_architecture_assets -q
```

Expected: PASS if Task 5 profile links and existing high-level diagrams satisfy the assertions. If the first test fails on generated HTML language, update the architecture page renderer in Step 3.

- [ ] **Step 3: Move architecture page generation into `pages.py`**

Add an `ARCHITECTURE_PERSPECTIVES` constant and `architecture_pages(model)` to `bootstrapper/docs/sitegen/pages.py`. Reuse the current `DIAGRAMS` slugs and descriptions from `scripts/generate-docs-site.py`. Keep the HTML output self-contained with inline SVG, dark slate background `#020617`, JetBrains Mono, semantic colors, arrows behind boxes, and the footer sentence required by the tests.

- [ ] **Step 4: Wire architecture renderer**

In `scripts/generate-docs-site.py`, import:

```python
from bootstrapper.docs.sitegen.pages import architecture_pages, reference_pages, static_pages
```

Replace `_diagram_pages()` artifact updates with:

```python
    artifacts.update(architecture_pages(model))
```

- [ ] **Step 5: Regenerate and run diagram checks plus service docs drift**

Run:

```bash
uv run --project bootstrapper python scripts/generate-docs-site.py
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_site_platform.py::test_required_diagram_catalog_is_linked_and_non_empty bootstrapper/tests/test_docs_site_platform.py::test_service_profiles_link_available_architecture_assets -q
uv run --project bootstrapper python -m bootstrapper.docs.regen --all --check
```

Expected: PASS.

- [ ] **Step 6: Commit architecture sync**

Run:

```bash
git add bootstrapper/docs/sitegen/pages.py scripts/generate-docs-site.py docs/architecture docs/site/services
git commit -m "feat: sync architecture docs catalog"
```

---

### Task 8: CI, Contributor Guidance, About URL Command, And Full Verification

**Files:**
- Modify: `.github/workflows/docs-pages.yml`
- Modify: `.github/workflows/services-lint.yml`
- Modify: `docs/README.md`
- Modify: `docs/CONTRIBUTING-services.md`
- Modify: `AGENTS.md`
- Modify: `bootstrapper/tests/test_docs_site_platform.py`
- Optional create: `scripts/update-repo-about.py`

**Interfaces:**
- Consumes: generated docs commands and GitHub CLI auth.
- Produces: verified local command list and optional repo About update helper.

- [ ] **Step 1: Add About helper test**

Add to `bootstrapper/tests/test_docs_site_platform.py`:

```python
def test_docs_guidance_mentions_repo_about_homepage_update() -> None:
    docs_readme = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "gh repo edit thekaveh/atlas --homepage https://thekaveh.github.io/atlas/" in docs_readme
```

- [ ] **Step 2: Update docs maintainer guidance**

In `docs/README.md`, `docs/CONTRIBUTING-services.md`, and `AGENTS.md`, ensure the docs command block contains:

```bash
uv run --project bootstrapper python scripts/generate-docs-site.py --check
uv run --project bootstrapper python scripts/check-docs-site.py
uv run --project bootstrapper python scripts/export-docs-wiki.py --check
uv run --project bootstrapper python scripts/check_doc_links.py
uv run --project bootstrapper python -m bootstrapper.docs.regen --all --check
```

Add this repo About command to `docs/README.md` in a maintainer publication section:

```bash
gh repo edit thekaveh/atlas --homepage https://thekaveh.github.io/atlas/
```

- [ ] **Step 3: Confirm workflows still run docs checks**

Ensure `.github/workflows/services-lint.yml` contains:

```yaml
run: uv run --project bootstrapper python scripts/check-docs-site.py
```

and:

```yaml
run: uv run --project bootstrapper python scripts/export-docs-wiki.py --check
```

Ensure `.github/workflows/docs-pages.yml` contains:

```yaml
run: uv run --project bootstrapper python scripts/check-docs-site.py
```

and:

```yaml
run: uv run --project bootstrapper python scripts/export-docs-wiki.py --push
```

- [ ] **Step 4: Run full docs generation and validation**

Run:

```bash
uv run --project bootstrapper python scripts/generate-docs-site.py
uv run --project bootstrapper python scripts/generate-docs-site.py --check
uv run --project bootstrapper python scripts/check-docs-site.py
uv run --project bootstrapper python scripts/export-docs-wiki.py --check
uv run --project bootstrapper python scripts/check_doc_links.py
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_site_platform.py bootstrapper/tests/test_docs_sitegen_model.py -q
```

Expected: all commands PASS.

- [ ] **Step 5: Run broader docs drift gates**

Run:

```bash
uv run --project bootstrapper python -m bootstrapper.docs.regen --all --check
uv run --project bootstrapper python scripts/check-docs-drift.py
```

Expected: both commands PASS.

- [ ] **Step 6: Build final site preview**

Run:

```bash
uv run --project bootstrapper mkdocs build --strict
```

Expected: PASS and generated `site/` contains Material HTML pages.

- [ ] **Step 7: Commit CI and guidance**

Run:

```bash
git add .github/workflows docs/README.md docs/CONTRIBUTING-services.md AGENTS.md bootstrapper/tests/test_docs_site_platform.py
git commit -m "docs: document generated docs publication workflow"
```

---

## Final Integration Checklist

- [ ] Run `git status --short` and confirm only intentional files are changed.
- [ ] Run `uv run --project bootstrapper pytest bootstrapper/tests/test_docs_site_platform.py bootstrapper/tests/test_docs_sitegen_model.py -q`.
- [ ] Run `uv run --project bootstrapper python scripts/check-docs-site.py`.
- [ ] Run `uv run --project bootstrapper python scripts/export-docs-wiki.py --check`.
- [ ] Run `uv run --project bootstrapper python scripts/check_doc_links.py`.
- [ ] Run `uv run --project bootstrapper python -m bootstrapper.docs.regen --all --check`.
- [ ] Run `uv run --project bootstrapper python scripts/check-docs-drift.py`.
- [ ] Run `git log --oneline --max-count=8` and confirm task commits are coherent.
- [ ] Push the branch.
- [ ] Create a PR to `main`.
- [ ] Wait for CI to go green.
- [ ] Merge through GitHub PR flow.
- [ ] After merge, run `gh repo edit thekaveh/atlas --homepage https://thekaveh.github.io/atlas/` if permissions allow.
- [ ] Clean up local and remote feature branches after merge.

## Self-Review Notes

- Spec coverage: The plan covers Material theme, IA, service profiles, wiki expansion, architecture catalog, sync checks, Pages workflow, repo About command, and contributor guidance.
- Placeholder scan: The plan does not use unresolved placeholder markers or deferred implementation language.
- Type consistency: The shared interfaces are `DocsModel`, `ServicePage`, `TrackPage`, `load_docs_model`, `static_pages`, `reference_pages`, `service_pages`, `wiki_pages`, `build_mkdocs_config`, `theme_artifacts`, and `binary_copy_artifacts`.
