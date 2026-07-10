from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient


def test_multipart_glb_postprocess_stores_content_addressed_local_artifact(
    monkeypatch, tmp_path
) -> None:
    from asset_worker import api
    from asset_worker.models import PostprocessParams

    output_bytes = b"optimized-glb"
    calls = []

    def fake_run(input_path, output_path, params: PostprocessParams) -> None:
        calls.append((input_path, output_path, params))
        output_path.write_bytes(output_bytes)

    monkeypatch.setattr(api, "run_gltf_transform", fake_run)
    monkeypatch.setenv("ASSET_WORKER_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv("ASSET_WORKER_MINIO_ENABLED", "false")

    client = TestClient(api.create_app())
    response = client.post(
        "/gltf/postprocess",
        files={"file": ("scene.glb", b"raw-glb", "model/gltf-binary")},
        data={
            "target_height_m": "1.8",
            "normalize_axis": "height",
            "simplify_ratio": "0.5",
            "draco": "true",
            "meshopt": "true",
            "ktx2": "true",
            "collider_decimation": "0.25",
        },
    )

    assert response.status_code == 200
    body = response.json()
    expected_sha = hashlib.sha256(output_bytes).hexdigest()
    assert body["status"] == "succeeded"
    assert body["sha256"] == expected_sha
    assert body["artifact"]["storage"] == "local"
    assert body["artifact"]["key"] == f"gltf/{expected_sha}.glb"
    assert body["artifact"]["content_type"] == "model/gltf-binary"
    assert body["download_url"] == f"/gltf/artifacts/{expected_sha}.glb"
    assert body["normalization"] == {
        "method": "min-aabb-auto-upright",
        "base_y": 0,
        "normalize_axis": "height",
        "target_height_m": 1.8,
    }
    assert body["optimization"] == {
        "simplify_ratio": 0.5,
        "draco": True,
        "meshopt": True,
        "ktx2": True,
        "collider_decimation": 0.25,
    }
    assert calls[0][2].target_height_m == 1.8
    assert calls[0][2].draco is True

    downloaded = client.get(body["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.content == output_bytes
    assert downloaded.headers["content-type"] == "model/gltf-binary"


def test_minio_reference_postprocess_round_trips_through_content_addressed_bucket(
    monkeypatch, tmp_path
) -> None:
    from asset_worker import api

    output_bytes = b"optimized-from-minio"
    stored = {}

    class FakeStorage:
        def __init__(self) -> None:
            self.output_bucket = "asset-worker"

        def fetch(self, bucket: str, key: str) -> bytes:
            assert (bucket, key) == ("raw-assets", "incoming/mesh.glb")
            return b"raw-from-minio"

        def store(self, data: bytes, *, sha256: str) -> dict[str, str]:
            stored["data"] = data
            stored["sha256"] = sha256
            return {
                "storage": "minio",
                "bucket": self.output_bucket,
                "key": f"gltf/{sha256}.glb",
                "uri": f"s3://{self.output_bucket}/gltf/{sha256}.glb",
                "content_type": "model/gltf-binary",
            }

    def fake_run(input_path, output_path, params) -> None:
        assert input_path.read_bytes() == b"raw-from-minio"
        output_path.write_bytes(output_bytes)

    monkeypatch.setattr(api, "ArtifactStorage", FakeStorage)
    monkeypatch.setattr(api, "run_gltf_transform", fake_run)
    monkeypatch.setenv("ASSET_WORKER_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv("ASSET_WORKER_MINIO_ENABLED", "true")

    client = TestClient(api.create_app())
    response = client.post(
        "/gltf/postprocess/ref",
        json={
            "input": {"bucket": "raw-assets", "key": "incoming/mesh.glb"},
            "params": {"target_width_m": 2.0, "normalize_axis": "width"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    expected_sha = hashlib.sha256(output_bytes).hexdigest()
    assert body["sha256"] == expected_sha
    assert body["artifact"]["storage"] == "minio"
    assert body["artifact"]["bucket"] == "asset-worker"
    assert body["artifact"]["key"] == f"gltf/{expected_sha}.glb"
    assert body["artifact"]["uri"] == f"s3://asset-worker/gltf/{expected_sha}.glb"
    assert body["normalization"]["normalize_axis"] == "width"
    assert body["normalization"]["target_width_m"] == 2.0
    assert stored == {"data": output_bytes, "sha256": expected_sha}


def test_postprocess_requires_glb_input(monkeypatch, tmp_path) -> None:
    from asset_worker import api

    monkeypatch.setenv("ASSET_WORKER_ARTIFACT_DIR", str(tmp_path))

    client = TestClient(api.create_app())
    response = client.post(
        "/gltf/postprocess",
        files={"file": ("scene.txt", b"not-glb", "text/plain")},
    )

    assert response.status_code == 400
    assert "GLB" in response.json()["detail"]
