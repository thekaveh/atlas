"""Durable, platform-adaptive ``<SVC>_SOURCE: auto`` (#753).

Manifest ``auto`` resolves once — via the service's declarative
``sources.auto_prefer`` matched against the shared host-capability probe — to
a concrete option id, persisted to ``.env`` and KEPT on later starts. A prior
concrete non-default value (an earlier resolution or an operator override) is
never clobbered; a cold ``.env`` regen re-resolves host-correctly instead of
silently squatting the service default. The source-selection analog of
``BASE_PORT: auto`` (#751, test_durable_base_port_auto.py).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace as NS

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

from services.host_capabilities import (  # noqa: E402
    KNOWN_CAPABILITIES,
    HostCapabilities,
    _probe_apple_silicon,
)


def _caps(**kw) -> HostCapabilities:
    base = dict(
        os_name="Linux", machine="x86_64",
        apple_silicon=False, nvidia_gpu=False, host_ollama=False,
    )
    base.update(kw)
    return HostCapabilities(**base)


class _Banner:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def show_status_message(self, message, level="info", *a, **k):
        self.messages.append((level, message))


def _starter(env, profile="default"):
    import start

    s = start.AtlasStarter.__new__(start.AtlasStarter)
    s.config_parser = NS(parse_env_file=lambda: dict(env))
    s.banner = _Banner()
    s.root_dir = REPO_ROOT
    s.profile = profile
    return s


def _patch_caps(monkeypatch, caps: HostCapabilities):
    import services.host_capabilities as hc

    monkeypatch.setattr(hc, "probe_host_capabilities", lambda: caps)


# ── host_capabilities ────────────────────────────────────────────────


def test_apple_silicon_probe_matrix():
    assert _probe_apple_silicon("Darwin", "arm64") is True
    assert _probe_apple_silicon("Darwin", "aarch64") is True
    assert _probe_apple_silicon("Darwin", "x86_64") is False
    assert _probe_apple_silicon("Linux", "arm64") is False


def test_has_unknown_capability_is_absent():
    caps = _caps(apple_silicon=True)
    assert caps.has("apple_silicon") is True
    assert caps.has("nvidia_gpu") is False
    assert caps.has("warp_drive") is False  # unknown → absent, safe fallback
    assert set(KNOWN_CAPABILITIES) == {"apple_silicon", "nvidia_gpu", "host_ollama"}


# ── resolver: platform-adaptive cold resolve ─────────────────────────


def test_cold_resolve_per_platform(monkeypatch):
    cases = [
        (_caps(apple_silicon=True), "managed-localhost-mps"),
        (_caps(nvidia_gpu=True), "container-gpu"),
        (_caps(), "container-cpu"),  # plain host → terminal fallback
    ]
    for caps, expected in cases:
        _patch_caps(monkeypatch, caps)
        s = _starter({"COMFYUI_SOURCE": "container-cpu"})  # cold: default in .env
        r = s._resolve_auto_source_overrides({"COMFYUI_SOURCE": "auto"})
        assert r["COMFYUI_SOURCE"] == expected, (caps, expected)


def test_ollama_host_capability_resolution(monkeypatch):
    _patch_caps(monkeypatch, _caps(host_ollama=True))
    s = _starter({"LLM_PROVIDER_SOURCE": "ollama-container-cpu"})
    r = s._resolve_auto_source_overrides({"LLM_PROVIDER_SOURCE": "auto"})
    assert r["LLM_PROVIDER_SOURCE"] == "ollama-localhost"

    _patch_caps(monkeypatch, _caps())
    s = _starter({"LLM_PROVIDER_SOURCE": "ollama-container-cpu"})
    r = s._resolve_auto_source_overrides({"LLM_PROVIDER_SOURCE": "auto"})
    assert r["LLM_PROVIDER_SOURCE"] == "ollama-container-cpu"


def test_resolution_message_names_capability(monkeypatch):
    _patch_caps(monkeypatch, _caps(apple_silicon=True))
    s = _starter({"COMFYUI_SOURCE": "container-cpu"})
    s._resolve_auto_source_overrides({"COMFYUI_SOURCE": "auto"})
    joined = " | ".join(m for _, m in s.banner.messages)
    assert "managed-localhost-mps" in joined and "apple_silicon" in joined


# ── resolver: durability ─────────────────────────────────────────────


def test_durable_keep_prior_resolution_and_operator_override(monkeypatch):
    # A concrete, valid, NON-default .env value is kept — even when the host
    # capability would resolve differently (never clobber).
    _patch_caps(monkeypatch, _caps(apple_silicon=True))
    s = _starter({"COMFYUI_SOURCE": "container-gpu"})  # operator's override
    r = s._resolve_auto_source_overrides({"COMFYUI_SOURCE": "auto"})
    assert r["COMFYUI_SOURCE"] == "container-gpu"


def test_idempotent_re_run_converges(monkeypatch):
    _patch_caps(monkeypatch, _caps(apple_silicon=True))
    s = _starter({"COMFYUI_SOURCE": "container-cpu"})
    first = s._resolve_auto_source_overrides({"COMFYUI_SOURCE": "auto"})
    assert first["COMFYUI_SOURCE"] == "managed-localhost-mps"
    # .env now carries the resolution; the next run is a durable-keep no-op.
    s2 = _starter({"COMFYUI_SOURCE": first["COMFYUI_SOURCE"]})
    second = s2._resolve_auto_source_overrides({"COMFYUI_SOURCE": "auto"})
    assert second["COMFYUI_SOURCE"] == first["COMFYUI_SOURCE"]


def test_invalid_env_value_re_resolves(monkeypatch):
    # Garbage in .env (not a valid option id) must not be "kept".
    _patch_caps(monkeypatch, _caps(apple_silicon=True))
    s = _starter({"COMFYUI_SOURCE": "totally-bogus"})
    r = s._resolve_auto_source_overrides({"COMFYUI_SOURCE": "auto"})
    assert r["COMFYUI_SOURCE"] == "managed-localhost-mps"


# ── resolver: profile awareness ──────────────────────────────────────


def test_durable_dev_only_value_re_resolves_under_prod(monkeypatch):
    """C1 hardening: a durably-kept dev-only value (managed-localhost-mps)
    must NOT survive into --profile prod — keeping it would fail prod source
    validation on every start until a hand-edit. Re-resolve instead."""
    _patch_caps(monkeypatch, _caps(apple_silicon=True))
    s = _starter({"COMFYUI_SOURCE": "managed-localhost-mps"}, profile="prod")
    r = s._resolve_auto_source_overrides({"COMFYUI_SOURCE": "auto"})
    assert r["COMFYUI_SOURCE"] == "container-cpu"  # prod-eligible fallback
    # ...and under default the same durable value IS kept (no behavior change).
    s2 = _starter({"COMFYUI_SOURCE": "managed-localhost-mps"}, profile="default")
    r2 = s2._resolve_auto_source_overrides({"COMFYUI_SOURCE": "auto"})
    assert r2["COMFYUI_SOURCE"] == "managed-localhost-mps"


def test_profile_prod_excludes_dev_only_options(monkeypatch):
    # managed-localhost-mps is profiles:[default] (dev-only): under prod an
    # Apple-Silicon host must fall through to the terminal container fallback.
    _patch_caps(monkeypatch, _caps(apple_silicon=True))
    s = _starter({"COMFYUI_SOURCE": "container-cpu"}, profile="prod")
    r = s._resolve_auto_source_overrides({"COMFYUI_SOURCE": "auto"})
    assert r["COMFYUI_SOURCE"] == "container-cpu"


# ── resolver: fallbacks and passthrough ──────────────────────────────


def test_service_without_auto_prefer_falls_back_to_default(monkeypatch):
    # weaviate declares no auto_prefer → default + warning, not a crash.
    _patch_caps(monkeypatch, _caps())
    env = {"WEAVIATE_SOURCE": "container"}
    s = _starter(env)
    r = s._resolve_auto_source_overrides({"WEAVIATE_SOURCE": "auto"})
    from services.manifests import load_manifests

    weaviate = next(
        m for m in load_manifests(REPO_ROOT / "services") if m.name == "weaviate"
    )
    assert r["WEAVIATE_SOURCE"] == weaviate.sources.default
    assert any(level == "warning" for level, _ in s.banner.messages)


def test_unknown_var_left_as_is_with_warning(monkeypatch):
    _patch_caps(monkeypatch, _caps())
    s = _starter({})
    r = s._resolve_auto_source_overrides({"FOO_SOURCE": "auto"})
    assert r["FOO_SOURCE"] == "auto"  # validator will reject it loudly
    assert any(level == "warning" for level, _ in s.banner.messages)


def test_non_auto_and_base_port_pass_through(monkeypatch):
    _patch_caps(monkeypatch, _caps(apple_silicon=True))
    s = _starter({"COMFYUI_SOURCE": "container-cpu"})
    r = s._resolve_auto_source_overrides(
        {"COMFYUI_SOURCE": "disabled", "BASE_PORT": "auto", "PROJECT_NAME": "p"}
    )
    assert r["COMFYUI_SOURCE"] == "disabled"  # explicit value untouched
    assert r["BASE_PORT"] == "auto"  # delegated to the base-port resolver
    assert r["PROJECT_NAME"] == "p"
    # No-auto input short-circuits without loading manifests at all.
    r2 = s._resolve_auto_source_overrides({"COMFYUI_SOURCE": "disabled"})
    assert r2 == {"COMFYUI_SOURCE": "disabled"}


def test_quiet_mode_emits_no_banner(monkeypatch):
    _patch_caps(monkeypatch, _caps(apple_silicon=True))
    s = _starter({"COMFYUI_SOURCE": "container-cpu"})
    r = s._resolve_auto_source_overrides({"COMFYUI_SOURCE": "auto"}, quiet=True)
    assert r["COMFYUI_SOURCE"] == "managed-localhost-mps"
    assert s.banner.messages == []


# ── manifest lint ────────────────────────────────────────────────────


def _mk_manifest(auto_prefer, options=("container-cpu", "container-gpu")):
    from services.manifests import (
        AutoPreference,
        Manifest,
        SourceOption,
        SourcesBlock,
    )

    return Manifest(
        name="fake",
        label="Fake",
        category="apps",
        env=[],
        containers=[],
        sources=SourcesBlock(
            var="FAKE_SOURCE",
            default="container-cpu",
            options=[SourceOption(id=o, label=o) for o in options],
            auto_prefer=[AutoPreference(**p) for p in auto_prefer],
        ),
    )


def test_lint_flags_unknown_option_capability_and_missing_fallback():
    from services.manifest_validator import _check_auto_prefer_integrity

    issues = _check_auto_prefer_integrity(
        [
            _mk_manifest(
                [
                    {"id": "not-an-option", "requires_capability": "apple_silicon"},
                    {"id": "container-gpu", "requires_capability": "warp_drive"},
                ]
            )
        ]
    )
    kinds = sorted(i.kind for i in issues)
    assert kinds == [
        "auto_prefer_no_fallback",
        "auto_prefer_unknown_capability",
        "auto_prefer_unknown_option",
    ]


def test_lint_passes_real_manifests_and_valid_fake():
    from services.manifests import load_manifests
    from services.manifest_validator import _check_auto_prefer_integrity

    assert _check_auto_prefer_integrity(load_manifests(REPO_ROOT / "services")) == []
    assert (
        _check_auto_prefer_integrity(
            [
                _mk_manifest(
                    [
                        {"id": "container-gpu", "requires_capability": "nvidia_gpu"},
                        {"id": "container-cpu"},
                    ]
                )
            ]
        )
        == []
    )


def test_lint_rejects_unconditional_fallback_before_terminal_position():
    from services.manifest_validator import _check_auto_prefer_integrity

    issues = _check_auto_prefer_integrity(
        [
            _mk_manifest(
                [
                    {"id": "container-cpu"},
                    {"id": "container-gpu", "requires_capability": "nvidia_gpu"},
                ]
            )
        ]
    )
    assert [issue.kind for issue in issues] == ["auto_prefer_fallback_not_terminal"]


# ── doctor ───────────────────────────────────────────────────────────


def test_doctor_reports_resolution_and_capability():
    import start

    s = start.AtlasStarter.__new__(start.AtlasStarter)
    s.config_parser = NS(
        parse_env_file=lambda: {"COMFYUI_SOURCE": "managed-localhost-mps"},
        load_consumer_config=lambda: NS(env_overrides={"COMFYUI_SOURCE": "auto"}),
    )
    s.root_dir = REPO_ROOT
    result = start._doctor_check_auto_sources(s)
    assert result["status"] == "pass"
    assert "managed-localhost-mps" in result["message"]
    assert "apple_silicon" in result["message"]
    assert result["details"]["COMFYUI_SOURCE"]["resolved"] == "managed-localhost-mps"


def test_doctor_warns_on_unresolved_and_passes_when_none_declared():
    import start

    s = start.AtlasStarter.__new__(start.AtlasStarter)
    s.config_parser = NS(
        parse_env_file=lambda: {"COMFYUI_SOURCE": "auto"},
        load_consumer_config=lambda: NS(env_overrides={"COMFYUI_SOURCE": "auto"}),
    )
    s.root_dir = REPO_ROOT
    assert start._doctor_check_auto_sources(s)["status"] == "warn"

    s2 = start.AtlasStarter.__new__(start.AtlasStarter)
    s2.config_parser = NS(
        parse_env_file=lambda: {},
        load_consumer_config=lambda: NS(env_overrides={}),
    )
    s2.root_dir = REPO_ROOT
    assert start._doctor_check_auto_sources(s2)["status"] == "pass"


def test_auto_sources_check_registered_in_doctor():
    import start

    assert start._doctor_check_auto_sources in start.DOCTOR_CHECKS
