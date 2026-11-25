"""#2111: the Speaches preload pins in compose.yml and in pin-cache.sh stay in step.

The compose command verifies the three weight hashes at boot and runs offline only when
they match. pin-cache.sh stages every file at its pinned commit. Both must name the same
hashes, or a boot check and a staged cache could disagree without a test failing.
"""

import re
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "services" / "speaches" / "compose.yml"
SCRIPT = ROOT / "services" / "speaches" / "pin-cache.sh"

PIN_LINE = re.compile(r"^\s*pin (\S+) (\S+) (\S+)$", re.MULTILINE)
SCRIPT_LINE = re.compile(r"^(\S+/\S+|\S+/\S+) ([0-9a-f]{40}) (\S+) ([0-9a-f]{64})$", re.MULTILINE)


def _compose_pins() -> dict[tuple[str, str], str]:
    text = COMPOSE.read_text(encoding="utf-8")
    pins = {}
    for cache_dir, file, sha in PIN_LINE.findall(text):
        repo = cache_dir.replace("--", "/", 1)
        pins[(repo, file)] = sha
    return pins


def _script_pins() -> dict[tuple[str, str], tuple[str, str]]:
    text = SCRIPT.read_text(encoding="utf-8")
    table = {}
    for repo, commit, file, sha in SCRIPT_LINE.findall(text):
        table[(repo, file)] = (commit, sha)
    return table


def test_compose_checks_the_three_weight_hashes_the_script_pins():
    compose = _compose_pins()
    assert set(compose) == {
        ("speaches-ai/Kokoro-82M-v1.0-ONNX", "model.onnx"),
        ("speaches-ai/Kokoro-82M-v1.0-ONNX", "voices.bin"),
        ("Systran/faster-whisper-large-v3", "model.bin"),
    }


def test_compose_and_script_name_the_same_sha256_for_each_weight():
    compose = _compose_pins()
    script = _script_pins()
    for key, sha in compose.items():
        assert key in script, key
        assert script[key][1] == sha, key


def test_script_stages_every_file_speaches_reads():
    files = {file for (_, file) in _script_pins()}
    assert {"model.onnx", "voices.bin", "model.bin", "config.json", "tokenizer.json",
            "vocabulary.json", "preprocessor_config.json", "README.md"} <= files


def test_script_is_executable_and_fails_closed_on_a_hash_mismatch():
    assert SCRIPT.stat().st_mode & stat.S_IXUSR
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'volume untouched' in text and 'exit 1' in text


def test_compose_runs_offline_only_after_the_pins_match():
    text = COMPOSE.read_text(encoding="utf-8")
    assert "export HF_HUB_OFFLINE=1" in text
    assert text.index("pin speaches-ai--Kokoro-82M-v1.0-ONNX model.onnx") < text.index("export HF_HUB_OFFLINE=1")
