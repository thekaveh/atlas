"""Tests for bootstrapper.tools.validate_fragments (the CI lint entry point)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.validate_fragments import run


def _synthetic_capabilities() -> list[dict[str, str]]:
    return [
        {
            "name": "Synthetic validation contract",
            "status": "supported",
            "verification": "tested",
            "note": "Tests exercise this synthetic validation manifest.",
        }
    ]


# ────────────────────────────────────────────────────────────────────────────
# Manifest-validation mode
# ────────────────────────────────────────────────────────────────────────────


def test_empty_services_dir_exits_clean(tmp_path: Path, capsys):
    (tmp_path / "services").mkdir()
    exit_code = run(project_root=tmp_path, check_env_example=False)
    assert exit_code == 0


def test_no_services_dir_exits_clean(tmp_path: Path, capsys):
    # Phase A: the services/ folder may not exist yet.
    exit_code = run(project_root=tmp_path, check_env_example=False)
    assert exit_code == 0


def _scaffold_generated_artifacts(project: Path) -> None:
    """Create README.md from generators so real-repo guards in
    validate_fragments pass for test trees that have service.yml files.

    The top-level architecture diagram is no longer generated — it lives
    at ``docs/diagrams/architecture.svg`` as a hand-authored artifact
    produced via the architecture-diagram skill, with the per-service
    diagrams emitted by ``bootstrapper.docs.regen`` covering the
    auto-generated surface.
    """
    from tools.generate_readme_topology import generate_block

    services_dir = project / "services"
    for manifest_path in services_dir.glob("*/service.yml"):
        readme = manifest_path.parent / "README.md"
        if not readme.exists():
            readme.write_text(f"# {manifest_path.parent.name}\n", encoding="utf-8")
    block = generate_block(services_dir)
    readme_text = f"# Test\n\n{block}\n"
    (project / "README.md").write_text(readme_text)
    includes = [
        path.relative_to(project).as_posix()
        for path in sorted(services_dir.glob("*/compose.yml"))
    ]
    if includes:
        include_lines = "\n".join(f"  - {path}" for path in includes)
        (project / "docker-compose.yml").write_text(
            f"include:\n{include_lines}\nservices: {{}}\n"
        )


def test_valid_manifest_exits_clean(
    tmp_path: Path, services_root, write_manifest, minimal_manifest_dict, capsys
):
    # The fixtures already created `services_root` inside their own tmp_path.
    # Build our own structure under this test's tmp_path instead.
    project = tmp_path / "project"
    project.mkdir()
    (project / "services").mkdir()
    (project / "services" / "redis").mkdir()
    import yaml

    (project / "services" / "redis" / "service.yml").write_text(
        yaml.safe_dump(minimal_manifest_dict("redis"))
    )
    # The fragment-containers rule (added in the post-Tier-3 hardening pass)
    # requires every non-virtual manifest to ship a sibling compose.yml whose
    # `services:` keys match the manifest's containers[] 1:1.
    (project / "services" / "redis" / "compose.yml").write_text(
        "services:\n  redis:\n    image: redis:latest\n"
    )
    _scaffold_generated_artifacts(project)
    exit_code = run(project_root=project, check_env_example=False)
    assert exit_code == 0


def test_broken_manifest_exits_nonzero(tmp_path: Path, capsys):
    project = tmp_path / "project"
    project.mkdir()
    (project / "services" / "redis").mkdir(parents=True)
    (project / "services" / "redis" / "service.yml").write_text("name: redis\n")  # missing required fields
    exit_code = run(project_root=project, check_env_example=False)
    assert exit_code != 0
    captured = capsys.readouterr()
    assert "redis" in (captured.out + captured.err)


def test_cross_manifest_issue_exits_nonzero(tmp_path: Path, capsys):
    project = tmp_path / "project"
    project.mkdir()
    (project / "services").mkdir()
    import yaml

    # Both services declare the same env var → duplicate_env_var.
    for name in ["redis", "alt"]:
        d = project / "services" / name
        d.mkdir()
        (d / "service.yml").write_text(
            yaml.safe_dump(
                {
                    "name": name,
                    "label": f"{name}",
                    "category": "data",
                    "containers": [name],
                    "capabilities": _synthetic_capabilities(),
                    "env": [{"name": "SHARED_PORT", "default": 1}],
                }
            )
        )
    exit_code = run(project_root=project, check_env_example=False)
    assert exit_code != 0
    captured = capsys.readouterr()
    assert "SHARED_PORT" in (captured.out + captured.err)


# ────────────────────────────────────────────────────────────────────────────
# --check-env-example mode
# ────────────────────────────────────────────────────────────────────────────


def test_check_env_example_matches_committed_file(
    tmp_path: Path, capsys
):
    """If the committed .env.example matches the assembled output → exit 0."""
    from services.env_assembler import assemble_env_example
    from services.manifests import load_manifests
    import yaml

    project = tmp_path / "project"
    project.mkdir()
    (project / "services").mkdir()
    demo_dir = project / "services" / "demo"
    demo_dir.mkdir()
    (demo_dir / "service.yml").write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "label": "Demo",
                "category": "data",
                "containers": ["demo"],
                "capabilities": _synthetic_capabilities(),
                "env": [{"name": "DEMO_PORT", "default": 1234}],
            }
        )
    )
    # Fragment required by the fragment-containers validator rule.
    (demo_dir / "compose.yml").write_text(
        "services:\n  demo:\n    image: demo:latest\n"
    )
    manifests = load_manifests(project / "services")
    expected = assemble_env_example(manifests, services_root=project / "services")
    assert "DEMO_PORT=63010" in expected
    assert "DEMO_PORT=1234" not in expected
    (project / ".env.example").write_text(expected)
    _scaffold_generated_artifacts(project)

    exit_code = run(project_root=project, check_env_example=True)
    assert exit_code == 0


def test_check_env_example_drift_exits_nonzero(tmp_path: Path, capsys):
    """If the committed .env.example does not match → exit non-zero with diff."""
    import yaml

    project = tmp_path / "project"
    project.mkdir()
    (project / "services").mkdir()
    redis_dir = project / "services" / "redis"
    redis_dir.mkdir()
    (redis_dir / "service.yml").write_text(
        yaml.safe_dump(
            {
                "name": "redis",
                "label": "Redis",
                "category": "data",
                "containers": ["redis"],
                "capabilities": _synthetic_capabilities(),
                "env": [{"name": "REDIS_PORT", "default": 6379}],
            }
        )
    )
    (project / ".env.example").write_text("# this is stale\n")
    exit_code = run(project_root=project, check_env_example=True)
    assert exit_code != 0
    captured = capsys.readouterr()
    assert "drift" in (captured.out + captured.err).lower() or "diff" in (captured.out + captured.err).lower()
