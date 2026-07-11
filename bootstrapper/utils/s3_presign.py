"""Pure-stdlib AWS Signature Version 4 presigned GET URL generator.

Issue #404 — the signature/host invariant
=========================================

AWS SigV4 folds the request ``host`` header into the signed canonical
request. That means a presigned URL is only valid when it is *served from
the exact host it was signed against*. The historical bug class this module
eliminates is:

    1. Sign the request against an internal address (e.g. ``minio:9000``,
       reachable only inside the Docker network).
    2. Rewrite the URL's host afterwards to the browser-visible public
       endpoint (e.g. ``http://localhost:63018``) so a browser can reach it.
    3. The browser presents a signature that was computed for ``minio:9000``
       against a URL whose host is now ``localhost:63018`` → S3/MinIO
       recomputes the signature over the *new* host, it does not match, and
       the download fails with ``SignatureDoesNotMatch``.

This module refuses to participate in that pattern. It signs against the
``endpoint`` you pass and returns that URL verbatim — it NEVER rewrites the
URL after signing. Callers MUST therefore pass the **browser-visible public
endpoint** (the same host+port the browser will actually hit), not an
internal service address.

The implementation depends only on the standard library
(:mod:`hashlib`, :mod:`hmac`, :mod:`datetime`, :mod:`urllib.parse`) — no
boto3, no botocore, no external dependencies.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from urllib.parse import urlsplit

__all__ = ["presign_get_url"]

_ALGORITHM = "AWS4-HMAC-SHA256"
_SERVICE = "s3"
_UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"

# RFC 3986 "unreserved" characters — never percent-encoded.
_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789"
    "-_.~"
)

# TTL bounds (seconds) for presigned URLs — AWS caps X-Amz-Expires at 7 days.
_MIN_EXPIRES = 1
_MAX_EXPIRES = 604800  # 7 * 24 * 60 * 60


def _uri_encode(s: str, encode_slash: bool) -> str:
    """Percent-encode ``s`` per the AWS SigV4 UriEncode rules.

    Encodes everything outside the RFC 3986 unreserved set
    (``A-Za-z0-9-_.~``). A space becomes ``%20`` (never ``+``). When
    ``encode_slash`` is False the ``/`` byte is passed through untouched so
    object-key path segments stay separated; when True ``/`` is encoded as
    ``%2F`` (used for query-parameter values).
    """
    out = []
    for ch in s:
        if ch in _UNRESERVED:
            out.append(ch)
        elif ch == "/" and not encode_slash:
            out.append("/")
        else:
            # Percent-encode the UTF-8 bytes, uppercase hex per AWS spec.
            for byte in ch.encode("utf-8"):
                out.append(f"%{byte:02X}")
    return "".join(out)


def _encode_key_path(key: str) -> str:
    """Encode an object key: each segment encoded, ``/`` preserved."""
    return _uri_encode(key, encode_slash=False)


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret_key: str, datestamp: str, region: str) -> bytes:
    k_date = _hmac_sha256(("AWS4" + secret_key).encode("utf-8"), datestamp)
    k_region = _hmac_sha256(k_date, region)
    k_service = _hmac_sha256(k_region, _SERVICE)
    return _hmac_sha256(k_service, "aws4_request")


def presign_get_url(
    *,
    endpoint: str,
    region: str,
    access_key: str,
    secret_key: str,
    bucket: str,
    key: str,
    expires: int = 3600,
    response_content_type: str | None = None,
    response_content_disposition: str | None = None,
    path_style: bool = True,
    now: "datetime | None" = None,
) -> str:
    """Return a fully-signed AWS SigV4 presigned GET URL.

    The URL is signed against ``endpoint`` and returned verbatim — the host
    is part of the signature and is NEVER rewritten afterwards (issue #404).
    Pass the browser-visible public endpoint.

    Parameters
    ----------
    endpoint:
        Scheme + host[:port] we sign against, e.g. ``http://localhost:63018``
        or ``https://s3.example.com``. Any trailing path/slash is ignored.
    region:
        AWS region string, e.g. ``us-east-1``.
    access_key, secret_key:
        Credentials used to derive the signing key.
    bucket, key:
        Object location. ``key`` may contain ``/`` and unicode; each segment
        is percent-encoded while ``/`` separators are preserved.
    expires:
        TTL in seconds, valid range 1..604800 (7 days) inclusive.
    response_content_type, response_content_disposition:
        Optional response-header overrides added as signed
        ``response-content-type`` / ``response-content-disposition`` query
        params.
    path_style:
        True → ``{endpoint}/{bucket}/{key}`` (MinIO default). False →
        virtual-host style ``{scheme}://{bucket}.{host}/{key}``.
    now:
        Injectable UTC datetime for deterministic tests. Defaults to
        ``datetime.now(timezone.utc)``.

    Raises
    ------
    ValueError:
        If ``expires`` is out of range, any required string is empty, or
        ``endpoint`` has no URL scheme.
    """
    if not endpoint:
        raise ValueError("endpoint must be a non-empty URL")
    if not access_key:
        raise ValueError("access_key must be non-empty")
    if not secret_key:
        raise ValueError("secret_key must be non-empty")
    if not bucket:
        raise ValueError("bucket must be non-empty")
    if not key:
        raise ValueError("key must be non-empty")
    if not isinstance(expires, int) or isinstance(expires, bool):
        raise ValueError("expires must be an integer number of seconds")
    if expires < _MIN_EXPIRES or expires > _MAX_EXPIRES:
        raise ValueError(
            f"expires must be within {_MIN_EXPIRES}..{_MAX_EXPIRES} seconds, got {expires}"
        )

    split = urlsplit(endpoint)
    if not split.scheme:
        raise ValueError(f"endpoint must include a scheme (http/https): {endpoint!r}")
    if not split.netloc:
        raise ValueError(f"endpoint must include a host: {endpoint!r}")

    scheme = split.scheme
    host = split.netloc  # host[:port]

    if now is None:
        now = datetime.now(timezone.utc)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")

    credential_scope = f"{datestamp}/{region}/{_SERVICE}/aws4_request"

    # ---- Canonical URI + endpoint base (host part of the signature) --------
    if path_style:
        signed_host = host
        endpoint_base = f"{scheme}://{host}"
        canonical_uri = "/" + bucket + "/" + _encode_key_path(key)
    else:
        signed_host = f"{bucket}.{host}"
        endpoint_base = f"{scheme}://{signed_host}"
        canonical_uri = "/" + _encode_key_path(key)

    # ---- Canonical query string (BEFORE the signature is appended) ---------
    query_params: dict[str, str] = {
        "X-Amz-Algorithm": _ALGORITHM,
        "X-Amz-Credential": f"{access_key}/{credential_scope}",
        "X-Amz-Date": amzdate,
        "X-Amz-Expires": str(expires),
        "X-Amz-SignedHeaders": "host",
    }
    if response_content_type is not None:
        query_params["response-content-type"] = response_content_type
    if response_content_disposition is not None:
        query_params["response-content-disposition"] = response_content_disposition

    # Encode key and value (values encode everything incl. "/" and "="), then
    # sort the encoded pairs by encoded key for the canonical query string.
    encoded_pairs = [
        (_uri_encode(k, encode_slash=True), _uri_encode(v, encode_slash=True))
        for k, v in query_params.items()
    ]
    encoded_pairs.sort(key=lambda kv: kv[0])
    canonical_querystring = "&".join(f"{k}={v}" for k, v in encoded_pairs)

    # ---- Canonical request -> string to sign -> signature ------------------
    canonical_headers = f"host:{signed_host}\n"
    signed_headers = "host"
    canonical_request = (
        "GET\n"
        f"{canonical_uri}\n"
        f"{canonical_querystring}\n"
        f"{canonical_headers}\n"
        f"{signed_headers}\n"
        f"{_UNSIGNED_PAYLOAD}"
    )

    hashed_canonical_request = hashlib.sha256(
        canonical_request.encode("utf-8")
    ).hexdigest()
    string_to_sign = (
        f"{_ALGORITHM}\n{amzdate}\n{credential_scope}\n{hashed_canonical_request}"
    )

    signing_key = _signing_key(secret_key, datestamp, region)
    signature = hmac.new(
        signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    # X-Amz-Signature is appended last and is NOT part of the signed query.
    return (
        f"{endpoint_base}{canonical_uri}?{canonical_querystring}"
        f"&X-Amz-Signature={signature}"
    )
