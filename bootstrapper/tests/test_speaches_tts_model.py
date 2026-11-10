"""#799: Speaches must answer STT/TTS out of the box instead of 404ing.

SPEACHES_TTS_MODEL must default to the Speaches Kokoro ONNX executor id
(`speaches-ai/Kokoro-82M-v1.0-ONNX`), not the invalid HuggingFace
`hexgrad/Kokoro-82M` id that Speaches 404s on. Covers the manifest default and
the service_config fallback that propagates the id into Open WebUI's
OPEN_WEB_UI_TTS_MODEL.

Speaches only serves models already in its cache, so the compose fragment
preloads every model an in-stack consumer requests and gives the healthcheck
room for the first-boot download. These tests pin the static contract; an
actual cold-cache boot still needs a live Speaches container.
"""
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_YML = REPO_ROOT / "services" / "speaches" / "service.yml"
COMPOSE_YML = REPO_ROOT / "services" / "speaches" / "compose.yml"
SERVICE_CONFIG = REPO_ROOT / "bootstrapper" / "services" / "service_config.py"
OPEN_WEBUI_COMPOSE = REPO_ROOT / "services" / "open-webui" / "compose.yml"
HERMES_TEMPLATE = (
    REPO_ROOT / "services" / "hermes" / "init" / "templates" / "config.yaml.tmpl"
)
HERMES_INIT = REPO_ROOT / "services" / "hermes" / "init" / "scripts" / "init-hermes.sh"

CORRECT = "speaches-ai/Kokoro-82M-v1.0-ONNX"
WRONG = "hexgrad/Kokoro-82M"

# model_aliases.json in speaches-ai/speaches @ v0.9.0-rc.3 (commit 24f209c9),
# which the image ships in its working directory. PRELOAD_MODELS entries are
# not alias-resolved, so the preload must name these targets directly.
PINNED_SPEACHES_RELEASE = "0.9.0-rc.3"
UPSTREAM_ALIASES = {
    "tts-1": CORRECT,
    "tts-1-hd": CORRECT,
    "whisper-1": "Systran/faster-whisper-large-v3",
}
# A cold cache downloads about 3 GB before Uvicorn serves /health.
MIN_PRELOAD_START_PERIOD_SECONDS = 600


def _manifest_default(name: str) -> str:
    doc = yaml.safe_load(SERVICE_YML.read_text(encoding="utf-8"))
    return next(e for e in doc["env"] if e["name"] == name)["default"]


def _speaches_service() -> dict:
    compose = yaml.safe_load(COMPOSE_YML.read_text(encoding="utf-8"))
    return compose["services"]["speaches"]


def _seconds(duration: str) -> int:
    match = re.fullmatch(r"(\d+)s", duration)
    assert match, f"expected a plain seconds duration, got {duration!r}"
    return int(match.group(1))


def _hermes_model(block: str) -> str:
    """The model a Hermes config block requests. The template nests it under
    the provider (``tts.openai.model``); a ``${VAR}`` value resolves to the
    default init-hermes.sh exports for it."""
    template = HERMES_TEMPLATE.read_text(encoding="utf-8")
    match = re.search(
        rf"# BEGIN-{block}\n.*?^\s+model: (\S+)$.*?# END-{block}",
        template,
        re.DOTALL | re.MULTILINE,
    )
    assert match, f"Hermes template has no {block} model line"
    model = match.group(1)
    ref = re.fullmatch(r"\$\{(\w+)\}", model)
    if ref:
        init = HERMES_INIT.read_text(encoding="utf-8")
        default = re.search(
            rf'^export {ref.group(1)}="\$\{{{ref.group(1)}:-([^}}]+)\}}"$', init, re.MULTILINE
        )
        assert default, f"init-hermes.sh exports no default for {ref.group(1)}"
        model = default.group(1)
    return model


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
    preload = _speaches_service()["environment"]["PRELOAD_MODELS"]
    assert "SPEACHES_STT_MODEL" not in preload, (
        "SPEACHES_STT_MODEL is documented as inert, so PRELOAD_MODELS must not "
        "interpolate it; update the description and capability if it is wired."
    )


def test_preload_models_is_a_literal_json_array_of_the_alias_targets():
    """AC #2: Speaches decodes PRELOAD_MODELS with json.loads and exits on an
    unknown id, so it is a literal JSON array of the two alias targets."""
    raw = _speaches_service()["environment"]["PRELOAD_MODELS"]
    assert "${" not in raw, (
        "PRELOAD_MODELS must not interpolate .env values: Speaches exits at "
        "boot on an unknown id, taking both STT and TTS down (#799)."
    )
    assert json.loads(raw) == [CORRECT, UPSTREAM_ALIASES["whisper-1"]]


def test_alias_targets_match_the_pinned_speaches_release():
    """The alias table above is release-specific; moving the image pin means
    re-reading model_aliases.json at the new tag and updating it."""
    doc = yaml.safe_load(SERVICE_YML.read_text(encoding="utf-8"))
    pins = [image["default"] for image in doc["images"]]
    pins.append(_manifest_default("SPEACHES_GPU_IMAGE"))
    for pin in pins:
        tag = pin.split("@", 1)[0].rsplit(":", 1)[1]
        assert tag.startswith(f"{PINNED_SPEACHES_RELEASE}-"), (
            f"{pin} moved off {PINNED_SPEACHES_RELEASE}: re-verify the upstream "
            "model_aliases.json targets and PRELOAD_MODELS for that release."
        )


def test_preload_covers_every_model_atlas_consumers_request():
    """AC #1/#2: every in-stack STT/TTS request resolves, through Speaches'
    alias table, to a preloaded id, so a cold cache volume does not 404."""
    webui = yaml.safe_load(OPEN_WEBUI_COMPOSE.read_text(encoding="utf-8"))
    webui_env = webui["services"]["open-web-ui"]["environment"]
    requested = {
        "Open WebUI STT": webui_env["AUDIO_STT_MODEL"],
        "Open WebUI TTS": _manifest_default("SPEACHES_TTS_MODEL"),
        "Hermes STT": _hermes_model("STT"),
        "Hermes TTS": _hermes_model("TTS"),
    }
    preloaded = set(
        json.loads(_speaches_service()["environment"]["PRELOAD_MODELS"])
    )
    for consumer, model in requested.items():
        resolved = UPSTREAM_ALIASES.get(model, model)
        assert resolved in preloaded, (
            f"{consumer} requests {model!r} ({resolved!r}), which Speaches "
            "would 404 because PRELOAD_MODELS does not include it (#799)."
        )


def test_start_period_covers_the_first_boot_preload_download():
    """AC #4: Uvicorn only serves /health after the preload, so start_period
    must cover a cold download while unhealthy still lands inside the
    bootstrapper's `up --wait` timeout."""
    from core.docker_manager import DockerManager

    healthcheck = _speaches_service()["healthcheck"]
    start_period = _seconds(healthcheck["start_period"])
    unhealthy_after = start_period + healthcheck["retries"] * _seconds(
        healthcheck["interval"]
    )
    wait_timeout = (
        inspect.signature(DockerManager.start_services)
        .parameters["wait_timeout_seconds"]
        .default
    )
    assert start_period >= MIN_PRELOAD_START_PERIOD_SECONDS
    assert unhealthy_after <= wait_timeout


def test_a_warm_cache_skips_the_hub_preload_so_offline_restarts_work():
    """#799: Speaches lists the preload models on the Hub at every boot and
    exits when it is unreachable, even with a warm cache (observed offline on
    0.9.0-rc.3). The start command clears PRELOAD_MODELS when every preloaded
    model's weight file is cached, and otherwise keeps the preload."""
    command = _speaches_service()["command"]
    script = command[2]

    assert command[:2] == ["sh", "-c"]
    for model, weight in (
        ("speaches-ai--Kokoro-82M-v1.0-ONNX", "model.onnx"),
        ("speaches-ai--Kokoro-82M-v1.0-ONNX", "voices.bin"),
        ("Systran--faster-whisper-large-v3", "model.bin"),
    ):
        assert f"cached {model} {weight}" in script
    assert "export PRELOAD_MODELS='[]'" in script
    assert script.rstrip().endswith("exec uvicorn --factory speaches.main:create_app")
    assert json.loads(_speaches_service()["environment"]["PRELOAD_MODELS"]) == [
        CORRECT, "Systran/faster-whisper-large-v3"]
