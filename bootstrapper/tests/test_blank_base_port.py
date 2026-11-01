"""Regression: every BASE_PORT read site must tolerate a blank value.

A blank BASE_PORT can happen when:
  * The user manually blanked it in .env.
  * The migration mid-way through a rename has left it temporarily blank.
  * A bug elsewhere produced ``BASE_PORT=``.

Three production call sites historically did ``int(env_vars.get("BASE_PORT", DEFAULT_BASE_PORT))``,
which crashes with ``ValueError: invalid literal for int() with base 10: ''``
because ``dict.get(key, default)`` returns the empty string (not the default)
when the key is present-but-blank. Each call site is now guarded.
"""

from __future__ import annotations


def test_run_port_migration_handles_blank_base_port(tmp_path):
    """``AtlasStarter.run_port_migration`` doesn't crash if .env's
    BASE_PORT is blank — falls back to DEFAULT_BASE_PORT."""
    (tmp_path / ".env").write_text(
        "BASE_PORT=\nBOOTSTRAPPER_PORT_LAYOUT_VERSION=\n"
    )
    (tmp_path / ".env.example").write_text("BASE_PORT=63000\n")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    from start import AtlasStarter

    starter = AtlasStarter()
    starter.config_parser.root_dir = tmp_path
    starter.config_parser.env_file_path = tmp_path / ".env"
    starter.config_parser.env_example_path = tmp_path / ".env.example"

    # Must not raise ValueError. (Will no-op the rewrite because the
    # synthetic .env has no port vars to migrate, but the sentinel
    # parse + topology build must complete cleanly.)
    starter.run_port_migration(no_port_migrate=False)


def test_wizard_build_handles_blank_base_port(tmp_path):
    """The REAL ``_build_steps_and_rows`` must not crash on a blank
    BASE_PORT and must resolve it to DEFAULT_BASE_PORT.

    (An earlier version of this test re-implemented the int-guard inline
    and asserted its own copy — it kept passing no matter what
    integration.py did.)"""
    import shutil
    from pathlib import Path

    from core.config_parser import ConfigParser, DEFAULT_BASE_PORT
    from ui.textual.integration import _build_steps_and_rows

    repo_root = Path(__file__).resolve().parents[2]
    env = tmp_path / ".env"
    shutil.copy(repo_root / ".env.example", env)
    text = env.read_text(encoding="utf-8").replace("BASE_PORT=63000", "BASE_PORT=")
    env.write_text(text, encoding="utf-8")

    cp = ConfigParser(str(repo_root))
    cp.env_file_path = env
    steps, rows, services_info, current_base_port, state, cloud = (
        _build_steps_and_rows(cp)
    )
    assert current_base_port == DEFAULT_BASE_PORT
    assert steps and rows


def test_wizard_build_handles_blank_kong_http_port(tmp_path):
    """When ``KONG_HTTP_PORT`` is blank in .env, the wizard's first paint
    must show alias_port=63000 for every Kong-routed service. The wizard,
    ``AppState.kong_port`` and the linear summary all read the port from
    ``state_builder.kong_listener_port``, which falls back to a numeric
    BASE_PORT, then to DEFAULT_BASE_PORT.

    Without this guard, _build_steps_and_rows()'s old empty-string
    fallback rendered alias_port='', and build_app_state() kept the blank
    value although a comment claimed the two derivations matched."""
    import shutil
    from pathlib import Path

    from core.config_parser import ConfigParser
    from ui.textual.integration import _build_steps_and_rows

    repo_root = Path(__file__).resolve().parents[2]
    env = tmp_path / ".env"
    shutil.copy(repo_root / ".env.example", env)
    text = env.read_text(encoding="utf-8").replace(
        "KONG_HTTP_PORT=63000", "KONG_HTTP_PORT="
    )
    env.write_text(text, encoding="utf-8")

    cp = ConfigParser(str(repo_root))
    cp.env_file_path = env
    _, rows, _, _, state, _ = _build_steps_and_rows(cp)
    assert state.kong_port == "63000"

    aliased = [r for r in rows if r.alias]
    assert aliased, "expected at least one Kong-aliased row in the wizard"
    for r in aliased:
        assert r.alias_port == "63000", (
            f"row {r.name!r} has alias_port={r.alias_port!r}; "
            f"expected '63000' (KONG_HTTP_PORT fallback)"
        )


def test_kong_listener_port_follows_a_numeric_base_port():
    """Kong holds slot 0 of the port block, so a blank KONG_HTTP_PORT takes
    a numeric BASE_PORT. 'auto' or a blank BASE_PORT falls back to 63000."""
    from wizard.model.state_builder import kong_listener_port

    assert kong_listener_port({"KONG_HTTP_PORT": "", "BASE_PORT": "64000"}) == "64000"
    assert kong_listener_port({"KONG_HTTP_PORT": " ", "BASE_PORT": "auto"}) == "63000"
    assert kong_listener_port({"BASE_PORT": ""}) == "63000"
    assert kong_listener_port({"KONG_HTTP_PORT": "63100", "BASE_PORT": "64000"}) == "63100"
