"""Report which .trivyignore.yaml rows each scanned image actually uses (#1289).

Trivy's ``--show-suppressed`` JSON lists every finding an ignore row
suppressed, but not which row did it. This attributes each one the way Trivy
matches rows: the same vulnerability ID, then the row's package URLs or path
globs when the row has them. Each scan prints one ``COVERAGE`` line, and
``--summarize`` folds the lines from many scan logs into a per-row report:
which images each row covers, and which rows cover nothing.

Coverage is evidence for a review, not a gate: this never fails on findings.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import fnmatch
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Iterable, Sequence
from urllib.parse import parse_qsl

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.container_security import load_image_scans  # noqa: E402

MARKER = "COVERAGE"
FINDING = "FINDING"
_TRIVY_ATTEMPTS = 3


@dataclass(frozen=True)
class Row:
    index: int
    id: str
    purls: tuple[str, ...]
    paths: tuple[str, ...]


def load_rows(ignorefile: Path) -> tuple[Row, ...]:
    document = yaml.safe_load(ignorefile.read_text(encoding="utf-8")) or {}
    return tuple(
        Row(
            index=index,
            id=str(row["id"]),
            purls=tuple(row.get("purls") or ()),
            paths=tuple(row.get("paths") or ()),
        )
        for index, row in enumerate(document.get("vulnerabilities") or ())
    )


def _purl_parts(purl: str) -> tuple[str, dict[str, str]]:
    base, _, query = purl.partition("?")
    return base, dict(parse_qsl(query))


def purl_matches(row_purl: str, finding_purl: str) -> bool:
    """A row purl matches when its base agrees and its qualifiers are a subset."""
    row_base, row_qualifiers = _purl_parts(row_purl)
    base, qualifiers = _purl_parts(finding_purl)
    return row_base == base and all(
        qualifiers.get(key) == value for key, value in row_qualifiers.items()
    )


def row_matches(row: Row, finding_id: str, purl: str, paths: Sequence[str]) -> bool:
    if row.id != finding_id:
        return False
    if row.purls and not any(purl_matches(item, purl) for item in row.purls):
        return False
    if row.paths and not any(
        fnmatch.fnmatchcase(path, pattern) for path in paths for pattern in row.paths
    ):
        return False
    return True


def _suppressed_findings(report: dict) -> Iterable[tuple[str, str, list[str]]]:
    """Yield ``(id, purl, paths)`` for each finding the ignore file suppressed."""
    for result in report.get("Results") or ():
        for modified in result.get("ExperimentalModifiedFindings") or ():
            if modified.get("Source") != ".trivyignore.yaml":
                continue
            finding = modified.get("Finding") or {}
            purl = str((finding.get("PkgIdentifier") or {}).get("PURL", ""))
            paths = [p for p in (finding.get("PkgPath"), result.get("Target")) if p]
            yield str(finding.get("VulnerabilityID", "")), purl, paths


def attribute(report: dict, rows: Sequence[Row]) -> tuple[set[int], set[str]]:
    """Return the indices of rows used, and any finding no row explains."""
    used: set[int] = set()
    unexplained: set[str] = set()
    by_id: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        by_id[row.id].append(row)
    for finding_id, purl, paths in _suppressed_findings(report):
        matched = {
            row.index for row in by_id.get(finding_id, ())
            if row_matches(row, finding_id, purl, paths)
        }
        used |= matched
        if not matched:
            unexplained.add(f"{finding_id}:{purl or (paths[0] if paths else '?')}")
    return used, unexplained


def open_findings(report: dict, label: str, platform: str) -> list[str]:
    """One FINDING line per HIGH/CRITICAL finding no ignore row suppressed.

    These are the findings the gate fails on, so a coverage dispatch also
    lists exactly what each image still needs fixed or reviewed.
    """
    lines = set()
    for result in report.get("Results") or ():
        for vuln in result.get("Vulnerabilities") or ():
            if vuln.get("Severity") not in ("HIGH", "CRITICAL"):
                continue
            path = vuln.get("PkgPath") or result.get("Target") or "?"
            lines.add("\t".join((
                FINDING, label, platform, str(vuln.get("VulnerabilityID", "")),
                str(vuln.get("PkgName", "")), str(vuln.get("InstalledVersion", "")),
                str(vuln.get("FixedVersion") or "-"), str(path),
            )))
    return sorted(lines)


def coverage_line(label: str, platform: str, used: Iterable[int], unexplained: Iterable[str]) -> str:
    rows = ",".join(str(index) for index in sorted(used)) or "-"
    extra = ",".join(sorted(unexplained)) or "-"
    return f"{MARKER}\t{label}\t{platform}\t{rows}\t{extra}"


def scan(image: str, platform: str, *, source: str, ignore_unfixed: bool = False) -> dict | None:
    """Run Trivy with suppressed findings shown; None if it never produced a report."""
    with tempfile.TemporaryDirectory() as tmp:
        report_path = Path(tmp) / "report.json"
        for _attempt in range(_TRIVY_ATTEMPTS):
            command = [
                "trivy", "image", "--image-src", source, "--scanners", "vuln",
                "--severity", "HIGH,CRITICAL", "--ignorefile", ".trivyignore.yaml",
                "--show-suppressed", "--format", "json", "--output", str(report_path),
                "--no-progress", "--quiet", "--timeout", "30m",
            ]
            if source == "remote":
                command += ["--platform", platform]
            if ignore_unfixed:
                # Mirror the local-image gate, which ignores unfixed findings.
                command.append("--ignore-unfixed")
            result = subprocess.run(
                [*command, image], cwd=ROOT, text=True, capture_output=True, check=False,
            )
            if result.returncode == 0 and report_path.is_file():
                return json.loads(report_path.read_text(encoding="utf-8"))
            print(f"trivy failed for {image} ({platform}): {result.stderr[-2000:]}", file=sys.stderr)
    return None


def run_scans(
    targets: Sequence[tuple[str, str, str, str]], rows: Sequence[Row], *, ignore_unfixed: bool = False,
) -> list[str]:
    """Scan ``(label, image, platform, source)`` targets and return their lines."""
    lines = []
    for label, image, platform, source in targets:
        report = scan(image, platform, source=source, ignore_unfixed=ignore_unfixed)
        if report is None:
            emitted = [f"{MARKER}\t{label}\t{platform}\tSCAN-FAILED\t-"]
        else:
            emitted = [coverage_line(label, platform, *attribute(report, rows))]
            emitted += open_findings(report, label, platform)
        for line in emitted:
            print(line, flush=True)
        lines.extend(emitted)
    return lines


@dataclass
class _Coverage:
    images: dict[int, set[str]]
    scanned: set[str]
    failed: set[str]
    unexplained: set[str]


def _fold(lines: Iterable[str]) -> _Coverage:
    folded = _Coverage(defaultdict(set), set(), set(), set())
    for line in lines:
        fields = line.rstrip("\n").split("\t")
        if len(fields) != 5 or fields[0] != MARKER:
            continue
        _, label, platform, used, extra = fields
        target = f"{label} [{platform}]"
        if used == "SCAN-FAILED":
            folded.failed.add(target)
            continue
        folded.scanned.add(target)
        for index in used.split(",") if used != "-" else ():
            folded.images[int(index)].add(target)
        for item in extra.split(",") if extra != "-" else ():
            folded.unexplained.add(f"{target}: {item}")
    return folded


def summarize(lines: Iterable[str], rows: Sequence[Row]) -> str:
    """Fold COVERAGE lines into one report: images per row, then unused rows."""
    folded = _fold(lines)
    report = [f"scanned {len(folded.scanned)} image-platforms; {len(folded.failed)} failed"]
    report += [f"FAILED {target}" for target in sorted(folded.failed)]
    report += [f"UNEXPLAINED {item}" for item in sorted(folded.unexplained)]
    for row in rows:
        covered = sorted(folded.images.get(row.index, ()))
        status = f"{len(covered)}" if covered else "UNUSED"
        report.append(f"ROW {row.index}\t{row.id}\t{status}\t" + "; ".join(covered))
    return "\n".join(report)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ignorefile", type=Path, default=ROOT / ".trivyignore.yaml")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--shard", type=int, help="scan one shard of the remote inventory")
    mode.add_argument("--local-image", help="scan one locally loaded image")
    mode.add_argument("--summarize", nargs="+", type=Path, help="fold scan logs into a report")
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--label", help="label for --local-image")
    parser.add_argument("--platform", default="linux/amd64")
    parser.add_argument(
        "--ignore-unfixed", action="store_true",
        help="ignore unfixed findings, as the local-image gate does",
    )
    args = parser.parse_args(argv)
    rows = load_rows(args.ignorefile)
    if args.summarize:
        lines = [
            line for path in args.summarize
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        ]
        print(summarize((line[line.find(MARKER):] for line in lines if MARKER in line), rows))
        return 0
    if args.local_image:
        targets = [(args.label or args.local_image, args.local_image, args.platform, "docker")]
    else:
        scans = load_image_scans(ROOT / "services")
        targets = [
            (item.image, item.image, item.platform, "remote")
            for position, item in enumerate(scans)
            if position % args.shards == args.shard
        ]
    lines = run_scans(targets, rows, ignore_unfixed=args.ignore_unfixed)
    print(f"== {MARKER} summary ({len(lines)} scans) ==")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
