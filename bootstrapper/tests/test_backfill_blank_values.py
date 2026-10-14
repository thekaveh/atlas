"""Regression: backfill must fill blank values from .env.example.

When the manifest's secret-default policy changes (e.g.,
SUPABASE_DB_PASSWORD goes from `secret-blank` to
`default: password`), users with a pre-existing .env retain the
blank value. The stack then fails to start because supabase-db-init
can't authenticate.

The fix: backfill scans for `KEY=` (blank) entries and fills them
from the .env.example's non-blank value, preserving intentional
autogen blanks (those have an empty value in .env.example too).
"""

from __future__ import annotations

from pathlib import Path


def _make_starter(tmp_path: Path, env_body: str, example_body: str):
    """Build a AtlasStarter pointing at a synthetic tmp_path repo.

    The starter caches its env paths via ConfigParser at construction;
    we patch them post-construction to point at tmp_path so tests
    don't touch the real .env at repo root.
    """
    (tmp_path / ".env").write_text(env_body)
    (tmp_path / ".env.example").write_text(example_body)
    from start import AtlasStarter

    starter = AtlasStarter()
    starter.config_parser.root_dir = tmp_path
    starter.config_parser.env_file_path = tmp_path / ".env"
    starter.config_parser.env_example_path = tmp_path / ".env.example"
    return starter


def test_backfill_fills_blank_from_example_default(tmp_path):
    """Blank existing value gets filled from non-blank example value."""
    env_body = "SUPABASE_DB_PASSWORD=\nLITELLM_MASTER_KEY=\n"
    example_body = (
        "SUPABASE_DB_PASSWORD=password\n"  # placeholder
        "LITELLM_MASTER_KEY=\n"            # intentional autogen blank
    )
    starter = _make_starter(tmp_path, env_body, example_body)
    assert starter.backfill_missing_env_vars()
    out = (tmp_path / ".env").read_text()
    assert "SUPABASE_DB_PASSWORD=password" in out
    # LITELLM_MASTER_KEY stays blank — both sides are intentionally empty.
    assert "LITELLM_MASTER_KEY=password" not in out
    assert "LITELLM_MASTER_KEY=" in out


def test_backfill_preserves_existing_non_blank_values(tmp_path):
    """User-customized values are NEVER overwritten."""
    env_body = "SUPABASE_DB_PASSWORD=my-custom-secret\n"
    example_body = "SUPABASE_DB_PASSWORD=password\n"
    starter = _make_starter(tmp_path, env_body, example_body)
    assert starter.backfill_missing_env_vars()
    out = (tmp_path / ".env").read_text()
    assert "SUPABASE_DB_PASSWORD=my-custom-secret" in out
    assert "SUPABASE_DB_PASSWORD=password" not in out


def test_backfill_upgrades_legacy_blank_host_bind_to_loopback(tmp_path):
    starter = _make_starter(
        tmp_path,
        "HOST_BIND_IP=\n",
        "HOST_BIND_IP=127.0.0.1:\n",
    )

    assert starter.backfill_missing_env_vars()
    assert (tmp_path / ".env").read_text() == "HOST_BIND_IP=127.0.0.1:\n"


def test_backfill_handles_inline_comments(tmp_path):
    """A quoted empty value with a comment remains eligible for backfill."""
    env_body = "SUPABASE_DB_PASSWORD='' # autogen at runtime\n"
    example_body = "SUPABASE_DB_PASSWORD=password\n"
    starter = _make_starter(tmp_path, env_body, example_body)
    assert starter.backfill_missing_env_vars()
    out = (tmp_path / ".env").read_text()
    # Value is now `password` — the prior quoted empty was a canonical blank.
    assert "SUPABASE_DB_PASSWORD=password" in out


def test_backfill_does_not_undo_a_deliberate_deselect_all(tmp_path):
    """A blank multi-select is an answer, not a missing value.

    Deselecting every Ollama model in the wizard writes
    `OLLAMA_USER_MODELS=`. `.env.example` ships a non-empty default, so the
    blank-backfill would restore it on the same run — and `ollama-pull` would
    then download several GB the user had just declined.
    """
    env_body = (
        "OLLAMA_USER_MODELS=\n"
        "WEAVIATE_ENABLE_MODULES=\n"
        "N8N_INIT_NODES=\n"
        "SUPABASE_DB_PASSWORD=\n"
    )
    example_body = (
        "OLLAMA_USER_MODELS=qwen3.8:latest,nomic-embed-text\n"
        "WEAVIATE_ENABLE_MODULES=text2vec-ollama,generative-ollama\n"
        "N8N_INIT_NODES=n8n-nodes-comfyui@0.0.9\n"
        "SUPABASE_DB_PASSWORD=password\n"
    )
    starter = _make_starter(tmp_path, env_body, example_body)
    assert starter.backfill_missing_env_vars()

    body = (tmp_path / ".env").read_text()
    assert "OLLAMA_USER_MODELS=\n" in body, "deselect-all was silently reverted"
    assert "N8N_INIT_NODES=\n" in body
    # ...while a genuinely missing value is still repaired.
    assert "SUPABASE_DB_PASSWORD=password\n" in body
    # WEAVIATE_ENABLE_MODULES is deliberately NOT exempt. Nothing in the
    # wizard writes it, so it can only be blank by hand-edit or a legacy
    # `.env` — exactly what backfill exists to repair. Exempting it let the
    # blank reach `ServiceConfig`, where a present-but-empty key made
    # `.get(key, default)` return `''` and collapse the module list to
    # nothing, which was then written back durably.
    assert "WEAVIATE_ENABLE_MODULES=text2vec-ollama,generative-ollama\n" in body

# Backfill and its headless summary share the canonical literal env grammar.

from types import SimpleNamespace

import pytest

_VALUE_KEY = "SUPABASE_DB_PASSWORD"


def _owned_backfill(tmp_path, body, template):
    from core.config_parser import ConfigParser
    from core.port_manager import PortManager
    from start import AtlasStarter

    env = tmp_path / ".env"
    example = tmp_path / ".env.example"
    env.write_bytes(body.encode("utf-8"))
    example.write_bytes(template.encode("utf-8"))
    events = []

    def record(*args, **kwargs):
        events.append(tuple(map(str, args)))

    starter = AtlasStarter.__new__(AtlasStarter)
    starter.root_dir = Path(__file__).resolve().parents[2]
    starter.config_parser = ConfigParser(str(tmp_path))
    starter.config_parser.env_file_path = env
    starter.config_parser.env_example_path = example
    starter.port_manager = PortManager(str(starter.root_dir))
    starter.banner = SimpleNamespace(console=SimpleNamespace(print=record), show_status_message=record)
    return starter, env, example, events


@pytest.mark.parametrize("assignment", [
    f"{_VALUE_KEY}=#OWNED", f"{_VALUE_KEY}=  # OWNED",
    f"{_VALUE_KEY}='#OWNED'", f"{_VALUE_KEY}=' '",
    f"{_VALUE_KEY}='a$z\\path # literal'", f'{_VALUE_KEY}="OWNED # literal"',
    f"\ufeff{_VALUE_KEY}=OWNED", f"\ufeffexport {_VALUE_KEY}=OWNED",
    f"\u00a0{_VALUE_KEY}\u00a0=OWNED", f"export\t{_VALUE_KEY}=",
    f"export {_VALUE_KEY}=''", f"\ufeffexport {_VALUE_KEY}=",
])
def test_backfill_preserves_supported_nonblank_or_exported_values(tmp_path, assignment):
    starter, env, _, events = _owned_backfill(tmp_path, assignment + "\n", f"{_VALUE_KEY}=password\n")
    original = env.read_bytes()
    before = starter.config_parser.parse_env_file()[_VALUE_KEY]
    assert starter.backfill_missing_env_vars()
    assert (env.read_bytes(), starter.config_parser.parse_env_file()[_VALUE_KEY], events) == (original, before, [])


@pytest.mark.parametrize("empty", ["", "'' # keep this explanation", '""'])
def test_backfill_fills_canonical_empty_values(tmp_path, empty):
    starter, env, _, events = _owned_backfill(tmp_path, f"{_VALUE_KEY}={empty}\n", f"{_VALUE_KEY}=password\n")
    assert starter.backfill_missing_env_vars()
    assert starter.config_parser.parse_env_file()[_VALUE_KEY] == "password"
    assert "Filled 1 blank value(s)" in str(events)
    first = env.read_bytes(), list(events)
    assert starter.backfill_missing_env_vars()
    assert (env.read_bytes(), events) == first


@pytest.mark.parametrize("case", [
    ("missing", "OWNED # literal"), ("fill", "OWNED # literal"),
    ("missing", "#OWNED"), ("fill", "#OWNED"),
    ("missing", "a$z\\path # literal"), ("fill", "a$z\\path # literal"),
    ("missing", "left=right"), ("fill", "left=right"),
    ("missing", " leading space "), ("fill", " leading space "),
    ("missing", 'literal "quote"'), ("fill", 'literal "quote"'),
])
def test_backfill_roundtrips_real_assembler_encoded_defaults(tmp_path, case):
    from services.env_assembler import _format_default

    mode, value = case
    encoded = _format_default(value, _VALUE_KEY)
    body = "KEEP=unchanged\n" if mode == "missing" else f"{_VALUE_KEY}=\nKEEP=unchanged\n"
    starter, env, _, _ = _owned_backfill(tmp_path, body, f"{_VALUE_KEY}={encoded}\nKEEP=unchanged\n")
    assert starter.backfill_missing_env_vars()
    parsed = starter.config_parser.parse_env_file()
    assert (parsed[_VALUE_KEY], parsed["KEEP"]) == (value, "unchanged")
    first = env.read_bytes()
    assert starter.backfill_missing_env_vars()
    assert env.read_bytes() == first


@pytest.mark.parametrize("lines", [
    f"{_VALUE_KEY}=\n{_VALUE_KEY}=OWNED\n",
    f"{_VALUE_KEY}=OWNED\n{_VALUE_KEY}=''\n",
    f"{_VALUE_KEY}=\nexport {_VALUE_KEY}=\n",
    f"export {_VALUE_KEY}=\n{_VALUE_KEY}=''\n",
])
def test_backfill_retains_duplicate_order_and_export_policy(tmp_path, lines):
    from utils.atomic_write import decode_env_assignment, env_lines

    starter, env, _, _ = _owned_backfill(tmp_path, lines, f"{_VALUE_KEY}=password\n")
    expected = []
    for line in env_lines(lines):
        key, value = decode_env_assignment(line, origin="owned fixture")
        expected.append((key, "password" if not value and not line.startswith("export") else value))
    assert starter.backfill_missing_env_vars()
    actual = [decode_env_assignment(line, origin=str(env)) for line in env_lines(env.read_text())]
    assert actual == expected
    assert starter.config_parser.parse_env_file()[_VALUE_KEY] == expected[-1][1]


@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r", ""])
def test_backfill_edit_preserves_bom_and_unrelated_literal_line_boundaries(tmp_path, ending):
    sep = ending or "\n"
    body = "\ufeffKEEP='a$z\\path # literal'" + sep + f"{_VALUE_KEY}=''" + ending
    starter, env, _, _ = _owned_backfill(tmp_path, body, "KEEP=default\n" + f"{_VALUE_KEY}=password\n")
    assert starter.backfill_missing_env_vars()
    expected = "\ufeffKEEP='a$z\\path # literal'" + sep + f"{_VALUE_KEY}=password" + ending
    assert env.read_bytes() == expected.encode("utf-8")


def test_backfill_does_not_promote_exotic_separator_inside_encoded_default(tmp_path):
    encoded = "'OWNED\u2028INJECTED=literal'"
    starter, env, _, _ = _owned_backfill(tmp_path, "KEEP=unchanged\n", f"{_VALUE_KEY}={encoded}\nKEEP=unchanged\n")
    assert starter.backfill_missing_env_vars()
    parsed = starter.config_parser.parse_env_file()
    assert parsed[_VALUE_KEY] == "OWNED\u2028INJECTED=literal"
    assert "INJECTED" not in parsed


@pytest.mark.parametrize("invalid_location", ["active", "template", "unused-template"])
def test_backfill_invalid_input_fails_before_any_write_without_values(tmp_path, invalid_location):
    body = f"{_VALUE_KEY}=\nKEEP=unchanged\n"
    template = f"{_VALUE_KEY}=password\nKEEP=unchanged\n"
    invalid = 'BAD="PRIVATE_INVALID_VALUE\n'
    if invalid_location == "active":
        body += invalid
    elif invalid_location == "unused-template":
        body += "BAD=unchanged\n"
        template += invalid
    else:
        template += invalid
    starter, env, _, events = _owned_backfill(tmp_path, body, template)
    original = env.read_bytes()
    assert starter.backfill_missing_env_vars() is False
    assert env.read_bytes() == original
    assert "BAD" in str(events) and "PRIVATE_INVALID_VALUE" not in str(events)
    assert "error" in str(events) and "Filled" not in str(events)


@pytest.mark.parametrize("bom", ["", "\ufeff"])
def test_backfill_in_place_append_keeps_final_nonblank_assignment_separate(tmp_path, bom):
    banner = "# ===\n# Owned section\n# ===\n"
    starter, env, _, _ = _owned_backfill(tmp_path, bom + banner + "KEEP=unchanged", banner + "KEEP=default\nNEW=added\n")
    assert starter.backfill_missing_env_vars()
    assert starter.config_parser.parse_env_file() == {"KEEP": "unchanged", "NEW": "added"}
    assert "KEEP=unchanged\nNEW=added\n" in env.read_text()


@pytest.mark.parametrize("last_export", [False, True])
def test_headless_backfill_reports_effective_canonical_values_only(tmp_path, monkeypatch, last_export):
    import start
    from click.testing import CliRunner

    lines = f"\ufeff{_VALUE_KEY}=''\n"
    if last_export:
        lines += f"export {_VALUE_KEY}=\n"
    starter, env, _, _ = _owned_backfill(tmp_path, lines, f"{_VALUE_KEY}=password\n")
    monkeypatch.setattr(start, "AtlasStarter", lambda: starter)
    result = CliRunner().invoke(start.main, ["env", "backfill"])
    assert result.exit_code == 0, result.output
    assert "password" not in result.output and "\ufeff" not in result.output
    if last_export:
        assert "No env changes needed" in result.output and "filled blank" not in result.output
    else:
        assert f"{_VALUE_KEY} (filled blank)" in result.output


def test_headless_backfill_invalid_active_input_is_expected_nonzero(tmp_path, monkeypatch):
    import start
    from click.testing import CliRunner

    starter, env, _, _ = _owned_backfill(tmp_path, 'BAD="PRIVATE_INVALID_VALUE\n', f"{_VALUE_KEY}=password\n")
    original = env.read_bytes()
    monkeypatch.setattr(start, "AtlasStarter", lambda: starter)
    result = CliRunner().invoke(start.main, ["env", "backfill"])
    assert result.exit_code == 1 and env.read_bytes() == original
    assert isinstance(result.exception, SystemExit) and "BAD" in result.output
    assert "PRIVATE_INVALID_VALUE" not in result.output and "Traceback" not in result.output


@pytest.mark.parametrize("key", [
    "OLLAMA_USER_MODELS", "OLLAMA_CUSTOM_MODELS", "OPENAI_USER_MODELS",
    "ANTHROPIC_USER_MODELS", "OPENROUTER_USER_MODELS", "COMFYUI_USER_MODELS",
    "N8N_INIT_NODES", "CLIP_INFERENCE_API", "REDPANDA_DEMO_TOPICS",
])
def test_backfill_keeps_intentionally_empty_operator_selections(tmp_path, key):
    starter, env, _, events = _owned_backfill(tmp_path, f"{key}=''\n", f"{key}=example-default\n")
    original = env.read_bytes()
    assert starter.backfill_missing_env_vars()
    assert (env.read_bytes(), starter.config_parser.parse_env_file()[key], events) == (original, "", [])


@pytest.mark.parametrize("invalid_location", ["active", "template"])
def test_backfill_invalid_utf8_leaves_active_file_untouched(tmp_path, invalid_location):
    starter, env, example, events = _owned_backfill(tmp_path, f"{_VALUE_KEY}=\n", f"{_VALUE_KEY}=password\n")
    invalid_path = env if invalid_location == "active" else example
    invalid_path.write_bytes(invalid_path.read_bytes() + b"BAD=PRIVATE_INVALID_\xff\n")
    original = env.read_bytes()
    assert starter.backfill_missing_env_vars() is False
    assert env.read_bytes() == original
    assert "UTF-8" in str(events) and "error" in str(events)
    assert "PRIVATE_INVALID" not in str(events) and "Filled" not in str(events)


@pytest.mark.parametrize("symlink", [False, True])
def test_backfill_uses_actual_config_parser_custom_env_selection(tmp_path, monkeypatch, symlink):
    from core.config_parser import ConfigParser

    starter, default, _, _ = _owned_backfill(tmp_path, "KEEP=default-file\n", f"{_VALUE_KEY}='OWNED # literal'\n")
    active = tmp_path / "consumer.env"
    active.write_text(f"{_VALUE_KEY}=''\n", encoding="utf-8")
    chosen = tmp_path / "consumer-link.env" if symlink else active
    if symlink:
        chosen.symlink_to(active.name)
    monkeypatch.setenv("ATLAS_ENV_FILE", chosen.name)
    starter.config_parser = ConfigParser(str(tmp_path))
    untouched = default.read_bytes()
    assert starter.config_parser.env_file_path == active.resolve()
    assert starter.backfill_missing_env_vars()
    assert starter.config_parser.parse_env_file()[_VALUE_KEY] == "OWNED # literal"
    assert default.read_bytes() == untouched
    assert not symlink or chosen.is_symlink()


def test_headless_backfill_invalid_utf8_is_expected_value_free_failure(tmp_path, monkeypatch):
    import start
    from click.testing import CliRunner

    starter, env, _, _ = _owned_backfill(tmp_path, f"{_VALUE_KEY}=\n", f"{_VALUE_KEY}=password\n")
    env.write_bytes(env.read_bytes() + b"BAD=PRIVATE_INVALID_\xff\n")
    original = env.read_bytes()
    monkeypatch.setattr(start, "AtlasStarter", lambda: starter)
    result = CliRunner().invoke(start.main, ["env", "backfill"])
    assert result.exit_code == 1 and isinstance(result.exception, SystemExit)
    assert "UTF-8" in result.output and "PRIVATE_INVALID" not in result.output
    assert "Traceback" not in result.output and env.read_bytes() == original
