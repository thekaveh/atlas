"""Tests for HF + civitai scrapers in comfyui_library.

Uses fixture JSON committed at bootstrapper/tests/fixtures/comfyui/ so
tests are hermetic. The live integration test (skip-if-no-network) lives
in test_live_comfyui_catalog.py (added later, separate task).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.comfyui_library import (
    ComfyUILibraryEntry,
    _parse_hf_response,
    _parse_civitai_response,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "comfyui"


def _load(name: str) -> list[dict]:
    return json.loads((FIXTURE_DIR / name).read_text())


def test_parse_hf_text2image():
    raw = _load("hf_text2image_search.json")
    entries = _parse_hf_response(raw, category="checkpoint")
    assert len(entries) >= 5, "fixture should have at least 5 checkpoint entries"
    for e in entries:
        assert isinstance(e, ComfyUILibraryEntry)
        assert e.category == "checkpoint"
        assert e.source == "huggingface"
        assert e.url.startswith("https://huggingface.co/")
        assert e.url.endswith((".safetensors", ".ckpt", ".bin", ".gguf"))
        assert e.target_dir == "checkpoints"


def test_parse_hf_text2video():
    raw = _load("hf_text2video_search.json")
    entries = _parse_hf_response(raw, category="video_model")
    assert len(entries) >= 3
    for e in entries:
        assert e.category == "video_model"
        assert e.target_dir == "checkpoints"  # upstream convention


def test_parse_hf_text2speech():
    raw = _load("hf_text2speech_search.json")
    entries = _parse_hf_response(raw, category="voice_model")
    for e in entries:
        assert e.category == "voice_model"
        assert e.target_dir == "voice"


def test_parse_hf_text2mesh():
    raw = _load("hf_text2mesh_search.json")
    entries = _parse_hf_response(raw, category="mesh_model")
    for e in entries:
        assert e.category == "mesh_model"
    # All 3D models today require a custom node — at least one entry
    # surfaces the badge so the wizard renders it.
    assert any(e.requires_custom_node for e in entries), \
        "at least one mesh_model fixture should require a custom node"


def test_parse_civitai_lora():
    raw = _load("civitai_models.json")
    entries = _parse_civitai_response(raw, category="lora")
    for e in entries:
        assert e.category == "lora"
        assert e.source == "civitai"
        assert e.target_dir == "loras"


def test_parse_civitai_skips_malformed_supplied_sha256_with_warning(capsys):
    raw = {
        "items": [{
            "id": 1,
            "modelVersions": [{
                "files": [{
                    "primary": True,
                    "name": "model.safetensors",
                    "downloadUrl": "https://civitai.com/api/download/models/1",
                    "hashes": {"SHA256": "not-a-digest"},
                }]
            }],
        }]
    }
    assert _parse_civitai_response(raw, category="checkpoint") == []
    assert "malformed SHA256" in capsys.readouterr().err


def test_parse_civitai_keeps_valid_sibling_when_one_digest_is_malformed(capsys):
    valid_sha = "AB" * 32
    raw = {
        "items": [
            {
                "id": 1,
                "modelVersions": [{"files": [{
                    "primary": True,
                    "name": "bad.safetensors",
                    "downloadUrl": "https://civitai.com/api/download/models/1",
                    "hashes": {"SHA256": "not-a-digest"},
                }]}],
            },
            {
                "id": 2,
                "modelVersions": [{"files": [{
                    "primary": True,
                    "name": "good.safetensors",
                    "downloadUrl": "https://civitai.com/api/download/models/2",
                    "hashes": {"SHA256": valid_sha},
                }]}],
            },
        ]
    }
    entries = _parse_civitai_response(raw, category="lora")
    assert [entry.name for entry in entries] == ["civitai-2"]
    assert entries[0].sha256 == valid_sha.lower()
    assert "malformed SHA256" in capsys.readouterr().err


def test_parse_hf_skips_malformed_entry():
    """A fixture with a missing siblings array shouldn't crash; skips entry."""
    raw = [{"id": "broken/model", "downloads": 100}]  # no siblings array
    entries = _parse_hf_response(raw, category="checkpoint")
    assert entries == []


def test_parse_hf_skips_entry_with_no_safetensors_or_compatible():
    """Models with only .ipynb / .txt files aren't useful — skip."""
    raw = [{
        "id": "fake/onlytext",
        "downloads": 1000,
        "siblings": [{"rfilename": "README.md"}, {"rfilename": "config.json"}],
    }]
    entries = _parse_hf_response(raw, category="checkpoint")
    assert entries == []


def test_parse_hf_unknown_category_raises():
    """Defense: parser must validate the category argument."""
    with pytest.raises(ValueError):
        _parse_hf_response([], category="not-a-category")


def test_parse_civitai_unknown_category_raises():
    with pytest.raises(ValueError):
        _parse_civitai_response({"items": []}, category="not-a-category")


# ─── Live-API request shape ───────────────────────────────────────────
# HF's /api/models endpoint returns lightweight model cards WITHOUT a
# ``siblings[]`` array unless the request asks for full metadata via
# ``full=true``. Without siblings, ``_pick_primary_file`` returns None
# for every entry and the entire HF tier of the catalog silently
# collapses to zero entries — which is exactly what was happening in
# production until the post-PR-#20 wizard screenshot exposed it.
# This test pins the request shape so the regression class can't recur.

def test_enrich_siblings_with_sizes_populates_size_via_per_repo_blobs(monkeypatch):
    """HF's list endpoint never returns sibling sizes (even with full=true).
    _enrich_siblings_with_sizes must fan out per-repo blobs=true fetches
    and copy real sizes into each item's siblings[] so _pick_primary_file
    can pick the largest file and _parse_hf_response can record size_gb."""
    from utils import comfyui_library

    items = [
        {"id": "fake/repoA",
         "siblings": [
             {"rfilename": "model.safetensors"},
             {"rfilename": "README.md"},
         ]},
        {"id": "fake/repoB",
         "siblings": [{"rfilename": "weights.bin"}]},
    ]
    per_repo = {
        "fake/repoA": [
            {"rfilename": "model.safetensors", "size": 5_368_709_120},  # 5 GB
            {"rfilename": "README.md", "size": 1024},
        ],
        "fake/repoB": [
            {"rfilename": "weights.bin", "size": 1_073_741_824},  # 1 GB
        ],
    }

    class _Resp:
        def __init__(self, j): self._j = j
        def raise_for_status(self): return None
        def json(self): return self._j

    def _fake_get(url, params=None, timeout=None):  # noqa: ARG001
        for repo_id, sibs in per_repo.items():
            if url.endswith(f"/api/models/{repo_id}"):
                return _Resp({"siblings": sibs})
        return _Resp([])

    monkeypatch.setattr(comfyui_library._requests, "get", _fake_get)
    comfyui_library._enrich_siblings_with_sizes(items)

    repoA = next(i for i in items if i["id"] == "fake/repoA")
    repoB = next(i for i in items if i["id"] == "fake/repoB")
    assert repoA["siblings"][0]["size"] == 5_368_709_120
    assert repoB["siblings"][0]["size"] == 1_073_741_824


def test_enrich_siblings_silent_on_per_repo_failure(monkeypatch):
    """One slow / 5xx repo must not block the catalog. The failing
    item's siblings keep their original sizeless shape; downstream
    _parse_hf_response records size_gb=0.0 for those entries."""
    from utils import comfyui_library
    import requests as _real_requests

    items = [
        {"id": "fake/ok",
         "siblings": [{"rfilename": "good.safetensors"}]},
        {"id": "fake/broken",
         "siblings": [{"rfilename": "x.safetensors"}]},
    ]

    class _Resp:
        def __init__(self, j, raise_exc=False):
            self._j, self._raise = j, raise_exc
        def raise_for_status(self):
            if self._raise:
                raise _real_requests.HTTPError("simulated 500")
        def json(self): return self._j

    def _fake_get(url, params=None, timeout=None):  # noqa: ARG001
        if url.endswith("/api/models/fake/ok"):
            return _Resp({"siblings": [{"rfilename": "good.safetensors", "size": 999}]})
        return _Resp({}, raise_exc=True)

    monkeypatch.setattr(comfyui_library._requests, "get", _fake_get)
    comfyui_library._enrich_siblings_with_sizes(items)

    ok = next(i for i in items if i["id"] == "fake/ok")
    broken = next(i for i in items if i["id"] == "fake/broken")
    assert ok["siblings"][0].get("size") == 999
    assert "size" not in broken["siblings"][0]


def test_list_huggingface_models_requests_full_metadata(monkeypatch):
    """list_huggingface_models() must pass ``full=true`` on every HF
    LIST call. Per-repo size-enrichment calls hit different URLs
    (``/api/models/{id}``) and are checked separately."""
    from utils import comfyui_library

    captured: list[tuple[str, dict]] = []  # (url, params)

    class _FakeResp:
        def __init__(self) -> None:
            self._json: list = []

        def raise_for_status(self) -> None:
            return None

        def json(self) -> list:
            return self._json

    def _fake_get(url, params=None, timeout=None):  # noqa: ARG001
        captured.append((url, dict(params or {})))
        return _FakeResp()

    monkeypatch.setattr(comfyui_library._requests, "get", _fake_get)
    comfyui_library.list_huggingface_models()

    # The list endpoint is exactly ``_HF_API_BASE``; per-repo enrichment
    # calls hit ``_HF_API_BASE/{owner}/{repo}``. Only the list calls must
    # carry full=true.
    list_calls = [(u, p) for u, p in captured if u == comfyui_library._HF_API_BASE]
    assert list_calls, "list_huggingface_models made zero list-endpoint calls"
    for url, params in list_calls:
        full = params.get("full")
        assert full in (True, "true", "True"), (
            f"HF list request missing `full=true`: url={url!r} params={params!r}. "
            f"Without it the siblings[] array is empty and _pick_primary_file "
            f"drops every entry."
        )
