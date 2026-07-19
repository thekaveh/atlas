"""Shared host-capability probe for platform-adaptive source selection (#753).

One small, dependency-free, testable snapshot of what the current host can
run. Consumed by the ``<SVC>_SOURCE: auto`` resolver in ``start.py`` (which
matches a manifest's declarative ``sources.auto_prefer`` entries against these
capabilities) and available to the managed-host managers, whose inline
``platform.system()``/``platform.machine()`` checks this consolidates
(see ``comfyui_mps_manager.preflight`` / ``vllm_metal_manager``).

Probes are deliberately cheap and offline: filesystem / PATH / platform-module
lookups only — no network calls, no subprocess spawns. An *unknown* capability
is reported as absent, so ``auto`` resolution degrades to the safe terminal
fallback (typically ``container-cpu``) rather than selecting an unstartable
source.
"""

from __future__ import annotations

import platform
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HostCapabilities:
    """Snapshot of host capabilities relevant to source selection."""

    os_name: str
    machine: str
    apple_silicon: bool
    nvidia_gpu: bool
    host_ollama: bool

    def has(self, capability: str) -> bool:
        """True when the named capability holds on this host.

        Unknown capability names return False (absent), keeping ``auto``
        resolution safe when a manifest declares a capability this Atlas
        version does not know how to probe.
        """
        return bool(getattr(self, capability, False))


# The capability vocabulary manifests may reference in `sources.auto_prefer[].
# requires_capability`. Kept in lockstep with the JSON schema enum and the
# manifest-validator lint.
KNOWN_CAPABILITIES = ("apple_silicon", "nvidia_gpu", "host_ollama")


def _probe_apple_silicon(os_name: str, machine: str) -> bool:
    """macOS on arm64 — the gate the MPS/Metal managed sources require."""
    return os_name == "Darwin" and machine in ("arm64", "aarch64")


def _probe_nvidia_gpu() -> bool:
    """Cheap, dependency-free NVIDIA presence probe.

    ``nvidia-smi`` on PATH or the ``/proc/driver/nvidia`` tree existing. No
    subprocess is spawned — presence is enough for source *selection*; the
    container runtime still validates GPU access at startup.
    """
    if shutil.which("nvidia-smi"):
        return True
    try:
        return Path("/proc/driver/nvidia").exists()
    except OSError:  # pragma: no cover - defensive (restricted /proc)
        return False


def _probe_host_ollama() -> bool:
    """A host Ollama installation (the ``ollama`` binary on PATH).

    Deliberately not a network probe: the daemon may not be running at
    resolve time, but an installed binary signals the operator's intent to
    serve models from the host (`ollama serve` autostarts on macOS installs).
    """
    return bool(shutil.which("ollama"))


def probe_host_capabilities() -> HostCapabilities:
    """Probe the current host. Pure function of the environment — cheap
    enough to call per start; tests construct ``HostCapabilities`` directly
    instead of monkeypatching the probes."""
    os_name = platform.system()
    machine = platform.machine().lower()
    return HostCapabilities(
        os_name=os_name,
        machine=machine,
        apple_silicon=_probe_apple_silicon(os_name, machine),
        nvidia_gpu=_probe_nvidia_gpu(),
        host_ollama=_probe_host_ollama(),
    )
