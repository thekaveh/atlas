from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from scripts import audit_runtime_locks
from scripts import check_runtime_locks
from scripts.bounded_subprocess import CommandTimedOut


def test_audit_runtime_lock_accepts_exact_reviewed_advisories(
    tmp_path: Path, monkeypatch
) -> None:
    lock = tmp_path / "requirements-locked.txt"
    lock.write_text("safe==1.0\nlocal-wheel==1.0+cpu\n", encoding="utf-8")
    spec = audit_runtime_locks.AuditSpec(
        str(lock),
        frozenset({"PYSEC-1"}),
        frozenset({"local-wheel==1.0+cpu"}),
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
        str(lock), frozenset({"PYSEC-REVIEWED", "PYSEC-STALE"})
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


def test_all_unlocked_runtime_graphs_are_resolved_before_audit() -> None:
    paths = {spec.requirements for spec in audit_runtime_locks.SOURCE_SPECS}
    assert paths == {
        "services/parakeet/provider/mlx/requirements.txt",
    }
    assert audit_runtime_locks.UV_PROJECTS == (
        "bootstrapper",
        "services/docling/provider/localhost",
    )
    assert audit_runtime_locks.NPM_PROJECTS == (
        "services/asset-worker/app",
        "services/n8n/init/config",
    )


def test_every_networked_lock_and_audit_subprocess_has_a_deadline() -> None:
    audit_source = Path(audit_runtime_locks.__file__).read_text(encoding="utf-8")
    check_source = Path(check_runtime_locks.__file__).read_text(encoding="utf-8")
    assert audit_source.count("timeout_seconds=COMMAND_TIMEOUT_SECONDS") == 4
    assert check_source.count("timeout_seconds=COMMAND_TIMEOUT_SECONDS") == 1
    assert audit_runtime_locks.COMMAND_TIMEOUT_SECONDS == 300
    assert check_runtime_locks.COMMAND_TIMEOUT_SECONDS == 300
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
