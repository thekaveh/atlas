from __future__ import annotations

from pathlib import Path

from services.topology import get_topology


ROOT = Path(__file__).resolve().parents[2]


def test_active_port_docs_do_not_use_retired_allocator_defaults() -> None:
    checks = {
        "README.md": ["default `63028`", "default `63029`", "default `63031`"],
        "services/comfyui/README.md": ["default `63041`"],
        "services/hermes/README.md": ["localhost:63030"],
        "services/jupyterhub/README.md": ["63081", "64156"],
        "services/neo4j/README.md": ["default: 63020", "default: 63021"],
        "services/ollama/README.md": ["localhost:63030", "LITELLM_PORT=63030"],
        "services/openclaw/README.md": ["localhost:63030"],
        "services/redis/README.md": ["default `63022`", "default 63022"],
        "services/searxng/README.md": ["default `63043`"],
        "services/supabase/README.md": [
            "default: 63010",
            "default: 63013",
            "default: 63014",
        ],
        "services/tts-provider/provider/README.md": ["63048"],
        "services/tts-provider/provider/localhost/README.md": ["63047"],
        "docs/ROADMAP.md": ["port 63030", "default 63031"],
        "services/tei-reranker/README.md": ["63030–63039"],
    }

    for relative, retired_literals in checks.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        for retired in retired_literals:
            assert retired not in text, f"{relative} still advertises retired port literal {retired!r}"


def test_active_service_port_claims_match_topology() -> None:
    ports = get_topology(ROOT / "services").port_defaults
    comfyui = ports["COMFYUI_PORT"]
    stt = ports["STT_PROVIDER_PORT"]
    searxng = ports["SEARXNG_PORT"]
    tika = ports["TIKA_PORT"]
    tts = ports["TTS_PROVIDER_PORT"]
    chatterbox = ports["CHATTERBOX_PORT"]
    speaches = ports["SPEACHES_PORT"]
    claims = {
        # Root README no longer advertises per-service endpoints directly (Task 2,
        # docs IA restructure) — it links to the generated docs/reference/ports-routes.md
        # and keeps only the generated TOPOLOGY table. Per-service READMEs below remain
        # the canonical, still-checked homes for these port claims.
        "services/comfyui/README.md": (
            f"`http://localhost:${{COMFYUI_PORT}}` (default `{comfyui}`)",
        ),
        "services/searxng/README.md": (
            f"`http://localhost:${{SEARXNG_PORT}}` (default `{searxng}`)",
        ),
        "services/stt-provider/README.md": (
            f"http://localhost:{speaches}/v1/audio/transcriptions",
            f"| `STT_PROVIDER_PORT` | `{stt}` |",
        ),
        "services/tts-provider/README.md": (
            f"http://localhost:{speaches}/v1/models/",
            f"http://localhost:{speaches}/v1/audio/speech",
            f"| `TTS_PROVIDER_PORT` | `{tts}` |",
            f"| `SPEACHES_PORT` | `{speaches}` |",
            f"| `CHATTERBOX_PORT` | `{chatterbox}` |",
        ),
        "services/tts-provider/provider/README.md": (
            f"http://localhost:{speaches}/v1/models/",
            f"http://localhost:{speaches}/v1/audio/speech",
        ),
        "services/tts-provider/provider/localhost/README.md": (
            f"container CHATTERBOX_PORT, which is {chatterbox}",
        ),
        "docs/quick-start/troubleshooting.md": (
            f"curl http://localhost:{comfyui}  # Direct port access (COMFYUI_PORT)",
        ),
        "services/parakeet/compose.yml": (
            f"STT_PROVIDER_PORT default ({stt})",
        ),
    }

    for relative, expected_claims in claims.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        for claim in expected_claims:
            assert claim in text, f"{relative} does not advertise topology claim {claim!r}"
