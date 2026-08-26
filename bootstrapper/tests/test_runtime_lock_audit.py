from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts import audit_runtime_locks
from scripts import check_runtime_locks
from scripts import check_test_locks
from scripts.bounded_subprocess import CommandLaunchError, CommandTimedOut


def _inline_docker_pins(dockerfile: Path) -> set[str]:
    text = dockerfile.read_text(encoding="utf-8").replace("\\\n", " ")
    commands = re.findall(r"^\s*RUN\s+([^\n]*)", text, flags=re.MULTILINE)
    install_commands = [
        command
        for command in commands
        if re.search(r"\bpip\d*\s+install\b", command)
    ]
    pin_pattern = re.compile(
        r"(?P<name>[A-Za-z0-9_.-]+)"
        r"(?:\[[A-Za-z0-9_.-]+(?:,[A-Za-z0-9_.-]+)*\])?"
        r"==(?P<version>[A-Za-z0-9](?:[A-Za-z0-9_.+-]*[A-Za-z0-9])?)"
    )
    return {
        f"{match.group('name').lower()}=={match.group('version')}"
        for command in install_commands
        for match in pin_pattern.finditer(command)
    }


@pytest.mark.parametrize(
    "command",
    [
        "RUN set -eux; pip install bad==1.0\n",
        "RUN uv pip install bad==1.0\n",
        "RUN /usr/local/bin/pip install bad==1.0\n",
    ],
)
def test_inline_docker_pin_discovery_cannot_be_bypassed_by_installer_prefixes(
    tmp_path: Path, command: str
) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(command, encoding="utf-8")

    assert _inline_docker_pins(dockerfile) == {"bad==1.0"}


def test_inline_docker_pin_discovery_normalizes_pep508_extras(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "RUN python -m pip install requests[socks,security]==2.0\n",
        encoding="utf-8",
    )

    assert _inline_docker_pins(dockerfile) == {"requests==2.0"}


def test_audit_runtime_lock_accepts_exact_reviewed_advisories(
    tmp_path: Path, monkeypatch
) -> None:
    lock = tmp_path / "requirements-locked.txt"
    lock.write_text("safe==1.0\nlocal-wheel==1.0+cpu\n", encoding="utf-8")
    spec = audit_runtime_locks.AuditSpec(
        str(lock),
        frozenset({"PYSEC-1"}),
        frozenset({"local-wheel==1.0+cpu"}),
        review_by=date.today() + timedelta(days=30),
    )
    captured: dict[str, str] = {}

    def fake_run(command, **kwargs):
        captured["command"] = " ".join(command)
        captured["timeout"] = str(kwargs.get("timeout_seconds"))
        audit_input = Path(command[command.index("-r") + 1])
        captured["input"] = audit_input.read_text(encoding="utf-8")
        payload = {
            "dependencies": [
                {
                    "name": "safe",
                    "version": "1.0",
                    "vulns": [{"id": "PYSEC-1"}],
                }
            ]
        }
        return SimpleNamespace(returncode=1, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(audit_runtime_locks, "run_bounded", fake_run)

    assert audit_runtime_locks.audit_spec(spec, root=Path("/")) == []
    assert "local-wheel" not in captured["input"]
    assert "--strict" in captured["command"]
    assert captured["timeout"] == str(audit_runtime_locks.COMMAND_TIMEOUT_SECONDS)


def test_audit_timeout_is_bounded_and_does_not_echo_subprocess_details(
    tmp_path: Path, monkeypatch
) -> None:
    lock = tmp_path / "requirements-locked.txt"
    lock.write_text("safe==1.0\n", encoding="utf-8")

    def time_out(*_args, **_kwargs):
        raise CommandTimedOut

    monkeypatch.setattr(audit_runtime_locks, "run_bounded", time_out)
    failures = audit_runtime_locks.audit_spec(
        audit_runtime_locks.AuditSpec(str(lock)), root=Path("/")
    )

    assert failures == [
        f"{lock}: pip-audit timed out after "
        f"{audit_runtime_locks.COMMAND_TIMEOUT_SECONDS} seconds"
    ]
    assert "secret-argument" not in failures[0]


def test_audit_failure_redacts_subprocess_output(tmp_path: Path, monkeypatch) -> None:
    lock = tmp_path / "requirements-locked.txt"
    lock.write_text("safe==1.0\n", encoding="utf-8")
    monkeypatch.setattr(
        audit_runtime_locks,
        "run_bounded",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=2,
            stdout="",
            stderr="https://user:secret-token@private.example/simple",
        ),
    )

    failures = audit_runtime_locks.audit_spec(
        audit_runtime_locks.AuditSpec(str(lock)), root=Path("/")
    )

    assert failures == [
        f"{lock}: pip-audit failed (exit 2; subprocess output redacted)"
    ]
    assert "secret-token" not in failures[0]


def test_audit_launch_failure_is_stable_and_redacted(
    tmp_path: Path, monkeypatch
) -> None:
    lock = tmp_path / "requirements-locked.txt"
    lock.write_text("safe==1.0\n", encoding="utf-8")

    def fail_to_launch(*_args, **_kwargs):
        raise CommandLaunchError

    monkeypatch.setattr(audit_runtime_locks, "run_bounded", fail_to_launch)
    failures = audit_runtime_locks.audit_spec(
        audit_runtime_locks.AuditSpec(str(lock)), root=Path("/")
    )

    assert failures == [
        f"{lock}: pip-audit could not complete (subprocess details redacted)"
    ]


def test_audit_runtime_lock_rejects_unreviewed_local_versions(
    tmp_path: Path, monkeypatch
) -> None:
    lock = tmp_path / "requirements-locked.txt"
    lock.write_text("new-local==2.0+cpu\n", encoding="utf-8")
    spec = audit_runtime_locks.AuditSpec(str(lock))
    monkeypatch.setattr(
        audit_runtime_locks,
        "run_bounded",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"dependencies": []}),
            stderr="",
        ),
    )

    failures = audit_runtime_locks.audit_spec(spec, root=Path("/"))

    assert failures == [
        f"{lock}: unreviewed local-version exclusions: new-local==2.0+cpu"
    ]


def test_audit_runtime_lock_rejects_new_and_stale_allowlist_entries(
    tmp_path: Path, monkeypatch
) -> None:
    lock = tmp_path / "requirements-locked.txt"
    lock.write_text("package==1.0\n", encoding="utf-8")
    spec = audit_runtime_locks.AuditSpec(
        str(lock),
        frozenset({"PYSEC-REVIEWED", "PYSEC-STALE"}),
        review_by=date.today() + timedelta(days=30),
    )
    payload = {
        "dependencies": [
            {
                "name": "package",
                "version": "1.0",
                "vulns": [
                    {"id": "PYSEC-REVIEWED"},
                    {"id": "PYSEC-NEW"},
                ],
            }
        ]
    }
    monkeypatch.setattr(
        audit_runtime_locks,
        "run_bounded",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout=json.dumps(payload), stderr=""
        ),
    )

    failures = audit_runtime_locks.audit_spec(spec, root=Path("/"))

    assert any("unreviewed advisories: PYSEC-NEW" in item for item in failures)
    assert any("stale allowlist entries: PYSEC-STALE" in item for item in failures)


def test_advisory_exceptions_require_a_current_bounded_review_deadline(
    tmp_path: Path, monkeypatch
) -> None:
    lock = tmp_path / "requirements-locked.txt"
    lock.write_text("package==1.0\n", encoding="utf-8")
    monkeypatch.setattr(
        audit_runtime_locks,
        "run_bounded",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout=json.dumps(
                {
                    "dependencies": [
                        {
                            "name": "package",
                            "version": "1.0",
                            "vulns": [{"id": "PYSEC-REVIEWED"}],
                        }
                    ]
                }
            ),
            stderr="",
        ),
    )
    review_date = date(2026, 8, 26)

    cases = (
        (None, "lack a review deadline"),
        (review_date, "review expired"),
        (review_date + timedelta(days=91), "horizon exceeds 90 days"),
    )
    for review_by, expected in cases:
        failures = audit_runtime_locks.audit_spec(
            audit_runtime_locks.AuditSpec(
                str(lock),
                frozenset({"PYSEC-REVIEWED"}),
                review_by=review_by,
            ),
            root=Path("/"),
            today=review_date,
        )
        assert any(expected in failure for failure in failures)


def test_jupyterhub_runtime_lock_is_checked_for_both_linux_architectures() -> None:
    spec = next(
        item
        for item in check_runtime_locks.RUNTIME_LOCKS
        if "jupyterhub" in item.requirements
    )
    assert spec.platforms == (
        "x86_64-manylinux_2_28",
        "aarch64-manylinux_2_28",
    )


def test_every_runtime_dependency_manifest_is_in_the_audit_inventory() -> None:
    assert (
        audit_runtime_locks.discover_runtime_manifests()
        == audit_runtime_locks.AUDITED_RUNTIME_MANIFESTS
    )


def test_shared_init_runtime_lock_is_regenerated_audited_and_used_by_builds() -> None:
    root = Path(audit_runtime_locks.__file__).parents[1]
    lock = "services/requirements-init-locked.txt"
    assert any(
        spec.requirements == lock and spec.lock == lock
        for spec in check_runtime_locks.RUNTIME_LOCKS
    )
    assert any(spec.lock == lock for spec in audit_runtime_locks.AUDIT_SPECS)
    installed = {
        service: _inline_docker_pins(root / f"services/{service}/init/Dockerfile")
        for service in ("open-webui", "litellm")
    }
    locked = set((root / lock).read_text(encoding="utf-8").splitlines())

    assert installed["open-webui"] == {
        "certifi==2026.7.22",
        "charset-normalizer==3.5.1",
        "idna==3.19",
        "psycopg2-binary==2.9.9",
        "pyjwt==2.13.0",
        "requests==2.33.0",
        "urllib3==2.7.0",
    }
    assert installed["litellm"] == {
        "psycopg2-binary==2.9.9",
        "pyyaml==6.0.2",
    }
    assert {pin for pins in installed.values() for pin in pins} == locked


def _assert_dependabot_pip_parity(config: dict) -> None:
    expected = {
        f"/{Path(spec.requirements).parent.as_posix()}"
        for spec in check_runtime_locks.RUNTIME_LOCKS
    } | {f"/{project}" for project in audit_runtime_locks.UV_PROJECTS}
    pip_directories = {
        directory
        for update in config["updates"]
        if update["package-ecosystem"] == "pip"
        for directory in update.get("directories", [update.get("directory")])
    }

    assert pip_directories == expected


def test_dependabot_exactly_covers_every_active_runtime_pip_manifest() -> None:
    root = Path(audit_runtime_locks.__file__).parents[1]
    config = yaml.safe_load((root / ".github/dependabot.yml").read_text(encoding="utf-8"))

    _assert_dependabot_pip_parity(config)


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_dependabot_runtime_pip_parity_rejects_inventory_drift(mutation: str) -> None:
    root = Path(audit_runtime_locks.__file__).parents[1]
    config = yaml.safe_load((root / ".github/dependabot.yml").read_text(encoding="utf-8"))
    mutated = deepcopy(config)
    directories = next(
        update["directories"]
        for update in mutated["updates"]
        if update["package-ecosystem"] == "pip"
    )
    if mutation == "missing":
        directories.remove("/bootstrapper")
    else:
        directories.append("/services/retired-phantom")

    with pytest.raises(AssertionError):
        _assert_dependabot_pip_parity(mutated)


def test_inline_dockerfile_pins_are_also_owned_by_a_compiled_lock() -> None:
    root = Path(audit_runtime_locks.__file__).parents[1]
    for dockerfile in (root / "services").rglob("Dockerfile*"):
        inline = _inline_docker_pins(dockerfile)
        inline = {pin for pin in inline if not pin.lower().startswith("uv==")}
        if not inline:
            continue
        locked = "\n".join(
            path.read_text(encoding="utf-8")
            for path in dockerfile.parent.glob("requirements*-locked.txt")
        )
        locked += "\n" + (
            root / "services/requirements-init-locked.txt"
        ).read_text(encoding="utf-8")
        assert inline <= set(locked.splitlines()), (
            f"{dockerfile.relative_to(root)} installs unaudited inline pins: "
            f"{sorted(inline - set(locked.splitlines()))}"
        )


def test_local_deep_researcher_nonstandard_runtime_graph_is_audited() -> None:
    paths = audit_runtime_locks.AUDITED_RUNTIME_MANIFESTS
    assert {
        "services/local-deep-researcher/build/config/runtime-requirements.lock",
        "services/local-deep-researcher/locks/runtime-pyproject.toml",
        "services/local-deep-researcher/locks/runtime.uv.lock",
    } <= paths
    assert any(
        spec.lock
        == "services/local-deep-researcher/build/config/runtime-requirements.lock"
        for spec in audit_runtime_locks.AUDIT_SPECS
    )


def test_local_deep_researcher_aiohttp_security_floor_is_exported() -> None:
    root = Path(audit_runtime_locks.__file__).parents[1]
    refresher = (root / "scripts/refresh-local-deep-researcher-lock.py").read_text(
        encoding="utf-8"
    )
    project = (
        root / "services/local-deep-researcher/locks/runtime-pyproject.toml"
    ).read_text(encoding="utf-8")
    runtime = (
        root / "services/local-deep-researcher/build/config/runtime-requirements.lock"
    ).read_text(encoding="utf-8")

    assert '"aiohttp>=3.14.3"' in refresher
    assert '"aiohttp>=3.14.3"' in project
    assert "aiohttp==3.14.3" in runtime


def test_all_unlocked_runtime_graphs_are_resolved_before_audit() -> None:
    paths = {spec.requirements for spec in audit_runtime_locks.SOURCE_SPECS}
    assert paths == set()
    assert audit_runtime_locks.UV_PROJECTS == (
        "bootstrapper",
        "services/docling/provider/localhost",
    )
    assert audit_runtime_locks.NPM_PROJECTS == (
        "services/asset-worker/app",
        "services/n8n/init/config",
    )


def test_bootstrapper_and_parakeet_mlx_runtime_locks_are_drift_checked() -> None:
    assert any(
        spec.project == "bootstrapper"
        and spec.lock == "bootstrapper/requirements-locked.txt"
        for spec in check_runtime_locks.UV_RUNTIME_LOCKS
    )
    assert any(
        spec.requirements == "services/parakeet/provider/mlx/requirements.txt"
        and spec.lock
        == "services/parakeet/provider/mlx/requirements-locked.txt"
        and spec.platforms == ("aarch64-apple-darwin",)
        for spec in check_runtime_locks.RUNTIME_LOCKS
    )


def test_every_networked_lock_and_audit_subprocess_has_a_deadline() -> None:
    audit_source = Path(audit_runtime_locks.__file__).read_text(encoding="utf-8")
    check_source = Path(check_runtime_locks.__file__).read_text(encoding="utf-8")
    test_check_source = Path(check_test_locks.__file__).read_text(encoding="utf-8")
    assert audit_source.count("timeout_seconds=COMMAND_TIMEOUT_SECONDS") == 4
    assert check_source.count("timeout_seconds=COMMAND_TIMEOUT_SECONDS") == 2
    assert test_check_source.count("timeout_seconds=COMMAND_TIMEOUT_SECONDS") == 1
    assert audit_runtime_locks.COMMAND_TIMEOUT_SECONDS == 300
    assert check_runtime_locks.COMMAND_TIMEOUT_SECONDS == 300
    assert check_test_locks.COMMAND_TIMEOUT_SECONDS == 300
    refresh_source = (
        Path(audit_runtime_locks.__file__).parent
        / "refresh-local-deep-researcher-lock.py"
    ).read_text(encoding="utf-8")
    assert "run_bounded(" in refresh_source
    workflow = (
        Path(audit_runtime_locks.__file__).parents[1]
        / ".github/workflows/services-lint.yml"
    ).read_text(encoding="utf-8")
    assert workflow.count("python -m scripts.bounded_subprocess") == 2
    assert "-- uv lock --locked" in workflow
    assert "uv tool install pip-audit==2.10.0" in workflow


def test_npm_audit_rejects_registry_error_json(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "n8n"
    project.mkdir()
    monkeypatch.setattr(
        audit_runtime_locks,
        "run_bounded",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout=json.dumps(
                {
                    "message": "request to registry failed",
                    "error": {"code": "ECONNREFUSED"},
                }
            ),
            stderr="",
        ),
    )

    failures = audit_runtime_locks.audit_npm_project(
        str(project.relative_to(tmp_path)), root=tmp_path
    )

    assert failures == ["n8n: npm audit registry request failed (details redacted)"]


def test_npm_audit_requires_vulnerability_totals(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "n8n"
    project.mkdir()
    monkeypatch.setattr(
        audit_runtime_locks,
        "run_bounded",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps({"metadata": {}}), stderr=""
        ),
    )

    failures = audit_runtime_locks.audit_npm_project("n8n", root=tmp_path)

    assert failures == ["n8n: npm audit response omitted vulnerability totals"]
