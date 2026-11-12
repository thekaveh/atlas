"""Provider keys can come from a file or stdin, so argv and shell history never hold them (#1381)."""

import io

import click
import pytest

import start


def test_value_flag_passes_through_unchanged():
    assert start._key_from_option("sk-test", None, "--openai-api-key") == "sk-test"
    assert start._key_from_option(None, None, "--openai-api-key") is None


def test_key_file_is_read_and_stripped(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("sk-from-file\n", encoding="utf-8")
    assert start._key_from_option(None, str(key_file), "--openai-api-key") == "sk-from-file"


def test_dash_reads_the_key_from_stdin(monkeypatch):
    monkeypatch.setattr(start.sys, "stdin", io.StringIO("sk-from-stdin\n"))
    assert start._key_from_option(None, "-", "--anthropic-api-key") == "sk-from-stdin"


def test_value_and_file_together_are_refused(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("sk-file", encoding="utf-8")
    with pytest.raises(click.UsageError, match="mutually exclusive"):
        start._key_from_option("sk-value", str(key_file), "--openrouter-api-key")


def test_empty_key_file_is_refused(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("  \n", encoding="utf-8")
    with pytest.raises(click.UsageError, match="is empty"):
        start._key_from_option(None, str(key_file), "--fal-api-key")


def test_terminal_stdin_is_read_without_echo(monkeypatch):
    """On a terminal the key is read with getpass, so it is not echoed (#1381 review)."""

    class FakeTerminal(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(start.sys, "stdin", FakeTerminal())
    monkeypatch.setattr(start.getpass, "getpass", lambda prompt="": "sk-typed")
    assert start._key_from_option(None, "-", "--fal-api-key") == "sk-typed"


@pytest.mark.parametrize("provider", ["openai", "anthropic", "openrouter", "fal"])
def test_key_file_options_reach_main_and_must_exist(provider):
    """The option is declared on the real command, is passed to main, and rejects a
    missing path at parse time instead of failing later."""
    params = {param.name: param for param in start.main.params}
    assert f"{provider}_api_key" in params
    file_param = params[f"{provider}_api_key_file"]
    assert file_param.opts == [f"--{provider}-api-key-file"]
    assert file_param.type.exists is True
    assert file_param.type.allow_dash is True
    import inspect
    callback_params = inspect.signature(start.main.callback).parameters
    assert f"{provider}_api_key_file" in callback_params
