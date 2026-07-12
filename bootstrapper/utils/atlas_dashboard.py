"""Generated Atlas root dashboard.

The dashboard is intentionally static at generation time: Kong serves the
HTML from its DB-less config via a pre-function plugin, while the browser
performs tiny reachability probes to turn active rows healthy/degraded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Literal

from core.config_parser import DEFAULT_BASE_PORT
from services.topology import (
    CATEGORY_COLORS,
    CATEGORY_LABELS,
    CATEGORY_ORDER,
    get_topology,
)


DashboardStatus = Literal["healthy", "degraded", "disabled"]


@dataclass(frozen=True)
class DashboardService:
    name: str
    category: str
    source: str
    status: DashboardStatus
    kong_url: str | None
    direct_url: str | None
    auth_note: str
    disabled_reason: str | None = None
    health_url: str | None = None
    # Raw topology category key (infra/data/llm/...) — drives grouping order
    # and the per-category accent color; ``category`` above stays the human
    # label for display/back-compat.
    category_key: str = ""
    # Short manifest-declared description shown on the service card;
    # degrades gracefully when a row declares none.
    description: str = ""


@dataclass(frozen=True)
class DashboardModel:
    brand_name: str
    tagline: str
    track_label: str
    kong_port: str
    services: list[DashboardService]
    warnings: list[str] = field(default_factory=list)


def build_dashboard_model(
    config_parser,
    *,
    track_key: str | None = None,
    overridden_services: frozenset[str] | None = None,
    hosts_configured: bool = True,
) -> DashboardModel:
    """Build a dashboard snapshot from topology + resolved env state."""
    env = config_parser.parse_env_file()
    service_sources = config_parser.parse_service_sources()
    kong_port = (env.get("KONG_HTTP_PORT") or str(DEFAULT_BASE_PORT)).strip()
    brand_name = env.get("BRAND_NAME") or "Atlas"
    tagline = (
        env.get("BRAND_TAGLINE")
        or "A self-hosted, source-configurable engineering platform."
    )
    track_label = _track_label(track_key)
    overridden = overridden_services or frozenset()

    topology = get_topology()
    rows = []
    warnings = []
    if not hosts_configured:
        warnings.append(
            "*.localhost hosts entries are not configured; run ./start.sh --setup-hosts."
        )

    for row in topology.rows:
        source = (
            service_sources.get(row.source_var)
            or env.get(row.source_var)
            or "container"
        ).strip()
        disabled_reason = None
        if source == "disabled":
            disabled_reason = _disabled_reason(
                row.manifest,
                row.locked,
                track_key=track_key,
                overridden_services=overridden,
            )
            status: DashboardStatus = "disabled"
        else:
            status = "degraded"

        direct_url = _direct_url(row, source, env)
        kong_url = _kong_url(row.alias, kong_port) if source != "disabled" else None
        health_url = kong_url or direct_url
        if "localhost" in source and source != "disabled":
            warnings.append(
                f"{row.display_name} uses a host-installed source; start it on the configured localhost port before using the Kong alias."
            )

        rows.append(DashboardService(
            name=row.display_name,
            category=CATEGORY_LABELS.get(row.category, row.category.title()),
            source=source,
            status=status,
            kong_url=kong_url,
            direct_url=direct_url,
            auth_note=_auth_note(row.display_name, row.alias),
            disabled_reason=disabled_reason,
            health_url=health_url if status != "disabled" else None,
            category_key=row.category,
            description=row.description or "",
        ))

    warnings.extend(_dependency_warnings(topology.rows, service_sources, env))
    return DashboardModel(
        brand_name=brand_name,
        tagline=tagline,
        track_label=track_label,
        kong_port=kong_port,
        services=rows,
        warnings=_dedupe(warnings),
    )


def render_dashboard_html(model: DashboardModel) -> str:
    """Render a complete HTML document for Kong's root route (#534).

    Category-grouped service cards (canonical CATEGORY_ORDER, per-category
    CATEGORY_COLORS accents shared with the TUI + architecture diagrams) with
    two themes: sleek dark + light, a keyboard-operable toggle, a
    ``prefers-color-scheme`` default applied before first paint, and
    ``localStorage`` persistence. Fully self-contained (inline CSS/JS, system
    fonts, no external assets) because the HTML is embedded in Kong's DB-less
    Lua config. Every model-derived value passes through ``escape()``.
    """
    active_count = sum(1 for row in model.services if row.status != "disabled")
    disabled_count = len(model.services) - active_count
    warning_html = "".join(
        f"<li>{escape(w)}</li>" for w in model.warnings
    ) or "<li>No startup warnings detected in the generated snapshot.</li>"

    label_by_key = {k: CATEGORY_LABELS.get(k, k.title()) for k in CATEGORY_ORDER}
    sections: list[str] = []
    for key in CATEGORY_ORDER:
        group = [row for row in model.services if row.category_key == key]
        if not group:
            continue
        accent = CATEGORY_COLORS.get(key, "#64748b")
        cards = "\n".join(_render_card(row, accent) for row in group)
        label = escape(label_by_key[key])
        sections.append(
            f'<section class="cat" aria-labelledby="cat-{escape(key, quote=True)}">\n'
            f'  <h2 id="cat-{escape(key, quote=True)}" class="cat-head"'
            f' style="--accent:{escape(accent, quote=True)}">'
            f'<span class="cat-mark" aria-hidden="true"></span>{label}</h2>\n'
            f'  <div class="grid">\n{cards}\n  </div>\n'
            f"</section>"
        )
    # Any row whose category key falls outside CATEGORY_ORDER still renders
    # (defensive — the topology validator rejects unknown categories today).
    leftover = [r for r in model.services if r.category_key not in label_by_key]
    if leftover:
        cards = "\n".join(_render_card(row, "#64748b") for row in leftover)
        sections.append(
            '<section class="cat" aria-labelledby="cat-other">\n'
            '  <h2 id="cat-other" class="cat-head" style="--accent:#64748b">'
            '<span class="cat-mark" aria-hidden="true"></span>Other</h2>\n'
            f'  <div class="grid">\n{cards}\n  </div>\n'
            "</section>"
        )
    sections_html = "\n".join(sections)

    return (
        _PAGE_TEMPLATE
        .replace("__TITLE__", escape(model.brand_name))
        .replace("__TAGLINE__", escape(model.tagline))
        .replace("__TRACK__", escape(model.track_label))
        .replace("__KONG_PORT__", escape(model.kong_port))
        .replace("__ACTIVE__", str(active_count))
        .replace("__DISABLED__", str(disabled_count))
        .replace("__WARNINGS__", warning_html)
        .replace("__SECTIONS__", sections_html)
    )


def _render_card(row: DashboardService, accent: str) -> str:
    """Render one service card.

    A service with a Kong alias dashboard renders as a whole-card link (mouse
    + keyboard activation for free via <a>); internal-only / disabled /
    host-source services render as an inert card with a plain-language reason.
    ``escape()`` guards every model value so nothing can break out of the Lua
    long-string or the HTML.
    """
    accent_attr = f' style="--accent:{escape(accent, quote=True)}"'
    health_attr = (
        f' data-health-url="{escape(row.health_url, quote=True)}"'
        if row.health_url else ""
    )
    status_html = (
        f'<span class="status" data-status="{row.status}"{health_attr}>'
        f'<span class="dot" aria-hidden="true"></span>'
        f'<span class="status-text">{row.status}</span></span>'
    )
    desc = escape(row.description) if row.description else ""
    desc_html = f'<p class="card-desc">{desc}</p>' if desc else ""
    src_html = f'<code class="src">{escape(row.source)}</code>'
    auth_html = f'<span class="auth">{escape(row.auth_note)}</span>'

    if row.kong_url:
        open_label = escape(row.kong_url.removeprefix("http://"))
        href = escape(row.kong_url, quote=True)
        return (
            f'    <a class="card" href="{href}"{accent_attr}'
            f' aria-label="Open {escape(row.name, quote=True)} dashboard">\n'
            f'      <span class="card-top">{status_html}{src_html}</span>\n'
            f'      <h3 class="card-name">{escape(row.name)}</h3>\n'
            f"      {desc_html}\n"
            f'      <span class="card-foot">{auth_html}'
            f'<span class="open">{open_label} &#8599;</span></span>\n'
            f"    </a>"
        )

    # Inert card: disabled, or enabled but with no browsable Kong dashboard.
    if row.status == "disabled":
        reason = escape(row.disabled_reason or "disabled")
    elif row.direct_url:
        reason = "No Kong alias &mdash; direct URL only"
    else:
        reason = "Internal service &mdash; no browser dashboard"
    direct_html = (
        f'<a class="direct" href="{escape(row.direct_url, quote=True)}">'
        f"{escape(row.direct_url.removeprefix('http://'))}</a>"
        if row.direct_url else ""
    )
    return (
        f'    <div class="card inert"{accent_attr}>\n'
        f'      <span class="card-top">{status_html}{src_html}</span>\n'
        f'      <h3 class="card-name">{escape(row.name)}</h3>\n'
        f"      {desc_html}\n"
        f'      <span class="card-foot">{auth_html}'
        f'<span class="reason">{reason}</span>{direct_html}</span>\n'
        f"    </div>"
    )


# Self-contained page shell. ``__TOKEN__`` placeholders are substituted in
# render_dashboard_html; no f-string so the CSS/JS braces stay literal. The
# document is embedded in a Lua long-bracket string by
# kong_config_generator._lua_long_string, which picks a safe ]=]-level
# delimiter automatically — keep the markup free of long "]=…=]" runs.
_PAGE_TEMPLATE = """<!doctype html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__TITLE__ service directory</title>
  <script>
    (function () {
      try {
        var t = localStorage.getItem("atlas-theme");
        if (t !== "dark" && t !== "light") {
          t = (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) ? "light" : "dark";
        }
        document.documentElement.dataset.theme = t;
      } catch (e) { document.documentElement.dataset.theme = "dark"; }
    })();
  </script>
  <style>
    :root { color-scheme: dark light; }
    html[data-theme="dark"] {
      --bg: #080910;
      --panel: #12131e;
      --panel-2: #161728;
      --line: #252840;
      --text: #e5e7eb;
      --muted: #9ca3af;
      --ok: #34d399;
      --warn: #fbbf24;
      --off: #64748b;
      --link: #67e8f9;
      --warn-bg: #1c1917;
      --warn-line: #713f12;
      --warn-text: #fde68a;
      --focus: #7dcfff;
    }
    html[data-theme="light"] {
      --bg: #f6f7f9;
      --panel: #ffffff;
      --panel-2: #eef0f4;
      --line: #d5dae3;
      --text: #1f2937;
      --muted: #5b6675;
      --ok: #047857;
      --warn: #b45309;
      --off: #6b7280;
      --link: #0e7490;
      --warn-bg: #fffbeb;
      --warn-line: #f59e0b;
      --warn-text: #92400e;
      --focus: #1d4ed8;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font: 14px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
      transition: background 0.15s ease, color 0.15s ease;
    }
    @media (prefers-reduced-motion: reduce) {
      *, body { transition: none !important; }
    }
    main { width: min(1240px, calc(100vw - 32px)); margin: 0 auto; padding: 28px 0 48px; }
    header { display: flex; align-items: end; justify-content: space-between; gap: 20px; margin-bottom: 20px; }
    h1 { margin: 0 0 6px; font-size: 28px; font-weight: 750; }
    header p { margin: 0; color: var(--muted); }
    .meta { text-align: right; color: var(--muted); }
    #theme-toggle {
      margin-top: 8px; padding: 6px 12px; min-height: 32px;
      border: 1px solid var(--line); border-radius: 999px;
      background: var(--panel); color: var(--text); cursor: pointer; font: inherit;
    }
    #theme-toggle:hover { background: var(--panel-2); }
    .stats { display: flex; gap: 10px; margin: 0 0 16px; flex-wrap: wrap; }
    .pill { border: 1px solid var(--line); background: var(--panel); padding: 6px 10px; border-radius: 6px; }
    .warnings { border: 1px solid var(--warn-line); background: var(--warn-bg); border-radius: 6px; padding: 12px 14px; margin-bottom: 22px; }
    .warnings h2 { font-size: 14px; margin: 0 0 8px; }
    .warnings ul { margin: 0; padding-left: 20px; color: var(--warn-text); }
    .cat { margin: 0 0 26px; }
    .cat-head {
      display: flex; align-items: center; gap: 10px;
      margin: 0 0 12px; font-size: 13px; font-weight: 700;
      letter-spacing: 0.18em; text-transform: uppercase; color: var(--muted);
    }
    .cat-mark { width: 22px; height: 4px; background: var(--accent); border-radius: 2px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }
    .card {
      display: flex; flex-direction: column; gap: 8px;
      border: 1px solid var(--line); border-left: 4px solid var(--accent);
      border-radius: 8px; background: var(--panel); padding: 14px 16px;
      color: var(--text); text-decoration: none;
      transition: border-color 0.15s ease, transform 0.15s ease;
    }
    a.card:hover { border-color: var(--accent); transform: translateY(-1px); }
    a.card:focus-visible, #theme-toggle:focus-visible, .direct:focus-visible {
      outline: 2px solid var(--focus); outline-offset: 2px;
    }
    .card.inert { opacity: 0.75; }
    .card-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .card-name { margin: 0; font-size: 16px; font-weight: 650; }
    .card-desc { margin: 0; color: var(--muted); font-size: 13px; }
    .card-foot {
      display: flex; align-items: center; justify-content: space-between;
      gap: 8px; margin-top: auto; padding-top: 6px; font-size: 12px; color: var(--muted);
      flex-wrap: wrap;
    }
    .open { color: var(--link); }
    .reason { font-style: italic; }
    .direct { color: var(--link); text-decoration: none; }
    .direct:hover { text-decoration: underline; }
    .src {
      font-size: 11px; color: var(--muted);
      border: 1px solid var(--line); border-radius: 4px;
      background: var(--panel-2); padding: 2px 6px;
    }
    .status { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; }
    .dot { width: 8px; height: 8px; border-radius: 999px; background: var(--warn); }
    .status[data-status="healthy"] .dot { background: var(--ok); }
    .status[data-status="disabled"] .dot { background: var(--off); }
    @media (max-width: 760px) {
      header { display: block; }
      .meta { text-align: left; margin-top: 12px; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>__TITLE__ service directory</h1>
        <p>__TAGLINE__</p>
      </div>
      <div class="meta">
        <div>Track: __TRACK__</div>
        <div>Kong: localhost:__KONG_PORT__</div>
        <button id="theme-toggle" type="button" aria-label="Toggle between dark and light theme">&#9681; theme</button>
      </div>
    </header>
    <section class="stats" aria-label="Service counts">
      <span class="pill">__ACTIVE__ active</span>
      <span class="pill">__DISABLED__ disabled</span>
    </section>
    <section class="warnings">
      <h2>Warnings</h2>
      <ul>__WARNINGS__</ul>
    </section>
__SECTIONS__
  </main>
  <script>
    document.getElementById("theme-toggle").addEventListener("click", function () {
      var next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("atlas-theme", next); } catch (e) {}
    });
    for (const el of document.querySelectorAll("[data-health-url]")) {
      const url = el.getAttribute("data-health-url");
      if (!url) continue;
      fetch(url, { mode: "no-cors", cache: "no-store" })
        .then(() => {
          el.dataset.status = "healthy";
          el.querySelector(".status-text").textContent = "healthy";
        })
        .catch(() => {
          el.dataset.status = "degraded";
          el.querySelector(".status-text").textContent = "degraded";
        });
    }
  </script>
</body>
</html>
"""


def _link(url: str | None) -> str:
    if not url:
        return '<span class="muted">-</span>'
    safe = escape(url, quote=True)
    return f'<a href="{safe}">{escape(url)}</a>'


def _direct_url(row, source: str, env: dict[str, str]) -> str | None:
    if source == "disabled":
        return None
    port = None
    if "localhost" in source and row.localhost_port_var:
        port = env.get(row.localhost_port_var)
    if not port and row.port_var:
        port = env.get(row.port_var)
    if not port:
        return None
    return f"http://localhost:{port.strip()}"


def _kong_url(alias: str | None, kong_port: str) -> str | None:
    if not alias:
        return None
    return f"http://{alias}:{kong_port}"


def _track_label(track_key: str | None) -> str:
    if not track_key:
        return "Not selected"
    try:
        from tracks import load_tracks

        track = load_tracks().by_key.get(track_key)
        return track.display_name if track else track_key
    except Exception:  # noqa: BLE001
        return track_key


def _disabled_reason(
    manifest: str,
    locked: bool,
    *,
    track_key: str | None,
    overridden_services: frozenset[str],
) -> str:
    if locked or not track_key:
        return "manually-disabled"
    try:
        from tracks import is_in_track, load_tracks

        registry = load_tracks()
        track = registry.by_key.get(track_key)
        if (
            track is not None
            and track.services is not None
            and manifest not in overridden_services
            and not is_in_track(track, manifest, always_on=registry.always_on)
        ):
            return "disabled-by-track"
    except Exception:  # noqa: BLE001
        pass
    return "manually-disabled"


def _auth_note(name: str, alias: str | None) -> str:
    name_l = name.lower()
    if alias == "supabase-studio.localhost" or alias == "ray.localhost":
        return "Kong basic-auth"
    if "minio" in name_l:
        return "MinIO credentials"
    if "neo4j" in name_l:
        return "Neo4j credentials"
    if "jupyter" in name_l:
        return "JupyterHub login"
    if name == "n8n":
        return "n8n owner account"
    if "grafana" in name_l:
        return "Grafana login"
    if "litellm" in name_l:
        return "LiteLLM admin key"
    if alias:
        return "Service-specific"
    return "Internal"


def _dependency_warnings(rows, service_sources: dict[str, str], env: dict[str, str]) -> list[str]:
    by_manifest = {row.manifest: row for row in rows}
    disabled_manifests = {
        row.manifest
        for row in rows
        if (service_sources.get(row.source_var) or env.get(row.source_var) or "container") == "disabled"
    }
    warnings = []
    try:
        from services.manifests import load_manifests

        services_root = Path(__file__).resolve().parent.parent.parent / "services"
        for manifest in load_manifests(services_root):
            if manifest.name in disabled_manifests:
                continue
            missing = [
                dep for dep in manifest.depends_on.required
                if dep in disabled_manifests and dep in by_manifest
            ]
            if missing:
                warnings.append(
                    f"{manifest.label or manifest.name} has disabled required dependencies: {', '.join(sorted(missing))}."
                )
    except Exception:  # noqa: BLE001
        pass
    return warnings


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
