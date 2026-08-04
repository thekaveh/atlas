"""Build the JSON/Markdown artifact bundle consumed by LightRAG."""

import io
import re
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable


_SAFE_STEM = re.compile(r"[^A-Za-z0-9._-]+")


def _canonical_stem(upload_name: str) -> str:
    raw_name = str(upload_name or "document").replace("\x00", "").replace("\\", "/")
    leaf = PurePosixPath(raw_name).name
    stem = PurePosixPath(leaf).stem
    safe = _SAFE_STEM.sub("_", stem).strip("._-")
    return safe or "document"


def _artifact_files(root: Path, artifacts: Path, json_path: Path, markdown_path: Path) -> Iterable[Path]:
    yield json_path
    yield markdown_path
    if not artifacts.is_dir():
        return
    for path in sorted(artifacts.rglob("*")):
        if path.is_file() and not path.is_symlink():
            resolved = path.resolve()
            try:
                resolved.relative_to(artifacts.resolve())
            except ValueError:
                continue
            yield path


def _archive_name(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    name = PurePosixPath(*relative.parts).as_posix()
    parsed = PurePosixPath(name)
    if parsed.is_absolute() or ".." in parsed.parts or "\x00" in name:
        raise ValueError("unsafe bundle artifact path")
    return name


def build_lightrag_bundle(conversion_result, *, upload_name: str) -> bytes:
    """Export one Docling conversion result as a deterministic safe zip."""
    document = getattr(conversion_result, "document", None)
    if document is None:
        raise ValueError("conversion result has no document")

    from docling_core.types.doc import ImageRefMode

    stem = _canonical_stem(upload_name)
    with tempfile.TemporaryDirectory(prefix="atlas-docling-bundle-") as temp:
        root = Path(temp)
        artifacts = root / "assets"
        json_path = root / f"{stem}.json"
        markdown_path = root / f"{stem}.md"

        document.save_as_json(
            json_path,
            artifacts_dir=artifacts,
            image_mode=ImageRefMode.REFERENCED,
        )
        document.save_as_markdown(
            markdown_path,
            artifacts_dir=artifacts,
            image_mode=ImageRefMode.REFERENCED,
        )

        entries = []
        seen = set()
        for path in _artifact_files(root, artifacts, json_path, markdown_path):
            name = _archive_name(root, path)
            if name in seen:
                raise ValueError("duplicate bundle artifact path")
            seen.add(name)
            entries.append((name, path))
        entries.sort(key=lambda entry: entry[0])

        output = io.BytesIO()
        with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, path in entries:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100600 << 16
                archive.writestr(info, path.read_bytes())
        return output.getvalue()
