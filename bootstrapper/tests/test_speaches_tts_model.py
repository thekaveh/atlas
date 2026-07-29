"""#799: SPEACHES_TTS_MODEL must default to the Speaches Kokoro ONNX executor id
(`speaches-ai/Kokoro-82M-v1.0-ONNX`), not the invalid HuggingFace
`hexgrad/Kokoro-82M` id that Speaches 404s on. Covers the manifest default and
the service_config fallback that propagates the id into Open WebUI's
OPEN_WEB_UI_TTS_MODEL. The PRELOAD_MODELS / first-boot-preload half of #799
needs a live Speaches container and is documented as downstream.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_YML = REPO_ROOT / "services" / "speaches" / "service.yml"
SERVICE_CONFIG = REPO_ROOT / "bootstrapper" / "services" / "service_config.py"

CORRECT = "speaches-ai/Kokoro-82M-v1.0-ONNX"
WRONG = "hexgrad/Kokoro-82M"


def test_speaches_tts_model_default_is_the_speaches_onnx_id():
    """AC #3: the manifest default is the Speaches Kokoro ONNX executor id."""
    doc = yaml.safe_load(SERVICE_YML.read_text(encoding="utf-8"))
    tts = next(e for e in doc["env"] if e["name"] == "SPEACHES_TTS_MODEL")
    assert tts["default"] == CORRECT, (
        f"SPEACHES_TTS_MODEL default must be {CORRECT!r} (the Speaches Kokoro "
        f"ONNX executor id); {WRONG!r} is invalid for Speaches and 404s (#799)."
    )


def test_service_config_speaches_tts_fallback_is_the_speaches_onnx_id():
    """AC #3: the OPEN_WEB_UI_TTS_MODEL fallback (used when SPEACHES_TTS_MODEL
    is absent from .env) is the correct id, and the wrong id is fully gone."""
    src = SERVICE_CONFIG.read_text(encoding="utf-8")
    assert CORRECT in src, (
        "service_config.py must fall back to speaches-ai/Kokoro-82M-v1.0-ONNX "
        "for the speaches TTS model propagation (#799)."
    )
    assert WRONG not in src, (
        f"service_config.py still references the invalid {WRONG!r} id (#799)."
    )


def test_speaches_stt_model_documented_as_inert():
    """AC #5: SPEACHES_STT_MODEL is a reserved knob wired to nothing
    (PRELOAD_MODELS is a hard-coded literal, not interpolated from it); the
    description must say so so an operator doesn't expect it to take effect."""
    doc = yaml.safe_load(SERVICE_YML.read_text(encoding="utf-8"))
    stt = next(e for e in doc["env"] if e["name"] == "SPEACHES_STT_MODEL")
    desc = (stt.get("description") or "").lower()
    assert "inert" in desc, (
        "SPEACHES_STT_MODEL description must document that it is currently "
        "inert (PRELOAD_MODELS is not interpolated from it) — #799 AC #5."
    )
