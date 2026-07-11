"""Optional live smoke test for the consumer storage presign contract (#404).

Skipped in generic CI. Opt in by exporting ``ATLAS_STORAGE_E2E=1`` against a
running Atlas MinIO, providing a scoped service-account credential (NOT root):

    ATLAS_STORAGE_E2E=1 \\
    ATLAS_E2E_INTERNAL_ENDPOINT=http://localhost:63020 \\
    ATLAS_E2E_PUBLIC_ENDPOINT=http://localhost:63020 \\
    ATLAS_E2E_BUCKET=daydreams-artifacts \\
    ATLAS_E2E_ACCESS_KEY=... ATLAS_E2E_SECRET_KEY=... ATLAS_E2E_REGION=us-east-1 \\
    uv run --project bootstrapper pytest bootstrapper/tests/test_storage_presign_e2e.py -q

It uploads an object with the scoped credential against the internal endpoint,
then fetches it through a presigned URL signed against the PUBLIC endpoint —
proving the signature stays valid across the browser-visible host with no
post-sign rewrite and without ever using root credentials.
"""
from __future__ import annotations

import os
import urllib.request

import pytest

from utils.s3_presign import presign_get_url

pytestmark = pytest.mark.skipif(
    os.environ.get("ATLAS_STORAGE_E2E") != "1",
    reason="live MinIO smoke test — opt in with ATLAS_STORAGE_E2E=1",
)


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} not set for the live storage smoke test")
    return value


def test_presigned_public_get_round_trips_without_root_creds() -> None:
    boto3 = pytest.importorskip("boto3")

    internal = _env("ATLAS_E2E_INTERNAL_ENDPOINT")
    public = _env("ATLAS_E2E_PUBLIC_ENDPOINT")
    bucket = _env("ATLAS_E2E_BUCKET")
    access_key = _env("ATLAS_E2E_ACCESS_KEY")
    secret_key = _env("ATLAS_E2E_SECRET_KEY")
    region = os.environ.get("ATLAS_E2E_REGION", "us-east-1")

    key = "atlas-e2e/presign-probe.txt"
    body = b"atlas-404-presign-e2e"

    # Upload with the SCOPED credential against the INTERNAL endpoint.
    s3 = boto3.client(
        "s3",
        endpoint_url=internal,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="text/plain")

    # Presign a GET against the PUBLIC endpoint (the browser-visible host).
    url = presign_get_url(
        endpoint=public,
        region=region,
        access_key=access_key,
        secret_key=secret_key,
        bucket=bucket,
        key=key,
        expires=120,
        response_content_type="text/plain",
    )

    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
        assert resp.status == 200
        assert resp.read() == body
