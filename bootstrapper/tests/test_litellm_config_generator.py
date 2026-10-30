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


def test_a_read_only_init_config_with_an_empty_model_list_is_replaced(tmp_path) -> None:
    """litellm-init runs as root, so on rootful Linux Docker its config is
    root-owned; the in-place rewrite of an init config with model_list: []
    raised EACCES and aborted the start (2026-10-08 run, cycle 14)."""
    import os
    import stat

    cfg = tmp_path / "config.yaml"
    cfg.write_text(SENTINEL + "\nmodel_list: []\n", encoding="utf-8")
    cfg.chmod(0o444)
    try:
        assert _gen().write_config(cfg, force=True) is True
        assert cfg.read_text(encoding="utf-8").startswith("# STUB")
        assert stat.S_IMODE(os.stat(cfg).st_mode) == 0o644
    finally:
        cfg.chmod(0o644)


def _rendered_retention_fixture(tmp_path, monkeypatch):
    import start

    from types import SimpleNamespace
    from tests.test_cold_cleanup_scope import _stack, _stack_root

    env, catalog, stack = _stack()
    env["COMFYUI_SOURCE"] = "disabled"
    root = _stack_root(tmp_path)
    monkeypatch.setattr(start, "_exec", lambda project, service, *args: stack.run(
        ["docker", "exec", f"{project}-{service}", *args]))
    monkeypatch.setattr(start, "_storage_context", lambda: (env, root, "atlas"))
    rendered = root / "volumes/litellm/config.yaml"
    valid = {"model_list": [{"litellm_params": {"model": "ollama/" + catalog[1]}},
                            {"litellm_params": {"model": "ollama_chat/someone/custom:7b"}}]}
    return SimpleNamespace(env=env, root=root, rendered=rendered, valid=valid, stack=stack,
                           selected=catalog[0], routed=catalog[1], custom="someone/custom:7b")


def _assert_rendered_route_models_retained(fixture):
    import start

    labels = {item["path"]: item["label"] for item in start._live_model_items(
        fixture.env, fixture.root, "atlas")}
    assert labels[fixture.routed] == labels[fixture.custom] == "retained"


def _change_rendered_retention_case(fixture, case, monkeypatch):
    from pathlib import Path
    import yaml

    if case == "malformed-yaml":
        fixture.rendered.write_text("model_list: [SENTINEL_CONFIG_VALUE\n", encoding="utf-8")
    elif case == "wrong-root":
        fixture.rendered.write_text("- SENTINEL_CONFIG_VALUE\n", encoding="utf-8")
    elif case == "invalid-later-row":
        fixture.rendered.write_text(yaml.safe_dump({"model_list": fixture.valid["model_list"] + [None]}))
    elif case == "read-failure":
        original_read = Path.read_text
        def refuse(path, *args, **kwargs):
            if path == fixture.rendered:
                raise PermissionError("SENTINEL_CONFIG_VALUE")
            return original_read(path, *args, **kwargs)
        monkeypatch.setattr(Path, "read_text", refuse)
    elif case == "valid-empty":
        fixture.rendered.write_text("model_list: []\n", encoding="utf-8")


def _assert_storage_retention_removals(fixture, before, expected, result):
    assert set(before) - set(fixture.stack.ollama) == expected, result.output
    assert fixture.selected in fixture.stack.ollama
    assert result.exit_code == 0, result.output
    assert "SENTINEL_CONFIG_VALUE" not in result.output


@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("case", ["malformed-yaml", "wrong-root", "invalid-later-row", "read-failure",
                                  "valid-routes", "valid-empty", "missing-first-start"])
def test_storage_clean_refuses_unknown_existing_rendered_retention(tmp_path, monkeypatch, case, named):
    import start
    import yaml
    from click.testing import CliRunner

    fixture = _rendered_retention_fixture(tmp_path, monkeypatch)
    if case != "missing-first-start":
        fixture.rendered.parent.mkdir(parents=True)
        fixture.rendered.write_text(yaml.safe_dump(fixture.valid), encoding="utf-8")
    if case not in {"valid-empty", "missing-first-start"}:
        _assert_rendered_route_models_retained(fixture)
    _change_rendered_retention_case(fixture, case, monkeypatch)
    before = dict(fixture.stack.ollama)
    args = ["storage", "clean", "--yes"] + (["--name", fixture.custom] if named else [])
    result = CliRunner().invoke(start.main, args)
    allowed = {fixture.routed, fixture.custom} if named else {fixture.routed}
    expected = allowed if case in {"valid-empty", "missing-first-start"} else set()
    _assert_storage_retention_removals(fixture, before, expected, result)
    if case in {"malformed-yaml", "wrong-root", "invalid-later-row", "read-failure"}:
        with pytest.raises(OSError) as error:
            start._rendered_litellm_models(fixture.root)
        assert error.value.__suppress_context__
        assert "SENTINEL_CONFIG_VALUE" not in str(error.value)
        assert "Ollama model retention is unavailable" in result.output


def test_unknown_ollama_retention_keeps_independent_comfyui_cleanup(tmp_path, monkeypatch):
    import start
    from click.testing import CliRunner

    fixture = _rendered_retention_fixture(tmp_path, monkeypatch)
    fixture.env["COMFYUI_SOURCE"] = "container-cpu"
    fixture.rendered.parent.mkdir(parents=True)
    fixture.rendered.write_text("model_list: [\n", encoding="utf-8")
    before = dict(fixture.stack.ollama)
    result = CliRunner().invoke(start.main, ["storage", "clean", "--yes", "--name", "loras/mystery.safetensors"])
    assert result.exit_code == 0, result.output
    assert fixture.stack.ollama == before
    assert "loras/mystery.safetensors" not in fixture.stack.comfyui
    assert "checkpoints/v1-5-pruned-emaonly.safetensors" in fixture.stack.comfyui
