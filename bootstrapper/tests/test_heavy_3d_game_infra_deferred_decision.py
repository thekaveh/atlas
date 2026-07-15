from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "docs" / "research" / "candidates" / "heavy-3d-game-infrastructure.md"
MATRIX = ROOT / "docs" / "research" / "integration-matrix.md"
STRATEGY_REPORT = ROOT / "docs" / "strategy" / "atlas-vnext-strategy-report.md"
BLENDER_MCP = ROOT / "services" / "blender-mcp" / "service.yml"
SERVICE_MANIFESTS = [
    ROOT / "services" / "hunyuan3d" / "service.yml",
    ROOT / "services" / "trellis" / "service.yml",
    ROOT / "services" / "nerfstudio" / "service.yml",
    ROOT / "services" / "unreal-mcp" / "service.yml",
    ROOT / "services" / "livekit" / "service.yml",
]


def _candidate_text() -> str:
    return CANDIDATE.read_text(encoding="utf-8")


def test_heavy_3d_game_infra_remains_deferred() -> None:
    text = _candidate_text()

    assert "Deferred decision (2026-07-04)" in text
    assert "must not add `services/hunyuan3d/service.yml`" in text
    assert "must not add `services/trellis/service.yml`" in text
    assert "must not add `services/nerfstudio/service.yml`" in text
    assert "must not add `services/unreal-mcp/service.yml`" in text
    assert "must not add `services/livekit/service.yml`" in text
    assert "asset pipeline first" in text
    assert "MCP safety posture" in text
    assert "not a default generate-a-whole-game promise" in text


def test_heavy_3d_future_contract_covers_service_admission() -> None:
    text = _candidate_text()

    expected_terms = [
        "`gen-ai-creative`",
        "`all`",
        "`media`",
        "`apps`",
        "`infra`",
        "`HUNYUAN3D_SOURCE=disabled|container-gpu|localhost`",
        "`TRELLIS_SOURCE=disabled|container-gpu|localhost`",
        "`NERFSTUDIO_SOURCE=disabled|container-gpu|localhost`",
        "`UNREAL_MCP_SOURCE=disabled|localhost`",
        "`LIVEKIT_SOURCE=disabled|container|localhost`",
        "disabled by default",
        "Wizard placement",
        "no Kong route for editor automation",
        "localhost-only",
        "custom `BASE_PORT`",
        "VRAM",
        "model-cache size",
        "output-format contracts",
        "glTF-Transform",
        "imgproxy",
        "MinIO",
        "ComfyUI",
        "Blender MCP",
        "Open WebUI",
        "Hermes",
        "LiveKit",
        "data_flow.calls",
        "init companion",
    ]

    for term in expected_terms:
        assert term in text


def test_heavy_3d_service_manifests_are_not_added() -> None:
    assert BLENDER_MCP.exists(), "Blender MCP remains the existing safe host-only bridge"
    for manifest in SERVICE_MANIFESTS:
        assert not manifest.exists()


def test_heavy_3d_decision_is_indexed_and_strategy_reflected() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")
    strategy = STRATEGY_REPORT.read_text(encoding="utf-8")

    assert "| Heavy 3D Game Infrastructure | media | _(none)_ | [candidates/heavy-3d-game-infrastructure.md]" in matrix
    assert "July 4, 2026 decision keeps heavy 3D/game infrastructure deferred" in strategy
