"""Behavioral contracts for the Jupyter base-image dependency refresh."""

from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "services/jupyterhub/build/refresh_pip_vendor.py"
OLD_MSGPACK_REF = "pkg:pypi/msgpack@1.1.2"
NEW_MSGPACK_REF = "pkg:pypi/msgpack@1.2.1"
SETUPTOOLS_REF = "pkg:pypi/setuptools@70.3.0"
PIP_REF = "bom-ref:pip"


def _load_module():
    spec = importlib.util.spec_from_file_location("refresh_pip_vendor", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record_values(payload: bytes) -> tuple[str, str]:
    digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
    return "sha256=" + digest.decode("ascii"), str(len(payload))


def _assert_record_matches(record: Path, root: Path, relative: str) -> None:
    with record.open(encoding="utf-8", newline="") as stream:
        matches = [row for row in csv.reader(stream) if row and row[0] == relative]
    assert len(matches) == 1
    assert tuple(matches[0][1:]) == _record_values((root / relative).read_bytes())


def _write_bom_with_coherent_record(
    vendor: Path, record: Path, bom: dict[str, object]
) -> None:
    bom_path = vendor / "bom.cdx.json"
    bom_path.write_text(json.dumps(bom) + "\n", encoding="utf-8")
    with record.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    for row in rows:
        if row and row[0] == "pip/_vendor/bom.cdx.json":
            row[1:] = _record_values(bom_path.read_bytes())
    with record.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows(rows)


def _tree_snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    vendor = tmp_path / "pip" / "_vendor"
    (vendor / "msgpack").mkdir(parents=True)
    (vendor / "msgpack" / "old.py").write_text("old\n", encoding="utf-8")
    (vendor / "vendor.txt").write_text(
        "msgpack==1.1.2\nsetuptools==70.3.0\n", encoding="utf-8"
    )
    bom_path = vendor / "bom.cdx.json"
    bom_path.write_text(
        json.dumps(
            {
                "components": [
                    {"name": "pip", "bom-ref": PIP_REF, "type": "library"},
                    {
                        "name": "msgpack",
                        "version": "1.1.2",
                        "bom-ref": OLD_MSGPACK_REF,
                        "purl": OLD_MSGPACK_REF,
                    },
                    {
                        "name": "setuptools",
                        "version": "70.3.0",
                        "bom-ref": SETUPTOOLS_REF,
                        "purl": SETUPTOOLS_REF,
                    },
                    {
                        "name": "packaging",
                        "version": "26.2",
                        "bom-ref": "pkg:pypi/packaging@26.2",
                    },
                ],
                "dependencies": [
                    {
                        "ref": PIP_REF,
                        "dependsOn": [
                            OLD_MSGPACK_REF,
                            "pkg:pypi/packaging@26.2",
                            SETUPTOOLS_REF,
                        ],
                    },
                    {"ref": OLD_MSGPACK_REF},
                    {"ref": "pkg:pypi/packaging@26.2"},
                    {"ref": SETUPTOOLS_REF},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    source = tmp_path / "msgpack-1.2.1"
    source.mkdir()
    (source / "__init__.py").write_text('__version__ = "1.2.1"\n', encoding="utf-8")
    (source / "fallback.py").write_text("def pack(value):\n    return value\n", encoding="utf-8")
    record = tmp_path / "pip-26.2.1.dist-info" / "RECORD"
    record.parent.mkdir()
    old_source = vendor / "msgpack" / "old.py"
    vendor_txt = vendor / "vendor.txt"
    with record.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows(
            (
                (
                    "pip/_vendor/msgpack/old.py",
                    *_record_values(old_source.read_bytes()),
                ),
                (
                    "pip/_vendor/msgpack/__pycache__/old.cpython-314.pyc",
                    "sha256=generated",
                    "8",
                ),
                (
                    "pip/_vendor/vendor.txt",
                    *_record_values(vendor_txt.read_bytes()),
                ),
                (
                    "pip/_vendor/bom.cdx.json",
                    *_record_values(bom_path.read_bytes()),
                ),
                ("pip/__init__.py", "sha256=keep", "5"),
            )
        )
    return vendor, source, record


def test_refresh_replaces_vendored_msgpack_and_truthfully_updates_inventory(
    tmp_path: Path,
) -> None:
    module = _load_module()
    vendor, source, record = _fixture(tmp_path)

    module.refresh(vendor, source, record)

    assert not (vendor / "msgpack" / "old.py").exists()
    assert (vendor / "msgpack" / "__init__.py").read_bytes() == (
        source / "__init__.py"
    ).read_bytes()
    assert (vendor / "vendor.txt").read_text(encoding="utf-8") == (
        "msgpack==1.2.1\n"
    )
    bom = json.loads((vendor / "bom.cdx.json").read_text(encoding="utf-8"))
    components = bom["components"]
    assert [item["name"] for item in components] == ["pip", "msgpack", "packaging"]
    assert components[1] == {
        "name": "msgpack",
        "version": "1.2.1",
        "bom-ref": NEW_MSGPACK_REF,
        "purl": NEW_MSGPACK_REF,
    }
    rendered_bom = json.dumps(bom)
    assert OLD_MSGPACK_REF not in rendered_bom
    assert SETUPTOOLS_REF not in rendered_bom
    dependencies = bom["dependencies"]
    pip_dependency = next(item for item in dependencies if item["ref"] == PIP_REF)
    assert pip_dependency["dependsOn"].count(NEW_MSGPACK_REF) == 1
    assert [item for item in dependencies if item["ref"] == NEW_MSGPACK_REF] == [
        {"ref": NEW_MSGPACK_REF}
    ]
    with record.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    assert [row[0] for row in rows] == [
        "pip/_vendor/msgpack/__init__.py",
        "pip/_vendor/msgpack/fallback.py",
        "pip/_vendor/vendor.txt",
        "pip/_vendor/bom.cdx.json",
        "pip/__init__.py",
    ]
    for relative in (
        "pip/_vendor/msgpack/__init__.py",
        "pip/_vendor/msgpack/fallback.py",
        "pip/_vendor/vendor.txt",
        "pip/_vendor/bom.cdx.json",
    ):
        _assert_record_matches(record, tmp_path, relative)


def test_refresh_is_idempotent_after_truthful_update(tmp_path: Path) -> None:
    module = _load_module()
    vendor, source, record = _fixture(tmp_path)
    module.refresh(vendor, source, record)
    first = {
        path: path.read_bytes()
        for path in (vendor / "vendor.txt", vendor / "bom.cdx.json", record)
    }
    first.update(
        {
            path: path.read_bytes()
            for path in (vendor / "msgpack").rglob("*")
            if path.is_file()
        }
    )

    module.refresh(vendor, source, record)

    assert all(path.read_bytes() == payload for path, payload in first.items())


@pytest.mark.parametrize("corruption", ["duplicate-pip", "duplicate-msgpack"])
def test_refresh_rejects_unexpected_dependency_graph_without_mutation(
    tmp_path: Path, corruption: str
) -> None:
    module = _load_module()
    vendor, source, record = _fixture(tmp_path)
    bom_path = vendor / "bom.cdx.json"
    bom = json.loads(bom_path.read_text(encoding="utf-8"))
    if corruption == "duplicate-pip":
        bom["dependencies"].append(dict(bom["dependencies"][0]))
    else:
        bom["dependencies"].append({"ref": OLD_MSGPACK_REF})
    _write_bom_with_coherent_record(vendor, record, bom)
    snapshot = {
        path: path.read_bytes()
        for path in (
            vendor / "vendor.txt",
            bom_path,
            vendor / "msgpack" / "old.py",
            record,
        )
    }

    with pytest.raises(ValueError, match="dependency"):
        module.refresh(vendor, source, record)

    assert all(path.read_bytes() == payload for path, payload in snapshot.items())


def test_refresh_fails_closed_before_mutation_on_unexpected_inventory(
    tmp_path: Path,
) -> None:
    module = _load_module()
    vendor, source, record = _fixture(tmp_path)
    bom_path = vendor / "bom.cdx.json"
    bom = json.loads(bom_path.read_text(encoding="utf-8"))
    msgpack = next(item for item in bom["components"] if item["name"] == "msgpack")
    msgpack["version"] = "unexpected"
    _write_bom_with_coherent_record(vendor, record, bom)
    snapshot = _tree_snapshot(tmp_path)

    with pytest.raises(ValueError, match="msgpack component"):
        module.refresh(vendor, source, record)

    assert _tree_snapshot(tmp_path) == snapshot


def test_refresh_rejects_unexpected_graph_layout_without_partial_mutation(
    tmp_path: Path,
) -> None:
    module = _load_module()
    vendor, source, record = _fixture(tmp_path)
    bom_path = vendor / "bom.cdx.json"
    bom = json.loads(bom_path.read_text(encoding="utf-8"))
    bom["dependencies"] = {"unexpected": "mapping"}
    _write_bom_with_coherent_record(vendor, record, bom)
    snapshot = {
        path: path.read_bytes()
        for path in (
            vendor / "vendor.txt",
            bom_path,
            vendor / "msgpack" / "old.py",
            record,
        )
    }

    with pytest.raises(ValueError, match="dependencies must be a list"):
        module.refresh(vendor, source, record)

    assert all(path.read_bytes() == payload for path, payload in snapshot.items())


@pytest.mark.parametrize(
    "corruption",
    (
        "extra-new-standalone",
        "old-cross-edge",
        "setuptools-cross-edge",
        "duplicate-component-ref",
        "duplicate-dependency-ref",
        "new-wrong-edge",
    ),
)
def test_refresh_rejects_coherent_hybrid_graph_before_any_mutation(
    tmp_path: Path, corruption: str
) -> None:
    module = _load_module()
    vendor, source, record = _fixture(tmp_path)
    bom = json.loads((vendor / "bom.cdx.json").read_text(encoding="utf-8"))
    packaging_dependency = next(
        item
        for item in bom["dependencies"]
        if item["ref"] == "pkg:pypi/packaging@26.2"
    )
    if corruption == "extra-new-standalone":
        bom["dependencies"].append({"ref": NEW_MSGPACK_REF})
    elif corruption == "old-cross-edge":
        packaging_dependency["dependsOn"] = [OLD_MSGPACK_REF]
    elif corruption == "setuptools-cross-edge":
        packaging_dependency["dependsOn"] = [SETUPTOOLS_REF]
    elif corruption == "duplicate-component-ref":
        bom["components"].append(
            {"name": "shadow", "bom-ref": OLD_MSGPACK_REF, "type": "library"}
        )
    elif corruption == "duplicate-dependency-ref":
        bom["dependencies"].append({"ref": "pkg:pypi/packaging@26.2"})
    else:
        packaging_dependency["dependsOn"] = [NEW_MSGPACK_REF]
    _write_bom_with_coherent_record(vendor, record, bom)
    snapshot = _tree_snapshot(tmp_path)

    with pytest.raises(ValueError):
        module.refresh(vendor, source, record)

    assert _tree_snapshot(tmp_path) == snapshot


def test_refresh_underscore_updates_only_nbclassic_assets_and_record(
    tmp_path: Path,
) -> None:
    module = _load_module()
    component = tmp_path / "nbclassic" / "static" / "components" / "underscore"
    source = tmp_path / "underscore-1.13.8"
    record = tmp_path / "nbclassic-1.3.3.dist-info" / "RECORD"
    relative_assets = (
        "README.md",
        "package.json",
        "modules/package.json",
        "underscore-min.js",
    )
    for relative in relative_assets:
        old = component / relative
        new = source / relative
        old.parent.mkdir(parents=True, exist_ok=True)
        new.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("old\n", encoding="utf-8")
        new.write_text("new\n", encoding="utf-8")
    (component / "package.json").write_text('{"version":"1.13.7"}\n')
    (source / "package.json").write_text('{"version":"1.13.8"}\n')
    record.parent.mkdir(parents=True)
    prefix = "nbclassic/static/components/underscore/"
    with record.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        for relative in relative_assets:
            writer.writerow((prefix + relative, "sha256=old", "4"))
        writer.writerow(("nbclassic/__init__.py", "sha256=keep", "5"))

    module.refresh_underscore(component, source, record)

    assert json.loads((component / "package.json").read_text())["version"] == "1.13.8"
    with record.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    changed = {row[0]: row[1:] for row in rows if row[0].startswith(prefix)}
    assert set(changed) == {prefix + relative for relative in relative_assets}
    assert all(values[0].startswith("sha256=") for values in changed.values())
    assert [row for row in rows if row[0] == "nbclassic/__init__.py"] == [
        ["nbclassic/__init__.py", "sha256=keep", "5"]
    ]


def test_dockerfile_applies_exact_security_refreshes() -> None:
    dockerfile = (ROOT / "services/jupyterhub/build/Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "ARG UNDERSCORE_VERSION=1.13.8" in dockerfile
    assert (
        "ARG UNDERSCORE_SHA256="
        "6547214df2878ae60cc552422ef14af38b8d4e3aa613be5bfe9c52b775d96f7a"
    ) in dockerfile
    assert "python /tmp/refresh_pip_vendor.py" in dockerfile
    assert (
        "COPY --chown=${NB_UID}:${NB_GID} refresh_pip_vendor.py "
        "/tmp/refresh_pip_vendor.py"
    ) in dockerfile
    assert "--pip-record /opt/conda/lib/python3.13/site-packages/pip-26.2.1.dist-info/RECORD" in dockerfile
    assert "from pip._vendor import msgpack" in dockerfile
    assert "msgpack.unpackb(msgpack.packb(payload)) == payload" in dockerfile
    assert "python -m pip uninstall -y conda-rattler-solver py-rattler" in dockerfile
    assert "conda --version" in dockerfile
    assert "mamba --version" in dockerfile
    assert "python -m pip check" in dockerfile
