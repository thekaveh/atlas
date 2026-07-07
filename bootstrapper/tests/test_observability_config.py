"""Unit tests for the observability bundle's adaptive hooks —
_generate_prometheus_config() and _generate_grafana_config().

Both hooks are pure functions of (source_value) and have
the same shape as _generate_ray_config — see test_ray_config.py for
the matching pattern. The prometheus hook is load-bearing because it
writes scales for FIVE containers across THREE manifests (prometheus,
node-exporter, cadvisor in services/prometheus/; postgres-exporter
in services/supabase/; redis-exporter in services/redis/), so
regressing it silently switches off chunks of the observability
bundle without any explicit failure.
"""

from __future__ import annotations


def _service_config_instance():
    """Build a ServiceConfig instance with no real env file — we only
    test the pure hook functions. Lazy-import to avoid module-load deps."""
    from services.service_config import ServiceConfig
    sc = ServiceConfig.__new__(ServiceConfig)
    sc.service_sources = {}
    return sc


# ─── _generate_prometheus_config ─────────────────────────────────────


def test_prometheus_container_scales_everything_to_one():
    """All five scales flip to 1 when PROMETHEUS_SOURCE=container."""
    sc = _service_config_instance()
    out = sc._generate_prometheus_config(source_value="container")
    assert out["PROMETHEUS_SCALE"] == "1"
    assert out["NODE_EXPORTER_SCALE"] == "1"
    assert out["CADVISOR_SCALE"] == "1"
    assert out["POSTGRES_EXPORTER_SCALE"] == "1"
    assert out["REDIS_EXPORTER_SCALE"] == "1"


def test_prometheus_container_sets_internal_endpoint():
    sc = _service_config_instance()
    out = sc._generate_prometheus_config(source_value="container")
    assert out["PROMETHEUS_ENDPOINT"] == "http://prometheus:9090"


def test_prometheus_disabled_scales_everything_to_zero():
    """All five scales flip to 0 when disabled — including the cross-manifest
    postgres-exporter / redis-exporter sidecars (they're useless without a
    scraper)."""
    sc = _service_config_instance()
    out = sc._generate_prometheus_config(source_value="disabled")
    assert out["PROMETHEUS_SCALE"] == "0"
    assert out["NODE_EXPORTER_SCALE"] == "0"
    assert out["CADVISOR_SCALE"] == "0"
    assert out["POSTGRES_EXPORTER_SCALE"] == "0"
    assert out["REDIS_EXPORTER_SCALE"] == "0"


def test_prometheus_disabled_blanks_the_endpoint():
    """PROMETHEUS_ENDPOINT is consumed by Grafana's datasource
    provisioning. Empty value when Prom is off means Grafana renders a
    'datasource unreachable' state instead of pointing at a stale URL."""
    sc = _service_config_instance()
    out = sc._generate_prometheus_config(source_value="disabled")
    assert out["PROMETHEUS_ENDPOINT"] == ""


def test_prometheus_unknown_source_defaults_to_disabled():
    """Defensive — an unrecognised source must not partially enable the bundle.
    Anything other than 'container' is treated as off."""
    sc = _service_config_instance()
    out = sc._generate_prometheus_config(source_value="some-future-source")
    assert out["PROMETHEUS_SCALE"] == "0"
    assert out["NODE_EXPORTER_SCALE"] == "0"
    assert out["CADVISOR_SCALE"] == "0"
    assert out["POSTGRES_EXPORTER_SCALE"] == "0"
    assert out["REDIS_EXPORTER_SCALE"] == "0"
    assert out["PROMETHEUS_ENDPOINT"] == ""


def test_prometheus_returns_only_expected_keys():
    """Guardrail against the hook accidentally widening its contract and
    overwriting an unrelated env var. The set of keys is fixed."""
    sc = _service_config_instance()
    out = sc._generate_prometheus_config(source_value="container")
    assert set(out.keys()) == {
        "PROMETHEUS_SCALE",
        "NODE_EXPORTER_SCALE",
        "CADVISOR_SCALE",
        "POSTGRES_EXPORTER_SCALE",
        "REDIS_EXPORTER_SCALE",
        "PROMETHEUS_ENDPOINT",
    }


# ─── _generate_grafana_config ─────────────────────────────────────────


def test_grafana_container_scales_to_one_and_sets_endpoint():
    sc = _service_config_instance()
    out = sc._generate_grafana_config(source_value="container")
    assert out["GRAFANA_SCALE"] == "1"
    assert out["GRAFANA_ENDPOINT"] == "http://grafana:3000"


def test_grafana_disabled_zeros_and_blanks():
    sc = _service_config_instance()
    out = sc._generate_grafana_config(source_value="disabled")
    assert out["GRAFANA_SCALE"] == "0"
    assert out["GRAFANA_ENDPOINT"] == ""


def test_grafana_unknown_source_treated_as_disabled():
    sc = _service_config_instance()
    out = sc._generate_grafana_config(source_value="future-source")
    assert out["GRAFANA_SCALE"] == "0"
    assert out["GRAFANA_ENDPOINT"] == ""


def test_grafana_returns_only_expected_keys():
    sc = _service_config_instance()
    out = sc._generate_grafana_config(source_value="container")
    assert set(out.keys()) == {"GRAFANA_SCALE", "GRAFANA_ENDPOINT"}


# ─── Cross-hook coupling — Prom controls the sidecars ────────────────


def test_grafana_does_not_touch_prometheus_or_exporter_scales():
    """_generate_grafana_config writes ONLY Grafana's two vars; the
    prometheus hook owns the rest. Keeping the boundary tight prevents
    a future Grafana-only change from accidentally rewriting Prom
    state."""
    sc = _service_config_instance()
    out = sc._generate_grafana_config(source_value="container")
    for forbidden in (
        "PROMETHEUS_SCALE",
        "NODE_EXPORTER_SCALE",
        "CADVISOR_SCALE",
        "POSTGRES_EXPORTER_SCALE",
        "REDIS_EXPORTER_SCALE",
        "PROMETHEUS_ENDPOINT",
    ):
        assert forbidden not in out, (
            f"Grafana hook leaked into Prometheus's domain: wrote {forbidden!r}"
        )


def test_prometheus_does_not_touch_grafana_scale_or_endpoint():
    """Symmetric — the Prom hook must not touch Grafana state."""
    sc = _service_config_instance()
    out = sc._generate_prometheus_config(source_value="container")
    for forbidden in ("GRAFANA_SCALE", "GRAFANA_ENDPOINT"):
        assert forbidden not in out, (
            f"Prometheus hook leaked into Grafana's domain: wrote {forbidden!r}"
        )
