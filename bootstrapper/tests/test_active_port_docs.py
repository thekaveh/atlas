from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_active_port_docs_do_not_use_retired_allocator_defaults() -> None:
    checks = {
        "services/tts-provider/provider/README.md": ["63048"],
        "services/tts-provider/provider/localhost/README.md": ["63047"],
        "docs/ROADMAP.md": ["port 63030", "default 63031"],
        "services/tei-reranker/README.md": ["63030–63039"],
    }

    for relative, retired_literals in checks.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        for retired in retired_literals:
            assert retired not in text, f"{relative} still advertises retired port literal {retired!r}"
