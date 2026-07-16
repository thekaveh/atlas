from __future__ import annotations

import os
from pathlib import Path


CONTENT_TYPE = "model/gltf-binary"


class ArtifactTooLargeError(ValueError):
    pass


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


class ArtifactStorage:
    def __init__(self) -> None:
        self.artifact_dir = Path(os.getenv("ASSET_WORKER_ARTIFACT_DIR", "/data/artifacts"))
        self.minio_enabled = _truthy(os.getenv("ASSET_WORKER_MINIO_ENABLED", "true"))
        self.output_bucket = os.getenv("ASSET_WORKER_MINIO_BUCKET", "asset-worker")

    def fetch(self, bucket: str, key: str) -> bytes:
        client = self._client()
        response = client.get_object(Bucket=bucket, Key=key)
        body = response["Body"]
        max_mb = float(os.getenv("ASSET_WORKER_MAX_UPLOAD_MB", "200"))
        max_bytes = max(1, int(max_mb * 1024 * 1024))
        try:
            content_length = response.get("ContentLength")
            if content_length is not None and int(content_length) > max_bytes:
                raise ArtifactTooLargeError(
                    f"GLB exceeds {max_bytes} byte limit"
                )
            data = body.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ArtifactTooLargeError(
                    f"GLB exceeds {max_bytes} byte limit"
                )
            return data
        finally:
            close = getattr(body, "close", None)
            if close:
                close()

    def store(self, data: bytes, *, sha256: str) -> dict[str, str]:
        key = f"gltf/{sha256}.glb"
        if self.minio_enabled:
            client = self._client()
            self._ensure_bucket(client, self.output_bucket)
            client.put_object(
                Bucket=self.output_bucket,
                Key=key,
                Body=data,
                ContentType=CONTENT_TYPE,
                Metadata={"sha256": sha256},
            )
            return {
                "storage": "minio",
                "bucket": self.output_bucket,
                "key": key,
                "uri": f"s3://{self.output_bucket}/{key}",
                "content_type": CONTENT_TYPE,
            }

        target = self.artifact_dir / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {"storage": "local", "key": key, "content_type": CONTENT_TYPE}

    def local_path(self, sha256: str) -> Path:
        return self.artifact_dir / "gltf" / f"{sha256}.glb"

    def _client(self):
        import boto3
        from botocore.client import Config

        return boto3.client(
            "s3",
            endpoint_url=os.getenv("ASSET_WORKER_MINIO_ENDPOINT") or os.getenv("MINIO_ENDPOINT", "http://minio:9000"),
            aws_access_key_id=os.getenv("ASSET_WORKER_MINIO_ACCESS_KEY") or os.getenv("MINIO_ROOT_USER", "minioadmin"),
            aws_secret_access_key=os.getenv("ASSET_WORKER_MINIO_SECRET_KEY") or os.getenv("MINIO_ROOT_PASSWORD", ""),
            region_name=os.getenv("MINIO_REGION", "us-east-1"),
            config=Config(s3={"addressing_style": "path"}),
        )

    @staticmethod
    def _ensure_bucket(client, bucket: str) -> None:
        try:
            client.head_bucket(Bucket=bucket)
        except Exception:
            client.create_bucket(Bucket=bucket)
