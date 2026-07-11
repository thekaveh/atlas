from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient


def _fake_artifacts(out_dir, *, with_textures=True, color_mean=0.42, mode="bake"):
    from asset_baker.runner import BakeArtifacts

    out_dir.mkdir(parents=True, exist_ok=True)
    glb = out_dir / "input_LP.glb"
    glb.write_bytes(b"lp-glb-bytes")
    bc = nm = None
    if with_textures:
        bc = out_dir / "bc.png"
        bc.write_bytes(b"basecolor-bytes")
        nm = out_dir / "nm.png"
        nm.write_bytes(b"normal-bytes")
    summary = {"mode": mode, "color_mean": color_mean, "faces_in": 1000, "tris_out": 500,
               "shells_kept": 1, "duration_s": 1.2}
    return BakeArtifacts(glb_path=glb, basecolor_path=bc, normal_path=nm, summary=summary)


def test_bake_upload_stores_content_addressed_local_artifacts(monkeypatch, tmp_path):
    from asset_baker import api

    def fake_run(input_path, out_dir, params):
        assert params.mode == "bake" and params.target_tris == 15000
        return _fake_artifacts(out_dir)

    monkeypatch.setattr(api, "run_bake", fake_run)
    monkeypatch.setenv("ASSET_BAKER_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv("ASSET_BAKER_MINIO_ENABLED", "false")

    client = TestClient(api.create_app())
    response = client.post(
        "/assets/bake",
        files={"file": ("cottage.glb", b"raw-glb", "model/gltf-binary")},
        data={"target_tris": "15000", "tex_size": "2048", "mode": "bake"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    glb_sha = hashlib.sha256(b"lp-glb-bytes").hexdigest()
    assert body["status"] == "succeeded"
    assert body["sha256"] == glb_sha
    assert body["artifact"]["storage"] == "local"
    assert body["artifact"]["key"] == f"bake/{glb_sha}.glb"
    assert body["download_url"] == f"/assets/artifacts/{glb_sha}.glb"
    assert body["summary"]["mode"] == "bake"
    assert body["summary"]["color_mean"] == 0.42
    assert {t["role"] for t in body["textures"]} == {"basecolor", "normal"}

    downloaded = client.get(body["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.content == b"lp-glb-bytes"
    assert downloaded.headers["content-type"] == "model/gltf-binary"

    bc_sha = hashlib.sha256(b"basecolor-bytes").hexdigest()
    tex = client.get(f"/assets/artifacts/{bc_sha}.png")
    assert tex.status_code == 200 and tex.content == b"basecolor-bytes"
    assert tex.headers["content-type"] == "image/png"


def test_bake_ref_round_trips_through_content_addressed_bucket(monkeypatch, tmp_path):
    from asset_baker import api

    stored = []

    class FakeStorage:
        output_bucket = "asset-baker"

        def fetch(self, bucket, key):
            assert (bucket, key) == ("raw-assets", "incoming/mesh.glb")
            return b"raw-from-minio"

        def store(self, data, *, sha256, suffix, content_type):
            stored.append((sha256, suffix, content_type))
            return {
                "storage": "minio", "bucket": self.output_bucket,
                "key": f"bake/{sha256}.{suffix}",
                "uri": f"s3://{self.output_bucket}/bake/{sha256}.{suffix}",
                "content_type": content_type,
            }

    def fake_run(input_path, out_dir, params):
        assert input_path.read_bytes() == b"raw-from-minio"
        return _fake_artifacts(out_dir)

    monkeypatch.setattr(api, "ArtifactStorage", FakeStorage)
    monkeypatch.setattr(api, "run_bake", fake_run)

    client = TestClient(api.create_app())
    response = client.post(
        "/assets/bake/ref",
        json={"input": {"bucket": "raw-assets", "key": "incoming/mesh.glb"},
              "params": {"target_tris": 20000}},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    glb_sha = hashlib.sha256(b"lp-glb-bytes").hexdigest()
    assert body["artifact"]["storage"] == "minio"
    assert body["artifact"]["bucket"] == "asset-baker"
    assert body["artifact"]["uri"] == f"s3://asset-baker/bake/{glb_sha}.glb"
    assert body["download_url"] is None
    assert ("glb" in {s[1] for s in stored}) and ("png" in {s[1] for s in stored})


def test_foliage_skip_mode_emits_no_textures(monkeypatch, tmp_path):
    from asset_baker import api

    def fake_run(input_path, out_dir, params):
        assert params.mode == "skip"
        return _fake_artifacts(out_dir, with_textures=False, color_mean=None, mode="skip")

    monkeypatch.setattr(api, "run_bake", fake_run)
    monkeypatch.setenv("ASSET_BAKER_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv("ASSET_BAKER_MINIO_ENABLED", "false")

    client = TestClient(api.create_app())
    response = client.post(
        "/assets/bake",
        files={"file": ("fern.glb", b"raw-glb", "model/gltf-binary")},
        data={"mode": "skip"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["textures"] == []
    assert body["summary"]["mode"] == "skip"
    assert body["summary"]["color_mean"] is None


def test_black_bake_returns_422(monkeypatch, tmp_path):
    from asset_baker import api
    from asset_baker.runner import BakeError

    def fake_run(input_path, out_dir, params):
        raise BakeError("baked color is black (mean 0.02)", kind="black_bake")

    monkeypatch.setattr(api, "run_bake", fake_run)
    monkeypatch.setenv("ASSET_BAKER_MINIO_ENABLED", "false")
    monkeypatch.setenv("ASSET_BAKER_ARTIFACT_DIR", str(tmp_path))

    client = TestClient(api.create_app())
    response = client.post(
        "/assets/bake",
        files={"file": ("metal.glb", b"raw-glb", "model/gltf-binary")},
    )
    assert response.status_code == 422
    assert "black" in response.json()["detail"]


def test_timeout_returns_504(monkeypatch, tmp_path):
    from asset_baker import api
    from asset_baker.runner import BakeError

    def fake_run(input_path, out_dir, params):
        raise BakeError("bake timed out after 600s", kind="timeout")

    monkeypatch.setattr(api, "run_bake", fake_run)
    monkeypatch.setenv("ASSET_BAKER_MINIO_ENABLED", "false")
    monkeypatch.setenv("ASSET_BAKER_ARTIFACT_DIR", str(tmp_path))

    client = TestClient(api.create_app())
    response = client.post("/assets/bake", files={"file": ("big.glb", b"raw", "model/gltf-binary")})
    assert response.status_code == 504


def test_bake_requires_glb_input(tmp_path):
    from asset_baker import api

    client = TestClient(api.create_app())
    response = client.post("/assets/bake", files={"file": ("scene.txt", b"nope", "text/plain")})
    assert response.status_code == 400
    assert "GLB" in response.json()["detail"]


def test_bake_rejects_empty_input(tmp_path, monkeypatch):
    from asset_baker import api

    monkeypatch.setattr(api, "run_bake", lambda *a, **k: None)
    client = TestClient(api.create_app())
    response = client.post("/assets/bake", files={"file": ("empty.glb", b"", "model/gltf-binary")})
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_bake_rejects_oversize_input(monkeypatch, tmp_path):
    from asset_baker import api

    monkeypatch.setattr(api, "run_bake", lambda *a, **k: None)
    monkeypatch.setenv("ASSET_BAKER_MAX_UPLOAD_MB", "0.00001")  # ~10 bytes
    client = TestClient(api.create_app())
    response = client.post(
        "/assets/bake",
        files={"file": ("huge.glb", b"x" * 1024, "model/gltf-binary")},
    )
    assert response.status_code == 413


def test_content_length_guard_rejects_oversize_before_buffering(monkeypatch):
    from types import SimpleNamespace

    import pytest
    from fastapi import HTTPException

    from asset_baker import api

    # Default cap 200 MiB; a 300 MiB Content-Length is rejected up front.
    big = SimpleNamespace(headers={"content-length": str(300 * 1024 * 1024)})
    with pytest.raises(HTTPException) as excinfo:
        api._enforce_content_length(big)
    assert excinfo.value.status_code == 413

    # A modest body passes the header pre-check (post-read guard is authoritative).
    small = SimpleNamespace(headers={"content-length": "2048"})
    api._enforce_content_length(small)  # must not raise


def test_worker_busy_returns_429(monkeypatch, tmp_path):
    from asset_baker import api

    class BusySemaphore:
        def acquire(self, blocking=True):
            return False

        def release(self):
            pass

    monkeypatch.setattr(api, "run_bake", lambda *a, **k: None)
    monkeypatch.setenv("ASSET_BAKER_ARTIFACT_DIR", str(tmp_path))

    app = api.create_app()
    app.state.bake_semaphore = BusySemaphore()  # simulate a saturated worker
    client = TestClient(app)
    response = client.post("/assets/bake", files={"file": ("m.glb", b"raw", "model/gltf-binary")})
    assert response.status_code == 429
