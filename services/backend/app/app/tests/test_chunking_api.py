from __future__ import annotations

import importlib
import os
import sys


def _stub_required_env(monkeypatch):
    for var, default in (
        ("KONG_URL", "http://kong-api-gateway:8000"),
        ("SUPABASE_SERVICE_KEY", "dummy-key"),
        ("DATABASE_URL", "postgresql://x:x@localhost/x"),
    ):
        if not os.environ.get(var):
            monkeypatch.setenv(var, default)


def _fresh_main(monkeypatch):
    _stub_required_env(monkeypatch)
    sys.modules.pop("main", None)
    return importlib.import_module("main")


def test_chunk_endpoint_returns_structured_chunks(monkeypatch):
    main = _fresh_main(monkeypatch)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/api/chunk",
        json={
            "text": "Atlas chunks text for retrieval. It returns stable offsets.",
            "strategy": "token",
            "chunk_size": 6,
            "overlap": 1,
            "tokenizer": "gpt2",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["strategy"] == "token"
    assert body["chunk_count"] >= 1
    assert body["chunks"][0]["index"] == 0
    assert body["chunks"][0]["start_char"] == 0
    assert body["chunks"][0]["end_char"] > 0
    assert body["chunks"][0]["content"]
    assert "token_count" in body["chunks"][0]
    assert body["metadata"]["tokenizer"] == "gpt2"


def test_chunk_endpoint_rejects_invalid_payload(monkeypatch):
    main = _fresh_main(monkeypatch)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/api/chunk",
        json={"text": "", "strategy": "token", "chunk_size": 0, "overlap": -1},
    )

    assert response.status_code == 422
