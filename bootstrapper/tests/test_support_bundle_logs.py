"""Boundary redaction and exact member-budget contracts for #1759."""
from base64 import b64encode
from dataclasses import replace
import io
import tarfile

import pytest

from core import support_bundle as sb
from core.support_bundle_logs import CONTEXT_BYTES, RAW_SNAPSHOT_BYTES, LogSnapshot
from ui.session_log import SessionLogConfig, SessionLogTee


def bundle_for(snapshot, *, env=None, limit=256 * 1024):
    return sb.build_bundle(sb.BundleRequest(env=env or {},
        logs=[sb.LogSource("session.log", snapshot=snapshot)],
        limits=sb.BundleLimits(max_log_bytes=limit)))


def rendered(bundle):
    preview = "\n".join(sb.preview_lines(bundle))
    with tarfile.open(fileobj=io.BytesIO(sb.archive_bytes(bundle))) as archive:
        members = {item.name: archive.extractfile(item).read() for item in archive.getmembers()}
    return preview, members


@pytest.mark.parametrize("spare", [1, 13, 31])
@pytest.mark.parametrize("secret", [
    "KnownSecretBoundary79", "Bearer Z9SecretBoundaryABC123456",
    "Authorization: Bearer Z9SecretBoundaryABC123456",
    "https://username:Z9SecretBoundaryABC123456@example.invalid/x",
    'api_key="Z9SecretBoundaryABC123456"',
    '"password": "Z9SecretBoundaryABC123456"',
    "-----BEGIN RSA PRIVATE KEY-----\nZ9SecretBoundaryABC123456\n-----END RSA PRIVATE KEY-----",
], ids=["known", "bearer", "header", "url", "assignment", "quoted", "pem"])
def test_segment_seams_reconstruct_before_redaction(tmp_path, secret, spare):
    tee = SessionLogTee(prefix="atlas-launch-secret-", config=SessionLogConfig(
        directory=str(tmp_path), segment_max_bytes=1024, max_segments=8))
    try:
        tee.write("diagnostic\n" + " " * (1024 - tee._bytes - 11 - spare))
        tee.write(secret + "\nTERMINAL_CANARY café🙂\n")
        bundle = bundle_for(tee.snapshot(), env={"PRIVATE_TOKEN": "KnownSecretBoundary79"})
        preview, members = rendered(bundle)
        combined = preview + b"".join(members.values()).decode()
        assert "KnownSecretBoundary79" not in combined
        assert "Z9SecretBoundary" not in combined and "ABC123456" not in combined
        assert "TERMINAL_CANARY café🙂" in combined
        assert "[REDACTED]" in combined
    finally:
        tee.close()


def test_cut_source_cannot_reintroduce_unsafe_context_after_redaction():
    secret = "begin\nMULTILINE_BOUNDARY_SECRET79\nend"
    raw = (secret[4:] + "\n" + "Authorization: Bearer Z9SECRET79\n" * 100
           + "safe terminal canary\n").encode()
    bundle = bundle_for(LogSnapshot(data=raw, original_bytes=len(raw) + 9, head_cut=True),
                        env={"PRIVATE_TOKEN": secret}, limit=128)
    preview, members = rendered(bundle)
    combined = preview + b"".join(members.values()).decode()
    assert "MULTILINE_BOUNDARY_SECRET79" not in combined and "end\n" not in combined
    assert "safe terminal canary" in combined
    assert bundle.body["logs"][0]["truncated"]


@pytest.mark.parametrize("raw", [
    b"fragment" * 10000,
    b"RANDOMBASE64BODY0123456789abcdef\n" * 8,
    b"-----BEGIN RSA PRIVATE KEY-----\n" + b"KEYBODYabc123456789\n" * 10,
], ids=["no-newline", "key-body", "missing-pem-end"])
def test_ambiguous_cut_head_is_safely_omitted(raw):
    bundle = bundle_for(LogSnapshot(data=raw, original_bytes=len(raw) + 1, head_cut=True))
    preview, members = rendered(bundle)
    assert b"RANDOMBASE64BODY" not in b"".join(members.values())
    assert "KEYBODYabc" not in preview and "fragmentfragment" not in preview
    assert bundle.body["logs"][0]["truncated"]


@pytest.mark.parametrize("tail", list(dict.fromkeys(["S3Cr3tTa1l=="] + [
    encoded for size in range(1, 12)
    for encoded in (b64encode(b"\xe7" * size).decode(),
                    b64encode(b"\xe7" * size).decode().rstrip("="))
    if len(encoded) < 16])))
@pytest.mark.parametrize("boundary", ["raw", "context", "next-line"])
def test_cut_pem_short_continuation_is_omitted_from_preview_and_archive(tail, boundary):
    raw = b"Q" * 64 + b"\n"
    if boundary == "next-line":
        raw += b"R" * 64 + b"\n"
    raw += tail.encode() + b"\nterminal failure canary\n"
    limit = len(raw) - 61 if boundary == "context" else len(raw)
    snapshot = LogSnapshot(data=raw, original_bytes=len(raw) + 1000,
                           history_omitted=True, head_cut=True)
    bundle = bundle_for(snapshot, limit=limit)
    preview, members = rendered(bundle)
    archived = members[f"{sb.ARCHIVE_ROOT}/logs/session.log"].decode()
    assert (tail in preview, tail in archived) == (False, False)
    assert archived == bundle.logs["session.log"] == "terminal failure canary\n"
    assert "terminal failure canary" in preview
    log = bundle.body["logs"][0]
    assert (log["available"], log["truncated"], log["safety_head_omitted"],
            log["source_head_cut"], log["history_omitted"], log["raw_read_bytes"],
            log["original_bytes"], log["kept_bytes"]) == (
        True, True, True, True, True, len(raw), len(raw) + 1000, len(archived.encode()))
    assert "unsafe leading context omitted" in " ".join(bundle.body["truncation"])


def test_short_diagnostic_without_ambiguous_body_and_complete_source_are_preserved(monkeypatch):
    monkeypatch.setattr(sb.os, "environ", {})
    diagnostic = "ERROR\nterminal failure canary\n"
    cut = bundle_for(LogSnapshot(data=("incomplete head\n" + diagnostic).encode(),
                                original_bytes=1000, head_cut=True))
    assert cut.logs["session.log"] == diagnostic
    complete = "Q" * 64 + "\nS3Cr3tTa1l==\n" + diagnostic
    uncut = bundle_for(LogSnapshot(data=complete.encode(), original_bytes=len(complete)))
    assert uncut.logs["session.log"] == complete
    assert not uncut.body["logs"][0]["truncated"]


def test_known_value_larger_than_context_omits_cut_source():
    secret = "LONG_KNOWN_SECRET_" * (CONTEXT_BYTES // 10)
    snapshot = LogSnapshot(data=b"fragment\notherwise safe\n", original_bytes=999999, head_cut=True)
    bundle = bundle_for(snapshot, env={"PRIVATE_TOKEN": secret})
    log = bundle.body["logs"][0]
    assert not log["available"] and "context" in log["error"]
    assert not bundle.logs


def test_snapshot_metadata_is_truthful_about_retained_history():
    snapshot = LogSnapshot(data=b"incomplete head\nrecent failure canary\n", original_bytes=123456,
                           segment_indexes=(13, 14), history_omitted=True, head_cut=True)
    bundle = bundle_for(snapshot)
    log = bundle.body["logs"][0]
    assert (log["original_bytes"], log["history_omitted"], log["segments"], log["cutoff"]) == (
        123456, True, [13, 14], "failure-export-request")
    assert log["truncated"] and log["kept_bytes"] == len(bundle.logs["session.log"].encode())
    assert "history" in " ".join(bundle.body["truncation"])


def test_path_reader_never_uses_unbounded_read_and_reports_raw_ceiling(tmp_path, monkeypatch):
    path = tmp_path / "raw.log"
    path.write_text("line café🙂\n" * 150000 + "terminal failure canary\n")
    original = type(path).open
    requests = []

    class Reader:
        def __enter__(self):
            self.handle = original(path, "rb")
            return self

        def __exit__(self, *args):
            self.handle.close()

        def seek(self, offset):
            return self.handle.seek(offset)

        def read(self, count=-1):
            assert 0 <= count <= RAW_SNAPSHOT_BYTES
            requests.append(count)
            return self.handle.read(count)

    monkeypatch.setattr(type(path), "open", lambda *_a, **_k: Reader())
    bundle = sb.build_bundle(sb.BundleRequest(env={}, logs=[sb.LogSource("x", path=path)],
        limits=sb.BundleLimits(max_log_bytes=2 * RAW_SNAPSHOT_BYTES)))
    assert requests and sum(requests) <= RAW_SNAPSHOT_BYTES
    assert bundle.body["logs"][0]["truncated"]
    assert "terminal failure canary" in bundle.logs["x"]


def test_shipped_bounds_and_positional_compatibility():
    assert RAW_SNAPSHOT_BYTES == 1024 * 1024 and CONTEXT_BYTES == 64 * 1024
    limits = sb.BundleLimits(31, 43, 7)
    assert (limits.max_log_bytes, limits.max_field_chars, limits.max_seconds,
            limits.max_bundle_bytes) == (31, 43, 7, 4 * 1024 * 1024)
    assert sb.LogSource("x", "text").text == "text"


def member_payload_size(bundle):
    return sum(len(payload) for _name, payload in sb._members(bundle))


def test_aggregate_cap_matches_exact_previewed_and_archived_members():
    request = sb.BundleRequest(env={}, logs=[
        sb.LogSource("same label", text="older café🙂\n" * 1000),
        sb.LogSource("same_label", text="newest failure canary\n" * 1000)])
    uncapped = sb.build_bundle(request)
    request.limits = replace(request.limits, max_bundle_bytes=6500)
    bundle = sb.build_bundle(request)
    preview, members = rendered(bundle)
    assert len(members) == 3 and len(bundle.logs) == 2
    assert sum(map(len, members.values())) == member_payload_size(bundle) <= 6500
    assert member_payload_size(uncapped) > 6500
    assert "newest failure canary" in bundle.logs["same_label.2"]
    assert "aggregate" in " ".join(bundle.body["truncation"])
    assert "aggregate" in preview
    for log in bundle.body["logs"]:
        assert log["kept_bytes"] == len(bundle.logs[log["name"]].encode())


def test_metadata_overflow_is_visible_before_any_archive_write(tmp_path):
    request = sb.BundleRequest(env={}, checks=[{"id": "must-preserve", "status": "fail",
        "message": "diagnostic evidence" * 30}], limits=sb.BundleLimits(max_bundle_bytes=128))
    destination = tmp_path / "must-not-exist.tar.gz"
    with pytest.raises(ValueError, match="metadata"):
        bundle = sb.build_bundle(request)
        sb.export(bundle, destination, lambda _line: None)
    assert not destination.exists()


def test_complete_unrotated_path_and_text_have_compatible_content(tmp_path):
    payload = "complete diagnostic café🙂\nTERMINAL_FAILURE\n"
    path = tmp_path / "x.log"
    path.write_text(payload)
    request = sb.BundleRequest(env={}, logs=[sb.LogSource("text", text=payload),
                                             sb.LogSource("path", path=path)])
    bundle = sb.build_bundle(request)
    assert bundle.logs == {"text": payload, "path": payload}
    assert all(not log["truncated"] for log in bundle.body["logs"])
    assert bundle.body["truncation"] == []


def test_read_chunk_seams_keep_secret_and_utf8_reconstruction(tmp_path, monkeypatch):
    from ui import session_log
    tee = SessionLogTee(prefix="atlas-launch-chunks-", config=SessionLogConfig(directory=str(tmp_path)))
    original = session_log.os.read
    counts = []

    def short_read(fd, count):
        counts.append(count)
        return original(fd, min(count, 7))

    try:
        tee.write("café🙂 Authorization: Bearer Z9SecretBoundaryABC123456\nterminal canary\n")
        monkeypatch.setattr(session_log.os, "read", short_read)
        snapshot = tee.snapshot()
        preview, members = rendered(bundle_for(snapshot))
        text = b"".join(members.values()).decode()
        assert len(counts) > 5 and "café🙂" in text
        assert "Z9SecretBoundary" not in text + preview and "ABC123456" not in text + preview
        assert "terminal canary" in text
    finally:
        tee.close()


def test_cut_pem_end_redacts_body_and_preserves_following_failure():
    raw = b"KEYBODYabc123456789\n" * 3 + b"-----END RSA PRIVATE KEY-----\nterminal canary\n"
    bundle = bundle_for(LogSnapshot(data=raw, original_bytes=999, head_cut=True))
    preview, members = rendered(bundle)
    assert b"KEYBODYabc" not in b"".join(members.values()) and "KEYBODYabc" not in preview
    assert "terminal canary" in bundle.logs["session.log"]
