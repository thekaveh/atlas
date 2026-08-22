"""Pre-flight drift warning for `.env` *_IMAGE pins vs `.env.example`.

CI tests against `.env.example`, so an image-pin migration (e.g.
`SPARK_IMAGE=bitnami/spark:4.1.2` → `apache/spark:4.1.2` in PR #35
after Bitnami went paywalled) is invisible to CI but breaks
`docker build` for users whose `.env` predates the migration.
PR #35's user incident produced
`docker.io/bitnami/spark:4.1.2: not found` at the spark-history
image-pull step.

`start._detect_env_image_drift()` is the pre-flight detector that
surfaces this class. Tests cover:
- The PR #35 SPARK_IMAGE incident (stale Bitnami value)
- The empty-value skip (auto-managed / placeholder lines defer to
  compose `:-` fallbacks)
- Scope discipline (only `*_IMAGE` keys, not other env divergence)
- The missing-file safe fallback
- Comment + whitespace tolerance
"""
from __future__ import annotations

from pathlib import Path

from start import _detect_env_image_drift


def _write_env_example(tmp_path: Path, body: str) -> Path:
    p = tmp_path / ".env.example"
    p.write_text(body, encoding="utf-8")
    return p


def test_stale_image_pin_surfaces_as_drift(tmp_path: Path):
    """The PR #35 SPARK_IMAGE incident: user's .env predates the
    Bitnami → apache migration, .env.example carries the new pin."""
    env_example = _write_env_example(tmp_path, "SPARK_IMAGE=apache/spark:4.1.2\n")
    user_env = {"SPARK_IMAGE": "bitnami/spark:4.1.2"}
    drift = _detect_env_image_drift(user_env, env_example)
    assert drift == [("SPARK_IMAGE", "bitnami/spark:4.1.2", "apache/spark:4.1.2")]


def test_matching_pin_no_drift(tmp_path: Path):
    """User's .env matches .env.example — no warning."""
    env_example = _write_env_example(tmp_path, "SPARK_IMAGE=apache/spark:4.1.2\n")
    user_env = {"SPARK_IMAGE": "apache/spark:4.1.2"}
    assert _detect_env_image_drift(user_env, env_example) == []


def test_empty_user_value_no_drift(tmp_path: Path):
    """Empty user value (placeholder / auto-managed) must NOT warn —
    compose's `:-` fallback to .env.example's default handles it."""
    env_example = _write_env_example(tmp_path, "SPARK_IMAGE=apache/spark:4.1.2\n")
    assert _detect_env_image_drift({"SPARK_IMAGE": ""}, env_example) == []
    assert _detect_env_image_drift({"SPARK_IMAGE": "   "}, env_example) == []
    assert _detect_env_image_drift({}, env_example) == []


def test_non_image_keys_ignored(tmp_path: Path):
    """Scope discipline: only `*_IMAGE` keys. Other divergence (ports,
    secrets, source toggles) is often intentional and would produce
    noisy false-positives — those are out of scope."""
    env_example = _write_env_example(tmp_path, "SPARK_SOURCE=disabled\nBASE_PORT=63000\nMINIO_ROOT_PASSWORD=changeme\n")
    user_env = {
        "SPARK_SOURCE": "container",      # intentional CLI override
        "BASE_PORT": "65000",             # intentional user customization
        "MINIO_ROOT_PASSWORD": "secret",  # intentional secret rotation
    }
    assert _detect_env_image_drift(user_env, env_example) == []


def test_multi_drift_reports_all(tmp_path: Path):
    """Every drifting *_IMAGE key surfaces, not just the first one."""
    env_example = _write_env_example(
        tmp_path,
        "SPARK_IMAGE=apache/spark:4.1.2\n"
        "ZEPPELIN_IMAGE=apache/zeppelin:0.12.0\n"
        "AIRFLOW_IMAGE=apache/airflow:3.3.1\n",
    )
    user_env = {
        "SPARK_IMAGE": "bitnami/spark:4.1.2",         # stale
        "ZEPPELIN_IMAGE": "apache/zeppelin:0.12.0",   # current — skip
        "AIRFLOW_IMAGE": "apache/airflow:3.1.0",      # stale minor
    }
    drift = _detect_env_image_drift(user_env, env_example)
    drift_keys = {k for (k, _, _) in drift}
    assert drift_keys == {"SPARK_IMAGE", "AIRFLOW_IMAGE"}


def test_missing_example_file_no_crash(tmp_path: Path):
    """Pre-flight must never crash if .env.example is absent."""
    assert _detect_env_image_drift({"SPARK_IMAGE": "x"}, tmp_path / "nonexistent") == []


def test_comments_and_whitespace_in_example_tolerated(tmp_path: Path):
    """Common .env.example shapes don't false-positive."""
    env_example = _write_env_example(
        tmp_path,
        "# Spark cluster\n"
        "SPARK_IMAGE=apache/spark:4.1.2    # via Apache (post-Bitnami paywall)\n"
        "\n"
        "# Not an image — should be skipped\n"
        "SPARK_NUM_WORKERS=2\n",
    )
    user_env = {"SPARK_IMAGE": "bitnami/spark:4.1.2"}
    drift = _detect_env_image_drift(user_env, env_example)
    assert drift == [("SPARK_IMAGE", "bitnami/spark:4.1.2", "apache/spark:4.1.2")]
