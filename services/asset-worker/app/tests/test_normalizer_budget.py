"""Aggregate admission precedes POSITION decoding and output mutation."""

from __future__ import annotations

import struct

import pytest

from asset_worker import normalizer
from asset_worker.models import PostprocessParams


def _alias_payload() -> bytes:
    points = [(1.0, 2.0, 3.0), (3.0, 4.0, 5.0), (5.0, 6.0, 7.0)]
    binary = b"".join(struct.pack("<fff", *point) for point in points)
    doc = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": 36}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 36, "byteStride": 12}],
        "accessors": [
            {
                "bufferView": 0, "componentType": 5126, "type": "VEC3",
                "count": 3, "min": [1, 2, 3], "max": [5, 6, 7],
            }
            for _ in range(4)
        ],
        "meshes": [{"primitives": [
            {"attributes": {"POSITION": index}, "mode": 0}
            for index in range(4)
        ]}],
    }
    return normalizer._build_glb(doc, binary)


_POINTS = ((1.0, 2.0, 3.0), (3.0, 4.0, 5.0), (5.0, 6.0, 7.0))
_EXPECTED = ((-1.0, 0.0, -1.0), (0.0, 1.0, 0.0), (1.0, 2.0, 1.0))
_SENTINELS = tuple(struct.pack("<fff", 100 + index, 200, 300) for index in range(3))


def _add_interleaved_attribute(accessors, primitives):
    attribute_index = len(accessors)
    accessors.append({"bufferView": 0, "byteOffset": 12, "componentType": 5126,
                      "type": "VEC3", "count": 3})
    for primitive in primitives:
        primitive["attributes"]["_SENTINEL"] = attribute_index


def _position_accessor(start, count, stride):
    selected = _POINTS[start:start + count]
    return {
        "bufferView": 0, "byteOffset": start * stride, "componentType": 5126,
        "type": "VEC3", "count": count,
        "min": [min(p[i] for p in selected) for i in range(3)],
        "max": [max(p[i] for p in selected) for i in range(3)],
    }


def _position_payload(ranges, *, interleaved=False):
    stride = 24 if interleaved else 12
    binary = b"".join(
        struct.pack("<fff", *point) + (_SENTINELS[index] if interleaved else b"")
        for index, point in enumerate(_POINTS)
    )
    accessors = []
    for start, count in ranges:
        accessors.append(_position_accessor(start, count, stride))
    primitives = [{"attributes": {"POSITION": i}, "mode": 0} for i in range(len(ranges))]
    if interleaved:
        _add_interleaved_attribute(accessors, primitives)
    doc = {
        "asset": {"version": "2.0"}, "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteLength": len(binary), "byteStride": stride}],
        "accessors": accessors, "meshes": [{"primitives": primitives}],
    }
    return normalizer._build_glb(doc, binary)


def _assert_position_oracle(path, ranges, *, interleaved=False):
    doc, binary = normalizer._parse_glb(path.read_bytes())
    stride = 24 if interleaved else 12
    for index, expected in enumerate(_EXPECTED):
        assert struct.unpack_from("<fff", binary, index * stride) == expected
        if interleaved:
            assert binary[index * stride + 12:index * stride + 24] == _SENTINELS[index]
    for index, (start, count) in enumerate(ranges):
        selected = _EXPECTED[start:start + count]
        assert doc["accessors"][index]["min"] == [min(p[i] for p in selected) for i in range(3)]
        assert doc["accessors"][index]["max"] == [max(p[i] for p in selected) for i in range(3)]
    return doc


def _observe_initial_reads(monkeypatch):
    counts = []
    real_read = normalizer._read_positions

    def read(binary, accessors):
        positions = real_read(binary, accessors)
        counts.append(len(positions))
        return positions

    monkeypatch.setattr(normalizer, "_read_positions", read)
    return counts


def test_alias_budget_accepts_exact_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_WORKER_MAX_NORMALIZATION_VERTICES", "12")
    source, destination = tmp_path / "in.glb", tmp_path / "out.glb"
    source.write_bytes(_alias_payload())
    counts = _observe_initial_reads(monkeypatch)

    normalizer.normalize_glb(source, destination, PostprocessParams(target_height_m=2))

    assert counts == [12]
    _assert_position_oracle(destination, [(0, 3)] * 4)


def test_thirteenth_entry_rejects_at_twelve(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_WORKER_MAX_NORMALIZATION_VERTICES", "12")
    source, destination = tmp_path / "in.glb", tmp_path / "out.glb"
    source.write_bytes(_position_payload([(0, 3)] * 4 + [(0, 1)]))
    counts = _observe_initial_reads(monkeypatch)

    with pytest.raises(normalizer.NormalizationWorkLimitError, match="12 decoded POSITION-entry"):
        normalizer.normalize_glb(source, destination, PostprocessParams())

    assert counts == []
    assert not destination.exists()


def test_repeated_references_and_shared_nodes_count_once(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_WORKER_MAX_NORMALIZATION_VERTICES", "3")
    doc, binary = normalizer._parse_glb(_position_payload([(0, 3)]))
    doc["meshes"] = [{"primitives": [{"attributes": {"POSITION": 0}, "mode": 0}] * 2}] * 2
    doc["nodes"] = [{"mesh": 0}, {"mesh": 0}, {"mesh": 1}]
    doc["scenes"], doc["scene"] = [{"nodes": [0, 1, 2]}], 0
    source, destination = tmp_path / "in.glb", tmp_path / "out.glb"
    source.write_bytes(normalizer._build_glb(doc, binary))
    counts = _observe_initial_reads(monkeypatch)

    normalizer.normalize_glb(source, destination, PostprocessParams(target_height_m=2))

    assert counts == [3]
    result = _assert_position_oracle(destination, [(0, 3)])
    for field in ("meshes", "nodes", "scenes", "scene"):
        assert result[field] == doc[field]


def test_uninstantiated_mesh_still_contributes_work(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_WORKER_MAX_NORMALIZATION_VERTICES", "3")
    doc, binary = normalizer._parse_glb(_position_payload([(0, 3), (0, 3)]))
    doc["meshes"] = [{"primitives": [primitive]} for primitive in doc["meshes"][0]["primitives"]]
    doc["nodes"], doc["scenes"], doc["scene"] = [{"mesh": 0}], [{"nodes": [0]}], 0
    source, destination = tmp_path / "in.glb", tmp_path / "out.glb"
    source.write_bytes(normalizer._build_glb(doc, binary))
    counts = _observe_initial_reads(monkeypatch)

    with pytest.raises(normalizer.NormalizationWorkLimitError):
        normalizer.normalize_glb(source, destination, PostprocessParams())

    assert counts == []
    assert not destination.exists()


def test_overlapping_accessors_count_separately_without_repeated_mutation(tmp_path, monkeypatch):
    source = tmp_path / "in.glb"
    source.write_bytes(_position_payload([(0, 2), (1, 2)]))
    counts = _observe_initial_reads(monkeypatch)
    monkeypatch.setenv("ASSET_WORKER_MAX_NORMALIZATION_VERTICES", "4")

    normalizer.normalize_glb(source, tmp_path / "accepted.glb", PostprocessParams(target_height_m=2))

    assert counts == [4]
    _assert_position_oracle(tmp_path / "accepted.glb", [(0, 2), (1, 2)])
    monkeypatch.setenv("ASSET_WORKER_MAX_NORMALIZATION_VERTICES", "3")
    with pytest.raises(normalizer.NormalizationWorkLimitError):
        normalizer.normalize_glb(source, tmp_path / "rejected.glb", PostprocessParams())
    assert counts == [4]
    assert not (tmp_path / "rejected.glb").exists()


def test_interleaved_attributes_remain_byte_exact(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_WORKER_MAX_NORMALIZATION_VERTICES", "3")
    source, destination = tmp_path / "in.glb", tmp_path / "out.glb"
    source.write_bytes(_position_payload([(0, 3)], interleaved=True))
    counts = _observe_initial_reads(monkeypatch)

    normalizer.normalize_glb(source, destination, PostprocessParams(target_height_m=2))

    assert counts == [3]
    result = _assert_position_oracle(destination, [(0, 3)], interleaved=True)
    original, _ = normalizer._parse_glb(source.read_bytes())
    assert result["meshes"] == original["meshes"]
    assert result["accessors"][1] == original["accessors"][1]


def test_huge_declared_count_is_bounded_before_admission(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_WORKER_MAX_NORMALIZATION_VERTICES", "3")
    doc, binary = normalizer._parse_glb(_position_payload([(0, 3)]))
    doc["accessors"][0]["count"] = 10**12
    source, destination = tmp_path / "in.glb", tmp_path / "out.glb"
    source.write_bytes(normalizer._build_glb(doc, binary))
    counts = _observe_initial_reads(monkeypatch)

    normalizer.normalize_glb(source, destination, PostprocessParams(target_height_m=2))

    assert counts == [3]
    _assert_position_oracle(destination, [(0, 3)])


@pytest.mark.parametrize("shape", ["unsupported", "dangling", "malformed", "zero", "outside-bin"])
def test_zero_or_unsupported_work_copies_before_config_parsing(tmp_path, monkeypatch, shape):
    monkeypatch.setenv("ASSET_WORKER_MAX_NORMALIZATION_VERTICES", "invalid")
    doc, binary = normalizer._parse_glb(_position_payload([(0, 3)]))
    if shape == "unsupported":
        doc["accessors"][0]["componentType"] = 5123
    elif shape == "dangling":
        doc["meshes"][0]["primitives"][0]["attributes"]["POSITION"] = 7
    elif shape == "malformed":
        doc["meshes"] = {"invalid": True}
    elif shape == "zero":
        doc["accessors"][0]["count"] = 0
    else:
        doc["accessors"][0]["byteOffset"] = 36
    source, destination = tmp_path / "in.glb", tmp_path / "out.glb"
    payload = normalizer._build_glb(doc, binary)
    source.write_bytes(payload)

    normalizer.normalize_glb(source, destination, PostprocessParams())

    assert destination.read_bytes() == payload


@pytest.mark.parametrize("bad_limit", ["invalid", "", "0", "-1", "1.5", "nan", "inf", "1e309"])
def test_invalid_budget_is_operator_error_before_decoding(tmp_path, monkeypatch, bad_limit):
    monkeypatch.setenv("ASSET_WORKER_MAX_NORMALIZATION_VERTICES", bad_limit)
    source, destination = tmp_path / "in.glb", tmp_path / "out.glb"
    source.write_bytes(_alias_payload())
    counts = _observe_initial_reads(monkeypatch)

    with pytest.raises(ValueError, match="ASSET_WORKER_MAX_NORMALIZATION_VERTICES must be a positive integer"):
        normalizer.normalize_glb(source, destination, PostprocessParams())

    assert counts == []
    assert not destination.exists()


@pytest.mark.parametrize("axis", ["keep", "auto", "x", "y", "z"])
def test_every_orientation_policy_applies_admission(tmp_path, monkeypatch, axis):
    monkeypatch.setenv("ASSET_WORKER_MAX_NORMALIZATION_VERTICES", "11")
    source, destination = tmp_path / "in.glb", tmp_path / "out.glb"
    source.write_bytes(_alias_payload())
    counts = _observe_initial_reads(monkeypatch)

    with pytest.raises(normalizer.NormalizationWorkLimitError):
        normalizer.normalize_glb(source, destination, PostprocessParams(up_axis=axis))

    assert counts == []
    assert not destination.exists()


@pytest.mark.parametrize("existing", [False, True])
def test_runner_maps_budget_failure_before_converter(tmp_path, monkeypatch, existing):
    import subprocess
    from asset_worker.runner import GltfTransformError, run_gltf_transform

    source, destination = tmp_path / "in.glb", tmp_path / "out.glb"
    normalized = destination.with_suffix(".normalized.glb")
    source.write_bytes(_alias_payload())
    if existing:
        destination.write_bytes(b"output sentinel")
        normalized.write_bytes(b"normalized sentinel")
    monkeypatch.setenv("ASSET_WORKER_MAX_NORMALIZATION_VERTICES", "11")
    calls = []

    def forbidden_run(*args, **kwargs):
        calls.append(args)
        raise AssertionError("over-budget normalization must not start a converter")

    monkeypatch.setattr(subprocess, "run", forbidden_run)
    with pytest.raises(GltfTransformError, match="11 decoded POSITION-entry") as failure:
        run_gltf_transform(source, destination, PostprocessParams())
    assert failure.value.kind == "failed"
    assert isinstance(failure.value.__cause__, normalizer.NormalizationWorkLimitError)
    assert calls == []
    if existing:
        assert destination.read_bytes() == b"output sentinel"
        assert normalized.read_bytes() == b"normalized sentinel"
    else:
        assert not destination.exists() and not normalized.exists()


def test_runner_preserves_operator_configuration_error(tmp_path, monkeypatch):
    import subprocess
    from asset_worker.runner import run_gltf_transform

    source, destination = tmp_path / "in.glb", tmp_path / "out.glb"
    source.write_bytes(_alias_payload())
    monkeypatch.setenv("ASSET_WORKER_MAX_NORMALIZATION_VERTICES", "invalid")

    def forbidden_run(*args, **kwargs):
        raise AssertionError("invalid configuration must not start a converter")

    monkeypatch.setattr(subprocess, "run", forbidden_run)
    with pytest.raises(ValueError, match="ASSET_WORKER_MAX_NORMALIZATION_VERTICES"):
        run_gltf_transform(source, destination, PostprocessParams())
    assert not destination.exists()
    assert not destination.with_suffix(".normalized.glb").exists()


def _budget_api_setup(monkeypatch):
    import subprocess
    from pathlib import Path
    from starlette.datastructures import UploadFile
    from asset_worker import api

    state = {"converters": [], "published": [], "paths": [], "files": []}
    real_runner, real_close = api.run_gltf_transform, UploadFile.close

    class Storage:
        def fetch(self, bucket, key):
            assert (bucket, key) == ("raw-assets", "mesh.glb")
            return _alias_payload()

        def store(self, data, *, sha256):
            state["published"].append(data)
            return {"storage": "local", "key": f"gltf/{sha256}.glb"}

    def run(source, destination, params):
        state["paths"].append((source, destination))
        return real_runner(source, destination, params)

    def converter(command, *, check, timeout):
        state["converters"].append(command)
        assert check is True and timeout == 300
        if command[1] == "optimize":
            Path(command[3]).write_bytes(Path(command[2]).read_bytes())
        return subprocess.CompletedProcess(command, 0)

    async def close(upload):
        state["files"].append(upload.file)
        return await real_close(upload)

    monkeypatch.setattr(api, "ArtifactStorage", Storage)
    monkeypatch.setattr(api, "run_gltf_transform", run)
    monkeypatch.setattr(subprocess, "run", converter)
    monkeypatch.setattr(UploadFile, "close", close)
    monkeypatch.setenv("ASSET_WORKER_ALLOWED_INPUT_BUCKETS", "raw-assets")
    monkeypatch.setenv("ASSET_WORKER_CONCURRENCY", "1")
    return api.create_app(api_token="budget-test-token"), state


def _post_budget_input(client, route):
    if route == "upload":
        return client.post("/gltf/postprocess", files={
            "file": ("mesh.glb", _alias_payload(), "model/gltf-binary")
        })
    return client.post("/gltf/postprocess/ref", json={
        "input": {"bucket": "raw-assets", "key": "mesh.glb"}, "params": {}
    })


def _assert_budget_resources_released(app, state):
    assert state["paths"]
    for source, destination in state["paths"]:
        assert not source.parent.exists()
        assert not destination.parent.exists()
    assert all(file.closed for file in state["files"])
    assert app.state.transform_semaphore.acquire(blocking=False)
    app.state.transform_semaphore.release()


@pytest.mark.parametrize("route", ["upload", "reference"])
def test_api_budget_rejection_cleans_resources_and_reuses_slot(monkeypatch, route):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ASSET_WORKER_MAX_NORMALIZATION_VERTICES", "11")
    app, state = _budget_api_setup(monkeypatch)
    with TestClient(app, headers={"Authorization": "Bearer budget-test-token"}) as client:
        response = _post_budget_input(client, route)
        assert response.status_code == 422
        assert "11 decoded POSITION-entry" in response.json()["detail"]
        assert state["converters"] == [] and state["published"] == []
        _assert_budget_resources_released(app, state)
        assert len(state["files"]) == (1 if route == "upload" else 0)

        monkeypatch.setenv("ASSET_WORKER_MAX_NORMALIZATION_VERTICES", "12")
        retry = _post_budget_input(client, route)
        assert retry.status_code == 200
        assert len(state["converters"]) == 3 and len(state["published"]) == 1
        _assert_budget_resources_released(app, state)


@pytest.mark.parametrize("route", ["upload", "reference"])
@pytest.mark.parametrize("bad_limit", ["invalid", "0", "-1"])
def test_api_invalid_budget_is_server_error_with_reusable_admission(monkeypatch, route, bad_limit):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ASSET_WORKER_MAX_NORMALIZATION_VERTICES", bad_limit)
    app, state = _budget_api_setup(monkeypatch)
    with TestClient(app, raise_server_exceptions=False,
                    headers={"Authorization": "Bearer budget-test-token"}) as client:
        response = _post_budget_input(client, route)
        assert response.status_code == 500
        assert state["converters"] == [] and state["published"] == []
        _assert_budget_resources_released(app, state)
        assert len(state["files"]) == (1 if route == "upload" else 0)

        monkeypatch.setenv("ASSET_WORKER_MAX_NORMALIZATION_VERTICES", "12")
        retry = _post_budget_input(client, route)
        assert retry.status_code == 200
        assert len(state["converters"]) == 3 and len(state["published"]) == 1
        _assert_budget_resources_released(app, state)


_FLOAT32_CASES = [
    ("height", 1e40, False), ("width", 1e40, False),
    ("height", 1e308, True), ("width", 1e308, True),
]


def _float32_payload(axis, thin):
    doc, binary = normalizer._parse_glb(_alias_payload())
    if thin:
        component = 1 if axis == "height" else 0
        for index, point in enumerate(_POINTS):
            struct.pack_into("<f", binary, index * 12 + component * 4,
                             point[component] * 1e-38)
        normalizer._update_accessor_bounds(
            doc, normalizer._position_accessors(doc, len(binary)), binary
        )
    return normalizer._build_glb(doc, binary)


def _post_float32_input(client, route, case):
    axis, size, thin = case
    params = {"normalize_axis": axis, f"target_{axis}_m": size}
    if route == "upload":
        return client.post("/gltf/postprocess", data=params, files={
            "file": ("mesh.glb", _float32_payload(axis, thin), "model/gltf-binary")
        })
    return client.post("/gltf/postprocess/ref", json={
        "input": {"bucket": "raw-assets", "key": "mesh.glb"}, "params": params
    })


@pytest.mark.parametrize("case", _FLOAT32_CASES)
def test_runner_rejects_float32_overflow_before_output_mutation(tmp_path, monkeypatch, case):
    import subprocess
    from asset_worker.runner import GltfTransformError, run_gltf_transform

    axis, size, thin = case
    source, destination = tmp_path / "in.glb", tmp_path / "out.glb"
    normalized = destination.with_suffix(".normalized.glb")
    source.write_bytes(_float32_payload(axis, thin))
    destination.write_bytes(b"output sentinel")
    normalized.write_bytes(b"normalized sentinel")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: calls.append(a))

    with pytest.raises(GltfTransformError, match="float32") as failure:
        run_gltf_transform(source, destination, PostprocessParams(
            normalize_axis=axis, **{f"target_{axis}_m": size}
        ))

    assert failure.value.kind == "failed"
    assert calls == []
    assert destination.read_bytes() == b"output sentinel"
    assert normalized.read_bytes() == b"normalized sentinel"


@pytest.mark.parametrize("route", ["upload", "reference"])
@pytest.mark.parametrize("case", _FLOAT32_CASES)
def test_api_float32_rejection_releases_resources_and_allows_retry(monkeypatch, route, case):
    from fastapi.testclient import TestClient
    from asset_worker import api

    axis, _, thin = case
    app, state = _budget_api_setup(monkeypatch)
    monkeypatch.setattr(api.ArtifactStorage, "fetch",
                        lambda self, bucket, key: _float32_payload(axis, thin))
    with TestClient(app, raise_server_exceptions=False,
                    headers={"Authorization": "Bearer budget-test-token"}) as client:
        response = _post_float32_input(client, route, case)
        assert response.status_code == 422
        assert "float32" in response.json()["detail"]
        assert (state["converters"], state["published"]) == ([], [])
        _assert_budget_resources_released(app, state)

        retry = _post_float32_input(client, route, (axis, 1.8, thin))
        assert retry.status_code == 200
        assert (len(state["converters"]), len(state["published"])) == (3, 1)
        _assert_budget_resources_released(app, state)
