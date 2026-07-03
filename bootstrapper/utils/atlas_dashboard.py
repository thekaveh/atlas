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
from services.topology import CATEGORY_LABELS, get_topology


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
    """Render a complete HTML document for Kong's root route."""
    active_count = sum(1 for row in model.services if row.status != "disabled")
    disabled_count = len(model.services) - active_count
    warning_html = "".join(
        f"<li>{escape(w)}</li>" for w in model.warnings
    ) or "<li>No startup warnings detected in the generated snapshot.</li>"
    rows_html = "\n".join(_render_row(row) for row in model.services)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(model.brand_name)} service directory</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #09090b;
      --panel: #111827;
      --line: #263244;
      --text: #e5e7eb;
      --muted: #9ca3af;
      --ok: #34d399;
      --warn: #fbbf24;
      --off: #64748b;
      --link: #67e8f9;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }}
    main {{ width: min(1180px, calc(100vw - 32px)); margin: 0 auto; padding: 28px 0 40px; }}
    header {{ display: flex; align-items: end; justify-content: space-between; gap: 20px; margin-bottom: 22px; }}
    h1 {{ margin: 0 0 6px; font-size: 28px; font-weight: 750; letter-spacing: 0; }}
    p {{ margin: 0; color: var(--muted); }}
    .meta {{ text-align: right; color: var(--muted); }}
    .stats {{ display: flex; gap: 10px; margin: 0 0 18px; flex-wrap: wrap; }}
    .pill {{ border: 1px solid var(--line); background: #0f172a; padding: 6px 10px; border-radius: 6px; }}
    .warnings {{ border: 1px solid #713f12; background: #1c1917; border-radius: 6px; padding: 12px 14px; margin-bottom: 18px; }}
    .warnings h2 {{ font-size: 14px; margin: 0 0 8px; }}
    .warnings ul {{ margin: 0; padding-left: 20px; color: #fde68a; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--panel); border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 10px 12px; text-align: left; vertical-align: top; }}
    th {{ color: #f8fafc; background: #0f172a; font-size: 12px; text-transform: uppercase; }}
    tr:last-child td {{ border-bottom: 0; }}
    a {{ color: var(--link); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .status {{ display: inline-flex; align-items: center; gap: 6px; min-width: 92px; }}
    .dot {{ width: 8px; height: 8px; border-radius: 999px; background: var(--warn); }}
    .status[data-status="healthy"] .dot {{ background: var(--ok); }}
    .status[data-status="disabled"] .dot {{ background: var(--off); }}
    .muted {{ color: var(--muted); }}
    @media (max-width: 760px) {{
      header {{ display: block; }}
      .meta {{ text-align: left; margin-top: 12px; }}
      table, thead, tbody, tr, th, td {{ display: block; }}
      thead {{ display: none; }}
      tr {{ border-bottom: 1px solid var(--line); }}
      td {{ border: 0; padding: 7px 12px; }}
      td::before {{ content: attr(data-label); display: block; color: var(--muted); font-size: 11px; text-transform: uppercase; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>{escape(model.brand_name)} service directory</h1>
        <p>{escape(model.tagline)}</p>
      </div>
      <div class="meta">
        <div>Track: {escape(model.track_label)}</div>
        <div>Kong: localhost:{escape(model.kong_port)}</div>
      </div>
    </header>
    <section class="stats" aria-label="Service counts">
      <span class="pill">{active_count} active</span>
      <span class="pill">{disabled_count} disabled</span>
    </section>
    <section class="warnings">
      <h2>Warnings</h2>
      <ul>{warning_html}</ul>
    </section>
    <table>
      <thead>
        <tr>
          <th>Status</th>
          <th>Service</th>
          <th>Category</th>
          <th>Source</th>
          <th>Kong URL</th>
          <th>Direct URL</th>
          <th>Auth</th>
        </tr>
      </thead>
      <tbody>
{rows_html}
      </tbody>
    </table>
  </main>
  <script>
    for (const el of document.querySelectorAll("[data-health-url]")) {{
      const url = el.getAttribute("data-health-url");
      if (!url) continue;
      fetch(url, {{ mode: "no-cors", cache: "no-store" }})
        .then(() => {{
          el.dataset.status = "healthy";
          el.querySelector(".status-text").textContent = "healthy";
        }})
        .catch(() => {{
          el.dataset.status = "degraded";
          el.querySelector(".status-text").textContent = "degraded";
        }});
    }}
  </script>
</body>
</html>
"""


def _render_row(row: DashboardService) -> str:
    disabled_detail = (
        f"<div class=\"muted\">{escape(row.disabled_reason or '')}</div>"
        if row.disabled_reason else ""
    )
    kong = _link(row.kong_url)
    direct = _link(row.direct_url)
    health_attr = (
        f' data-health-url="{escape(row.health_url, quote=True)}"'
        if row.health_url else ""
    )
    return f"""        <tr>
          <td data-label="Status"><span class="status" data-status="{row.status}"{health_attr}><span class="dot"></span><span class="status-text">{row.status}</span></span>{disabled_detail}</td>
          <td data-label="Service">{escape(row.name)}</td>
          <td data-label="Category">{escape(row.category)}</td>
          <td data-label="Source"><code>{escape(row.source)}</code></td>
          <td data-label="Kong URL">{kong}</td>
          <td data-label="Direct URL">{direct}</td>
          <td data-label="Auth">{escape(row.auth_note)}</td>
        </tr>"""


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
    if alias == "studio.localhost" or alias == "ray.localhost":
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
