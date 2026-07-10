from __future__ import annotations

import json
import shutil
import struct
from dataclasses import dataclass
from pathlib import Path

from .models import PostprocessParams


JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942
FLOAT = 5126


@dataclass(frozen=True)
class PositionAccessor:
    accessor_index: int
    buffer_view_index: int
    count: int
    offset: int
    stride: int


def normalize_glb(input_path: Path, output_path: Path, params: PostprocessParams) -> None:
    """Apply Atlas' deterministic GLB normalization contract.

    The transform is intentionally conservative: only binary GLB v2 files with
    float32 POSITION accessors are mutated. Unsupported files are copied through
    so glTF-Transform can still validate and produce the authoritative error.
    """
    data = input_path.read_bytes()
    parsed = _parse_glb(data)
    if parsed is None:
        shutil.copyfile(input_path, output_path)
        return

    doc, bin_chunk = parsed
    accessors = _position_accessors(doc)
    if not accessors:
        shutil.copyfile(input_path, output_path)
        return

    positions = _read_positions(bin_chunk, accessors)
    if not positions:
        shutil.copyfile(input_path, output_path)
        return

    transformed = _transform_positions(positions, params)
    _write_positions(bin_chunk, accessors, transformed)
    _update_accessor_bounds(doc, accessors, bin_chunk)
    output_path.write_bytes(_build_glb(doc, bytes(bin_chunk)))


def _parse_glb(data: bytes) -> tuple[dict, bytearray] | None:
    if len(data) < 20:
        return None
    magic, version, _length = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67 or version != 2:
        return None
    offset = 12
    doc = None
    bin_chunk = None
    while offset + 8 <= len(data):
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        chunk = data[offset: offset + chunk_length]
        offset += chunk_length
        if chunk_type == JSON_CHUNK:
            doc = json.loads(chunk.rstrip(b" \x00").decode("utf-8"))
        elif chunk_type == BIN_CHUNK:
            bin_chunk = bytearray(chunk)
    if not isinstance(doc, dict) or bin_chunk is None:
        return None
    return doc, bin_chunk


def _position_accessors(doc: dict) -> list[PositionAccessor]:
    result: list[PositionAccessor] = []
    accessors = doc.get("accessors") or []
    buffer_views = doc.get("bufferViews") or []
    meshes = doc.get("meshes") or []
    used_indices: set[int] = set()
    for mesh in meshes:
        for primitive in mesh.get("primitives") or []:
            position_index = (primitive.get("attributes") or {}).get("POSITION")
            if isinstance(position_index, int):
                used_indices.add(position_index)
    for index in sorted(used_indices):
        accessor = accessors[index]
        if accessor.get("componentType") != FLOAT or accessor.get("type") != "VEC3":
            continue
        view_index = accessor.get("bufferView")
        if not isinstance(view_index, int):
            continue
        view = buffer_views[view_index]
        if view.get("buffer", 0) != 0:
            continue
        view_offset = int(view.get("byteOffset", 0))
        accessor_offset = int(accessor.get("byteOffset", 0))
        stride = int(view.get("byteStride", 12))
        result.append(
            PositionAccessor(
                accessor_index=index,
                buffer_view_index=view_index,
                count=int(accessor.get("count", 0)),
                offset=view_offset + accessor_offset,
                stride=stride,
            )
        )
    return result


def _read_positions(bin_chunk: bytearray, accessors: list[PositionAccessor]) -> list[tuple[float, float, float]]:
    positions: list[tuple[float, float, float]] = []
    for accessor in accessors:
        for i in range(accessor.count):
            offset = accessor.offset + i * accessor.stride
            if offset + 12 <= len(bin_chunk):
                positions.append(struct.unpack_from("<fff", bin_chunk, offset))
    return positions


def _transform_positions(
    positions: list[tuple[float, float, float]],
    params: PostprocessParams,
) -> list[tuple[float, float, float]]:
    mins = [min(point[i] for point in positions) for i in range(3)]
    maxs = [max(point[i] for point in positions) for i in range(3)]
    extents = [maxs[i] - mins[i] for i in range(3)]
    up_axis = max(range(3), key=lambda i: extents[i])

    def remap(point: tuple[float, float, float]) -> tuple[float, float, float]:
        x, y, z = point
        if up_axis == 0:
            return (y, x, z)
        if up_axis == 2:
            return (x, z, y)
        return (x, y, z)

    remapped = [remap(point) for point in positions]
    mins = [min(point[i] for point in remapped) for i in range(3)]
    maxs = [max(point[i] for point in remapped) for i in range(3)]
    height = max(maxs[1] - mins[1], 1e-9)
    width = max(maxs[0] - mins[0], maxs[2] - mins[2], 1e-9)
    if params.normalize_axis == "height" and params.target_height_m:
        scale = params.target_height_m / height
    elif params.normalize_axis == "width" and params.target_width_m:
        scale = params.target_width_m / width
    else:
        scale = 1.0
    center_x = (mins[0] + maxs[0]) / 2
    center_z = (mins[2] + maxs[2]) / 2
    return [
        ((point[0] - center_x) * scale, (point[1] - mins[1]) * scale, (point[2] - center_z) * scale)
        for point in remapped
    ]


def _write_positions(
    bin_chunk: bytearray,
    accessors: list[PositionAccessor],
    transformed: list[tuple[float, float, float]],
) -> None:
    cursor = 0
    for accessor in accessors:
        for i in range(accessor.count):
            if cursor >= len(transformed):
                return
            offset = accessor.offset + i * accessor.stride
            if offset + 12 <= len(bin_chunk):
                struct.pack_into("<fff", bin_chunk, offset, *transformed[cursor])
            cursor += 1


def _update_accessor_bounds(doc: dict, accessors: list[PositionAccessor], bin_chunk: bytearray) -> None:
    all_accessors = doc.get("accessors") or []
    for accessor_ref in accessors:
        values = []
        for i in range(accessor_ref.count):
            offset = accessor_ref.offset + i * accessor_ref.stride
            if offset + 12 <= len(bin_chunk):
                values.append(struct.unpack_from("<fff", bin_chunk, offset))
        if not values:
            continue
        accessor = all_accessors[accessor_ref.accessor_index]
        accessor["min"] = [min(point[i] for point in values) for i in range(3)]
        accessor["max"] = [max(point[i] for point in values) for i in range(3)]


def _pad4(data: bytes, pad_byte: bytes) -> bytes:
    remainder = len(data) % 4
    if remainder == 0:
        return data
    return data + pad_byte * (4 - remainder)


def _build_glb(doc: dict, bin_chunk: bytes) -> bytes:
    json_chunk = _pad4(json.dumps(doc, separators=(",", ":")).encode("utf-8"), b" ")
    padded_bin = _pad4(bin_chunk, b"\x00")
    total_length = 12 + 8 + len(json_chunk) + 8 + len(padded_bin)
    return b"".join(
        [
            struct.pack("<III", 0x46546C67, 2, total_length),
            struct.pack("<II", len(json_chunk), JSON_CHUNK),
            json_chunk,
            struct.pack("<II", len(padded_bin), BIN_CHUNK),
            padded_bin,
        ]
    )
