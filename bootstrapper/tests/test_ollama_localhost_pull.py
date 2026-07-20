"""Host-side Ollama model provisioning for ``ollama-localhost`` (#757).

The host analog of the ``ollama-pull`` init container: same declared union
(OLLAMA_USER_MODELS ∪ OLLAMA_CUSTOM_MODELS — so ``model_sidecars.ollama``
provisions identically across sources), same ``POST /api/pull`` mechanism,
same non-fatal per-tag philosophy. All network is mocked — hermetic on CI.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

import services.ollama_localhost as ol  # noqa: E402


# ── declared set + base url ──────────────────────────────────────────


def test_declared_models_union_order_dedup():
    env = {
        "OLLAMA_USER_MODELS": "qwen3.6:latest, nomic-embed-text",
        "OLLAMA_CUSTOM_MODELS": "ornith:35b,qwen3.6:latest, ,",
    }
    assert ol.declared_models(env) == [
        "qwen3.6:latest", "nomic-embed-text", "ornith:35b",
    ]
    assert ol.declared_models({}) == []


def test_host_base_url_default_and_override():
    assert ol.host_base_url({}) == "http://localhost:11434"
    assert ol.host_base_url({"OLLAMA_LOCALHOST_PORT": "21434"}) == "http://localhost:21434"


# ── /api/tags listing ────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._buf = io.BytesIO(payload)

    def read(self):
        return self._buf.read()

    def __iter__(self):
        return iter(self._buf.readlines())

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_list_host_tags_normalizes_and_handles_unreachable(monkeypatch):
    payload = json.dumps(
        {"models": [{"name": "ornith:35b"}, {"model": "nomic-embed-text"}]}
    ).encode()
    monkeypatch.setattr(
        ol.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(payload)
    )
    tags = ol.list_host_tags("http://localhost:11434")
    assert tags == {"ornith:35b", "nomic-embed-text:latest"}  # bare → :latest

    def boom(*a, **k):
        raise ol.urllib.error.URLError("connection refused")

    monkeypatch.setattr(ol.urllib.request, "urlopen", boom)
    assert ol.list_host_tags("http://localhost:11434") is None


# ── pull streaming ───────────────────────────────────────────────────


def test_pull_one_streams_status_transitions_and_raises_on_error(monkeypatch):
    lines = b"\n".join(
        json.dumps(e).encode()
        for e in (
            {"status": "pulling manifest"},
            {"status": "downloading", "completed": 1},
            {"status": "downloading", "completed": 2},  # duplicate status: coalesced
            {"status": "success"},
        )
    )
    monkeypatch.setattr(
        ol.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(lines)
    )
    logs: list[str] = []
    ol._pull_one("http://localhost:11434", "ornith:35b", log=logs.append)
    assert [l for l in logs if "downloading" in l] == ["  ornith:35b: downloading"]
    assert any("success" in l for l in logs)

    err = json.dumps({"error": "pull model manifest: file does not exist"}).encode()
    monkeypatch.setattr(
        ol.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(err)
    )
    try:
        ol._pull_one("http://localhost:11434", "typo:1b", log=logs.append)
    except RuntimeError as exc:
        assert "does not exist" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("error event must raise")


# ── orchestration ────────────────────────────────────────────────────


def _env(**extra):
    env = {"OLLAMA_USER_MODELS": "present:1b", "OLLAMA_CUSTOM_MODELS": "missing:2b"}
    env.update(extra)
    return env


def test_pull_declared_skips_present_pulls_missing(monkeypatch):
    monkeypatch.setattr(ol, "list_host_tags", lambda base_url, **k: {"present:1b"})
    pulled: list[str] = []
    monkeypatch.setattr(
        ol, "_pull_one", lambda base_url, tag, *, log, **k: pulled.append(tag)
    )
    result = ol.pull_declared_models(_env())
    assert result.ok
    assert result.skipped == ["present:1b"]
    assert result.pulled == ["missing:2b"] and pulled == ["missing:2b"]


def test_pull_declared_isolates_per_tag_failures(monkeypatch):
    monkeypatch.setattr(ol, "list_host_tags", lambda base_url, **k: set())

    def flaky(base_url, tag, *, log, **k):
        if tag == "present:1b":
            raise RuntimeError("boom")

    monkeypatch.setattr(ol, "_pull_one", flaky)
    result = ol.pull_declared_models(_env())
    assert not result.ok
    assert result.failed and "present:1b: boom" in result.failed[0]
    assert result.pulled == ["missing:2b"]  # later tag unaffected


def test_pull_declared_unreachable_short_circuits(monkeypatch):
    monkeypatch.setattr(ol, "list_host_tags", lambda base_url, **k: None)
    result = ol.pull_declared_models(_env())
    assert not result.reachable and not result.ok
    assert not result.pulled and not result.skipped and not result.failed


def test_pull_declared_nothing_declared_is_noop(monkeypatch):
    def explode(*a, **k):  # pragma: no cover - must not be called
        raise AssertionError("no network on empty declaration")

    monkeypatch.setattr(ol, "list_host_tags", explode)
    result = ol.pull_declared_models({})
    assert result.ok and not result.pulled and not result.skipped


def test_bare_declared_tag_matches_latest_on_host(monkeypatch):
    monkeypatch.setattr(
        ol, "list_host_tags", lambda base_url, **k: {"nomic-embed-text:latest"}
    )
    monkeypatch.setattr(
        ol, "_pull_one",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should skip")),
    )
    result = ol.pull_declared_models(
        {"OLLAMA_USER_MODELS": "nomic-embed-text", "OLLAMA_CUSTOM_MODELS": ""}
    )
    assert result.skipped == ["nomic-embed-text"]


# ── finalize hook gating ─────────────────────────────────────────────


class _Banner:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def show_status_message(self, message, level="info", *a, **k):
        self.messages.append((level, message))


def _starter(env):
    import start

    s = start.AtlasStarter.__new__(start.AtlasStarter)
    s.config_parser = NS(parse_env_file=lambda: dict(env))
    s.banner = _Banner()
    return s


def test_finalize_noop_for_container_source_and_empty_declaration(monkeypatch):
    def explode(*a, **k):  # pragma: no cover
        raise AssertionError("must not pull")

    monkeypatch.setattr(ol, "pull_declared_models", explode)
    s = _starter({"LLM_PROVIDER_SOURCE": "ollama-container-cpu",
                  "OLLAMA_USER_MODELS": "x:1b"})
    s._finalize_ollama_localhost_models()
    assert s.banner.messages == []
    s = _starter({"LLM_PROVIDER_SOURCE": "ollama-localhost"})
    s._finalize_ollama_localhost_models()
    assert s.banner.messages == []


def test_finalize_pulls_and_reports(monkeypatch):
    monkeypatch.setattr(
        ol, "pull_declared_models",
        lambda env, log=None: ol.OllamaPullResult(pulled=["a:1b"], skipped=["b:2b"]),
    )
    s = _starter({"LLM_PROVIDER_SOURCE": "ollama-localhost",
                  "OLLAMA_USER_MODELS": "a:1b,b:2b"})
    s._finalize_ollama_localhost_models()
    assert any("declared model tag(s)" in m for _, m in s.banner.messages)
    assert not any(level == "warning" for level, _ in s.banner.messages)


def test_finalize_warns_on_unreachable_and_failures(monkeypatch):
    monkeypatch.setattr(
        ol, "pull_declared_models",
        lambda env, log=None: ol.OllamaPullResult(reachable=False),
    )
    s = _starter({"LLM_PROVIDER_SOURCE": "ollama-localhost",
                  "OLLAMA_USER_MODELS": "a:1b"})
    s._finalize_ollama_localhost_models()
    assert any("ollama serve" in m for level, m in s.banner.messages if level == "warning")

    monkeypatch.setattr(
        ol, "pull_declared_models",
        lambda env, log=None: ol.OllamaPullResult(failed=["a:1b: boom"]),
    )
    s = _starter({"LLM_PROVIDER_SOURCE": "ollama-localhost",
                  "OLLAMA_USER_MODELS": "a:1b"})
    s._finalize_ollama_localhost_models()
    warnings = [m for level, m in s.banner.messages if level == "warning"]
    assert any("boom" in m for m in warnings)
    assert any("stack starts anyway" in m for m in warnings)
