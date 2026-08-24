from datetime import datetime, timezone

import pytest

from scripts import upstream_drift_watch as watch


def test_load_curated_models_deduplicates_multimodal_entries(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text("content:\n  - name: qwen:latest\nvision:\n  - name: qwen:latest\n")
    assert watch.load_curated_ollama_models(path) == ("qwen:latest",)


def test_report_contains_stable_marker_and_all_failures():
    report = watch.render_report(
        [
            watch.ProbeResult("library", False, "too few"),
            watch.ProbeResult("images", False, "missing ref"),
        ],
        datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    assert "<!-- atlas-upstream-drift-watch -->" in report
    assert "too few" in report and "missing ref" in report


def test_load_manifest_image_refs_reads_literal_defaults_sorted_and_unique(tmp_path):
    services = tmp_path / "services"
    (services / "zeta").mkdir(parents=True)
    (services / "alpha").mkdir()
    (services / "zeta" / "service.yml").write_text(
        "images:\n  - var: Z_IMAGE\n    default: zeta:1\n"
    )
    (services / "alpha" / "service.yml").write_text(
        "images:\n  - var: A_IMAGE\n    default: zeta:1\n  - var: B_IMAGE\n    default: alpha:2\n"
    )
    assert watch.load_manifest_image_refs(services) == ("alpha:2", "zeta:1")


def test_report_normalizes_timestamp_and_bounds_detail():
    report = watch.render_report(
        [watch.ProbeResult("probe", False, "x" * 600)],
        datetime(2026, 8, 24, 12, 30),
    )
    assert "2026-08-24T12:30:00+00:00" in report
    assert len(report) < 700


@pytest.mark.parametrize(
    "contents, expected",
    [
        ("content: {name: qwen:latest}\n", "content"),
        ("content:\n  - qwen:latest\n", r"content\[0\]"),
        ("content:\n  - description: missing-name\n", r"content\[0\]\.name"),
    ],
)
def test_load_curated_models_rejects_malformed_declared_rows(tmp_path, contents, expected):
    path = tmp_path / "models.yaml"
    path.write_text(contents)
    with pytest.raises(ValueError, match=expected):
        watch.load_curated_ollama_models(path)


@pytest.mark.parametrize(
    "image_yaml, expected",
    [
        ("images:\n  - bad-row\n", r"images\[0\]"),
        ("images:\n  - var: IMAGE\n    default: 42\n", r"images\[0\]\.default"),
    ],
)
def test_load_manifest_image_refs_rejects_malformed_declared_rows(tmp_path, image_yaml, expected):
    services = tmp_path / "services"
    service = services / "demo"
    service.mkdir(parents=True)
    (service / "service.yml").write_text(image_yaml)
    with pytest.raises(ValueError, match=expected):
        watch.load_manifest_image_refs(services)


def test_discovery_allows_absent_optional_sections(tmp_path):
    models = tmp_path / "models.yaml"
    models.write_text("content:\n  - name: qwen:latest\n")
    services = tmp_path / "services"
    (services / "empty").mkdir(parents=True)
    (services / "empty" / "service.yml").write_text("name: empty\n")
    assert watch.load_curated_ollama_models(models) == ("qwen:latest",)
    assert watch.load_manifest_image_refs(services) == ()
