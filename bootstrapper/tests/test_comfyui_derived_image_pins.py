"""#2108 stage 2: the derived ComfyUI image rebuilds its venv on Python 3.11 from the audited lock.

These static checks pin the uv binary, the Python build, the lock install, the pip constraints
that keep a boot-time install from changing torch or adding torchaudio, and the G3 check. A change
to any of them needs a test change. The lock itself is checked against its removal list.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "services" / "comfyui" / "build"
DOCKERFILE = BUILD / "Dockerfile"
LOCK = BUILD / "requirements-venv311-locked.txt"

UV_VERSION = "0.11.19"
UV_SHA256 = "7035608168e106375b36d0c818d537a889c51a8625fe7f8f7cad5e62b947c368"
COMFYUI_PYTHON = "3.11.15"

# Dropped from the ai-dock Python 3.10 set (see the lock header and the #2108 ticket).
REMOVED_FROM_LOCK = {
    "torchaudio",
    "transparent-background",
    "flet",
    "flet-runtime",
    "matrix-client",
}

EXPECTED_LOCK_PINS = {
    "torch": "2.14.0+cpu",
    "torchvision": "0.29.0+cpu",
    "pillow": "13.0.0",
    "rembg": "2.0.88",
    "soupsieve": "4.1.3",
    "tornado": "6.6.1",
    "pyjwt": "2.15.1",
    "setuptools": "84.2.1",
    "certifi": "2026.10.19",
    "starlette": "1.3.1",
}


def _stage2() -> str:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert text.count("# Stage 2 (#2108)") == 1
    return text.split("# Stage 2 (#2108)", 1)[1]


def _lock_pins() -> dict[str, str]:
    pins = {}
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9_.\-]+)==(\S+)", line.strip())
        if match:
            pins[match.group(1).lower()] = match.group(2)
    return pins


def test_stage2_builds_python_311_from_a_checksummed_uv():
    block = _stage2()
    assert f"ARG UV_VERSION={UV_VERSION}" in block
    assert f"ARG UV_SHA256={UV_SHA256}" in block
    assert f"ARG COMFYUI_PYTHON={COMFYUI_PYTHON}" in block
    assert "sha256sum -c -" in block
    assert "uv-x86_64-unknown-linux-gnu/uv" in block
    assert 'uv python install "${COMFYUI_PYTHON}"' in block
    assert 'uv venv --seed --python "${COMFYUI_PYTHON}" /opt/environments/python/comfyui' in block


def test_stage2_installs_only_the_audited_lock_with_no_inline_pins():
    text = DOCKERFILE.read_text(encoding="utf-8")
    block = _stage2()
    assert "COPY requirements-venv311-locked.txt /opt/atlas/comfyui-venv311-locked.txt" in text
    assert "-r /opt/atlas/comfyui-venv311-locked.txt" in block
    assert "--extra-index-url https://download.pytorch.org/whl/cpu" in block
    assert "UV_INDEX_STRATEGY=unsafe-best-match" in block
    assert not re.search(r'"[A-Za-z0-9_.\-]+==', block), "stage 2 must not carry inline pins"


def test_lock_has_the_reviewed_versions_and_no_removed_packages():
    pins = _lock_pins()
    for name, version in EXPECTED_LOCK_PINS.items():
        assert pins.get(name) == version, (name, pins.get(name))
    assert not REMOVED_FROM_LOCK & set(pins), REMOVED_FROM_LOCK & set(pins)


def test_boot_time_installs_cannot_change_torch_or_add_torchaudio():
    # ai-dock's AUTO_UPDATE step runs pip install -r requirements.txt on every boot, and that file
    # lists torchaudio unpinned. The constraint file refuses torchaudio and keeps the lock pins.
    block = _stage2()
    assert "torchaudio<0" in block
    assert "cp /opt/atlas/comfyui-venv311-locked.txt /opt/atlas/comfyui-venv311-constraints.txt" in block
    assert "constraint = /opt/atlas/comfyui-venv311-constraints.txt" in block
    assert "/opt/environments/python/comfyui/pip.conf" in block


def test_stage2_fails_the_build_if_g3_remains_in_the_comfy_certifi():
    assert "emSign ECC Root CA - G3 is still in the ComfyUI environment certifi" in _stage2()


def test_quick_tunnels_are_off_in_the_derived_image():
    # ai-dock reads CF_QUICK_TUNNELS, not the ENABLE_QUICKTUNNEL flag Atlas sets in compose.
    # Without it the image publishes ComfyUI, Jupyter, the portal and syncthing on public URLs.
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "ENV CF_QUICK_TUNNELS=false" in text
