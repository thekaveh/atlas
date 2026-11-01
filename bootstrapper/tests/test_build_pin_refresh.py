"""Retired build-pin defaults are refreshed; operator values are kept."""
from __future__ import annotations

from pathlib import Path

from services.migrations import build_pin_refresh as pins
from utils.atomic_write import decode_env_assignment, env_lines

ROOT = Path(__file__).resolve().parents[2]
LDR_PIN = "LOCAL_DEEP_RESEARCHER_LANGGRAPH_CLI_VERSION"
GLTF_PIN = "ASSET_WORKER_GLTF_TRANSFORM_VERSION"
EXAMPLE = f"{LDR_PIN}=0.4.32\n{GLTF_PIN}=4.5.1\nOTHER=1\n"


def _write(tmp_path: Path, env_text: str) -> tuple[Path, Path]:
    env, example = tmp_path / ".env", tmp_path / ".env.example"
    env.write_text(env_text, encoding="utf-8", newline="")
    example.write_text(EXAMPLE, encoding="utf-8")
    return env, example


def test_retired_defaults_are_rewritten_in_place_and_keep_their_form():
    text = (f"export {LDR_PIN} = '0.4.31'  # shipped\r\n{GLTF_PIN}=4.4.1\n"
            "OTHER=0.4.31\n# LOCAL_DEEP_RESEARCHER_LANGGRAPH_CLI_VERSION=0.4.31\n")
    updated, changes = pins.refresh_text(text, pins.current_defaults(EXAMPLE))
    assert updated == (f"export {LDR_PIN} = '0.4.32'  # shipped\r\n{GLTF_PIN}=4.5.1\n"
                       "OTHER=0.4.31\n# LOCAL_DEEP_RESEARCHER_LANGGRAPH_CLI_VERSION=0.4.31\n")
    assert changes == [(LDR_PIN, "0.4.31", "0.4.32"), (GLTF_PIN, "4.4.1", "4.5.1")]


def test_operator_values_and_current_defaults_are_left_alone():
    text = f"{LDR_PIN}=0.4.30\n{GLTF_PIN}=\n{GLTF_PIN}=4.5.1\n"
    assert pins.refresh_text(text, pins.current_defaults(EXAMPLE)) == (text, [])


def test_a_pin_an_overlay_or_manifest_set_is_kept():
    text = f"{LDR_PIN}=0.4.31\n{GLTF_PIN}=4.5.0\n"
    updated, changes = pins.refresh_text(text, pins.current_defaults(EXAMPLE), frozenset({LDR_PIN}))
    assert (updated, changes) == (f"{LDR_PIN}=0.4.31\n{GLTF_PIN}=4.5.1\n", [(GLTF_PIN, "4.5.0", "4.5.1")])


def test_refresh_backs_up_once_and_is_idempotent(tmp_path):
    env, example = _write(tmp_path, f"A=1\n{GLTF_PIN}=4.5.0\n")
    changes, backup = pins.refresh(env, example)
    assert (changes, env.read_text()) == ([(GLTF_PIN, "4.5.0", "4.5.1")], f"A=1\n{GLTF_PIN}=4.5.1\n")
    assert backup.read_text() == f"A=1\n{GLTF_PIN}=4.5.0\n"
    assert (env.stat().st_mode & 0o777, backup.stat().st_mode & 0o777) == (0o600, 0o600)
    assert pins.refresh(env, example) == ([], None)
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted([".env", ".env.example", backup.name])


def test_refresh_without_an_env_file_writes_nothing(tmp_path):
    example = tmp_path / ".env.example"
    example.write_text(EXAMPLE, encoding="utf-8")
    assert pins.refresh(tmp_path / ".env", example) == ([], None)
    assert not (tmp_path / ".env").exists()


def test_every_pin_ships_a_current_default_outside_its_retired_set():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    shipped = dict(filter(None, (decode_env_assignment(line, origin=".env.example")
                                 for line in env_lines(example))))
    current = pins.current_defaults(example)
    for key, retired in pins.RETIRED_DEFAULTS.items():
        assert current[key] == shipped[key] and current[key], key
        assert current[key] not in retired, key


def test_backfill_refreshes_a_retired_pin_and_reports_it(tmp_path):
    from types import SimpleNamespace

    import start

    env, example = _write(tmp_path, f"{LDR_PIN}=0.4.31\n{GLTF_PIN}=4.5.1\nOTHER=1\n")
    messages = []
    starter = start.AtlasStarter.__new__(start.AtlasStarter)
    starter.config_parser = SimpleNamespace(env_file_path=env, env_example_path=example)
    starter.banner = SimpleNamespace(show_status_message=lambda text, level: messages.append((level, text)))
    assert starter.backfill_missing_env_vars() is True
    assert env.read_text() == f"{LDR_PIN}=0.4.32\n{GLTF_PIN}=4.5.1\nOTHER=1\n"
    assert [level for level, _text in messages] == ["info"]
    assert messages[0][1].startswith(f"Refreshed {LDR_PIN} from the retired default 0.4.31 to 0.4.32")


def test_backfill_keeps_a_pin_this_run_took_from_an_overlay(tmp_path):
    from types import SimpleNamespace

    import start

    env, example = _write(tmp_path, f"{LDR_PIN}=0.4.31\n{GLTF_PIN}=4.5.1\nOTHER=1\n")
    messages = []
    starter = start.AtlasStarter.__new__(start.AtlasStarter)
    starter.config_parser = SimpleNamespace(env_file_path=env, env_example_path=example)
    starter.banner = SimpleNamespace(show_status_message=lambda text, level: messages.append((level, text)))
    starter._env_user_keys = {LDR_PIN}
    assert starter.backfill_missing_env_vars() is True
    assert (env.read_text(), messages) == (f"{LDR_PIN}=0.4.31\n{GLTF_PIN}=4.5.1\nOTHER=1\n", [])
