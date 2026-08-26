from __future__ import annotations

import asyncio
import hashlib
import threading

import httpx2 as httpx

import pytest
from fastapi.testclient import TestClient


_TOKEN = "test-asset-worker-token"


def _client(api) -> TestClient:
    return TestClient(
        api.create_app(api_token=_TOKEN),
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )


def test_health_requires_gltf_transform_binary(monkeypatch) -> None:
    from asset_worker import api

    monkeypatch.setattr(api.shutil, "which", lambda _binary: None)
    response = TestClient(api.create_app()).get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "gltf_transform": False}

    monkeypatch.setattr(api.shutil, "which", lambda _binary: "/usr/bin/gltf-transform")
    response = TestClient(api.create_app()).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "gltf_transform": True}


def test_metrics_are_available_without_asset_token() -> None:
    from asset_worker import api

    response = TestClient(api.create_app(api_token=_TOKEN)).get("/metrics")

    assert response.status_code == 200
    assert "http_requests_total" in response.text


class _BucketProbeError(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        }


@pytest.mark.parametrize("code", ["404", "NoSuchBucket", "NotFound"])
def test_bucket_probe_creates_only_when_bucket_is_missing(code) -> None:
    from asset_worker.storage import ArtifactStorage

    class Client:
        created = []

        def head_bucket(self, **_kwargs):
            raise _BucketProbeError(code, 404)

        def create_bucket(self, **kwargs):
            self.created.append(kwargs)

    client = Client()
    ArtifactStorage._ensure_bucket(client, "artifacts")
    assert client.created == [{"Bucket": "artifacts"}]


@pytest.mark.parametrize(
    "error",
    [_BucketProbeError("AccessDenied", 403), TimeoutError("probe timed out")],
)
def test_bucket_probe_propagates_non_missing_failures(error) -> None:
    from asset_worker.storage import ArtifactStorage

    class Client:
        created = []

        def head_bucket(self, **_kwargs):
            raise error

        def create_bucket(self, **kwargs):
            self.created.append(kwargs)

    client = Client()
    with pytest.raises(type(error), match=str(error)):
        ArtifactStorage._ensure_bucket(client, "artifacts")
    assert client.created == []


@pytest.mark.parametrize(
    ("field", "value"),
    [("Error", "malformed"), ("ResponseMetadata", ["malformed"])],
)
def test_bucket_probe_preserves_error_with_malformed_nested_response(
    field, value
) -> None:
    from asset_worker.storage import ArtifactStorage

    error = RuntimeError("probe failed")
    error.response = {field: value}

    class Client:
        created = []

        def head_bucket(self, **_kwargs):
            raise error

        def create_bucket(self, **kwargs):
            self.created.append(kwargs)

    client = Client()
    with pytest.raises(RuntimeError, match="probe failed"):
        ArtifactStorage._ensure_bucket(client, "artifacts")
    assert client.created == []


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

    client = _client(api)
    response = client.post(
        "/gltf/postprocess",
        files={"file": ("scene.glb", b"raw-glb", "model/gltf-binary")},
        data={
            "target_height_m": "1.8",
            "normalize_axis": "height",
            "up_axis": "auto",
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
        "method": "min-aabb-volume",
        "up_axis": "auto",
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
    assert calls[0][2].up_axis == "auto"
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

    client = _client(api)
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

    client = _client(api)
    response = client.post(
        "/gltf/postprocess",
        files={"file": ("scene.txt", b"not-glb", "text/plain")},
    )

    assert response.status_code == 400
    assert "GLB" in response.json()["detail"]


def test_postprocess_rejects_oversize_upload_before_transform(
    monkeypatch, tmp_path
) -> None:
    from asset_worker import api

    transformed = False

    def fake_run(*args):
        nonlocal transformed
        transformed = True

    monkeypatch.setattr(api, "run_gltf_transform", fake_run)
    monkeypatch.setenv("ASSET_WORKER_MAX_UPLOAD_MB", "0.00001")
    monkeypatch.setenv("ASSET_WORKER_ARTIFACT_DIR", str(tmp_path))

    response = _client(api).post(
        "/gltf/postprocess",
        files={"file": ("large.glb", b"x" * 1024, "model/gltf-binary")},
    )

    assert response.status_code == 413
    assert transformed is False


def test_mutating_routes_require_bearer_token() -> None:
    from asset_worker import api

    client = TestClient(api.create_app(api_token=_TOKEN))
    response = client.post(
        "/gltf/postprocess/ref",
        json={"input": {"bucket": "raw-assets", "key": "mesh.glb"}, "params": {}},
    )
    assert response.status_code == 401


def test_authentication_precedes_body_parsing_and_docs_are_disabled() -> None:
    from asset_worker import api

    client = TestClient(api.create_app(api_token=_TOKEN))
    malformed = client.post(
        "/gltf/postprocess",
        content=b"not-a-multipart-body",
        headers={"Content-Type": "multipart/form-data; boundary=missing"},
    )
    assert malformed.status_code == 401
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path, headers={"Authorization": f"Bearer {_TOKEN}"}).status_code == 404


def test_reference_route_rejects_bucket_outside_allowlist(monkeypatch) -> None:
    from asset_worker import api

    monkeypatch.setenv("ASSET_WORKER_ALLOWED_INPUT_BUCKETS", "raw-assets")
    response = _client(api).post(
        "/gltf/postprocess/ref",
        json={"input": {"bucket": "private", "key": "mesh.glb"}, "params": {}},
    )
    assert response.status_code == 403


def test_minio_reference_rejects_oversize_object_before_read(monkeypatch) -> None:
    from asset_worker.storage import ArtifactStorage, ArtifactTooLargeError

    class Body:
        closed = False

        def read(self, size):
            raise AssertionError("oversize body must not be read")

        def close(self):
            self.closed = True

    body = Body()

    class Client:
        def get_object(self, **kwargs):
            return {"Body": body, "ContentLength": 1024}

    storage = ArtifactStorage()
    monkeypatch.setenv("ASSET_WORKER_MAX_UPLOAD_MB", "0.00001")
    monkeypatch.setattr(storage, "_client", lambda: Client())

    with pytest.raises(ArtifactTooLargeError):
        storage.fetch("raw-assets", "large.glb")
    assert body.closed is True


@pytest.mark.parametrize(
    ("path", "request_kwargs"),
    [
        (
            "/gltf/postprocess",
            {"files": {"file": ("scene.glb", b"raw-glb", "model/gltf-binary")}},
        ),
        (
            "/gltf/postprocess/ref",
            {
                "json": {
                    "input": {"bucket": "raw-assets", "key": "mesh.glb"},
                    "params": {},
                }
            },
        ),
    ],
)
def test_saturated_worker_returns_429(monkeypatch, tmp_path, path, request_kwargs) -> None:
    from asset_worker import api

    class BusySemaphore:
        def acquire(self, *, blocking):
            assert blocking is False
            return False

        def release(self):
            raise AssertionError("an unacquired semaphore must not be released")

    class FakeStorage:
        def fetch(self, bucket, key):
            raise AssertionError("busy requests must not fetch input objects")

    def fail_copy(*_args):
        raise AssertionError("busy requests must not spool uploads")

    monkeypatch.setattr(api, "ArtifactStorage", FakeStorage)
    monkeypatch.setattr(api, "_copy_upload_to_path", fail_copy)
    monkeypatch.setenv("ASSET_WORKER_ARTIFACT_DIR", str(tmp_path))
    app = api.create_app(api_token=_TOKEN)
    app.state.transform_semaphore = BusySemaphore()

    response = TestClient(
        app, headers={"Authorization": f"Bearer {_TOKEN}"}
    ).post(path, **request_kwargs)

    assert response.status_code == 429


def test_cancelled_request_holds_slot_until_transform_thread_exits(
    monkeypatch, tmp_path
) -> None:
    from asset_worker import api

    started = threading.Event()
    release = threading.Event()
    calls = 0

    def blocking_run(_input_path, output_path, _params):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            assert release.wait(timeout=5)
        output_path.write_bytes(b"optimized")

    monkeypatch.setattr(api, "run_gltf_transform", blocking_run)
    monkeypatch.setenv("ASSET_WORKER_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv("ASSET_WORKER_MINIO_ENABLED", "false")
    app = api.create_app(api_token=_TOKEN)

    async def scenario() -> int:
        transport = httpx.ASGITransport(app=app)
        headers = {"Authorization": f"Bearer {_TOKEN}"}
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://asset-worker.test",
            headers=headers,
        ) as client:
            first = asyncio.create_task(
                client.post(
                    "/gltf/postprocess",
                    files={"file": ("first.glb", b"raw", "model/gltf-binary")},
                )
            )
            assert await asyncio.to_thread(started.wait, 2)
            first.cancel()
            await asyncio.sleep(0.05)

            second = await client.post(
                "/gltf/postprocess",
                files={"file": ("second.glb", b"raw", "model/gltf-binary")},
            )
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await first
            return second.status_code

    assert asyncio.run(scenario()) == 429
    assert calls == 1


def test_postprocess_invalid_form_param_returns_422() -> None:
    from asset_worker import api

    # up_axis outside the Literal keep|auto|x|y|z is a client error → 422,
    # not a 500 (the params model is built inside the handler).
    response = _client(api).post(
        "/gltf/postprocess",
        files={"file": ("scene.glb", b"raw-glb", "model/gltf-binary")},
        data={"up_axis": "sideways"},
    )
    assert response.status_code == 422


def test_non_ascii_bearer_token_is_401_not_500() -> None:
    from asset_worker import api

    # A non-ASCII bearer (raw bytes >= 0x80, latin-1-decoded by Starlette) must
    # yield a clean 401, not a secrets.compare_digest TypeError -> 500.
    client = TestClient(api.create_app(api_token=_TOKEN))
    response = client.post(
        "/gltf/postprocess/ref",
        headers={"Authorization": b"Bearer caf\xe9-token"},
        json={"input": {"bucket": "raw-assets", "key": "mesh.glb"}, "params": {}},
    )
    assert response.status_code == 401
