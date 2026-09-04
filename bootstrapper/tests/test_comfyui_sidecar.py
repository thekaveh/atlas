"""Tests for the sidecar YAML loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from utils.comfyui_library import load_custom_models, ComfyUILibraryEntry


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "comfyui"
STUB_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "services" / "comfyui" / "custom-models.yaml"
)


def test_empty_models_list_returns_empty():
    """The shipped stub has `models: []` — must load without error."""
    entries = load_custom_models(str(STUB_PATH))
    assert entries == []


def test_valid_entry_parses():
    entries = load_custom_models(str(FIXTURE_DIR / "custom-models-valid.yaml"))
    assert len(entries) >= 1
    e = next(e for e in entries if e.name == "my-flux-lora-portrait")
    assert e.category == "lora"
    assert e.target_dir == "loras"
    assert e.source == "custom"
    assert "ComfyUI-GGUF" in e.requires_custom_node


def test_missing_file_returns_empty():
    entries = load_custom_models("/nonexistent/path.yaml")
    assert entries == []


def test_invalid_entries_skipped_with_warning(capsys):
    """File has 4 entries: 1 valid + 3 invalid (missing name, missing url,
    unknown category). Loader emits warnings, returns only the valid one.
    """
    entries = load_custom_models(str(FIXTURE_DIR / "custom-models-invalid.yaml"))
    captured = capsys.readouterr()
    assert len(entries) == 1
    assert entries[0].name == "valid-entry"
    assert "skipping" in captured.err.lower()


def test_unknown_category_skipped(tmp_path):
    """Category 'not-a-category' must skip with a warning."""
    raw = "models:\n  - name: x\n    category: not-a-category\n    url: https://e.com/x\n"
    tmp = tmp_path / "u.yaml"
    tmp.write_text(raw)
    entries = load_custom_models(str(tmp))
    assert entries == []


def test_diffusion_models_and_text_encoders_accepted(tmp_path):
    """The two modern ComfyUI categories (FLUX/DiT/Krea 2 era) must be
    valid — not silently skipped as 'unknown'. This is the regression guard
    for issue #348."""
    raw = (
        "models:\n"
        "  - name: my-flux-transformer\n"
        "    category: diffusion_models\n"
        "    url: https://huggingface.co/org/flux/resolve/main/transformer.safetensors\n"
        "    size_gb: 11.9\n"
        "  - name: my-t5-encoder\n"
        "    category: text_encoders\n"
        "    url: https://huggingface.co/org/t5/resolve/main/t5xxl.safetensors\n"
        "    size_gb: 9.8\n"
    )
    tmp = tmp_path / "modern.yaml"
    tmp.write_text(raw)
    entries = load_custom_models(str(tmp))
    assert len(entries) == 2
    cats = {e.category for e in entries}
    assert "diffusion_models" in cats
    assert "text_encoders" in cats
    # target_dir must resolve to the standard ComfyUI directory names
    dirs = {e.target_dir for e in entries}
    assert "diffusion_models" in dirs
    assert "text_encoders" in dirs


def test_custom_bundle_entry_parses_files(tmp_path):
    raw = (
        "models:\n"
        "  - name: my-krea-bundle\n"
        "    family: Krea 2\n"
        "    category: diffusion_models\n"
        "    precision: bf16\n"
        "    variant: mps-safe\n"
        "    host_constraints:\n"
        "      - mps\n"
        "    files:\n"
        "      - role: diffusion\n"
        "        category: diffusion_models\n"
        "        url: https://huggingface.co/org/krea/resolve/main/model.safetensors\n"
        "        filename: model.safetensors\n"
        "      - role: text_encoder\n"
        "        category: text_encoders\n"
        "        url: https://huggingface.co/org/krea/resolve/main/t5xxl.safetensors\n"
        "        filename: t5xxl.safetensors\n"
        "      - role: vae\n"
        "        category: vae\n"
        "        url: https://huggingface.co/org/krea/resolve/main/vae.safetensors\n"
        "        filename: vae.safetensors\n"
    )
    tmp = tmp_path / "bundle.yaml"
    tmp.write_text(raw)

    entries = load_custom_models(str(tmp))

    assert len(entries) == 1
    entry = entries[0]
    assert entry.name == "my-krea-bundle"
    assert entry.url.endswith("/model.safetensors")
    assert entry.precision == "bf16"
    assert entry.variant == "mps-safe"
    assert entry.host_constraints == ("mps",)
    assert [file.role for file in entry.files] == ["diffusion", "text_encoder", "vae"]
    assert [file.target_dir for file in entry.files] == [
        "diffusion_models",
        "text_encoders",
        "vae",
    ]


def test_custom_bundle_file_url_must_be_http_or_https(tmp_path, capsys):
    raw = (
        "models:\n"
        "  - name: bad-bundle\n"
        "    category: diffusion_models\n"
        "    files:\n"
        "      - role: diffusion\n"
        "        category: diffusion_models\n"
        "        url: ftp://example.com/model.safetensors\n"
    )
    tmp = tmp_path / "bad_bundle.yaml"
    tmp.write_text(raw)

    entries = load_custom_models(str(tmp))

    assert entries == []
    captured = capsys.readouterr()
    assert "bad-bundle" in captured.err
    assert "non-http" in captured.err


def test_invalid_yaml_returns_empty(tmp_path):
    tmp = tmp_path / "bad.yaml"
    tmp.write_text("models: [unclosed")
    entries = load_custom_models(str(tmp))
    assert entries == []


def test_url_must_be_http_or_https(tmp_path, capsys):
    """A non-http URL is skipped with a warning."""
    raw = "models:\n  - name: ftp-entry\n    category: lora\n    url: ftp://e.com/x.safetensors\n"
    tmp = tmp_path / "ftp.yaml"
    tmp.write_text(raw)
    entries = load_custom_models(str(tmp))
    assert entries == []
    captured = capsys.readouterr()
    assert "ftp-entry" in captured.err


def test_non_dict_entry_skipped(tmp_path, capsys):
    """A model entry that's a bare string or null is skipped."""
    raw = "models:\n  - not-a-dict\n  - null\n"
    tmp = tmp_path / "non_dict.yaml"
    tmp.write_text(raw)
    entries = load_custom_models(str(tmp))
    assert entries == []
    captured = capsys.readouterr()
    assert "not a mapping" in captured.err.lower()


def test_entry_overrides_via_kwargs(tmp_path):
    """Optional fields propagate when present."""
    raw = (
        "models:\n"
        "  - name: x\n"
        "    family: TestFam\n"
        "    category: lora\n"
        "    url: https://e.com/x.safetensors\n"
        "    size_gb: 2.5\n"
        "    sha256: deadbeef\n"
        "    cpu_supported: false\n"
        "    requires_custom_node:\n"
        "      - SomeNode\n"
    )
    tmp = tmp_path / "f.yaml"
    tmp.write_text(raw)
    entries = load_custom_models(str(tmp))
    assert len(entries) == 1
    e = entries[0]
    assert e.family == "TestFam"
    assert e.size_gb == 2.5
    assert e.sha256 == "deadbeef"
    assert e.cpu_supported is False
    assert e.requires_custom_node == ("SomeNode",)

def test_provisioning_required_defaults_true_and_rejects_nonboolean(tmp_path, capsys):
    valid = tmp_path / "valid.yaml"
    valid.write_text(
        "models:\n"
        "  - name: optional-model\n"
        "    category: checkpoint\n"
        "    url: https://example.test/model.bin\n"
        "    provisioning_required: false\n",
        encoding="utf-8",
    )
    assert load_custom_models(str(valid))[0].provisioning_required is False

    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(
        "models:\n"
        "  - name: ambiguous-model\n"
        "    category: checkpoint\n"
        "    url: https://example.test/model.bin\n"
        "    provisioning_required: optional\n",
        encoding="utf-8",
    )
    assert load_custom_models(str(invalid)) == []
    assert "provisioning_required must be a boolean" in capsys.readouterr().err


def test_bundle_file_null_provisioning_required_is_rejected_but_omission_inherits(
    tmp_path, capsys
):
    base = (
        "models:\n"
        "  - name: bundle\n"
        "    category: checkpoint\n"
        "    files:\n"
        "      - role: weights\n"
        "        category: checkpoint\n"
        "        url: https://example.test/model.bin\n"
    )
    omitted = tmp_path / "omitted.yaml"
    omitted.write_text(base, encoding="utf-8")
    assert load_custom_models(str(omitted))[0].files[0].provisioning_required is None

    explicit_null = tmp_path / "null.yaml"
    explicit_null.write_text(base + "        provisioning_required: null\n", encoding="utf-8")
    assert load_custom_models(str(explicit_null)) == []
    assert "bundle file provisioning_required must be a boolean" in capsys.readouterr().err
