"""Boundary redaction and exact member-budget contracts for #1759."""
from base64 import b64encode
from dataclasses import replace
import datetime
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


def test_redaction_inside_the_omitted_head_keeps_the_safe_boundary():
    raw = b"abc PARTIALSECRETVALUE99 more-excluded\nnext head line\n" + b"ok line\n" * 3
    bundle = bundle_for(LogSnapshot(data=raw, original_bytes=len(raw) + 5, head_cut=True),
                        env={"PRIVATE_TOKEN": "PARTIALSECRETVALUE99"})
    assert bundle.logs["session.log"] == "next head line\n" + "ok line\n" * 3


@pytest.mark.parametrize(("key", "secret", "line"), [
    ("POSTGRES_PASSWORD", "p4ss;word,99", "PGPASSWORD={} started"),
    ("GRAPH_DB_PASSWORD", "Tr0ub;dor&3x", "export GRAPH_DB_PASSWORD={}"),
    ("OPENAI_API_KEY", "abc def ghij klm", "OPENAI_API_KEY={}"),
    ("MY_PASSWORD", "red horse 99 battery", "my_password: {}"),
    ("MY_PASSWORD", "red horse 99 battery", "run --password {}"),
], ids=["comma", "semicolon-ampersand", "space-assignment", "space-yaml", "space-flag"])
def test_known_value_with_a_delimiter_is_fully_redacted_after_a_key(monkeypatch, key, secret, line):
    monkeypatch.setattr(sb.os, "environ", {})
    text = line.format(secret) + "\nterminal canary\n"
    redactor = sb.Redactor({key: secret})
    bundle = sb.build_bundle(sb.BundleRequest(
        env={key: secret}, logs=[sb.LogSource("transcript.log", text=text)]))
    expected = line.format(sb.REDACTED) + "\nterminal canary\n"
    assert (redactor.text(text), redactor.window(text, 0)[0],
            bundle.logs["transcript.log"]) == (expected, expected, expected)


def test_known_value_holding_a_key_word_still_lets_its_assignment_redact():
    redactor = sb.Redactor({"ROOT_PASSWORD": "PASSWORD_ROOT1"})
    assert redactor.text("PASSWORD_ROOT1=xyz123") == f"{sb.REDACTED}={sb.REDACTED}"


def test_a_short_secret_inside_a_key_word_secret_does_not_split_it():
    """The early pass must not break a longer key-word value apart."""
    env = {"SHORT_TOKEN": "abc123xyz", "LONG_PASSWORD": "mypasswordabc123xyzTAIL"}
    redactor = sb.Redactor(env)
    line = "a mypasswordabc123xyzTAIL b abc123xyz c"
    for out in (redactor.text(line), redactor.window(line, 0)[0]):
        assert "TAIL" not in out and "mypassword" not in out and "abc123xyz" not in out


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
    assert any(log.get("aggregate_truncated") and log["truncated"] for log in bundle.body["logs"])


def test_aggregate_cap_leaves_a_bundle_that_fits_exactly_untouched():
    now = datetime.datetime(2026, 11, 1, 12, 0, tzinfo=datetime.timezone.utc)
    request = sb.BundleRequest(env={}, logs=[sb.LogSource("x", text="failure canary\n" * 50)])
    request.limits = replace(request.limits, max_bundle_bytes=member_payload_size(sb.build_bundle(request, now=now)))
    bundle = sb.build_bundle(request, now=now)
    assert bundle.logs["x"] == "failure canary\n" * 50
    assert bundle.body["truncation"] == [] and not bundle.body["logs"][0]["truncated"]


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


@pytest.mark.parametrize(("address", "local"), [
    ("/var/run/docker.sock", True),
    (("localhost", 1), True),
    (("127.0.0.1", 1), True),
    (("::1", 1, 0, 0), True),
    (("::ffff:127.0.0.1", 1, 0, 0), True),
    (("fe80::1%lo0", 1, 0, 0), False),
    (("example.com", 80), False),
    (("192.0.2.1", 1), False),
])
def test_offline_guard_admits_only_loopback_and_unix_sockets(address, local):
    """`offline()` keeps loopback and Unix sockets usable; any hostname counts as remote."""
    assert sb._is_local_address(address) is local


def test_six_character_secret_value_is_scrubbed_from_free_text():
    """`_MIN_SECRET_LEN` is inclusive: a 6-character non-wordlike secret is matched by value."""
    scrubbed = sb.Redactor({"API_KEY": "a1#b2!"}).text("upstream echoed a1#b2! back")
    assert "a1#b2!" not in scrubbed
    assert sb.REDACTED in scrubbed


def test_bundle_fields_are_clipped_to_the_limit_with_a_truncation_note():
    checks = [{"id": "c", "status": "fail", "message": "m" * 40},
              {"id": "d", "status": "ok", "message": "n" * 16}]
    body = sb.build_bundle(sb.BundleRequest(
        env={}, checks=checks, limits=sb.BundleLimits(max_field_chars=16))).body
    assert body["checks"][0]["message"] == "m" * 16 + " … [truncated]"
    assert body["checks"][1]["message"] == "n" * 16
    assert "bundle.checks[0].message: cut to 16 of 40 characters" in body["truncation"]
    assert not any(note.startswith("bundle.checks[1]") for note in body["truncation"])


def test_archive_is_deterministic_owner_only_and_sanitizes_member_names():
    now = datetime.datetime(2026, 11, 1, 12, 0, tzinfo=datetime.timezone.utc)
    request = sb.BundleRequest(env={}, logs=[
        sb.LogSource("zeta", text="z\n"), sb.LogSource("../x", text="escape\n"),
        sb.LogSource("", text="unnamed\n")])
    archive = sb.archive_bytes(sb.build_bundle(request, now=now))
    assert archive == sb.archive_bytes(sb.build_bundle(request, now=now))
    stamp = int(now.timestamp())
    assert int.from_bytes(archive[4:8], "little") == stamp  # gzip header MTIME
    with tarfile.open(fileobj=io.BytesIO(archive)) as opened:
        members = opened.getmembers()
    assert [member.name for member in members] == [
        f"{sb.ARCHIVE_ROOT}/{name}" for name in ("bundle.json", "logs/.._x", "logs/log", "logs/zeta")]
    assert {(member.mode, member.mtime, member.uid, member.gid, member.uname, member.gname)
            for member in members} == {(0o600, stamp, 0, 0, "atlas", "atlas")}


def test_failed_archive_write_leaves_no_temporary_file(tmp_path, monkeypatch):
    bundle = sb.build_bundle(sb.BundleRequest(env={}))

    def refuse(*_args):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(sb.os, "replace", refuse)
    with pytest.raises(OSError, match="synthetic replace failure"):
        sb.write_bundle(bundle, tmp_path / "bundle.tar.gz")
    assert list(tmp_path.iterdir()) == []


def test_preview_shows_bundle_json_in_full_and_elides_only_long_logs():
    bundle = sb.build_bundle(sb.BundleRequest(env={}, logs=[
        sb.LogSource("even", text="".join(f"even {index}\n" for index in range(4))),
        sb.LogSource("long", text="".join(f"long {index}\n" for index in range(5)))]))
    full = sb.preview_lines(bundle)
    start = full.index("long 0")
    expected = [*full[:start], "long 0", "long 1",
                "… 1 more redacted lines, as shown in the log pane …", "long 3", "long 4",
                *full[start + 5:]]
    assert sb.preview_lines(bundle, log_lines=2) == expected


@pytest.mark.parametrize("case", [
    ("BASE_PORT", "atlas.consumer.yml", "Change `env.values.BASE_PORT` in {origin}. Every start writes "
     "the manifest's values into .env, so an edit to .env alone does not stick."),
    ("PROJECT_NAME", "atlas.consumer.yaml", "Change `project_name` in {origin}. Every start writes "
     "the manifest's values into .env, so an edit to .env alone does not stick."),
    ("BASE_PORT", "override.env", "Change BASE_PORT in {origin}, the env file that sets it; "
     "it is applied over .env on every start."),
    ("BASE_PORT", None, "Change BASE_PORT in {origin} (or pass the matching ./start.sh flag), then restart."),
], ids=["manifest", "manifest-project-name", "overlay", "active-env"])
def test_config_entry_action_names_the_file_that_sets_the_value(tmp_path, case):
    name, origin_name, action = case
    env_file = tmp_path / "active.env"
    env_file.write_text(f"{name}=1\n", encoding="utf-8")
    origins = {name: str(tmp_path / origin_name)} if origin_name else {}
    request = sb.BundleRequest(env={name: "1"}, env_origins=origins, env_file=env_file)
    entries, _omitted = sb.config_entries(request, sb.Redactor())
    origin = origins.get(name, str(env_file))
    assert entries == [{"key": name, "value": "1", "origin": origin, "action": action.format(origin=origin)}]


def test_file_key_discovery_ignores_a_line_without_an_assignment(tmp_path):
    env_file = tmp_path / "active.env"
    env_file.write_text("BASE_PORT\nPROJECT_NAME=atlas\n", encoding="utf-8")
    assert sb._file_keys(env_file) == {"PROJECT_NAME"}
