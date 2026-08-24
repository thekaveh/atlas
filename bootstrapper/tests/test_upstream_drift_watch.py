from datetime import datetime, timezone

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
