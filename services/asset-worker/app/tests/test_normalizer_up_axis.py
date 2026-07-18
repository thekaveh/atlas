"""#524: orientation policy — up_axis keep|auto|x|y|z, default keep.

The old behavior remapped the LARGEST-extent axis to +Y unconditionally, so a
spec-correct +Y-up model that is wider than tall (most props) was silently
rotated onto its side. Default is now `keep` (trust incoming orientation;
scale/center/ground only) and `auto` is a genuine minimum-AABB-volume search
over small pitch/roll with a dead-band — never an axis swap by extent.
"""

from __future__ import annotations

import math
import struct

from asset_worker.models import PostprocessParams, normalization_metadata
from asset_worker.normalizer import _build_glb, _parse_glb, normalize_glb


def _box_positions(w: float, h: float, d: float, *, tilt_z_deg: float = 0.0):
    """8 corners of a w×h×d box centered on origin, optionally rolled about Z."""
    xs, ys, zs = w / 2, h / 2, d / 2
    corners = [
        (sx * xs, sy * ys, sz * zs)
        for sx in (-1, 1)
        for sy in (-1, 1)
        for sz in (-1, 1)
    ]
    if tilt_z_deg:
        a = math.radians(tilt_z_deg)
        c, s = math.cos(a), math.sin(a)
        corners = [(x * c - y * s, x * s + y * c, z) for x, y, z in corners]
    return corners


def _glb_from_positions(positions) -> bytes:
    blob = b"".join(struct.pack("<fff", *p) for p in positions)
    doc = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(blob)}],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": len(positions),
                "type": "VEC3",
            }
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
    }
    return _build_glb(doc, blob)


def _dims_of(glb: bytes):
    doc, bin_chunk = _parse_glb(glb)
    count = doc["accessors"][0]["count"]
    pts = [struct.unpack_from("<fff", bin_chunk, i * 12) for i in range(count)]
    mins = [min(p[i] for p in pts) for i in range(3)]
    maxs = [max(p[i] for p in pts) for i in range(3)]
    return [maxs[i] - mins[i] for i in range(3)], mins


def _roundtrip(tmp_path, positions, **params):
    src = tmp_path / "in.glb"
    dst = tmp_path / "out.glb"
    src.write_bytes(_glb_from_positions(positions))
    normalize_glb(src, dst, PostprocessParams(**params))
    return _dims_of(dst.read_bytes())


# ── AC: the regression box — Y-up, wider than tall (2×1×2) ─────────────────
def test_keep_preserves_y_up_for_wide_box(tmp_path):
    """Default `keep`: vertical extent derives from the INPUT's Y extent, not
    its max extent — the castle/rock tip-over cannot recur."""
    dims, mins = _roundtrip(
        tmp_path, _box_positions(2, 1, 2), target_height_m=3.0
    )
    # scale = 3.0 / input Y extent (1) → output [6, 3, 6]
    assert abs(dims[1] - 3.0) < 1e-4
    assert abs(dims[0] - 6.0) < 1e-4 and abs(dims[2] - 6.0) < 1e-4
    assert abs(mins[1]) < 1e-4  # grounded at y=0


def test_auto_does_not_rotate_wide_but_upright_box(tmp_path):
    """AC: `auto` must not rotate a model already within a few degrees of
    Y-up, even when it is wider than tall."""
    dims, _ = _roundtrip(
        tmp_path, _box_positions(2, 1, 2), up_axis="auto", target_height_m=3.0
    )
    assert abs(dims[1] - 3.0) < 1e-4  # Y stayed the height axis


def test_auto_uprights_a_tilted_box(tmp_path):
    """`auto` is a real min-AABB-volume search: a box rolled 15° about Z comes
    back (near-)axis-aligned, so the AABB tightens back to the true dims."""
    dims, _ = _roundtrip(
        tmp_path,
        _box_positions(2, 1, 2, tilt_z_deg=15.0),
        up_axis="auto",
    )
    # Un-tilted, no scaling target → dims return to ~[2, 1, 2].
    assert abs(dims[0] - 2.0) < 0.05
    assert abs(dims[1] - 1.0) < 0.05
    assert abs(dims[2] - 2.0) < 0.05


def test_keep_leaves_tilt_alone(tmp_path):
    """`keep` performs no reorientation at all — a tilted input stays tilted
    (its AABB stays inflated)."""
    dims, _ = _roundtrip(tmp_path, _box_positions(2, 1, 2, tilt_z_deg=15.0))
    assert dims[1] > 1.4  # 15° roll inflates the Y extent well past 1


def test_explicit_axis_remap(tmp_path):
    """`x`/`z` explicitly remap that axis to +Y (the historical swap, opt-in)."""
    dims_x, _ = _roundtrip(tmp_path, _box_positions(5, 1, 2), up_axis="x")
    assert abs(dims_x[1] - 5.0) < 1e-4  # X became the height axis
    dims_z, _ = _roundtrip(tmp_path, _box_positions(2, 1, 5), up_axis="z")
    assert abs(dims_z[1] - 5.0) < 1e-4  # Z became the height axis


# ── golden fixtures: tall + squat assets round-trip under the default ──────
def test_golden_tall_and_squat_preserve_orientation_under_default(tmp_path):
    tall_dims, _ = _roundtrip(tmp_path, _box_positions(1, 3, 1))
    assert [round(d, 4) for d in tall_dims] == [1.0, 3.0, 1.0]
    squat_dims, _ = _roundtrip(tmp_path, _box_positions(3, 1, 3))
    assert [round(d, 4) for d in squat_dims] == [3.0, 1.0, 3.0]


# ── AC: honest metadata ─────────────────────────────────────────────────────
def test_metadata_reports_actual_algorithm():
    assert normalization_metadata(PostprocessParams())["method"] == "keep"
    assert (
        normalization_metadata(PostprocessParams(up_axis="auto"))["method"]
        == "min-aabb-volume"
    )
    assert (
        normalization_metadata(PostprocessParams(up_axis="z"))["method"] == "axis:z"
    )
    assert normalization_metadata(PostprocessParams())["up_axis"] == "keep"


def test_parse_glb_returns_none_on_corrupt_json_chunk() -> None:
    import struct

    from asset_worker.normalizer import _parse_glb

    body = b"{ not valid json "
    while len(body) % 4:
        body += b" "
    chunk = struct.pack("<II", len(body), 0x4E4F534A) + body  # JSON chunk
    data = struct.pack("<III", 0x46546C67, 2, 12 + len(chunk)) + chunk  # glTF v2 magic

    # Valid magic + version but a corrupt JSON chunk: must return None
    # (→ copy-through → gltf-transform authoritative 422), not raise.
    assert _parse_glb(data) is None
