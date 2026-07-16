from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest
import start as start_module


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class _Status:
    running: bool
    pid: int | None = None
    port: int = 8000
    log_file: str = "/tmp/managed-host.log"


class _Manager:
    def __init__(self, *, running: bool = False, fail_start: Exception | None = None):
        self.running = running
        self.fail_start = fail_start
        self.stop_calls = 0

    def status(self) -> _Status:
        return _Status(self.running, 42 if self.running else None)

    def ensure_running_with_ownership(self) -> tuple[_Status, bool]:
        if self.fail_start is not None:
            raise self.fail_start
        created = not self.running
        self.running = True
        return self.status(), created

    def wait_healthy(self, **_kwargs) -> dict[str, object]:
        return {"reachable": True, "device": "mps"}

    def stop(self) -> bool:
        self.stop_calls += 1
        was_running = self.running
        self.running = False
        return was_running


def test_generate_service_configuration_does_not_launch_native_hosts(monkeypatch):
    starter = start_module.AtlasStarter()
    monkeypatch.setattr(starter.service_config, "generate_and_update_env", lambda: True)
    for name in (
        "_finalize_consumer_storage",
        "_finalize_consumer_litellm_models",
        "_finalize_consumer_n8n_workflows",
        "_finalize_consumer_rag_ingestion_profiles",
        "_finalize_consumer_lightrag_query_profiles",
    ):
        monkeypatch.setattr(starter, name, lambda: True)
    monkeypatch.setattr(
        starter,
        "_finalize_managed_comfyui_mps",
        lambda: (_ for _ in ()).throw(AssertionError("configuration launched ComfyUI")),
    )
    monkeypatch.setattr(
        starter,
        "_finalize_managed_vllm_metal",
        lambda: (_ for _ in ()).throw(AssertionError("configuration launched vLLM")),
    )

    assert starter.generate_service_configuration() is True


def test_second_managed_host_failure_rolls_back_only_newly_started_host(monkeypatch):
    from services import comfyui_mps_manager, vllm_metal_manager

    starter = start_module.AtlasStarter()
    comfy = _Manager()
    vllm = _Manager(fail_start=vllm_metal_manager.VllmMetalError("boom"))
    monkeypatch.setattr(
        starter.config_parser,
        "parse_env_file",
        lambda: {
            "COMFYUI_SOURCE": "managed-localhost-mps",
            "VLLM_METAL_SOURCE": "managed-localhost",
            "VLLM_METAL_MODEL": "example/model",
        },
    )
    monkeypatch.setattr(comfyui_mps_manager, "manager_from_env", lambda _env: comfy)
    monkeypatch.setattr(vllm_metal_manager, "manager_from_env", lambda _env: vllm)

    assert starter.start_managed_host_processes() is False
    assert comfy.stop_calls == 1
    assert comfy.running is False


def test_unexpected_second_host_error_still_rolls_back_first(monkeypatch):
    from services import comfyui_mps_manager, vllm_metal_manager

    starter = start_module.AtlasStarter()
    comfy = _Manager()
    vllm = _Manager(fail_start=RuntimeError("unexpected"))
    monkeypatch.setattr(
        starter.config_parser,
        "parse_env_file",
        lambda: {
            "COMFYUI_SOURCE": "managed-localhost-mps",
            "VLLM_METAL_SOURCE": "managed-localhost",
        },
    )
    monkeypatch.setattr(comfyui_mps_manager, "manager_from_env", lambda _env: comfy)
    monkeypatch.setattr(vllm_metal_manager, "manager_from_env", lambda _env: vllm)

    with pytest.raises(RuntimeError, match="unexpected"):
        starter.start_managed_host_processes()

    assert comfy.stop_calls == 1
    assert comfy.running is False


def test_surviving_untracked_child_is_added_to_rollback_ownership(monkeypatch):
    from services import comfyui_mps_manager

    starter = start_module.AtlasStarter()
    failure = comfyui_mps_manager.ComfyUiMpsError(
        "metadata and compensation failed", surviving_process=True
    )
    comfy = _Manager(running=True, fail_start=failure)
    monkeypatch.setattr(
        starter.config_parser,
        "parse_env_file",
        lambda: {
            "COMFYUI_SOURCE": "managed-localhost-mps",
            "VLLM_METAL_SOURCE": "disabled",
        },
    )
    monkeypatch.setattr(comfyui_mps_manager, "manager_from_env", lambda _env: comfy)

    assert starter.start_managed_host_processes() is False
    assert comfy.stop_calls == 1
    assert comfy.running is False


def test_rollback_does_not_stop_preexisting_managed_host(monkeypatch):
    from services import comfyui_mps_manager

    starter = start_module.AtlasStarter()
    comfy = _Manager(running=True)
    monkeypatch.setattr(
        starter.config_parser,
        "parse_env_file",
        lambda: {
            "COMFYUI_SOURCE": "managed-localhost-mps",
            "VLLM_METAL_SOURCE": "disabled",
        },
    )
    monkeypatch.setattr(comfyui_mps_manager, "manager_from_env", lambda _env: comfy)

    assert starter.start_managed_host_processes() is True
    assert starter.rollback_managed_host_processes() is True
    assert comfy.stop_calls == 0
    assert comfy.running is True


def test_docker_start_failure_rolls_back_managed_hosts(monkeypatch):
    starter = start_module.AtlasStarter()
    calls: list[str] = []
    monkeypatch.setattr(
        starter.docker_manager,
        "start_services",
        lambda **_kwargs: 1,
    )
    monkeypatch.setattr(
        starter,
        "rollback_managed_host_processes",
        lambda: calls.append("rollback") or True,
    )

    assert starter.start_docker_services() is False
    assert calls == ["rollback"]


def test_docker_start_success_commits_managed_hosts(monkeypatch):
    starter = start_module.AtlasStarter()
    calls: list[str] = []
    monkeypatch.setattr(
        starter.docker_manager,
        "start_services",
        lambda **_kwargs: 0,
    )
    monkeypatch.setattr(starter, "verify_one_shot_init_containers", lambda: True)
    monkeypatch.setattr(
        starter,
        "commit_managed_host_processes",
        lambda: calls.append("commit"),
    )

    assert starter.start_docker_services() is True
    assert calls == ["commit"]


def test_tui_starts_managed_hosts_after_setup_and_rolls_back_failures():
    source = (
        REPO_ROOT / "bootstrapper/ui/textual/screens/wizard_screen.py"
    ).read_text(encoding="utf-8")
    setup = source.index("starter.generate_service_configuration),")
    managed = source.index("starter.start_managed_host_processes", setup)
    compose_up = source.index('self._run_compose(["up"', managed)

    assert setup < managed < compose_up
    assert "asyncio.shield(managed_host_start_task)" in source[managed:compose_up]
    assert source.count("starter.rollback_managed_host_processes", managed) == 1
    assert source.index("starter.commit_managed_host_processes", managed) > compose_up


def test_uncancellable_cleanup_wait_survives_repeated_cancellation():
    from ui.textual.screens.wizard_screen import _await_uncancellable

    async def scenario():
        release = asyncio.Event()

        async def work():
            await release.wait()
            return "settled"

        worker = asyncio.create_task(work())
        waiter = asyncio.create_task(_await_uncancellable(worker))
        await asyncio.sleep(0)
        waiter.cancel()
        await asyncio.sleep(0)
        waiter.cancel()
        release.set()
        return await waiter

    assert asyncio.run(scenario()) == "settled"
