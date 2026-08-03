from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_container_healthchecks_use_real_readiness_contracts() -> None:
    assert "http://localhost:8000/ready" in _text("services/backend/app/Dockerfile")

    otel = yaml.safe_load(_text("services/otel-collector/compose.yml"))["services"][
        "otel-collector"
    ]
    assert otel["healthcheck"]["test"] == [
        "CMD",
        "/otelcol-contrib",
        "validate",
        "--config=/etc/otelcol/config.yaml",
    ]

    parakeet = yaml.safe_load(_text("services/parakeet/compose.yml"))["services"][
        "parakeet-gpu"
    ]
    assert "PRELOAD_MODEL" not in parakeet["environment"]
    assert parakeet["healthcheck"]["test"] == [
        "CMD",
        "curl",
        "-f",
        "http://localhost:8000/health",
    ]


def test_provider_health_endpoints_check_runtime_dependencies() -> None:
    assert "shutil.which(binary)" in _text(
        "services/asset-worker/app/asset_worker/api.py"
    )
    assert "shutil.which(binary)" in _text(
        "services/asset-baker/app/asset_baker/api.py"
    )
    assert "await processor_status()" in _text(
        "services/docling/provider/shared/api_server.py"
    )
    assert "model_is_loaded()" in _text(
        "services/parakeet/provider/shared/api_server.py"
    )
    assert "_model_startup.start()" in _text(
        "services/parakeet/provider/shared/api_server.py"
    )
