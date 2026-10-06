"""Resolve LightRAG's LLM/embedding model names + embedding dim from LiteLLM.

Reads LiteLLM's /v1/models, picks LITELLM_DEFAULT_MODEL for chat/VLM and
LITELLM_EMBEDDING_MODEL for embedding, computes the embedding dimension from
a known lookup table (or by issuing a probe embedding), and emits
KEY=VALUE lines on stdout for the calling shell to consume.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
import urllib.error
import urllib.request

# Force line-buffered stdout. The shell that invokes this script consumes
# the KEY=VALUE output below; if Python's block-buffer holds the lines and
# the script crashes between print() and a normal exit (or is killed by
# the init container's `set -e`), the shell sees partial / empty output
# and the resolved model names silently flip to '' at runtime. Same
# pattern as the open-webui init scripts.
sys.stdout.reconfigure(line_buffering=True)

LITELLM_URL = "http://litellm:4000/v1/models"
MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")

# Known embedding dims for the commonly-used models in this stack. If the
# resolved model isn't here, fall back to a probe embedding.
KNOWN_DIMS = {
    "nomic-embed-text": 768,
    "ollama/nomic-embed-text": 768,
    "bge-m3": 1024,
    "BAAI/bge-m3": 1024,
    # Catalog default (services/ollama/models.yaml). Without it the dim came
    # from a live probe that, on first boot, runs before ollama-pull has
    # downloaded the model and silently fell back to 768.
    "qwen3-embedding:0.6b": 1024,  # exact tag: the 4b/8b variants are larger
    "mxbai-embed-large": 1024,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


def fetch_models() -> list[str]:
    req = urllib.request.Request(
        LITELLM_URL,
        headers={"Authorization": f"Bearer {MASTER_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            payload = json.loads(r.read().decode("utf-8"))
        return [m["id"] for m in payload.get("data", [])]
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"# WARN could not fetch /v1/models: {e}", file=sys.stderr)
        return []


def resolve_dim(model: str) -> int:
    # Direct hit
    if model in KNOWN_DIMS:
        return KNOWN_DIMS[model]
    # Substring match (e.g. "ollama/bge-m3" matches "bge-m3")
    for key, dim in KNOWN_DIMS.items():
        if key in model:
            return dim
    # Probe embedding fallback
    try:
        req = urllib.request.Request(
            "http://litellm:4000/v1/embeddings",
            data=json.dumps({"input": "probe", "model": model}).encode(),
            headers={
                "Authorization": f"Bearer {MASTER_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            payload = json.loads(r.read().decode("utf-8"))
        return len(payload["data"][0]["embedding"])
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, IndexError) as e:
        # Narrow except so a genuinely unexpected error (bug in this script,
        # OOM, etc.) crashes lightrag-init loudly. Returning 768 when the
        # real model has dim 1024 silently writes a dim-768 PGVector index
        # against a 1024-dim store → every insert at runtime fails with
        # "dimension mismatch" and no log trail back to this fallback.
        print(
            f"# WARN dim probe failed for {model} ({type(e).__name__}: {e});"
            f" falling back to 768 (nomic-embed-text). Override via"
            f" LIGHTRAG_EMBEDDING_DIM if your model uses a different size.",
            file=sys.stderr,
        )
        return 768  # safe fallback for nomic-embed-text


def main() -> None:
    available = fetch_models()
    # LIGHTRAG_* are the service-specific overrides the manifest + README
    # document; they take precedence over the stack-wide LITELLM_* picks.
    # (They were declared-but-unread before — the "Override via …" advice
    # pointed at vars nothing consumed.)
    chat = (os.environ.get("LIGHTRAG_LLM_MODEL", "").strip()
            or os.environ.get("LITELLM_DEFAULT_MODEL", "").strip())
    embed = (os.environ.get("LIGHTRAG_EMBEDDING_MODEL", "").strip()
             or os.environ.get("LITELLM_EMBEDDING_MODEL", "").strip())
    if not chat and available:
        # LITELLM_DEFAULT_MODEL is empty in the stock .env, so this
        # fallback is the DEFAULT path. /v1/models sorts by provider/name
        # and the first entry is typically ollama/nomic-embed-text — an
        # embeddings-only route. Filter out embedding models and the
        # agent/self entries (hermes-agent answers via its own loop;
        # lightrag pointing at itself would loop) before picking.
        chat_candidates = [
            m for m in available
            if "embed" not in m.lower()
            and m not in ("hermes-agent", "lightrag")
        ]
        chat = chat_candidates[0] if chat_candidates else available[0]
    if not chat:
        print(
            "# ERROR could not resolve a chat model from LIGHTRAG_LLM_MODEL, "
            "LITELLM_DEFAULT_MODEL, or LiteLLM /v1/models",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if not embed:
        # Prefer anything with "embed" in the name
        embed_candidates = [m for m in available if "embed" in m.lower()]
        embed = embed_candidates[0] if embed_candidates else "ollama/nomic-embed-text"
    dim_override = os.environ.get("LIGHTRAG_EMBEDDING_DIM", "").strip()
    if dim_override.isdigit():
        dim = int(dim_override)
    else:
        if dim_override:
            print(
                f"# WARN LIGHTRAG_EMBEDDING_DIM={dim_override!r} is not an "
                f"integer; falling back to auto-probe.",
                file=sys.stderr,
            )
        dim = resolve_dim(embed)
    # Write the BARE env var names LightRAG reads (`LLM_MODEL`,
    # `EMBEDDING_MODEL`, `EMBEDDING_DIM`) — NOT the `LIGHTRAG_*` prefixed
    # versions. LightRAG's server reads these unprefixed names directly;
    # writing the prefixed names leaves them sitting unused in the env
    # while LightRAG falls back to internal defaults (caught 2026-06-07:
    # /health showed embedding_model=None despite prefixed vars being set).
    # The entrypoint `sh`-sources this file: quote user-controlled names.
    print(f"LLM_MODEL={shlex.quote(chat)}")
    print(f"EMBEDDING_MODEL={shlex.quote(embed)}")
    print(f"EMBEDDING_DIM={dim}")


if __name__ == "__main__":
    main()
