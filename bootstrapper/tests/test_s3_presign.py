"""Tests for the pure-stdlib SigV4 presigned-URL generator (issue #404).

The core guarantee under test: the URL is signed against the exact host it
will be served from, and is NEVER rewritten after signing — so signing
against one host and serving from another (the classic ``minio:9000`` →
``localhost`` rewrite bug) is structurally impossible.
"""

from datetime import datetime, timezone

import pytest

from utils.s3_presign import _uri_encode, presign_get_url

# Fixed clock for every deterministic assertion in this file.
FIXED_NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

# Common credential fixture.
CREDS = dict(
    region="us-east-1",
    access_key="AKIAEXAMPLE",
    secret_key="secretkey123",
)


def test_known_answer_url_is_stable():
    """Lock the full URL against regressions with a computed literal.

    The expected string below was produced once by running this exact
    implementation; any change to the canonicalization or signing algorithm
    changes the signature and fails this test.
    """
    url = presign_get_url(
        endpoint="http://localhost:63018",
        bucket="artifacts",
        key="img/out.png",
        expires=3600,
        path_style=True,
        now=FIXED_NOW,
        **CREDS,
    )
    expected = (
        "http://localhost:63018/artifacts/img/out.png"
        "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
        "&X-Amz-Credential=AKIAEXAMPLE%2F20260102%2Fus-east-1%2Fs3%2Faws4_request"
        "&X-Amz-Date=20260102T030405Z"
        "&X-Amz-Expires=3600"
        "&X-Amz-SignedHeaders=host"
        "&X-Amz-Signature="
        "8aad0fd02d1e035ea78a6039c921b1ca3f1d7712d00a62a969360315720012cd"
    )
    assert url == expected

    # Sanity checks called out by the spec.
    assert "X-Amz-Date=20260102T030405Z" in url
    assert "X-Amz-Credential=AKIAEXAMPLE%2F20260102%2Fus-east-1%2Fs3%2Faws4_request" in url


def test_signs_against_given_host_no_rewrite():
    """The host is part of the signature — changing BASE_PORT changes both.

    Proves we sign against the public endpoint we were handed rather than
    signing against an internal host and rewriting afterwards.
    """
    url_a = presign_get_url(
        endpoint="http://localhost:63018",
        bucket="artifacts",
        key="img/out.png",
        now=FIXED_NOW,
        **CREDS,
    )
    url_b = presign_get_url(
        endpoint="http://localhost:64018",
        bucket="artifacts",
        key="img/out.png",
        now=FIXED_NOW,
        **CREDS,
    )

    assert "localhost:63018" in url_a
    assert "localhost:64018" in url_b

    sig_a = url_a.split("X-Amz-Signature=")[1]
    sig_b = url_b.split("X-Amz-Signature=")[1]
    assert sig_a != sig_b, "different host must yield a different signature"


def test_path_style_layout():
    url = presign_get_url(
        endpoint="http://localhost:63018",
        bucket="artifacts",
        key="img/out.png",
        path_style=True,
        now=FIXED_NOW,
        **CREDS,
    )
    path = url.split("?", 1)[0]
    assert path == "http://localhost:63018/artifacts/img/out.png"
    assert path.startswith("http://localhost:63018/artifacts/img/out.png")


def test_virtual_host_layout():
    url = presign_get_url(
        endpoint="http://localhost:63018",
        bucket="artifacts",
        key="img/out.png",
        path_style=False,
        now=FIXED_NOW,
        **CREDS,
    )
    # Virtual-host: bucket becomes a subdomain of the endpoint host.
    assert url.startswith("http://artifacts.localhost:63018/img/out.png?")
    path = url.split("?", 1)[0]
    assert path == "http://artifacts.localhost:63018/img/out.png"


@pytest.mark.parametrize("bad_expires", [0, -1, 604801, -3600, 999999])
def test_ttl_out_of_bounds_raises(bad_expires):
    with pytest.raises(ValueError):
        presign_get_url(
            endpoint="http://localhost:63018",
            bucket="artifacts",
            key="img/out.png",
            expires=bad_expires,
            now=FIXED_NOW,
            **CREDS,
        )


@pytest.mark.parametrize("ok_expires", [1, 604800, 3600])
def test_ttl_in_bounds_ok(ok_expires):
    url = presign_get_url(
        endpoint="http://localhost:63018",
        bucket="artifacts",
        key="img/out.png",
        expires=ok_expires,
        now=FIXED_NOW,
        **CREDS,
    )
    assert f"X-Amz-Expires={ok_expires}" in url


def test_response_overrides_are_signed_and_encoded():
    ct = "image/png"
    cd = 'attachment; filename="o.png"'

    with_overrides = presign_get_url(
        endpoint="http://localhost:63018",
        bucket="artifacts",
        key="img/out.png",
        response_content_type=ct,
        response_content_disposition=cd,
        now=FIXED_NOW,
        **CREDS,
    )

    # Correctly percent-encoded query params (space->%20, / and = and ; and ").
    assert "response-content-type=image%2Fpng" in with_overrides
    assert (
        "response-content-disposition=attachment%3B%20filename%3D%22o.png%22"
        in with_overrides
    )
    # No raw spaces and no "+" for spaces anywhere in the query.
    assert " " not in with_overrides
    assert "+" not in with_overrides

    without_overrides = presign_get_url(
        endpoint="http://localhost:63018",
        bucket="artifacts",
        key="img/out.png",
        now=FIXED_NOW,
        **CREDS,
    )

    sig_with = with_overrides.split("X-Amz-Signature=")[1]
    sig_without = without_overrides.split("X-Amz-Signature=")[1]
    assert sig_with != sig_without, "response overrides must be part of the signature"


def test_key_encoding_preserves_slashes_and_encodes_space_unicode():
    url = presign_get_url(
        endpoint="http://localhost:63018",
        bucket="bucket",
        key="a b/ünïcode.png",
        now=FIXED_NOW,
        **CREDS,
    )
    path = url.split("?", 1)[0]
    # "/" preserved between segments; space -> %20 (never +); unicode encoded.
    assert path == "http://localhost:63018/bucket/a%20b/%C3%BCn%C3%AFcode.png"
    assert "%20" in path
    assert "+" not in path
    assert "%C3%BC" in path  # ü as UTF-8 percent-encoding
    # The slash between "a b" and "ünïcode.png" segments stays literal.
    assert "a%20b/%C3%BC" in path


def test_uri_encode_helper():
    # encode_slash=False keeps "/" literal; space -> %20.
    assert _uri_encode("a b/c", encode_slash=False) == "a%20b/c"
    # encode_slash=True encodes "/" too.
    assert _uri_encode("a/b", encode_slash=True) == "a%2Fb"
    # Unreserved set is untouched, including "~".
    assert _uri_encode("Az0-_.~", encode_slash=True) == "Az0-_.~"


def test_empty_bucket_raises():
    with pytest.raises(ValueError):
        presign_get_url(
            endpoint="http://localhost:63018",
            bucket="",
            key="img/out.png",
            now=FIXED_NOW,
            **CREDS,
        )


def test_empty_key_raises():
    with pytest.raises(ValueError):
        presign_get_url(
            endpoint="http://localhost:63018",
            bucket="artifacts",
            key="",
            now=FIXED_NOW,
            **CREDS,
        )


def test_endpoint_without_scheme_raises():
    with pytest.raises(ValueError):
        presign_get_url(
            endpoint="localhost:63018",  # no http:// scheme
            bucket="artifacts",
            key="img/out.png",
            now=FIXED_NOW,
            **CREDS,
        )


@pytest.mark.parametrize(
    "field",
    ["endpoint", "access_key", "secret_key"],
)
def test_other_empty_inputs_raise(field):
    kwargs = dict(
        endpoint="http://localhost:63018",
        region="us-east-1",
        access_key="AKIAEXAMPLE",
        secret_key="secretkey123",
        bucket="artifacts",
        key="img/out.png",
        now=FIXED_NOW,
    )
    kwargs[field] = ""
    with pytest.raises(ValueError):
        presign_get_url(**kwargs)


def test_signature_is_deterministic():
    kwargs = dict(
        endpoint="http://localhost:63018",
        bucket="artifacts",
        key="img/out.png",
        expires=1200,
        response_content_type="image/png",
        now=FIXED_NOW,
        **CREDS,
    )
    assert presign_get_url(**kwargs) == presign_get_url(**kwargs)
