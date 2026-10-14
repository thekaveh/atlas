"""The exception-coverage report attributes suppressed findings to rows (#1289)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json

import pytest

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


def test_open_findings_list_only_unsuppressed_high_and_critical() -> None:
    report = {"Results": [
        {
            "Target": "img",
            "Vulnerabilities": [
                {"VulnerabilityID": "CVE-1", "Severity": "HIGH", "PkgName": "jackson-databind",
                 "InstalledVersion": "2.21.2", "FixedVersion": "2.21.7",
                 "PkgPath": "opt/spark/jars/jackson-databind-2.21.2.jar"},
                {"VulnerabilityID": "CVE-2", "Severity": "MEDIUM", "PkgName": "x",
                 "InstalledVersion": "1"},
            ],
        },
        _finding("CVE-3", purl="pkg:npm/y@1"),
    ]}

    assert coverage.open_findings(report, "services/spark/build", "linux/amd64") == [
        "FINDING\tservices/spark/build\tlinux/amd64\tCVE-1\tjackson-databind\t2.21.2\t2.21.7"
        "\topt/spark/jars/jackson-databind-2.21.2.jar",
    ]


def test_finding_lines_do_not_disturb_the_row_summary() -> None:
    lines = [
        "COVERAGE\tsvc/a\tlinux/amd64\t2\t-",
        "FINDING\tsvc/a\tlinux/amd64\tCVE-9\tpkg\t1\t2\tsome/path",
    ]
    report = coverage.summarize(lines, _rows()).splitlines()

    assert report[0] == "scanned 1 image-platforms; 0 failed"
    assert "ROW 2\tCVE-2\t1\tsvc/a [linux/amd64]" in report

def _selected_policy_cli_probe(monkeypatch, tmp_path, ignore_form, mode):
    root = tmp_path / "repository"
    caller = tmp_path / "caller"
    root.mkdir()
    caller.mkdir()
    policy = "vulnerabilities:\n  - id: CVE-SELECTED\n  - id: CVE-UNUSED\n"
    default = root / ".trivyignore.yaml"
    default.write_text(policy.replace("CVE-SELECTED", "CVE-DEFAULT"))
    custom = caller / "custom policy.yaml"
    custom.write_text(policy)
    selected = default if ignore_form == "default" else custom
    state = SimpleNamespace(root=root, selected=selected, calls=[], failures=0)
    monkeypatch.setattr(coverage, "ROOT", root)
    monkeypatch.chdir(caller)
    monkeypatch.setattr(coverage, "load_image_scans", lambda _path: [
        SimpleNamespace(image="controlled-image:1", platform="linux/arm64"),
        SimpleNamespace(image="unselected-image:1", platform="linux/amd64"),
    ])

    def run(command, **kwargs):
        state.calls.append(command)
        supplied = command[command.index("--ignorefile") + 1]
        actual = Path(supplied)
        if not actual.is_absolute():
            actual = kwargs["cwd"] / actual
        assert actual.resolve() == selected.resolve(), "scanner selected a different policy"
        assert kwargs["cwd"] == root
        if len(state.calls) <= state.failures:
            return SimpleNamespace(returncode=1, stderr="controlled scan refusal")
        result = _finding("CVE-DEFAULT" if ignore_form == "default" else "CVE-SELECTED")
        result["ExperimentalModifiedFindings"][0]["Source"] = Path(supplied).as_posix()
        foreign = _finding("CVE-FOREIGN")
        foreign["ExperimentalModifiedFindings"][0]["Source"] = (
            caller / "other" / selected.name
        ).as_posix()
        result["ExperimentalModifiedFindings"] += foreign["ExperimentalModifiedFindings"]
        if ignore_form != "default":
            result["ExperimentalModifiedFindings"] += _finding("CVE-FOREIGN")["ExperimentalModifiedFindings"]
        result["Vulnerabilities"] = [
            {"VulnerabilityID": "CVE-OPEN", "Severity": "HIGH", "PkgName": "demo",
             "InstalledVersion": "1", "FixedVersion": "2"},
            {"VulnerabilityID": "CVE-MEDIUM", "Severity": "MEDIUM"},
        ]
        report = Path(command[command.index("--output") + 1])
        report.write_text(json.dumps({"Results": [result]}))
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(coverage, "subprocess", SimpleNamespace(run=run))
    args = ["--local-image", "controlled-image:1", "--label", "controlled"] if mode == "local" else [
        "--shard", "0", "--shards", "2",
    ]
    args += ["--platform", "linux/arm64"]
    if ignore_form != "default":
        args += ["--ignorefile", str(custom) if ignore_form == "absolute" else custom.name]
    return args, state


@pytest.mark.parametrize("case", [
    (policy, mode, failures)
    for policy in ("default", "absolute", "relative")
    for mode in ("local", "shard")
    for failures in (0, 2, 3)
])
def test_selected_policy_cli_uses_one_policy_through_scan_and_report(monkeypatch, tmp_path, capsys, case):
    policy, mode, failures = case
    args, state = _selected_policy_cli_probe(monkeypatch, tmp_path, policy, mode)
    state.failures = failures
    if failures == 2:
        args += ["--ignore-unfixed"]

    assert coverage.main(args) == 0

    captured = capsys.readouterr()
    label = "controlled" if mode == "local" else "controlled-image:1"
    expected = f"COVERAGE\t{label}\tlinux/arm64\t"
    if failures == 3:
        assert captured.out.splitlines() == [
            expected + "SCAN-FAILED\t-", "== COVERAGE summary (1 scans) ==",
            expected + "SCAN-FAILED\t-",
        ]
    else:
        finding = f"FINDING\t{label}\tlinux/arm64\tCVE-OPEN\tdemo\t1\t2\timg"
        assert captured.out.splitlines() == [
            expected + "0\t-", finding, "== COVERAGE summary (2 scans) ==",
            expected + "0\t-", finding,
        ]
    assert len(state.calls) == min(failures + 1, 3)
    assert captured.err.count("controlled scan refusal") == failures
    _assert_policy_scan_commands(state, mode, failures)


def _assert_policy_scan_commands(state, mode, failures):
    for command in state.calls:
        assert command[command.index("--image-src") + 1] == ("docker" if mode == "local" else "remote")
        assert ("--platform" in command) == (mode == "shard")
        if mode == "shard":
            assert command[command.index("--platform") + 1] == "linux/arm64"
        assert ("--ignore-unfixed" in command) == (failures == 2)


def test_selected_policy_summary_reads_custom_rows_without_scanning(monkeypatch, tmp_path, capsys):
    args, state = _selected_policy_cli_probe(monkeypatch, tmp_path, "relative", "local")
    log = tmp_path / "scan.log"
    log.write_text("timestamp COVERAGE\tcontrolled\tlinux/arm64\t0\t-\n")

    assert coverage.main(["--ignorefile", state.selected.name, "--summarize", str(log)]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "scanned 1 image-platforms; 0 failed",
        "ROW 0\tCVE-SELECTED\t1\tcontrolled [linux/arm64]",
        "ROW 1\tCVE-UNUSED\tUNUSED\t",
    ]
    assert state.calls == []
