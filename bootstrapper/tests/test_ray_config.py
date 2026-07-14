"""Unit tests for _generate_ray_config() — derives RAY_IMAGE, RAY_WORKER_SCALE,
RAY_ADDRESS, RAY_HEAD_SCALE from RAY_SOURCE + RAY_WORKER_COUNT.

The hook is the only adaptive piece in the ray manifest — its correctness
is load-bearing for the entire compose substitution chain (image tag,
worker replicas, RAY_ADDRESS pushed into Backend / JupyterHub).
"""

from __future__ import annotations


def _service_config_instance(env_on_disk=None):
    """Build a ServiceConfig instance with a stub env file — we only
    test the pure hook function. Lazy-import to avoid module-load deps.

    ``env_on_disk`` simulates `.env` contents read via
    ``config_parser.parse_env_file()`` (where RAY_WORKER_COUNT actually
    lives). Defaults to empty so the existing tests exercise the
    shared_env fallback path."""
    from unittest.mock import MagicMock
    from services.service_config import ServiceConfig
    sc = ServiceConfig.__new__(ServiceConfig)
    # Minimal fields the hook reads; everything else stays uninitialized.
    sc.service_sources = {}
    sc.config_parser = MagicMock()
    sc.config_parser.parse_env_file.return_value = dict(env_on_disk or {})
    return sc


def test_disabled_returns_empty_address_and_zero_scales():
    sc = _service_config_instance()
    out = sc._generate_ray_config(
        source_value="disabled",
        shared_env={"RAY_WORKER_COUNT": "2"},
    )
    assert out["RAY_HEAD_SCALE"] == "0"
    assert out["RAY_WORKER_SCALE"] == "0"
    assert out["RAY_ADDRESS"] == ""
    # Image is irrelevant when disabled but must be set to a valid default
    # (compose won't pull a missing image with replicas: 0, but tests for
    # env-example consistency need *some* value).
    assert out["RAY_IMAGE"].startswith("rayproject/ray:")


def test_container_cpu_returns_cpu_image_and_resolved_scales():
    sc = _service_config_instance()
    out = sc._generate_ray_config(
        source_value="ray-container-cpu",
        shared_env={"RAY_WORKER_COUNT": "3", "RAY_IMAGE": "rayproject/ray:2.56.0",
                    "RAY_GPU_IMAGE": "rayproject/ray:2.56.0-gpu"},
    )
    assert out["RAY_IMAGE"] == "rayproject/ray:2.56.0"
    assert out["RAY_HEAD_SCALE"] == "1"
    assert out["RAY_WORKER_SCALE"] == "3"
    assert out["RAY_ADDRESS"] == "ray://ray-head:10001"


def test_container_gpu_returns_gpu_image_and_resolved_scales():
    sc = _service_config_instance()
    out = sc._generate_ray_config(
        source_value="ray-container-gpu",
        shared_env={"RAY_WORKER_COUNT": "2", "RAY_IMAGE": "rayproject/ray:2.56.0",
                    "RAY_GPU_IMAGE": "rayproject/ray:2.56.0-gpu"},
    )
    assert out["RAY_IMAGE"] == "rayproject/ray:2.56.0-gpu"
    assert out["RAY_HEAD_SCALE"] == "1"
    assert out["RAY_WORKER_SCALE"] == "2"
    assert out["RAY_ADDRESS"] == "ray://ray-head:10001"


def test_unknown_source_value_disables_everything():
    """Defensive: a SOURCE value not in the known list should degrade
    gracefully (scale=0, no address) rather than crash."""
    sc = _service_config_instance()
    out = sc._generate_ray_config(
        source_value="some-unknown-future-source",
        shared_env={"RAY_WORKER_COUNT": "2"},
    )
    assert out["RAY_HEAD_SCALE"] == "0"
    assert out["RAY_WORKER_SCALE"] == "0"
    assert out["RAY_ADDRESS"] == ""


def test_worker_count_zero_means_head_only():
    sc = _service_config_instance()
    out = sc._generate_ray_config(
        source_value="ray-container-cpu",
        shared_env={"RAY_WORKER_COUNT": "0", "RAY_IMAGE": "rayproject/ray:2.56.0",
                    "RAY_GPU_IMAGE": "rayproject/ray:2.56.0-gpu"},
    )
    assert out["RAY_HEAD_SCALE"] == "1"  # head still on
    assert out["RAY_WORKER_SCALE"] == "0"  # head-only single-node Ray


def test_invalid_worker_count_falls_back_to_default():
    """A malformed RAY_WORKER_COUNT (non-integer, negative) should fall
    back to the manifest's stated default `2` rather than crash."""
    sc = _service_config_instance()
    out = sc._generate_ray_config(
        source_value="ray-container-cpu",
        shared_env={"RAY_WORKER_COUNT": "not-a-number",
                    "RAY_IMAGE": "rayproject/ray:2.56.0",
                    "RAY_GPU_IMAGE": "rayproject/ray:2.56.0-gpu"},
    )
    assert out["RAY_WORKER_SCALE"] == "2"


def test_worker_count_read_from_disk_not_shared_env():
    """Regression: RAY_WORKER_COUNT lives in `.env`, not in the freshly-built
    shared_env the pipeline passes. The hook must read it from disk (like
    SPARK_WORKER_COUNT) so the user's --ray-worker-count actually takes effect.
    Previously the hook read shared_env only, so the override was silently
    ignored and replicas were always 2."""
    # Disk says 4; shared_env carries no RAY_WORKER_COUNT (the real pipeline
    # never seeds it) — only the image pins.
    sc = _service_config_instance(env_on_disk={"RAY_WORKER_COUNT": "4"})
    out = sc._generate_ray_config(
        source_value="ray-container-cpu",
        shared_env={"RAY_IMAGE": "rayproject/ray:2.56.0",
                    "RAY_GPU_IMAGE": "rayproject/ray:2.56.0-gpu"},
    )
    assert out["RAY_WORKER_SCALE"] == "4"


def test_worker_count_disk_overrides_shared_env():
    """When both disagree, the on-disk value wins (it's where the wizard/CLI
    persists the override)."""
    sc = _service_config_instance(env_on_disk={"RAY_WORKER_COUNT": "6"})
    out = sc._generate_ray_config(
        source_value="ray-container-cpu",
        shared_env={"RAY_WORKER_COUNT": "2",
                    "RAY_IMAGE": "rayproject/ray:2.56.0",
                    "RAY_GPU_IMAGE": "rayproject/ray:2.56.0-gpu"},
    )
    assert out["RAY_WORKER_SCALE"] == "6"
