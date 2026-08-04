import feature_flags
from datetime import date


def test_splash_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ATLAS_SPLASH", raising=False)
    assert feature_flags.splash_enabled() is False


def test_splash_env_enables(monkeypatch):
    monkeypatch.setenv("ATLAS_SPLASH", "1")
    assert feature_flags.splash_enabled() is True
    monkeypatch.setenv("ATLAS_SPLASH", "true")
    assert feature_flags.splash_enabled() is True


def test_splash_env_falsey_disables(monkeypatch):
    for v in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("ATLAS_SPLASH", v)
        assert feature_flags.splash_enabled() is False


def test_default_constant_drives_unset_env(monkeypatch):
    # When ATLAS_SPLASH is unset, the compiled-in default decides.
    monkeypatch.delenv("ATLAS_SPLASH", raising=False)
    monkeypatch.setattr(feature_flags, "_SPLASH_DEFAULT", True)
    assert feature_flags.splash_enabled() is True
    monkeypatch.setattr(feature_flags, "_SPLASH_DEFAULT", False)
    assert feature_flags.splash_enabled() is False


def test_temporary_splash_flag_has_lifecycle_metadata():
    lifecycle = feature_flags.SPLASH_FLAG_LIFECYCLE

    assert lifecycle["owner"]
    assert date.fromisoformat(lifecycle["introduced"])
    assert date.fromisoformat(lifecycle["review_by"])
    assert lifecycle["decision"]
