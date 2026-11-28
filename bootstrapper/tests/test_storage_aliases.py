"""Storage rename alias window: old MINIO_* Atlas names read as SILO_* for one release."""

from __future__ import annotations

from pathlib import Path

import pytest

from services import storage_aliases as sa

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _reset_warning(monkeypatch):
    monkeypatch.setattr(sa, "_WARNED", False)


def test_static_mapping_targets_are_shipped_atlas_variables():
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    shipped = {
        line.split("=", 1)[0]
        for line in example.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    assert sa.STATIC_RENAMES
    for old, new in sa.STATIC_RENAMES.items():
        assert "MINIO" in old and "SILO" in new, (old, new)
        assert new in shipped, f"{new} is not a shipped .env.example key"


@pytest.mark.parametrize(
    "old,new",
    [
        ("MINIO_SOURCE", "SILO_SOURCE"),
        ("MINIO_ROOT_USER", "SILO_ROOT_USER"),
        ("MINIO_ROOT_PASSWORD", "SILO_ROOT_PASSWORD"),
        ("MINIO_REGION", "SILO_REGION"),
        ("MINIO_BUCKET_ICEBERG_LAKEHOUSE", "SILO_BUCKET_ICEBERG_LAKEHOUSE"),
        ("MINIO_SPARK_ACCESS_KEY", "SILO_SPARK_ACCESS_KEY"),
        ("ASSET_WORKER_MINIO_BUCKET", "ASSET_WORKER_SILO_BUCKET"),
        # Consumer-declared keys are not in the static table; the prefix rule maps them.
        ("MINIO_BUCKET_DAYDREAMS_MEDIA_EXTRA_0", "SILO_BUCKET_DAYDREAMS_MEDIA_EXTRA_0"),
        ("MINIO_RAG_CORPUS_SECRET_KEY", "SILO_RAG_CORPUS_SECRET_KEY"),
    ],
)
def test_old_atlas_names_map_to_silo_names(old, new):
    assert sa.silo_name_for(old) == new


@pytest.mark.parametrize(
    "name",
    [
        "MINIO_BROWSER_REDIRECT_URL",
        "MINIO_PROMETHEUS_AUTH_TYPE",
        "MINIO_IDENTITY_OPENID_CONFIG_URL",
        "SILO_SOURCE",
        "PROJECT_NAME",
        "MINIO",
        "MINIO_",
    ],
)
def test_silo_server_keys_and_non_atlas_names_are_not_mapped(name):
    assert sa.silo_name_for(name) is None


def test_fallback_reads_the_old_value_when_the_silo_key_is_absent(capsys):
    values = {"MINIO_SOURCE": "container", "MINIO_ROOT_PASSWORD": "pw"}
    resolved = sa.apply_alias_fallback(values)
    assert resolved["SILO_SOURCE"] == "container"
    assert resolved["SILO_ROOT_PASSWORD"] == "pw"
    assert values == {"MINIO_SOURCE": "container", "MINIO_ROOT_PASSWORD": "pw"}
    err = capsys.readouterr().err
    assert err.count("WARNING") == 1
    assert "MINIO_SOURCE" in err and "SILO_SOURCE" in err


def test_fallback_warns_once_per_run(capsys):
    sa.apply_alias_fallback({"MINIO_SOURCE": "container"})
    sa.apply_alias_fallback({"MINIO_PORT": "63020"})
    assert capsys.readouterr().err.count("WARNING") == 1


def test_a_present_silo_key_wins_and_server_keys_are_not_aliased(capsys):
    values = {
        "MINIO_SOURCE": "disabled",
        "SILO_SOURCE": "container",
        "MINIO_BROWSER_REDIRECT_URL": "http://localhost:63021",
    }
    resolved = sa.apply_alias_fallback(values)
    assert resolved["SILO_SOURCE"] == "container"
    assert "SILO_BROWSER_REDIRECT_URL" not in resolved
    assert resolved["MINIO_BROWSER_REDIRECT_URL"] == "http://localhost:63021"
    # Nothing was aliased, so nothing is reported.
    assert capsys.readouterr().err == ""


def test_no_warning_when_no_old_names_are_present(capsys):
    sa.apply_alias_fallback({"SILO_SOURCE": "container", "PROJECT_NAME": "atlas"})
    assert capsys.readouterr().err == ""


def test_config_parser_applies_the_alias_to_the_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLAS_ENV_FILE", raising=False)
    monkeypatch.delenv("GENAI_ENV_FILE", raising=False)
    (tmp_path / ".env").write_text(
        "MINIO_SOURCE=container\nMINIO_PORT=63020\nSILO_PORT=63999\n", encoding="utf-8"
    )
    from core.config_parser import ConfigParser

    env = ConfigParser(root_dir=str(tmp_path)).parse_env_file()
    assert env["SILO_SOURCE"] == "container"
    assert env["SILO_PORT"] == "63999"


def test_minio_source_flag_is_still_accepted():
    from click.testing import CliRunner

    from start import main

    result = CliRunner().invoke(main, ["--list-tracks", "--minio-source", "container"])
    assert result.exit_code == 0, result.output


def test_minio_source_flag_warns_once_and_names_the_replacement(capsys):
    from start import _warn_deprecated_source_flags

    _warn_deprecated_source_flags(["--minio-source", "container"])
    _warn_deprecated_source_flags(["--minio-source=disabled"])
    _warn_deprecated_source_flags(["--silo-source", "container"])
    err = capsys.readouterr().err
    assert err.count("--minio-source is deprecated; use --silo-source") == 2
