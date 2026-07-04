from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_active_port_docs_do_not_use_retired_allocator_defaults() -> None:
    checks = {
        "README.md": ["default `63028`", "default `63029`", "default `63031`"],
        "services/comfyui/README.md": ["default `63041`"],
        "services/hermes/README.md": ["localhost:63030"],
        "services/jupyterhub/README.md": ["63081", "64156"],
        "services/jupyterhub/build/README.md": ["63081"],
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
