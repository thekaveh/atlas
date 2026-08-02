from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from scripts import audit_runtime_locks
from scripts import check_runtime_locks


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

    def fake_run(command, **_kwargs):
        captured["command"] = " ".join(command)
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

    monkeypatch.setattr(audit_runtime_locks.subprocess, "run", fake_run)

    assert audit_runtime_locks.audit_spec(spec, root=Path("/")) == []
    assert "local-wheel" not in captured["input"]
    assert "--strict" in captured["command"]


def test_audit_runtime_lock_rejects_unreviewed_local_versions(
    tmp_path: Path, monkeypatch
) -> None:
    lock = tmp_path / "requirements-locked.txt"
    lock.write_text("new-local==2.0+cpu\n", encoding="utf-8")
    spec = audit_runtime_locks.AuditSpec(str(lock))
    monkeypatch.setattr(
        audit_runtime_locks.subprocess,
        "run",
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
        audit_runtime_locks.subprocess,
        "run",
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


def test_npm_audit_rejects_registry_error_json(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "n8n"
    project.mkdir()
    monkeypatch.setattr(
        audit_runtime_locks.subprocess,
        "run",
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

    assert failures == ["n8n: npm audit failed: request to registry failed"]


def test_npm_audit_requires_vulnerability_totals(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "n8n"
    project.mkdir()
    monkeypatch.setattr(
        audit_runtime_locks.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps({"metadata": {}}), stderr=""
        ),
    )

    failures = audit_runtime_locks.audit_npm_project("n8n", root=tmp_path)

    assert failures == ["n8n: npm audit response omitted vulnerability totals"]
