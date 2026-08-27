"""Deployment-profile bundles (#755): declarative dev/prod environment sets.

`bootstrapper/profiles.yml` carries the platform bundles (`default`/`prod`;
`dev` aliases `default`); a consumer manifest may name its default profile
(`profile:`) and override bundle fields (`profile_overrides:` — override-only,
never new profile names). `apply_profile_overrides` applies the merged bundle
with the historical per-field disciplines (bind always-asserted under prod,
sources asserted unless an explicit flag this run, env values
unless-operator-set) plus marker-gated switch cleanup so transitions leave no
source residue while same-profile restarts never reset a wizard choice.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

import start  # noqa: E402
from services.host_capabilities import HostCapabilities  # noqa: E402
from services.profiles import (  # noqa: E402
    CANONICAL_PROFILES,
    ProfileBundle,
    ProfileConfigError,
    canonical_profile,
    load_profile_bundles,
    merge_consumer_profile_overrides,
)


# ── registry / loader ────────────────────────────────────────────────


def test_canonical_profile_aliases_and_defaults():
    assert canonical_profile("dev") == "default"
    assert canonical_profile("DEV") == "default"
    assert canonical_profile("prod") == "prod"
    assert canonical_profile(None) == "default"
    assert canonical_profile("") == "default"
    assert set(CANONICAL_PROFILES) == {"default", "prod"}


def test_shipped_bundles_express_legacy_behavior():
    bundles = load_profile_bundles()
    prod = bundles["prod"]
    assert prod.host_bind_ip == "127.0.0.1:"
    assert prod.sources == {"prometheus": "container", "grafana": "container"}
    assert prod.env == {"LOG_MAX_SIZE": "10m", "LOG_MAX_FILE": "3"}
    default = bundles["default"]
    assert default.host_bind_ip == ""
    assert default.sources == {} and default.env == {}


def test_loader_rejects_unknown_profile_and_field(tmp_path):
    bad_name = tmp_path / "p1.yml"
    bad_name.write_text("profiles:\n  staging: {}\n", encoding="utf-8")
    with pytest.raises(ProfileConfigError, match="unknown profile 'staging'"):
        load_profile_bundles(bad_name)
    bad_field = tmp_path / "p2.yml"
    bad_field.write_text("profiles:\n  prod:\n    limitz: {}\n", encoding="utf-8")
    with pytest.raises(ProfileConfigError, match="unknown field"):
        load_profile_bundles(bad_field)


def test_missing_profiles_file_yields_empty_bundles(tmp_path):
    bundles = load_profile_bundles(tmp_path / "absent.yml")
    assert set(bundles) == set(CANONICAL_PROFILES)
    assert all(b == ProfileBundle() for b in bundles.values())


def test_profile_bundles_reject_duplicate_yaml_mapping_keys(tmp_path):
    path = tmp_path / "profiles.yml"
    path.write_text(
        """
profiles:
  default:
    host_bind_ip: 127.0.0.1
    host_bind_ip: 0.0.0.0
  prod: {}
""".strip()
    )

    with pytest.raises(ProfileConfigError, match="duplicate key.*host_bind_ip"):
        load_profile_bundles(path)


def test_profile_bundles_wrap_unhashable_yaml_key(tmp_path):
    path = tmp_path / "profiles.yml"
    path.write_text("? [bad, key]\n: value\n", encoding="utf-8")

    with pytest.raises(ProfileConfigError, match="unhashable key"):
        load_profile_bundles(path)


def test_merge_consumer_overrides_override_only():
    bundles = load_profile_bundles()
    merged = merge_consumer_profile_overrides(
        bundles,
        {
            "dev": {"env": {"WEAVIATE_MEMORY_LIMIT": "4g"}},
            "prod": {"sources": {"comfyui": "container-gpu"}, "host_bind_ip": "10.0.0.1:"},
        },
    )
    assert merged["default"].env["WEAVIATE_MEMORY_LIMIT"] == "4g"  # dev → default
    assert merged["prod"].sources["comfyui"] == "container-gpu"
    assert merged["prod"].sources["prometheus"] == "container"  # platform kept
    assert merged["prod"].host_bind_ip == "10.0.0.1:"
    # inputs not mutated
    assert "WEAVIATE_MEMORY_LIMIT" not in bundles["default"].env
    with pytest.raises(ProfileConfigError, match="unknown profile"):
        merge_consumer_profile_overrides(bundles, {"staging": {}})


# ── applier (integration, tmp .env) ──────────────────────────────────


def _make_starter(tmp_path: Path, env_body: str) -> "start.AtlasStarter":
    (tmp_path / ".env").write_text(env_body, encoding="utf-8")
    (tmp_path / ".env.example").write_text("HOST_BIND_IP=\n", encoding="utf-8")
    starter = start.AtlasStarter()
    starter.config_parser.root_dir = tmp_path
    starter.config_parser.env_file_path = tmp_path / ".env"
    starter.config_parser.env_example_path = tmp_path / ".env.example"
    starter.source_override_manager.config_parser = starter.config_parser
    return starter


def _env(tmp_path: Path) -> dict:
    out = {}
    for line in (tmp_path / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            out[k] = v
    return out


def test_prod_then_default_switch_resets_asserted_sources(tmp_path):
    body = "HOST_BIND_IP=\nPROMETHEUS_SOURCE=disabled\nGRAFANA_SOURCE=disabled\n"
    s = _make_starter(tmp_path, body)
    assert s.apply_profile_overrides("prod") is True
    env = _env(tmp_path)
    assert env["PROMETHEUS_SOURCE"] == "container"
    assert env["HOST_BIND_IP"] == "127.0.0.1:"
    assert env["ATLAS_PROFILE_APPLIED"] == "prod"
    # Switch to default: prod's asserted sources reset to their service
    # defaults, bind sentinel cleared, marker updated — no residue.
    assert s.apply_profile_overrides("default") is True
    env = _env(tmp_path)
    assert env["HOST_BIND_IP"] == ""
    assert env["PROMETHEUS_SOURCE"] == "disabled"
    assert env["GRAFANA_SOURCE"] == "disabled"
    assert env["ATLAS_PROFILE_APPLIED"] == "default"


def test_same_profile_restart_keeps_operator_source_choice(tmp_path):
    """A wizard/operator selection equal to a bundle value must survive a
    SAME-profile restart (no marker change → no sentinel undo)."""
    body = (
        "HOST_BIND_IP=\nPROMETHEUS_SOURCE=container\nGRAFANA_SOURCE=disabled\n"
        "ATLAS_PROFILE_APPLIED=default\n"
    )
    s = _make_starter(tmp_path, body)
    assert s.apply_profile_overrides("default") is True
    env = _env(tmp_path)
    assert env["PROMETHEUS_SOURCE"] == "container"  # untouched


def test_explicit_flag_this_run_beats_profile_source(tmp_path):
    body = "HOST_BIND_IP=\nPROMETHEUS_SOURCE=disabled\nGRAFANA_SOURCE=disabled\n"
    s = _make_starter(tmp_path, body)
    ok = s.apply_profile_overrides("prod", explicit_prometheus="disabled")
    assert ok is True
    env = _env(tmp_path)
    assert env["PROMETHEUS_SOURCE"] == "disabled"  # operator wins
    assert env["GRAFANA_SOURCE"] == "container"  # unflagged one asserted


def test_explicit_source_vars_generalize_operator_wins(tmp_path):
    body = "HOST_BIND_IP=\nPROMETHEUS_SOURCE=disabled\nGRAFANA_SOURCE=disabled\n"
    s = _make_starter(tmp_path, body)
    s._explicit_source_vars = {"GRAFANA_SOURCE"}
    assert s.apply_profile_overrides("prod") is True
    env = _env(tmp_path)
    assert env["GRAFANA_SOURCE"] == "disabled"  # stashed explicit flag wins
    assert env["PROMETHEUS_SOURCE"] == "container"


def test_dev_alias_applies_default_bundle(tmp_path):
    body = "HOST_BIND_IP=127.0.0.1:\nATLAS_PROFILE_APPLIED=prod\n"
    s = _make_starter(tmp_path, body)
    assert s.apply_profile_overrides("dev") is True
    env = _env(tmp_path)
    assert env["HOST_BIND_IP"] == ""
    assert env["ATLAS_PROFILE_APPLIED"] == "default"


def test_idempotent_same_profile_reapply_is_byte_stable(tmp_path):
    body = "HOST_BIND_IP=\nPROMETHEUS_SOURCE=disabled\nGRAFANA_SOURCE=disabled\n"
    s = _make_starter(tmp_path, body)
    assert s.apply_profile_overrides("prod") is True
    once = (tmp_path / ".env").read_text(encoding="utf-8")
    assert s.apply_profile_overrides("prod") is True
    assert (tmp_path / ".env").read_text(encoding="utf-8") == once


def test_consumer_profile_overrides_reach_the_applier(tmp_path, monkeypatch):
    body = "HOST_BIND_IP=\nWEAVIATE_MEMORY_LIMIT=\n"
    s = _make_starter(tmp_path, body)
    monkeypatch.setattr(
        s.config_parser,
        "load_consumer_config",
        lambda: NS(
            profile="dev",
            profile_overrides={"dev": {"env": {"WEAVIATE_MEMORY_LIMIT": "4g"}}},
        ),
    )
    assert s.apply_profile_overrides("dev") is True
    assert _env(tmp_path)["WEAVIATE_MEMORY_LIMIT"] == "4g"


def test_profile_auto_source_delegates_to_753_resolver(tmp_path, monkeypatch):
    import services.host_capabilities as hc

    monkeypatch.setattr(
        hc,
        "probe_host_capabilities",
        lambda: HostCapabilities(
            os_name="Darwin", machine="arm64",
            apple_silicon=True, nvidia_gpu=False, host_ollama=False,
        ),
    )
    body = "HOST_BIND_IP=\nCOMFYUI_SOURCE=container-cpu\n"
    s = _make_starter(tmp_path, body)
    monkeypatch.setattr(
        s.config_parser,
        "load_consumer_config",
        lambda: NS(
            profile="dev",
            profile_overrides={"dev": {"sources": {"comfyui": "auto"}}},
        ),
    )
    # The resolver reads .env via the redirected config_parser; root_dir stays
    # the real repo so manifests load.
    assert s.apply_profile_overrides("dev") is True
    assert _env(tmp_path)["COMFYUI_SOURCE"] == "managed-localhost-mps"


def test_invalid_profile_source_id_fails(tmp_path, monkeypatch):
    body = "HOST_BIND_IP=\n"
    s = _make_starter(tmp_path, body)
    monkeypatch.setattr(
        s.config_parser,
        "load_consumer_config",
        lambda: NS(
            profile=None,
            profile_overrides={"prod": {"sources": {"comfyui": "bogus-id"}}},
        ),
    )
    assert s.apply_profile_overrides("prod") is False


# ── consumer manifest parsing ────────────────────────────────────────


def _write_manifest(tmp_path: Path, body: str) -> Path:
    manifest = tmp_path / "atlas.consumer.yml"
    manifest.write_text(body, encoding="utf-8")
    return manifest


def test_manifest_profile_and_overrides_parse(tmp_path):
    from core.consumer_manifest import load_consumer_config

    manifest = _write_manifest(
        tmp_path,
        "name: demo\nprofile: dev\n"
        "profile_overrides:\n  dev:\n    env:\n      WEAVIATE_MEMORY_LIMIT: 4g\n",
    )
    cfg = load_consumer_config(tmp_path, explicit_paths=[str(manifest)])
    assert cfg.profile == "default"  # dev normalized to canonical
    # The override bucket is canonicalized the same way `profile` is. Keying it
    # on the raw name instead let a `dev:` block and a `default:` block land in
    # separate buckets, so the parse-time conflict check never fired and
    # `merge_consumer_profile_overrides` — which canonicalizes anyway — applied
    # both to one bundle with dict order silently deciding the winner.
    assert "dev" not in cfg.profile_overrides
    assert cfg.profile_overrides["default"]["env"]["WEAVIATE_MEMORY_LIMIT"] == "4g"


def test_manifest_rejects_unknown_profile_and_override_names(tmp_path):
    from core.consumer_manifest import ConsumerManifestError, load_consumer_config

    m1 = _write_manifest(tmp_path, "name: demo\nprofile: staging\n")
    with pytest.raises(ConsumerManifestError, match="not a known deployment profile"):
        load_consumer_config(tmp_path, explicit_paths=[str(m1)])
    m2 = _write_manifest(
        tmp_path, "name: demo\nprofile_overrides:\n  staging: {}\n"
    )
    with pytest.raises(ConsumerManifestError, match="unknown profile"):
        load_consumer_config(tmp_path, explicit_paths=[str(m2)])


def test_manifest_profile_conflict_across_manifests(tmp_path):
    from core.consumer_manifest import ConsumerManifestError, load_consumer_config

    a = tmp_path / "a.yml"
    a.write_text("name: a\nprofile: dev\n", encoding="utf-8")
    b = tmp_path / "b.yml"
    b.write_text("name: b\nprofile: prod\n", encoding="utf-8")
    with pytest.raises(ConsumerManifestError, match="conflicting"):
        load_consumer_config(tmp_path, explicit_paths=[str(a), str(b)])


def test_profile_keys_in_top_level_allowlist():
    from core.consumer_manifest import _CONSUMER_ALLOWED_TOP_LEVEL_KEYS

    assert {"profile", "profile_overrides"} <= _CONSUMER_ALLOWED_TOP_LEVEL_KEYS


# ── doctor ───────────────────────────────────────────────────────────


def test_doctor_profile_reports_bundle_and_tiers():
    s = start.AtlasStarter.__new__(start.AtlasStarter)
    s.config_parser = NS(
        parse_env_file=lambda: {
            "HOST_BIND_IP": "127.0.0.1:",
            "PROMETHEUS_SOURCE": "container",
            "GRAFANA_SOURCE": "disabled",
            "LOG_MAX_SIZE": "50m",
            "ATLAS_PROFILE_APPLIED": "prod",
        },
        load_consumer_config=lambda: NS(profile="prod", profile_overrides={}),
    )
    s.root_dir = REPO_ROOT
    result = start._doctor_check_profile(s)
    assert result["status"] == "pass"
    fields = result["details"]["fields"]
    assert fields["HOST_BIND_IP"]["tier"] == "profile"
    assert fields["PROMETHEUS_SOURCE"]["tier"] == "profile"
    assert fields["GRAFANA_SOURCE"]["tier"] == "operator"
    assert fields["LOG_MAX_SIZE"]["tier"] == "operator"
    assert result["details"]["last_applied"] == "prod"


def test_doctor_profile_registered():
    assert start._doctor_check_profile in start.DOCTOR_CHECKS
