# 9.4. Architecture Diagrams

The top-level architecture diagram presents the platform's major tiers and
runtime call direction. Its source artifacts are:

- `architecture.svg` — SVG embedded in the project root `README.md` and viewable standalone.
- `architecture.html` — standalone HTML page containing the same inline SVG plus summary cards and a footer.

## 1. Updating the top-level diagram

The HTML/SVG masters are hand-authored rather than generated from manifests.
Edit both through the repository's `architecture-diagram` skill and keep their
SVG elements byte-equivalent. Render the committed PNG before building and
checking the generated surfaces:

```bash
uv run --project bootstrapper python -m scripts.docs.render_diagrams
make docs-build
make docs-check
```

The documentation gate rejects master drift and stale derived assets.

## 2. Per-service diagrams (auto-generated)

Each `services/<name>/architecture.{svg,html}` is **auto-generated** from
that service's manifest (`service.yml::data_flow.calls`) by
`bootstrapper/docs/regen.py`:

```bash
PYTHONPATH=bootstrapper uv run --project bootstrapper python -m bootstrapper.docs.regen <service>   # one service
PYTHONPATH=bootstrapper uv run --project bootstrapper python -m bootstrapper.docs.regen --all       # all services
```

The drift gate (`bootstrapper/tests/test_docs_drift.py`) enforces that the
committed per-service SVG / HTML / README-deps-section match what the
generator would emit.

Per-service diagrams are rendered by `bootstrapper/docs/diagram_renderer.py`.
Edit that file to change the rendered shape; manifest field changes alone
regenerate the content without a renderer edit.
