"""Tests for LiteLLMConfigGenerator.write_config() idempotency + preserve contract.

write_config() is invoked in production at every ``./start.sh`` launch
(start.py calls ``generator.write_config(config_path, force=True)``). It
implements a user-data-protection guarantee: re-running the launcher must NOT
blow away a real litellm-init-managed ``config.yaml`` (sentinel header +
non-empty ``model_list``) — the previous run's active model_list survives a
re-run that hasn't yet completed a ``docker compose up``. Until this test,
write_config() and the _is_litellm_init_managed() predicate — including the
Docker bind-mount empty-directory edge case — were 0%-covered.
"""
from __future__ import annotations

import pytest

from utils.litellm_config_generator import LiteLLMConfigGenerator


class _StubConfigParser:
    """parse_env_file() returning {} is tolerated by base_settings (all .get())."""

    def parse_env_file(self) -> dict:
        return {}


def _gen() -> LiteLLMConfigGenerator:
    return LiteLLMConfigGenerator(_StubConfigParser())


SENTINEL = LiteLLMConfigGenerator._LITELLM_INIT_SENTINEL


def _write_real_config(path) -> None:
    path.write_text(
        SENTINEL + "\n"
        "model_list:\n"
        "  - model_name: gpt-4o\n"
        "    litellm_params:\n"
        "      model: gpt-4o\n",
        encoding="utf-8",
    )


def _write_stub(path) -> None:
    path.write_text("# STUB\nmodel_list: []\n", encoding="utf-8")


# ── write_config matrix ────────────────────────────────────────────────────


def test_missing_file_written_force_false(tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    assert _gen().write_config(cfg, force=False) is True
    assert cfg.is_file()


def test_missing_file_written_force_true(tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    assert _gen().write_config(cfg, force=True) is True
    assert cfg.is_file()


def test_real_config_survives_force_true(tmp_path) -> None:
    """The headline guarantee: force=True must NOT clobber a real config."""
    cfg = tmp_path / "config.yaml"
    _write_real_config(cfg)
    assert _gen().write_config(cfg, force=True) is False
    assert "gpt-4o" in cfg.read_text(encoding="utf-8")


def test_real_config_preserved_force_false(tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    _write_real_config(cfg)
    assert _gen().write_config(cfg, force=False) is False


def test_stub_preserved_without_force(tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    _write_stub(cfg)
    assert _gen().write_config(cfg, force=False) is False


def test_stub_overwritten_with_force(tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    _write_stub(cfg)
    assert _gen().write_config(cfg, force=True) is True
    assert "STUB" in cfg.read_text(encoding="utf-8")


def test_empty_directory_is_cleared_and_written(tmp_path) -> None:
    """Docker bind-mount creates an empty dir when the file is absent at compose-up."""
    cfg = tmp_path / "config.yaml"
    cfg.mkdir()
    assert _gen().write_config(cfg, force=False) is True
    assert cfg.is_file()


def test_non_empty_directory_raises(tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.mkdir()
    (cfg / "junk").write_text("x", encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"non-empty directory"):
        _gen().write_config(cfg, force=False)


# ── _is_litellm_init_managed branch coverage ───────────────────────────────


def test_predicate_false_when_sentinel_absent(tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    _write_stub(cfg)
    assert LiteLLMConfigGenerator._is_litellm_init_managed(cfg) is False


def test_predicate_false_when_model_list_empty(tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(SENTINEL + "\nmodel_list: []\n", encoding="utf-8")
    assert LiteLLMConfigGenerator._is_litellm_init_managed(cfg) is False


def test_predicate_false_when_unparseable(tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(SENTINEL + "\n: : : not yaml", encoding="utf-8")
    assert LiteLLMConfigGenerator._is_litellm_init_managed(cfg) is False


def test_predicate_true_for_real_config(tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    _write_real_config(cfg)
    assert LiteLLMConfigGenerator._is_litellm_init_managed(cfg) is True
