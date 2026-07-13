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
    remapped = _orient_positions(positions, params)
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


def _orient_positions(
    positions: list[tuple[float, float, float]],
    params: PostprocessParams,
) -> list[tuple[float, float, float]]:
    """Apply the requested orientation policy (#524).

    ``keep`` (default) and ``y`` perform NO reorientation — glTF is +Y-up by
    spec, so incoming orientation is trusted and only scale/center/ground
    runs. ``x``/``z`` explicitly remap that axis to +Y (the historical swap,
    now opt-in). ``auto`` runs a genuine minimum-AABB-volume search over
    small pitch/roll tilts (what the metadata name promises) with a dead-band
    so a model already within a few degrees of Y-up is never touched — even
    when it is wider than tall (the extent-argmax bug this replaces).
    """
    if params.up_axis in ("keep", "y"):
        return positions
    if params.up_axis == "x":
        return [(y, x, z) for x, y, z in positions]
    if params.up_axis == "z":
        return [(x, z, y) for x, y, z in positions]
    return _auto_upright(positions)


# auto (min-AABB-volume) search bounds: small tilts only — never axis swaps.
_AUTO_MAX_TILT_DEG = 45.0
_AUTO_COARSE_STEP_DEG = 5.0
_AUTO_FINE_STEP_DEG = 1.0
# Dead-band: don't rotate when identity is already (near-)optimal.
_AUTO_DEADBAND_DEG = 3.0
_AUTO_MIN_IMPROVEMENT = 0.02  # best must beat identity volume by >2%
# AABB-volume search runs on a subsample so huge meshes stay fast; the chosen
# rotation is then applied to every vertex.
_AUTO_SAMPLE_LIMIT = 2048


def _auto_upright(
    positions: list[tuple[float, float, float]],
) -> list[tuple[float, float, float]]:
    import math

    stride = max(1, len(positions) // _AUTO_SAMPLE_LIMIT)
    sample = positions[::stride]

    def rotate(
        points: list[tuple[float, float, float]], rx: float, rz: float
    ) -> list[tuple[float, float, float]]:
        cx, sx = math.cos(rx), math.sin(rx)
        cz, sz = math.cos(rz), math.sin(rz)
        out = []
        for x, y, z in points:
            # pitch about X
            y1 = y * cx - z * sx
            z1 = y * sx + z * cx
            # roll about Z
            x2 = x * cz - y1 * sz
            y2 = x * sz + y1 * cz
            out.append((x2, y2, z1))
        return out

    def aabb_volume(points: list[tuple[float, float, float]]) -> float:
        mins = [min(p[i] for p in points) for i in range(3)]
        maxs = [max(p[i] for p in points) for i in range(3)]
        return max(
            (maxs[0] - mins[0]) * (maxs[1] - mins[1]) * (maxs[2] - mins[2]),
            1e-12,
        )

    identity_volume = aabb_volume(sample)

    def search(center_rx: float, center_rz: float, span: float, step: float):
        best = (identity_volume, 0.0, 0.0)
        steps = int(span // step)
        for i in range(-steps, steps + 1):
            for j in range(-steps, steps + 1):
                rx = center_rx + i * step
                rz = center_rz + j * step
                if abs(rx) > _AUTO_MAX_TILT_DEG or abs(rz) > _AUTO_MAX_TILT_DEG:
                    continue
                volume = aabb_volume(
                    rotate(sample, math.radians(rx), math.radians(rz))
                )
                if volume < best[0]:
                    best = (volume, rx, rz)
        return best

    _, rx, rz = search(0.0, 0.0, _AUTO_MAX_TILT_DEG, _AUTO_COARSE_STEP_DEG)
    best_volume, rx, rz = search(rx, rz, _AUTO_COARSE_STEP_DEG, _AUTO_FINE_STEP_DEG)

    near_upright = abs(rx) <= _AUTO_DEADBAND_DEG and abs(rz) <= _AUTO_DEADBAND_DEG
    improvement = (identity_volume - best_volume) / identity_volume
    if near_upright or improvement <= _AUTO_MIN_IMPROVEMENT:
        return positions

    return rotate(positions, math.radians(rx), math.radians(rz))


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
