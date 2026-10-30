from __future__ import annotations

import json
import re
import shlex
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

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


_PIP_OPTIONS_WITH_VALUES = {
    "--cache-dir",
    "--cert",
    "--client-cert",
    "--extra-index-url",
    "--find-links",
    "--index-url",
    "--proxy",
    "--retries",
    "--root-user-action",
    "--timeout",
    "--trusted-host",
}
_EXACT_PIP_PIN = re.compile(
    r"^[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_.-]+(?:,[A-Za-z0-9_.-]+)*\])?"
    r"==[A-Za-z0-9](?:[A-Za-z0-9_.+-]*[A-Za-z0-9])?$"
)


def _pip_operand(
    tokens: list[str], index: int
) -> tuple[str, str | None, int]:
    token = tokens[index]
    paired = {
        "-c": "constraint",
        "--constraint": "constraint",
        "-r": "requirement",
        "--requirement": "requirement",
        "-e": "editable",
        "--editable": "editable",
    }
    if token in paired:
        value = tokens[index + 1] if index + 1 < len(tokens) else token
        return paired[token], value, index + 2
    if token.startswith("--constraint=") or token.startswith("-c"):
        return "constraint", token, index + 1
    if token.startswith("--requirement=") or token.startswith("-r"):
        return "requirement", token, index + 1
    if token.startswith("--editable=") or token.startswith("-e"):
        return "editable", token, index + 1
    if token in _PIP_OPTIONS_WITH_VALUES:
        return "option", None, index + 2
    return "token", token, index + 1


@dataclass
class _PipInstallState:
    requirements: list[str] = field(default_factory=list)
    violations: set[str] = field(default_factory=set)
    has_constraint: bool = False
    requires_hashes: bool = False


def _record_pip_operand(
    state: _PipInstallState, kind: str, value: str | None
) -> None:
    if kind == "constraint":
        state.has_constraint = True
    elif kind == "requirement" and value is not None:
        state.requirements.append(value)
    elif kind == "editable" and value is not None:
        state.violations.add(value)
    elif value == "--require-hashes":
        state.requires_hashes = True
    elif value is not None and not value.startswith("-"):
        if not _EXACT_PIP_PIN.fullmatch(value):
            state.violations.add(value)


def _pip_install_violations(tokens: list[str]) -> set[str]:
    state = _PipInstallState()
    index = 0
    while index < len(tokens):
        kind, value, index = _pip_operand(tokens, index)
        _record_pip_operand(state, kind, value)
    if state.requirements and not (
        state.has_constraint or state.requires_hashes
    ):
        state.violations.update(state.requirements)
    return state.violations


def _inline_docker_install_violations(dockerfile: Path) -> set[str]:
    """Return direct pip operands that are not lock-owned exact pins."""

    text = dockerfile.read_text(encoding="utf-8").replace("\\\n", " ")
    commands = re.findall(r"^\s*RUN\s+([^\n]*)", text, flags=re.MULTILINE)
    violations: set[str] = set()
    for command in commands:
        for match in re.finditer(r"\bpip\d*\s+install\s+", command):
            tail = re.split(r"\s*(?:&&|\|\||;)\s*", command[match.end() :], 1)[0]
            violations.update(_pip_install_violations(shlex.split(tail)))
    return violations


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


@pytest.mark.parametrize(
    "operand",
    [
        "unsafe-package",
        "unsafe-package>=1.0",
        "https://example.invalid/unsafe.whl",
        "git+https://example.invalid/unsafe.git",
        "--editable=git+https://example.invalid/unsafe.git",
        "-egit+https://example.invalid/unsafe.git",
    ],
)
def test_inline_docker_installs_reject_unlocked_operands(
    tmp_path: Path, operand: str
) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(f"RUN pip install {operand}\n", encoding="utf-8")

    assert _inline_docker_install_violations(dockerfile) == {operand}


def test_inline_docker_installs_allow_constrained_requirements(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "RUN pip install -r requirements.txt -c requirements-locked.txt\n",
        encoding="utf-8",
    )

    assert _inline_docker_install_violations(dockerfile) == set()


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


def _runtime_audit_spec(lock: str) -> audit_runtime_locks.AuditSpec:
    return next(spec for spec in audit_runtime_locks.AUDIT_SPECS if spec.lock == lock)


@pytest.mark.parametrize(
    ("lock_path", "advisory", "installed_pin"),
    (
        (
            "services/jupyterhub/build/requirements-locked.txt",
            "PYSEC-2026-2447",
            "diskcache==5.6.3",
        ),
        (
            "services/jupyterhub/build/requirements-locked.txt",
            "PYSEC-2026-3046",
            "ragas==0.4.3",
        ),
        (
            "services/parakeet/provider/gpu/requirements-locked.txt",
            "PYSEC-2026-3850",
            "hydra-core==1.3.2",
        ),
        (
            "services/parakeet/provider/gpu/requirements-locked.txt",
            "PYSEC-2026-3624",
            "lightning==2.4.0",
        ),
        (
            "services/parakeet/provider/gpu/requirements-locked.txt",
            "PYSEC-2026-4217",
            "fsspec==2024.12.0",
        ),
        (
            "services/parakeet/provider/gpu/requirements-locked.txt",
            "PYSEC-2026-4221",
            "hydra-core==1.3.2",
        ),
        (
            "services/parakeet/provider/mlx/requirements-locked.txt",
            "PYSEC-2025-138",
            "mlx==0.29.3",
        ),
        (
            "services/parakeet/provider/mlx/requirements-locked.txt",
            "PYSEC-2025-139",
            "mlx==0.29.3",
        ),
    ),
)
def test_each_current_reviewed_advisory_names_its_exact_installed_version(
    lock_path: str, advisory: str, installed_pin: str
) -> None:
    root = Path(audit_runtime_locks.__file__).parents[1]
    spec = _runtime_audit_spec(lock_path)

    assert advisory in spec.reviewed_advisories
    assert spec.review_by == date(2026, 11, 27)
    assert installed_pin in (root / lock_path).read_text(encoding="utf-8")


def test_jupyterhub_advisory_review_records_current_unreachable_paths() -> None:
    root = Path(audit_runtime_locks.__file__).parents[1]
    lock_path = "services/jupyterhub/build/requirements-locked.txt"
    spec = _runtime_audit_spec(lock_path)
    requirements = (root / "services/jupyterhub/build/requirements.txt").read_text(
        encoding="utf-8"
    )
    notebook = (
        root / "services/jupyterhub/build/notebooks/14_ragas_evaluation.ipynb"
    ).read_text(encoding="utf-8")
    mlflow_notebook = (
        root / "services/jupyterhub/build/notebooks/11_financial_research_kit.ipynb"
    ).read_text(encoding="utf-8")
    dockerfile = (root / "services/jupyterhub/build/Dockerfile").read_text(
        encoding="utf-8"
    )
    startup = (root / "services/jupyterhub/build/scripts/startup.sh").read_text(
        encoding="utf-8"
    )
    compose = (root / "services/jupyterhub/compose.yml").read_text(encoding="utf-8")
    lock = (root / lock_path).read_text(encoding="utf-8")

    assert spec.reviewed_advisories == frozenset(
        {
            "PYSEC-2026-2447",
            "PYSEC-2026-3046",
        }
    )
    assert spec.review_by == date(2026, 11, 27)
    for pin in (
        "diskcache==5.6.3",
        "ragas==0.4.3",
    ):
        assert pin in lock
        assert pin in requirements
    # NLTK 3.10.4 guards model-artifact paths; retain the fixed floor while
    # the integrity-locked VADER loader and assets remain unchanged.
    assert "nltk==3.10.4" in lock
    assert "nltk>=3.10.4" in requirements
    for evidence in (
        "14_ragas_evaluation.ipynb",
        "multimodalfaithfulness",
        "Atlas never imports diskcache",
        "does not pass",
        "cache=",
        "DiskCacheBackend",
        "no fixed release",
        "PYSEC-2026-3740",
        "install_nlp_assets.py",
        "nlp-assets.toml",
        "caller-controlled model path",
    ):
        assert evidence in requirements
    assert "multimodalfaithfulness" not in notebook
    assert "DiskCacheBackend" not in notebook
    assert "cache=" not in notebook
    for tracking_api in (
        "mlflow.set_tracking_uri",
        "mlflow.set_experiment",
        "mlflow.start_run",
        "mlflow.log_param",
        "mlflow.log_metric",
    ):
        assert tracking_api in mlflow_notebook
    for gateway_api in ("create_gateway", "CreateGatewaySecret", "auth_config"):
        assert gateway_api not in mlflow_notebook
    assert "mlflow server" not in dockerfile
    assert "mlflow server" not in startup
    assert "mlflow server" not in compose
    assert 'ENTRYPOINT ["/usr/local/bin/startup.sh"]' in dockerfile
    assert 'CMD ["start-notebook.sh"]' in dockerfile
    assert "      - start-notebook.sh" in compose


def test_parakeet_gpu_advisory_review_retains_only_unfixed_constraints() -> None:
    root = Path(audit_runtime_locks.__file__).parents[1]
    lock_path = "services/parakeet/provider/gpu/requirements-locked.txt"
    spec = _runtime_audit_spec(lock_path)
    requirements = (root / "services/parakeet/provider/gpu/requirements.txt").read_text(
        encoding="utf-8"
    )
    provider = (root / "services/parakeet/provider/gpu/transcribe.py").read_text(
        encoding="utf-8"
    )
    lock = (root / lock_path).read_text(encoding="utf-8")

    assert spec.reviewed_advisories == frozenset(
        {
            "PYSEC-2026-3624",
            "PYSEC-2026-3850",
            "PYSEC-2026-3972",
            "PYSEC-2026-4217",
            "PYSEC-2026-4221",
        }
    )
    assert spec.review_by == date(2026, 11, 27)
    for pin in (
        "hydra-core==1.3.2",
        "lightning==2.4.0",
        "nemo-toolkit==3.0.0",
        "transformers==5.16.1",
    ):
        assert pin in lock
        assert pin in requirements
    for evidence in (
        "nemo-toolkit==3.0.0",
        "hydra-core<=1.3.2",
        "lightning<=2.4.0",
        "leaves Transformers unconstrained",
        "transcribe.py:39",
        "ASRModel.from_pretrained",
        "load_from_checkpoint",
        "hydra.utils.instantiate",
        "Serialization.from_config_dict",
        "safe_instantiate",
        "recursively validates",
    ):
        assert evidence in requirements
    assert "ASRModel.from_pretrained" in provider
    assert provider.splitlines()[38].strip() == (
        "model = nemo_asr.models.ASRModel.from_pretrained(model_name)"
    )
    for unreachable in (
        "load_from_checkpoint",
        "hydra.utils.instantiate",
    ):
        assert unreachable not in provider


def test_parakeet_gpu_transformers_advisories_are_fixed_not_reviewed() -> None:
    root = Path(audit_runtime_locks.__file__).parents[1]
    lock_path = "services/parakeet/provider/gpu/requirements-locked.txt"
    spec = _runtime_audit_spec(lock_path)
    requirements = (root / "services/parakeet/provider/gpu/requirements.txt").read_text(
        encoding="utf-8"
    )
    lock = (root / lock_path).read_text(encoding="utf-8")
    fixed_ids = {
        "PYSEC-2025-217",
        "PYSEC-2026-2288",
        "PYSEC-2026-2289",
        "PYSEC-2026-2290",
    }

    assert fixed_ids.isdisjoint(spec.reviewed_advisories)
    assert "nemo_toolkit[asr]>=3.0.0,<4.0" in requirements
    assert "transformers>=5.10.0,<6" in requirements
    assert "nemo-toolkit==3.0.0" in lock
    assert "transformers==5.16.1" in lock
    assert "torch==2.13.0" in lock
    assert "cuda-toolkit==13.0.3.0" in lock


def test_parakeet_mlx_advisory_review_records_incompatible_fixed_wheel() -> None:
    root = Path(audit_runtime_locks.__file__).parents[1]
    lock_path = "services/parakeet/provider/mlx/requirements-locked.txt"
    spec = _runtime_audit_spec(lock_path)
    requirements = (root / "services/parakeet/provider/mlx/requirements.txt").read_text(
        encoding="utf-8"
    )
    provider = (root / "services/parakeet/provider/mlx/api_server.py").read_text(
        encoding="utf-8"
    )
    lock = (root / lock_path).read_text(encoding="utf-8")

    assert spec.reviewed_advisories == frozenset(
        {"PYSEC-2025-138", "PYSEC-2025-139"}
    )
    assert spec.review_by == date(2026, 11, 27)
    assert "mlx==0.29.3" in lock
    for evidence in (
        "mlx==0.29.3",
        "mlx 0.29.4",
        "macOS 14/15",
        "aarch64-apple-darwin",
        "api_server.py:64",
        "parakeet_mlx.from_pretrained",
        "model.safetensors",
        "mlx.core.load (.npy)",
        "load_gguf",
        "audio-only request path",
    ):
        assert evidence in requirements
    assert "model = from_pretrained(model_name)" in provider
    assert provider.splitlines()[63].strip() == "model = from_pretrained(model_name)"


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


def test_jupyterhub_runtime_lock_matches_the_base_python() -> None:
    spec = next(
        item
        for item in check_runtime_locks.RUNTIME_LOCKS
        if "jupyterhub" in item.requirements
    )
    dockerfile = (
        Path(check_runtime_locks.__file__).parents[1]
        / "services/jupyterhub/build/Dockerfile"
    ).read_text(encoding="utf-8")

    assert spec.python_version == "3.13"
    assert f"/opt/conda/lib/python{spec.python_version}/site-packages" in dockerfile


def test_airflow_runtime_lock_matches_the_base_python_on_both_architectures() -> None:
    spec = next(
        item
        for item in check_runtime_locks.RUNTIME_LOCKS
        if "airflow" in item.requirements
    )

    assert spec.python_version == "3.13"
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
        "certifi==2026.10.19",
        "charset-normalizer==3.5.1",
        "idna==3.19",
        "psycopg2-binary==2.9.9",
        "pyjwt==2.15.1",
        "requests==2.33.0",
        "urllib3==2.8.0",
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
        assert not _inline_docker_install_violations(dockerfile), (
            f"{dockerfile.relative_to(root)} installs unlocked inline operands: "
            f"{sorted(_inline_docker_install_violations(dockerfile))}"
        )
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

    assert '"aiohttp>=3.14.4"' in refresher
    assert '"aiohttp>=3.14.4"' in project
    pins = re.findall(r"^aiohttp==(\S+) \\$", runtime, re.MULTILINE)
    assert len(pins) == 1 and Version(pins[0]) >= Version("3.14.4")


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
    assert audit_source.count("timeout_seconds=COMMAND_TIMEOUT_SECONDS") == 3
    # npm audit resolves the whole dependency graph against the registry and
    # runs just over four minutes for services/asset-worker/app, so it carries
    # its own longer deadline rather than sitting on the edge of the shared one.
    assert audit_source.count("timeout_seconds=NPM_AUDIT_TIMEOUT_SECONDS") == 1
    assert audit_runtime_locks.NPM_AUDIT_TIMEOUT_SECONDS == 900
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
    # The registry-error path retries with backoff; keep the suite fast.
    monkeypatch.setattr(audit_runtime_locks.time, "sleep", lambda _seconds: None)
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


def _npm_payload(*advisories: str) -> dict:
    """An npm audit report: each advisory on one package, plus a transitive parent."""
    vulnerabilities = {
        f"pkg-{index}": {"via": [{"url": f"https://github.com/advisories/{ghsa}"}]}
        for index, ghsa in enumerate(advisories)
    }
    if advisories:
        vulnerabilities["parent"] = {"via": ["pkg-0"]}
    total = len(vulnerabilities)
    return {"vulnerabilities": vulnerabilities, "metadata": {"vulnerabilities": {"total": total}}}


@pytest.mark.parametrize(
    ("advisories", "expected"),
    [
        (("GHSA-reviewed",), []),
        (
            ("GHSA-reviewed", "GHSA-new"),
            ["svc: unreviewed advisories: GHSA-new"],
        ),
        ((), ["svc: stale allowlist entries: GHSA-reviewed"]),
    ],
)
def test_npm_audit_accepts_only_reviewed_advisories(
    advisories: tuple[str, ...], expected: list[str], monkeypatch
) -> None:
    """#1332: npm findings pass only when each is a reviewed, live advisory."""
    monkeypatch.setattr(
        audit_runtime_locks,
        "NPM_PROJECT_EXCEPTIONS",
        {"svc": (frozenset({"GHSA-reviewed"}), date.today() + timedelta(days=30))},
    )
    payload = _npm_payload(*advisories)
    total = payload["metadata"]["vulnerabilities"]["total"]

    assert audit_runtime_locks._npm_finding_failures("svc", payload, total) == expected


def test_npm_audit_exceptions_share_the_review_deadline_contract(monkeypatch) -> None:
    review_date = date(2026, 10, 3)
    payload = _npm_payload("GHSA-reviewed")
    for review_by, expected in (
        (review_date, "review expired"),
        (review_date + timedelta(days=91), "horizon exceeds 90 days"),
    ):
        monkeypatch.setattr(
            audit_runtime_locks,
            "NPM_PROJECT_EXCEPTIONS",
            {"svc": (frozenset({"GHSA-reviewed"}), review_by)},
        )
        failures = audit_runtime_locks._npm_finding_failures(
            "svc", payload, 2, today=review_date
        )
        assert any(expected in failure for failure in failures)
    # A finding with no reviewed entry still names its advisory.
    assert audit_runtime_locks._npm_finding_failures("other", payload, 2) == [
        "other: unreviewed advisories: GHSA-reviewed"
    ]


def test_npm_audit_reviews_only_the_capped_n8n_zod_advisory() -> None:
    """The braces entry was retired on 2026-10-23; only the capped n8n Zod entry remains."""
    assert audit_runtime_locks.NPM_PROJECT_EXCEPTIONS == {
        "services/n8n/init/config": (frozenset({"GHSA-mrhx-c7xc-m3w5"}), date(2026, 11, 28)),
    }


def test_pip_audit_retries_only_an_empty_report(tmp_path: Path, monkeypatch) -> None:
    """An empty pip-audit report is retried, and a later good one is accepted."""
    monkeypatch.setattr(audit_runtime_locks.time, "sleep", lambda _seconds: None)
    attempts: list[int] = []

    def fake_run(*_args, **_kwargs):
        attempts.append(1)
        stdout = "" if len(attempts) == 1 else json.dumps({"dependencies": []})
        return SimpleNamespace(returncode=1, stdout=stdout, stderr="")

    monkeypatch.setattr(audit_runtime_locks, "run_bounded", fake_run)

    payload, failures = audit_runtime_locks._pip_audit_report(
        "svc/requirements-locked.txt", "requests==2.32.3\n", root=tmp_path
    )

    assert payload == {"dependencies": []}
    assert failures == []
    assert len(attempts) == 2


def test_pip_audit_gives_up_after_repeated_empty_reports(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(audit_runtime_locks.time, "sleep", lambda _seconds: None)
    attempts: list[int] = []

    def fake_run(*_args, **_kwargs):
        attempts.append(1)
        return SimpleNamespace(returncode=1, stdout="  \n", stderr="")

    monkeypatch.setattr(audit_runtime_locks, "run_bounded", fake_run)

    payload, failures = audit_runtime_locks._pip_audit_report(
        "svc/requirements-locked.txt", "requests==2.32.3\n", root=tmp_path
    )

    assert payload is None
    assert failures == [
        "svc/requirements-locked.txt: pip-audit returned no report (details redacted)"
    ]
    assert len(attempts) == audit_runtime_locks._PIP_AUDIT_ATTEMPTS


def test_pip_audit_does_not_retry_a_malformed_report(
    tmp_path: Path, monkeypatch
) -> None:
    """Only the empty case is transient; garbage must fail on the first attempt."""
    monkeypatch.setattr(audit_runtime_locks.time, "sleep", lambda _seconds: None)
    attempts: list[int] = []

    def fake_run(*_args, **_kwargs):
        attempts.append(1)
        return SimpleNamespace(returncode=0, stdout="<html>502</html>", stderr="")

    monkeypatch.setattr(audit_runtime_locks, "run_bounded", fake_run)

    payload, failures = audit_runtime_locks._pip_audit_report(
        "svc/requirements-locked.txt", "requests==2.32.3\n", root=tmp_path
    )

    assert payload is None
    assert len(failures) == 1
    assert failures[0].startswith(
        "svc/requirements-locked.txt: invalid pip-audit JSON:"
    )
    assert len(attempts) == 1


def test_mlflow_lock_is_checked_for_the_images_python() -> None:
    """The gate resolved the MLflow lock for 3.10 after the v3.16.1 base
    moved to Python 3.11 (#1323): a pin without a cp311 wheel would pass
    the gate and fail in the image. The two move together."""
    env_example = (Path(check_runtime_locks.__file__).parents[1] / ".env.example").read_text(encoding="utf-8")
    assert "MLFLOW_IMAGE=ghcr.io/mlflow/mlflow:v3.16.1" in env_example
    spec = next(item for item in check_runtime_locks.RUNTIME_LOCKS if "mlflow" in item.requirements)
    assert spec.python_version == "3.11"


def test_every_test_lock_pins_the_shipped_runtime_versions():
    """The backend test lock pinned openai 2.52.1 / typer 0.27.1 while the
    image shipped 2.52.0 / 0.27.0, so the suite never ran the shipped
    closure (2026-10-08 run, cycle 41)."""
    assert [d for spec in check_test_locks.TEST_LOCKS for d in check_test_locks.runtime_drift(spec)] == []


def test_runtime_drift_reports_a_differing_pin(tmp_path, monkeypatch):
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "requirements-locked.txt").write_text("openai==2.52.0\nshared==1.0\n")
    (tmp_path / "svc" / "requirements-test-locked.txt").write_text("openai==2.52.1\nshared==1.0\npytest==9\n")
    monkeypatch.setattr(check_test_locks, "ROOT", tmp_path)
    spec = check_test_locks.TestLock("svc/requirements-test.txt", "svc/requirements-test-locked.txt")
    assert check_test_locks.runtime_drift(spec) == [
        "svc/requirements-test-locked.txt: openai==2.52.1 but svc/requirements-locked.txt ships 2.52.0"
    ]


@pytest.mark.parametrize(("directory", "package", "floor"), (
    ("services/backend/app/app", "langgraph-sdk", "0.4.6"),
    ("services/jupyterhub/build", "langgraph-sdk", "0.4.6"),
    ("services/jupyterhub/build", "tornado", "6.6"),
    ("services/airflow/build", "tornado", "6.6"),
    ("services/airflow/build", "snowflake-connector-python", "4.8.1"),
    ("services/airflow/build", "filelock", "4.2.1"),
))
def test_fixed_sdk_and_server_dependencies_survive_refresh(directory, package, floor):
    root = Path(check_runtime_locks.__file__).parents[1] / directory
    declarations = [
        Requirement(line.split("#", 1)[0].strip())
        for line in (root / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    matching = [req for req in declarations if req.name.lower().replace("_", "-") == package]
    assert len(matching) == 1, f"Missing durable {package} security floor"
    assert matching[0].marker is None
    assert any(bound.operator == ">=" and Version(bound.version) >= Version(floor)
               for bound in matching[0].specifier)
    assert Version(check_test_locks._pins(root / "requirements-locked.txt")[package]) >= Version(floor)


def test_airflow_snowflake_repair_retains_reviewed_base_compatibility():
    root = Path(check_runtime_locks.__file__).parents[1] / "services/airflow/build"
    declarations = set((root / "requirements.txt").read_text().splitlines())
    assert {
        "botocore<1.43.76", "importlib-metadata<9", "protobuf<6.34", "websockets<17",
        "snowflake-connector-python>=4.8.1,<5", "filelock>=4.2.1,<5",
    } <= declarations
    locked = check_test_locks._pins(root / "requirements-locked.txt")
    assert locked.get("snowflake-connector-python") == "4.8.1"
    assert locked.get("filelock") == "4.2.1"
    # Public metadata for the unchanged base's Snowpark1.55.0, SQLAlchemy1.11.1,
    # Snowflake provider6.17.0, and Flower2.1.0, plus the existing overlay's
    # virtualenv21.14.10. A connector5/Filelock5 refresh would break this family.
    for package, accepted in (
        ("snowflake-connector-python", ">=3.17.0,<5.0.0"),
        ("snowflake-connector-python", "<5.0.0"),
        ("snowflake-connector-python", ">=3.17.0"),
        ("filelock", ">=3.24.2,<5"),
        ("tornado", ">=6.5.7,<7.0.0"),
    ):
        assert Version(locked[package]) in SpecifierSet(accepted)



_DOCLING_LOCALHOST = "services/docling/provider/localhost"


def _uv_export_audit_collector(monkeypatch, export_text, vulnerable=frozenset()):
    """Fake `uv export` with ``export_text`` and record each pip-audit input."""
    captured: list[dict[str, str]] = []

    def run(command, **kwargs):
        if command[0] == "uv":
            Path(command[command.index("--output-file") + 1]).write_text(export_text)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        text = Path(command[command.index("-r") + 1]).read_text()
        pairs = dict(line.split("==", 1) for line in text.splitlines())
        captured.append(pairs)
        payload = {"dependencies": [
            {"name": name, "version": version,
             "vulns": [{"id": "GHSA-test"}] if (name, version) in vulnerable else []}
            for name, version in pairs.items()
        ]}
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(audit_runtime_locks, "run_bounded", run)
    return captured


_DOCLING_SHAPED_EXPORT = (
    "# This file was autogenerated by uv via the following command:\n"
    "accelerate==1.15.0\n    # via docling-ibm-models\n"
    "nvidia-cublas==13.1.1.3 ; sys_platform == 'linux'\n"
    "pywin32==311 ; sys_platform == 'win32'\n"
    "scipy==1.15.3 ; sys_platform != 'darwin'\n"
    "scipy==1.16.0 ; sys_platform == 'darwin'\n"
)


def test_docling_localhost_uv_audit_projects_every_supported_target(monkeypatch):
    environments = audit_runtime_locks.UV_PROJECT_MARKER_ENVIRONMENTS
    assert set(environments) == {_DOCLING_LOCALHOST}
    assert set(environments[_DOCLING_LOCALHOST]) == {
        "linux-x86_64", "linux-aarch64", "darwin-arm64",
    }
    captured = _uv_export_audit_collector(monkeypatch, _DOCLING_SHAPED_EXPORT)

    assert audit_runtime_locks.audit_uv_project(_DOCLING_LOCALHOST) == []
    linux = {"accelerate": "1.15.0", "nvidia-cublas": "13.1.1.3", "scipy": "1.15.3"}
    assert captured == [linux, linux, {"accelerate": "1.15.0", "scipy": "1.16.0"}]


def test_docling_localhost_uv_audit_reports_a_macos_only_advisory(monkeypatch):
    _uv_export_audit_collector(
        monkeypatch, _DOCLING_SHAPED_EXPORT, vulnerable={("scipy", "1.16.0")}
    )

    assert audit_runtime_locks.audit_uv_project(_DOCLING_LOCALHOST) == [
        f"{_DOCLING_LOCALHOST}/uv.lock [darwin-arm64]: unreviewed advisories: GHSA-test"
    ]


def test_unprojected_uv_project_audit_keeps_one_universal_pass(monkeypatch):
    captured = _uv_export_audit_collector(monkeypatch, "safe==1.0\n")

    assert "bootstrapper" not in audit_runtime_locks.UV_PROJECT_MARKER_ENVIRONMENTS
    assert audit_runtime_locks.audit_uv_project("bootstrapper") == []
    assert captured == [{"safe": "1.0"}]


def test_docling_localhost_export_selects_the_platform_scipy_branches(monkeypatch):
    """The committed lock keeps SciPy 1.16.0 on macOS and 1.15.3 elsewhere."""
    import shutil

    if not shutil.which("uv"):
        pytest.skip("uv is not installed")
    monkeypatch.setenv("UV_OFFLINE", "1")
    real_run = audit_runtime_locks.run_bounded
    captured: list[dict[str, str]] = []

    def run(command, **kwargs):
        if command[0] == "uv":
            return real_run(command, **kwargs)
        text = Path(command[command.index("-r") + 1]).read_text()
        captured.append(dict(line.split("==", 1) for line in text.splitlines()))
        payload = {"dependencies": [
            {"name": name, "version": version, "vulns": []}
            for name, version in captured[-1].items()
        ]}
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(audit_runtime_locks, "run_bounded", run)
    assert audit_runtime_locks.audit_uv_project(_DOCLING_LOCALHOST) == []
    assert [pairs["scipy"] for pairs in captured] == ["1.15.3", "1.15.3", "1.16.0"]
    assert "torch" in captured[2] and "nvidia-cublas" not in captured[2]
    assert all("nvidia-cublas" in pairs for pairs in captured[:2])
