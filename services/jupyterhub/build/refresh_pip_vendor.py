#!/usr/bin/env python3
"""Refresh pip's vendored msgpack while keeping its SBOM truthful.

pip 26.2.1 ships msgpack 1.1.2 in ``pip._vendor`` and also lists a build-only
setuptools 70.3.0 component in its embedded CycloneDX inventory.  The Jupyter
image already installs the reviewed msgpack 1.2.1 wheel, so copy that exact
implementation into pip's private namespace and update the inventory.  Every
upstream expectation is checked before the first mutation so a future pip
layout fails closed instead of receiving a partial or misdirected patch.
"""

from __future__ import annotations

import argparse
import base64
import copy
import csv
import hashlib
import io
import json
import shutil
from pathlib import Path


OLD_MSGPACK = "1.1.2"
NEW_MSGPACK = "1.2.1"
BUILD_ONLY_SETUPTOOLS = "70.3.0"
UNDERSCORE_ASSETS = (
    "README.md",
    "package.json",
    "modules/package.json",
    "underscore-min.js",
)
UNDERSCORE_RECORD_PREFIX = "nbclassic/static/components/underscore/"
MSGPACK_RECORD_PREFIX = "pip/_vendor/msgpack/"
VENDOR_TXT_RECORD_PATH = "pip/_vendor/vendor.txt"
BOM_RECORD_PATH = "pip/_vendor/bom.cdx.json"
PIP_REF = "bom-ref:pip"
OLD_MSGPACK_REF = f"pkg:pypi/msgpack@{OLD_MSGPACK}"
NEW_MSGPACK_REF = f"pkg:pypi/msgpack@{NEW_MSGPACK}"
SETUPTOOLS_REF = f"pkg:pypi/setuptools@{BUILD_ONLY_SETUPTOOLS}"
SPECIAL_REFS = (OLD_MSGPACK_REF, NEW_MSGPACK_REF, SETUPTOOLS_REF)


def _one_component(components: list[dict[str, object]], name: str) -> dict[str, object]:
    matches = [component for component in components if component.get("name") == name]
    if len(matches) != 1:
        raise ValueError(f"expected one {name} component, found {len(matches)}")
    return matches[0]


def _record_values(payload: bytes) -> tuple[str, str]:
    digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
    return "sha256=" + digest.decode("ascii"), str(len(payload))


def _package_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
    )


def _one_dependency(
    dependencies: list[dict[str, object]], reference: str
) -> dict[str, object]:
    matches = [item for item in dependencies if item.get("ref") == reference]
    if len(matches) != 1:
        raise ValueError(
            f"expected one {reference} dependency, found {len(matches)}"
        )
    return matches[0]


def _validate_record_entry(
    rows: list[list[str]], relative: str, payload: bytes
) -> None:
    matches = [row for row in rows if row and row[0] == relative]
    if len(matches) != 1:
        raise ValueError(f"expected one RECORD row for {relative}, found {len(matches)}")
    if tuple(matches[0][1:]) != _record_values(payload):
        raise ValueError(f"stale RECORD row for {relative}")


def _special_counts(values: list[object]) -> dict[str, int]:
    return {reference: values.count(reference) for reference in SPECIAL_REFS}


def _validate_reference_contract(
    components: list[dict[str, object]],
    dependencies: list[dict[str, object]],
    state: str,
) -> None:
    component_refs = [component.get("bom-ref") for component in components]
    component_purls = [component.get("purl") for component in components]
    dependency_refs = [dependency.get("ref") for dependency in dependencies]
    edge_locations: list[tuple[object, str]] = []
    for dependency in dependencies:
        depends_on = dependency.get("dependsOn", [])
        if not isinstance(depends_on, list) or not all(
            isinstance(reference, str) for reference in depends_on
        ):
            raise ValueError("CycloneDX dependency edge layout is invalid")
        if len(depends_on) != len(set(depends_on)):
            raise ValueError("duplicate CycloneDX dependency edge")
        edge_locations.extend(
            (dependency.get("ref"), reference)
            for reference in depends_on
            if reference in SPECIAL_REFS
        )

    if state == "old":
        expected_counts = {
            OLD_MSGPACK_REF: 1,
            NEW_MSGPACK_REF: 0,
            SETUPTOOLS_REF: 1,
        }
        expected_edges = {
            (PIP_REF, OLD_MSGPACK_REF),
            (PIP_REF, SETUPTOOLS_REF),
        }
    else:
        expected_counts = {
            OLD_MSGPACK_REF: 0,
            NEW_MSGPACK_REF: 1,
            SETUPTOOLS_REF: 0,
        }
        expected_edges = {(PIP_REF, NEW_MSGPACK_REF)}

    for label, values in (
        ("component bom-ref", component_refs),
        ("component purl", component_purls),
        ("dependency ref", dependency_refs),
    ):
        if _special_counts(values) != expected_counts:
            raise ValueError(f"unexpected CycloneDX {label} reference contract")
    if set(edge_locations) != expected_edges or len(edge_locations) != len(
        expected_edges
    ):
        raise ValueError("unexpected CycloneDX dependency edge reference contract")


def _validate_state(
    vendor: Path, msgpack_source: Path, record: Path
) -> tuple[
    str,
    list[str],
    dict[str, object],
    list[list[str]],
]:
    vendor_txt = vendor / "vendor.txt"
    bom_path = vendor / "bom.cdx.json"
    vendored_msgpack = vendor / "msgpack"

    if not (msgpack_source / "__init__.py").is_file():
        raise ValueError("msgpack 1.2.1 source package is incomplete")
    if (vendor / "setuptools").exists():
        raise ValueError("setuptools is now shipped and may not be removed from the SBOM")

    vendor_lines = vendor_txt.read_text(encoding="utf-8").splitlines()
    old_line = f"msgpack=={OLD_MSGPACK}"
    new_line = f"msgpack=={NEW_MSGPACK}"
    setuptools_line = f"setuptools=={BUILD_ONLY_SETUPTOOLS}"
    if (
        vendor_lines.count(old_line) == 1
        and new_line not in vendor_lines
        and vendor_lines.count(setuptools_line) == 1
    ):
        state = "old"
    elif (
        vendor_lines.count(new_line) == 1
        and old_line not in vendor_lines
        and setuptools_line not in vendor_lines
    ):
        state = "new"
    else:
        raise ValueError("unexpected msgpack vendor.txt contract")

    bom = json.loads(bom_path.read_text(encoding="utf-8"))
    with record.open(encoding="utf-8", newline="") as stream:
        record_rows = list(csv.reader(stream))
    record_paths = [row[0] for row in record_rows if row]
    if len(record_paths) != len(set(record_paths)):
        raise ValueError("duplicate RECORD path")
    _validate_record_entry(record_rows, BOM_RECORD_PATH, bom_path.read_bytes())

    components = bom.get("components")
    if not isinstance(components, list):
        raise ValueError("CycloneDX components must be a list")
    if not all(isinstance(component, dict) for component in components):
        raise ValueError("CycloneDX component layout is invalid")
    component_refs = [component.get("bom-ref") for component in components]
    if any(not isinstance(reference, str) for reference in component_refs):
        raise ValueError("CycloneDX component ref is missing")
    if len(component_refs) != len(set(component_refs)):
        raise ValueError("duplicate CycloneDX component ref")
    pip_component = _one_component(components, "pip")
    if pip_component.get("bom-ref") != PIP_REF:
        raise ValueError("unexpected pip component ref")
    msgpack = _one_component(components, "msgpack")
    setuptools_matches = [
        component for component in components if component.get("name") == "setuptools"
    ]

    dependencies = bom.get("dependencies")
    if not isinstance(dependencies, list) or not all(
        isinstance(dependency, dict) for dependency in dependencies
    ):
        raise ValueError("CycloneDX dependencies must be a list")
    dependency_refs = [dependency.get("ref") for dependency in dependencies]
    if any(not isinstance(reference, str) for reference in dependency_refs):
        raise ValueError("CycloneDX dependency ref is missing")
    if len(dependency_refs) != len(set(dependency_refs)):
        raise ValueError("duplicate CycloneDX dependency ref")
    _validate_reference_contract(components, dependencies, state)
    pip_dependency = _one_dependency(dependencies, PIP_REF)
    depends_on = pip_dependency.get("dependsOn")
    if not isinstance(depends_on, list) or not all(
        isinstance(reference, str) for reference in depends_on
    ):
        raise ValueError("pip dependency edge layout is invalid")
    if len(depends_on) != len(set(depends_on)):
        raise ValueError("duplicate pip dependency edge")

    if state == "old":
        if (
            msgpack.get("version") != OLD_MSGPACK
            or msgpack.get("bom-ref") != OLD_MSGPACK_REF
            or msgpack.get("purl") != OLD_MSGPACK_REF
        ):
            raise ValueError("unexpected msgpack component version")
        if len(setuptools_matches) != 1:
            raise ValueError("unexpected setuptools component layout")
        setuptools = setuptools_matches[0]
        if (
            setuptools.get("version") != BUILD_ONLY_SETUPTOOLS
            or setuptools.get("bom-ref") != SETUPTOOLS_REF
            or setuptools.get("purl") != SETUPTOOLS_REF
        ):
            raise ValueError("unexpected setuptools component version")
        if depends_on.count(OLD_MSGPACK_REF) != 1 or depends_on.count(SETUPTOOLS_REF) != 1:
            raise ValueError("unexpected pip dependency edge")
        if NEW_MSGPACK_REF in depends_on:
            raise ValueError("unexpected new msgpack dependency edge")
        if _one_dependency(dependencies, OLD_MSGPACK_REF) != {"ref": OLD_MSGPACK_REF}:
            raise ValueError("unexpected msgpack dependency layout")
        if _one_dependency(dependencies, SETUPTOOLS_REF) != {"ref": SETUPTOOLS_REF}:
            raise ValueError("unexpected setuptools dependency layout")
    else:
        if (
            msgpack.get("version") != NEW_MSGPACK
            or msgpack.get("bom-ref") != NEW_MSGPACK_REF
            or msgpack.get("purl") != NEW_MSGPACK_REF
        ):
            raise ValueError("unexpected refreshed msgpack component")
        if setuptools_matches:
            raise ValueError("stale setuptools component")
        if depends_on.count(NEW_MSGPACK_REF) != 1:
            raise ValueError("unexpected refreshed msgpack dependency edge")
        if OLD_MSGPACK_REF in depends_on or SETUPTOOLS_REF in depends_on:
            raise ValueError("stale pip dependency edge")
        if _one_dependency(dependencies, NEW_MSGPACK_REF) != {"ref": NEW_MSGPACK_REF}:
            raise ValueError("unexpected refreshed msgpack dependency layout")
        rendered = json.dumps(bom)
        if OLD_MSGPACK_REF in rendered or SETUPTOOLS_REF in rendered:
            raise ValueError("stale CycloneDX dependency reference")

    installed_paths = {
        MSGPACK_RECORD_PREFIX + path.relative_to(vendored_msgpack).as_posix()
        for path in _package_files(vendored_msgpack)
    }
    recorded_paths = {
        row[0]
        for row in record_rows
        if row
        and row[0].startswith(MSGPACK_RECORD_PREFIX)
        and "/__pycache__/" not in row[0]
        and not row[0].endswith(".pyc")
    }
    if installed_paths != recorded_paths:
        raise ValueError("unexpected pip msgpack RECORD contract")
    root = vendor.parents[1]
    for relative in sorted(installed_paths):
        _validate_record_entry(record_rows, relative, (root / relative).read_bytes())
    _validate_record_entry(record_rows, VENDOR_TXT_RECORD_PATH, vendor_txt.read_bytes())
    _validate_record_entry(record_rows, BOM_RECORD_PATH, bom_path.read_bytes())

    if state == "new":
        source_files = {
            path.relative_to(msgpack_source).as_posix(): path.read_bytes()
            for path in _package_files(msgpack_source)
        }
        installed_files = {
            path.relative_to(vendored_msgpack).as_posix(): path.read_bytes()
            for path in _package_files(vendored_msgpack)
        }
        if installed_files != source_files:
            raise ValueError("refreshed msgpack source does not match reviewed wheel")

    return state, vendor_lines, bom, record_rows


def _render_record(rows: list[list[str]]) -> bytes:
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows(rows)
    return stream.getvalue().encode("utf-8")


def refresh(vendor: Path, msgpack_source: Path, record: Path) -> None:
    vendor_txt = vendor / "vendor.txt"
    bom_path = vendor / "bom.cdx.json"
    vendored_msgpack = vendor / "msgpack"
    state, vendor_lines, bom, record_rows = _validate_state(
        vendor, msgpack_source, record
    )
    if state == "new":
        return

    updated_bom = copy.deepcopy(bom)
    updated_components = updated_bom["components"]
    updated_dependencies = updated_bom["dependencies"]
    updated_msgpack = _one_component(updated_components, "msgpack")
    updated_setuptools = _one_component(updated_components, "setuptools")
    updated_msgpack["version"] = NEW_MSGPACK
    updated_msgpack["bom-ref"] = NEW_MSGPACK_REF
    updated_msgpack["purl"] = NEW_MSGPACK_REF
    updated_components.remove(updated_setuptools)
    updated_pip = _one_dependency(updated_dependencies, PIP_REF)
    updated_pip["dependsOn"] = [
        NEW_MSGPACK_REF if reference == OLD_MSGPACK_REF else reference
        for reference in updated_pip["dependsOn"]
        if reference != SETUPTOOLS_REF
    ]
    updated_old_msgpack = _one_dependency(updated_dependencies, OLD_MSGPACK_REF)
    updated_old_msgpack["ref"] = NEW_MSGPACK_REF
    updated_setuptools_dependency = _one_dependency(
        updated_dependencies, SETUPTOOLS_REF
    )
    updated_dependencies.remove(updated_setuptools_dependency)

    updated_vendor_lines = list(vendor_lines)
    updated_vendor_lines[updated_vendor_lines.index(f"msgpack=={OLD_MSGPACK}")] = (
        f"msgpack=={NEW_MSGPACK}"
    )
    updated_vendor_lines.remove(f"setuptools=={BUILD_ONLY_SETUPTOOLS}")
    vendor_payload = ("\n".join(updated_vendor_lines) + "\n").encode("utf-8")
    bom_payload = (json.dumps(updated_bom, indent=2) + "\n").encode("utf-8")

    replacement = vendor / f".msgpack-{NEW_MSGPACK}.tmp"
    vendor_temp = vendor / ".vendor.txt.tmp"
    bom_temp = vendor / ".bom.cdx.json.tmp"
    record_temp = record.with_name(".RECORD.tmp")
    for path in (replacement, vendor_temp, bom_temp, record_temp):
        if path.exists():
            raise ValueError(f"unexpected refresh temp path: {path}")
    shutil.copytree(
        msgpack_source,
        replacement,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    replacement_rows: list[list[str]] = []
    for path in _package_files(replacement):
        relative = path.relative_to(replacement).as_posix()
        replacement_rows.append(
            [MSGPACK_RECORD_PREFIX + relative, *_record_values(path.read_bytes())]
        )
    replacement_rows.extend(
        (
            [VENDOR_TXT_RECORD_PATH, *_record_values(vendor_payload)],
            [BOM_RECORD_PATH, *_record_values(bom_payload)],
        )
    )
    updated_record: list[list[str]] = []
    inserted = False
    for row in record_rows:
        if row and (
            row[0].startswith(MSGPACK_RECORD_PREFIX)
            or row[0] in {VENDOR_TXT_RECORD_PATH, BOM_RECORD_PATH}
        ):
            if not inserted:
                updated_record.extend(replacement_rows)
                inserted = True
            continue
        updated_record.append(row)

    vendor_temp.write_bytes(vendor_payload)
    bom_temp.write_bytes(bom_payload)
    record_temp.write_bytes(_render_record(updated_record))

    shutil.rmtree(vendored_msgpack)
    replacement.replace(vendored_msgpack)
    vendor_temp.replace(vendor_txt)
    bom_temp.replace(bom_path)
    record_temp.replace(record)

    _validate_state(vendor, msgpack_source, record)


def refresh_underscore(component: Path, source: Path, record: Path) -> None:
    installed_package = json.loads((component / "package.json").read_text())
    replacement_package = json.loads((source / "package.json").read_text())
    if installed_package.get("version") != "1.13.7":
        raise ValueError("unexpected installed underscore version")
    if replacement_package.get("version") != "1.13.8":
        raise ValueError("unexpected replacement underscore version")

    installed_assets = {
        path.relative_to(component).as_posix()
        for path in component.rglob("*")
        if path.is_file()
    }
    if installed_assets != set(UNDERSCORE_ASSETS):
        raise ValueError("unexpected nbclassic underscore asset layout")
    if any(not (source / relative).is_file() for relative in UNDERSCORE_ASSETS):
        raise ValueError("replacement underscore package is incomplete")

    with record.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    record_paths = [row[0] for row in rows if row]
    expected_paths = {
        UNDERSCORE_RECORD_PREFIX + relative for relative in UNDERSCORE_ASSETS
    }
    if {path for path in record_paths if path.startswith(UNDERSCORE_RECORD_PREFIX)} != expected_paths:
        raise ValueError("unexpected nbclassic RECORD contract")
    if any(record_paths.count(path) != 1 for path in expected_paths):
        raise ValueError("duplicate nbclassic underscore RECORD entry")

    replacements: dict[str, tuple[str, str]] = {}
    for relative in UNDERSCORE_ASSETS:
        destination = component / relative
        shutil.copy2(source / relative, destination)
        payload = destination.read_bytes()
        replacements[UNDERSCORE_RECORD_PREFIX + relative] = _record_values(payload)

    for row in rows:
        if row and row[0] in replacements:
            row[1:] = replacements[row[0]]
    with record.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vendor", type=Path, required=True)
    parser.add_argument("--msgpack-source", type=Path, required=True)
    parser.add_argument("--pip-record", type=Path, required=True)
    parser.add_argument("--underscore-component", type=Path, required=True)
    parser.add_argument("--underscore-source", type=Path, required=True)
    parser.add_argument("--nbclassic-record", type=Path, required=True)
    args = parser.parse_args()
    refresh(args.vendor, args.msgpack_source, args.pip_record)
    refresh_underscore(
        args.underscore_component, args.underscore_source, args.nbclassic_record
    )


if __name__ == "__main__":
    main()
