from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "docs" / "research" / "candidates" / "imgproxy.md"
SERVICE_MANIFEST = ROOT / "services" / "imgproxy" / "service.yml"


def _candidate_text() -> str:
    return CANDIDATE.read_text(encoding="utf-8")


def test_imgproxy_remains_watchlist_until_signed_asset_browser_flow_exists() -> None:
    text = _candidate_text()

    assert "Watchlist decision (2026-07-04)" in text
    assert "must not add `services/imgproxy/service.yml` yet" in text
    assert "`IMGPROXY_SOURCE=disabled|container`" in text
    assert "disabled by default" in text
    assert "no public `imgproxy.localhost` route" in text
    assert "backend-generated signed URLs" in text
    assert "`IMGPROXY_KEY`" in text
    assert "`IMGPROXY_SALT`" in text


def test_imgproxy_future_service_spec_covers_atlas_topology_and_integrations() -> None:
    text = _candidate_text()

    expected_terms = [
        "`media`",
        "`gen-ai-creative`",
        "Wizard",
        "topology",
        "MinIO",
        "ComfyUI",
        "Blender MCP",
        "root dashboard",
        "asset browser",
        "`backend -> imgproxy`",
        "`imgproxy -> minio`",
        "`IMGPROXY_S3_ALLOWED_BUCKETS`",
        "read-only MinIO credentials",
    ]

    for term in expected_terms:
        assert term in text


def test_imgproxy_service_manifest_is_not_added_by_watchlist_decision() -> None:
    assert not SERVICE_MANIFEST.exists()
