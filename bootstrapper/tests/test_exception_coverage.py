"""The exception-coverage report attributes suppressed findings to rows (#1289)."""
from __future__ import annotations

from pathlib import Path

from scripts import exception_coverage as coverage

ROOT = Path(__file__).resolve().parents[2]


def _rows() -> tuple[coverage.Row, ...]:
    return (
        coverage.Row(0, "CVE-1", ("pkg:apk/alpine/libssl3@3.5.7-r0?arch=x86_64",), ()),
        coverage.Row(1, "CVE-1", (), ("opt/app/*.jar",)),
        coverage.Row(2, "CVE-2", (), ()),
        coverage.Row(3, "CVE-3", (), ()),
    )


def _finding(vid: str, purl: str = "", path: str = "", target: str = "img") -> dict:
    return {
        "Target": target,
        "ExperimentalModifiedFindings": [{
            "Type": "vulnerability", "Status": "ignored", "Source": ".trivyignore.yaml",
            "Finding": {
                "VulnerabilityID": vid, "PkgPath": path or None,
                "PkgIdentifier": {"PURL": purl},
            },
        }],
    }


def test_purl_rows_match_on_base_and_a_subset_of_qualifiers() -> None:
    row = "pkg:apk/alpine/libssl3@3.5.7-r0?arch=x86_64"
    assert coverage.purl_matches(row, row + "&distro=3.24.1")
    assert not coverage.purl_matches(row, "pkg:apk/alpine/libssl3@3.5.7-r0?arch=aarch64")
    assert not coverage.purl_matches(row, "pkg:apk/alpine/libssl3@3.5.8-r0?arch=x86_64")


def test_findings_are_attributed_to_the_rows_trivy_would_match() -> None:
    report = {"Results": [
        _finding("CVE-1", purl="pkg:apk/alpine/libssl3@3.5.7-r0?arch=x86_64&distro=3.24.1"),
        _finding("CVE-1", path="opt/app/lib.jar"),
        _finding("CVE-2", purl="pkg:golang/stdlib@v1.24.6"),
        _finding("CVE-9", purl="pkg:npm/x@1"),
    ]}

    used, unexplained = coverage.attribute(report, _rows())

    assert used == {0, 1, 2}
    assert unexplained == {"CVE-9:pkg:npm/x@1"}


def test_summary_names_each_rows_images_and_flags_unused_rows() -> None:
    lines = [
        "2026-10-01T00:00:00Z COVERAGE\tsvc/a\tlinux/amd64\t0,2\t-",
        "COVERAGE\tsvc/b\tlinux/arm64\t2\t-",
        "COVERAGE\tsvc/c\tlinux/amd64\tSCAN-FAILED\t-",
        "unrelated log line",
    ]
    report = coverage.summarize(
        (line[line.find(coverage.MARKER):] for line in lines if coverage.MARKER in line),
        _rows(),
    ).splitlines()

    assert report[0] == "scanned 2 image-platforms; 1 failed"
    assert "FAILED svc/c [linux/amd64]" in report
    assert "ROW 2\tCVE-2\t2\tsvc/a [linux/amd64]; svc/b [linux/arm64]" in report
    assert "ROW 1\tCVE-1\tUNUSED\t" in report


def test_the_committed_ignorefile_loads_as_rows() -> None:
    rows = coverage.load_rows(ROOT / ".trivyignore.yaml")
    assert rows and [row.index for row in rows] == list(range(len(rows)))


def test_coverage_dispatch_replaces_gating_with_reporting() -> None:
    workflow = (ROOT / ".github/workflows/container-security.yml").read_text()
    assert "coverage:\n        description:" in workflow
    assert "if: ${{ !inputs.coverage }}" in workflow
    assert "python -m scripts.exception_coverage --shard" in workflow
    assert "--local-image \"$image_tag\"" in workflow
