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


def copy_artifacts(root: Path) -> list[tuple[Path, Path]]:
    return [
        (root / "assets" / "atlas-source.png", root / "docs" / "assets" / "images" / "atlas-source.png"),
    ]


def binary_copy_artifacts(root: Path) -> list[tuple[Path, Path]]:
    return copy_artifacts(root)
