"""#2108 stage 2: the derived ComfyUI image pins the ComfyUI venv to its Trivy fixed versions.

The base image's venv is Python 3.10, so pillow, rembg and soupsieve stay on their newest
Python 3.10 releases. This static test pins the reviewed set, the torch CPU index and the
G3 check, so a pin cannot drift without a test change.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "services" / "comfyui" / "build" / "Dockerfile"

STAGE2_PINS = [
    "GitPython==3.2.1",
    "PyJWT==2.14.0",
    "aiohttp==3.14.4",
    "anyio==4.14.2",
    "certifi==2026.10.19",
    "cryptography==50.0.0",
    "fsspec==2026.6.0",
    "h11==0.16.0",
    "jaraco.context==6.1.0",
    "jupyter_core==5.8.1",
    "oauthlib==4.0.1",
    "opencv-python==4.8.1.78",
    "pillow==12.3.0",
    "wheel==0.46.2",
    "protobuf==6.33.5",
    "rembg==2.0.69",
    "sentencepiece==0.2.1",
    "setuptools==78.1.1",
    "soupsieve==2.10",
    "starlette==1.3.1",
    "tornado==6.5.9",
    "transformers==5.10.0",
    "urllib3==2.8.0",
    "torch==2.10.0+cpu",
    "torchvision==0.25.0+cpu",
    "torchaudio==2.10.0+cpu",
]


def _stage2() -> str:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert text.count("# Stage 2 (#2108)") == 1
    return text.split("# Stage 2 (#2108)", 1)[1]


def test_stage2_pins_every_reviewed_package():
    block = _stage2()
    for pin in STAGE2_PINS:
        assert f'"{pin}"' in block, pin


def test_torch_family_comes_from_the_cpu_index_and_moves_together():
    block = _stage2()
    assert "--extra-index-url https://download.pytorch.org/whl/cpu" in block
    for name in ("torch", "torchvision", "torchaudio"):
        assert f'"{name}==' in block and '+cpu"' in block


def test_stage2_fails_the_build_if_g3_remains_in_the_comfy_certifi():
    assert "emSign ECC Root CA - G3 is still in the ComfyUI environment certifi" in _stage2()
