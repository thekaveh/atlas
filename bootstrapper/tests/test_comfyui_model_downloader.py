"""Behavioral trust-boundary tests for comfyui-init model downloads."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import signal
import subprocess
import time

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "services/comfyui/init/scripts/download_models.sh"
PAYLOAD = b"verified model payload"
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()
IMMUTABLE_HF_URL = (
    "https://huggingface.co/example/models/resolve/"
    "0123456789abcdef0123456789abcdef01234567/success/model.bin"
)


def _live_process_identity(pid: int, env: dict[str, str]) -> tuple[str, str]:
    """Return the start fingerprint and PID namespace for ``pid``.

    ``download_models.sh`` reads ``/proc`` directly whenever it is readable and
    only falls back to the stubbed ``ps``/``readlink`` otherwise. On Linux the
    real values therefore win, so a lock file has to describe the live process
    the way the script will actually see it -- otherwise the owner looks like a
    different process and the lock is legitimately reclaimed.
    """
    stat_path = Path(f"/proc/{pid}/stat")
    if stat_path.is_file():
        remainder = stat_path.read_text(encoding="utf-8").rsplit(") ", 1)[-1]
        start_ticks = remainder.split()[19]
        boot_id_path = Path("/proc/sys/kernel/random/boot_id")
        boot_id = (
            boot_id_path.read_text(encoding="utf-8").strip()
            if boot_id_path.is_file()
            else "unknown-boot"
        )
        fingerprint = f"{boot_id}:{start_ticks}"
    else:
        fingerprint = env["TEST_PS_FINGERPRINT"]
    try:
        namespace = os.readlink(f"/proc/{pid}/ns/pid")
    except OSError:
        namespace = env["TEST_PID_NAMESPACE"]
    return fingerprint, namespace


def _row(
    *,
    url: str = IMMUTABLE_HF_URL,
    sha: str = PAYLOAD_SHA,
    source: str = "curated",
    name: str = "test-model",
    category: str = "checkpoint",
    filename: str = "model.bin",
    target_dir: str = "checkpoints",
    provisioning: str | None = None,
) -> str:
    columns = [name, category, filename, url, sha, target_dir, source]
    if provisioning is not None:
        columns.append(provisioning)
    return "\t".join(columns)


def test_manifest_trust_preflight_precedes_package_network_access() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.index("validate_manifest_rows") < text.index("wget --help")
    assert 'timeout -k 10 "$TOTAL_TIMEOUT" wget' in text


def _fake_tools(tmp_path: Path) -> tuple[Path, Path, Path]:
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    counter = tmp_path / "wget-calls"
    ready = tmp_path / "wget-ready"

    ps = fakebin / "ps"
    ps.write_text(
        """#!/bin/sh
printf '%s\n' "${TEST_PS_FINGERPRINT:-test-process-start}"
""",
        encoding="utf-8",
    )
    ps.chmod(0o755)

    readlink = fakebin / "readlink"
    readlink.write_text(
        """#!/bin/sh
printf '%s\n' "${TEST_PID_NAMESPACE:-pid:[100]}"
""",
        encoding="utf-8",
    )
    readlink.chmod(0o755)

    wget = fakebin / "wget"
    wget.write_text(
        """#!/bin/sh
if [ "${1:-}" = "--help" ]; then
  printf 'call\n' >> "$TEST_TOOLCHECK_COUNTER"
  if [ -n "${TEST_TOOLCHECK_SWAP_MANIFEST:-}" ]; then
    printf '%s\n' "$TEST_TOOLCHECK_SWAP_ROW" > "$TEST_TOOLCHECK_SWAP_MANIFEST"
  fi
  if [ -n "${TEST_SNAPSHOT_DIR:-}" ]; then
    set -- "$TEST_SNAPSHOT_DIR"/comfy-active.*
    printf '%s\n' "$#" > "$TEST_SNAPSHOT_COUNT"
    stat -c '%a' "$1" > "$TEST_SNAPSHOT_MODE" 2>/dev/null || stat -f '%Lp' "$1" > "$TEST_SNAPSHOT_MODE" 2>/dev/null
  fi
  if [ -n "${TEST_TOOLCHECK_SYMLINK_DIR:-}" ]; then
    rmdir "$TEST_TOOLCHECK_SYMLINK_DIR"
    ln -s "$TEST_TOOLCHECK_SYMLINK_OUTSIDE" "$TEST_TOOLCHECK_SYMLINK_DIR"
  fi
  [ "${TEST_TOOLCHECK_FAIL:-0}" = "1" ] && exit 1
  exit 0
fi
out=
url=
while [ "$#" -gt 0 ]; do
  case "$1" in
    -O) out="$2"; shift 2 ;;
    --timeout=*|--tries=*) shift ;;
    *) url="$1"; shift ;;
  esac
done
printf 'call\n' >> "$TEST_WGET_COUNTER"
case "$url" in
  *success*) printf '%s' "$TEST_PAYLOAD" > "$out" ;;
  *wrong*) printf '%s' 'wrong payload' > "$out" ;;
  *empty*) : > "$out" ;;
  *failure*) printf 'download failed for %s\n' "$url" >&2; exit 8 ;;
  *slow*)
    printf '%s' 'partial payload' > "$out"
    : > "$TEST_WGET_READY"
    sleep "${TEST_WGET_DELAY:-1}"
    printf '%s' "$TEST_PAYLOAD" > "$out"
    ;;
  *hang*)
    printf '%s' 'partial payload' > "$out"
    : > "$TEST_WGET_READY"
    sleep 30
    ;;
  *) exit 9 ;;
esac
""",
        encoding="utf-8",
    )
    wget.chmod(0o755)
    return fakebin, counter, ready


def _environment(tmp_path: Path, manifest: Path, models: Path, fakebin: Path, counter: Path, ready: Path):
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fakebin}:/opt/homebrew/bin:/usr/bin:/bin",
            "COMFYUI_MANIFEST_TSV": str(manifest),
            "COMFYUI_MODELS_PATH": str(models),
            "COMFYUI_DOWNLOAD_CONNECT_TIMEOUT_SECONDS": "1",
            "COMFYUI_DOWNLOAD_TOTAL_TIMEOUT_SECONDS": "5",
            "COMFYUI_DOWNLOAD_LOCK_TIMEOUT_SECONDS": "5",
            "TEST_PAYLOAD": PAYLOAD.decode(),
            "TEST_WGET_COUNTER": str(counter),
            "TEST_WGET_READY": str(ready),
            "TEST_TOOLCHECK_COUNTER": str(tmp_path / "toolcheck-calls"),
            "TEST_PS_FINGERPRINT": "test-process-start",
            "TEST_PID_NAMESPACE": "pid:[100]",
        }
    )
    return env


def _setup(tmp_path: Path, row: str):
    manifest = tmp_path / "active-models.tsv"
    manifest.write_text(row + "\n", encoding="utf-8")
    models = tmp_path / "models"
    fakebin, counter, ready = _fake_tools(tmp_path)
    env = _environment(tmp_path, manifest, models, fakebin, counter, ready)
    return manifest, models, counter, ready, env


def _run(tmp_path: Path, row: str) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    _, models, counter, _, env = _setup(tmp_path, row)
    result = subprocess.run(
        ["sh", str(SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    return result, models, counter


def _assert_no_transfer_debris(models: Path) -> None:
    assert not list(models.rglob("*.part.*"))
    assert not list(models.rglob("*.download.lock"))


def _assert_plan_rejected_without_effects(
    tmp_path: Path,
    manifest_content: bytes,
    *,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    manifest = tmp_path / "active-models.tsv"
    manifest.write_bytes(manifest_content)
    models = tmp_path / "models"
    fakebin, counter, ready = _fake_tools(tmp_path)
    env = _environment(tmp_path, manifest, models, fakebin, counter, ready)
    if environment:
        env.update(environment)
    result = subprocess.run(
        ["sh", str(SCRIPT)], env=env, text=True, capture_output=True, timeout=10, check=False
    )
    assert result.returncode != 0
    assert not (tmp_path / "toolcheck-calls").exists()
    assert not counter.exists()
    assert not models.exists()
    return result


@pytest.mark.parametrize(
    ("environment", "label"),
    [
        ({"COMFYUI_DOWNLOAD_CONNECT_TIMEOUT_SECONDS": "0"}, "zero"),
        ({"COMFYUI_DOWNLOAD_CONNECT_TIMEOUT_SECONDS": "01"}, "non-canonical"),
        ({"COMFYUI_DOWNLOAD_CONNECT_TIMEOUT_SECONDS": "301"}, "out of range"),
        ({"COMFYUI_DOWNLOAD_TOTAL_TIMEOUT_SECONDS": "604801"}, "out of range"),
        ({"COMFYUI_DOWNLOAD_LOCK_TIMEOUT_SECONDS": "3601"}, "out of range"),
        ({"COMFYUI_DOWNLOAD_RETRIES": "11"}, "out of range"),
    ],
)
def test_invalid_numeric_knobs_fail_before_any_external_effect(
    tmp_path: Path, environment: dict[str, str], label: str
) -> None:
    result = _assert_plan_rejected_without_effects(
        tmp_path, (_row() + "\n").encode(), environment=environment
    )
    assert "invalid downloader configuration" in result.stdout
    assert label


@pytest.mark.parametrize(
    "knob",
    [
        "COMFYUI_DOWNLOAD_CONNECT_TIMEOUT_SECONDS",
        "COMFYUI_DOWNLOAD_TOTAL_TIMEOUT_SECONDS",
        "COMFYUI_DOWNLOAD_LOCK_TIMEOUT_SECONDS",
        "COMFYUI_DOWNLOAD_RETRIES",
    ],
)
@pytest.mark.parametrize("value", ["9223372036854775808", "9" * 300])
def test_overflow_numeric_knobs_fail_lexically_before_any_effect(
    tmp_path: Path, knob: str, value: str
) -> None:
    result = _assert_plan_rejected_without_effects(
        tmp_path, (_row() + "\n").encode(), environment={knob: value}
    )
    assert "invalid downloader configuration" in result.stdout
    assert "integer expression expected" not in result.stderr


@pytest.mark.parametrize(
    "content",
    [
        _row().encode(),  # missing final newline
        (_row() + "\textra\n").encode(),
        (_row(name="bad\rname") + "\n").encode(),
        (_row(name="../bad") + "\n").encode(),
        (_row(category="unknown") + "\n").encode(),
        (_row(filename="../model.bin") + "\n").encode(),
        (_row(target_dir="unknown") + "\n").encode(),
        (_row(source="unknown") + "\n").encode(),
        (_row(url="file:///tmp/model.bin") + "\n").encode(),
        (_row(url="https://huggingface.co/org/repo/resolve/main/model.bin") + "\n").encode(),
        (_row(sha="") + "\n").encode(),
        (_row(source="custom", sha="ABC") + "\n").encode(),
        (_row(name="bad\x01name") + "\n").encode(),
    ],
)
def test_malformed_plan_fails_before_package_target_lock_or_network_effects(
    tmp_path: Path, content: bytes
) -> None:
    result = _assert_plan_rejected_without_effects(tmp_path, content)
    assert "invalid download plan" in result.stdout


def test_duplicate_target_metadata_disagreement_fails_before_effects(tmp_path: Path) -> None:
    content = (_row() + "\n" + _row(name="other", url="https://models.example/other") + "\n").encode()
    result = _assert_plan_rejected_without_effects(tmp_path, content)
    assert "conflicting duplicate target" in result.stdout


def test_downloader_processes_private_snapshot_not_manifest_swapped_during_toolcheck(tmp_path: Path) -> None:
    manifest, models, counter, _, env = _setup(tmp_path, _row())
    env["TEST_TOOLCHECK_SWAP_MANIFEST"] = str(manifest)
    env["TEST_TOOLCHECK_SWAP_ROW"] = _row(
        name="swapped", filename="swapped.bin", url="https://huggingface.co/example/models/resolve/0123456789abcdef0123456789abcdef01234567/wrong/swapped.bin"
    )
    result = subprocess.run(
        ["sh", str(SCRIPT)], env=env, text=True, capture_output=True, timeout=10, check=False
    )
    assert result.returncode == 0
    assert counter.read_text().splitlines() == ["call"]
    assert (models / "checkpoints/model.bin").read_bytes() == PAYLOAD
    assert not (models / "checkpoints/swapped.bin").exists()


def test_exactly_one_private_0600_snapshot_exists_at_package_boundary(tmp_path: Path) -> None:
    _, models, _, _, env = _setup(tmp_path, _row())
    snapshot_dir = tmp_path / "snapshots"
    snapshot_dir.mkdir()
    env.update({
        "TMPDIR": str(snapshot_dir),
        "TEST_SNAPSHOT_DIR": str(snapshot_dir),
        "TEST_SNAPSHOT_COUNT": str(tmp_path / "snapshot-count"),
        "TEST_SNAPSHOT_MODE": str(tmp_path / "snapshot-mode"),
    })
    result = subprocess.run(
        ["sh", str(SCRIPT)], env=env, text=True, capture_output=True, timeout=10, check=False
    )
    assert result.returncode == 0
    assert (tmp_path / "snapshot-count").read_text().strip() == "1"
    assert (tmp_path / "snapshot-mode").read_text().strip() == "600"
    assert (models / "checkpoints/model.bin").read_bytes() == PAYLOAD


def test_invalid_curated_hash_is_rejected_before_download_or_target_effects(tmp_path: Path) -> None:
    result, models, counter = _run(tmp_path, _row(sha="ABC"))
    assert result.returncode != 0
    assert "invalid lowercase SHA-256" in result.stdout
    assert not counter.exists()
    assert not (models / "checkpoints/model.bin").exists()
    _assert_no_transfer_debris(models)


def test_wrong_download_hash_never_becomes_the_target(tmp_path: Path) -> None:
    result, models, _ = _run(tmp_path, _row(url="https://huggingface.co/example/models/resolve/0123456789abcdef0123456789abcdef01234567/wrong?token=secret"))
    assert result.returncode != 0
    assert "checksum mismatch" in result.stdout
    assert "secret" not in result.stdout + result.stderr
    assert not (models / "checkpoints/model.bin").exists()
    _assert_no_transfer_debris(models)


def test_failed_replacement_never_destroys_existing_target(tmp_path: Path) -> None:
    _, models, _, _, env = _setup(
        tmp_path, _row(url="https://huggingface.co/example/models/resolve/0123456789abcdef0123456789abcdef01234567/wrong/model.bin")
    )
    target = models / "checkpoints/model.bin"
    target.parent.mkdir(parents=True)
    original = b"existing target remains until a replacement verifies"
    target.write_bytes(original)

    result = subprocess.run(
        ["sh", str(SCRIPT)], env=env, text=True, capture_output=True, timeout=10, check=False
    )

    assert result.returncode != 0
    assert "checksum mismatch" in result.stdout
    assert target.read_bytes() == original
    _assert_no_transfer_debris(models)


def test_corrupt_cache_is_replaced_only_after_verified_download(tmp_path: Path) -> None:
    manifest, models, _, _, env = _setup(tmp_path, _row())
    target = models / "checkpoints/model.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"corrupt cache")

    result = subprocess.run(
        ["sh", str(SCRIPT)], env=env, text=True, capture_output=True, timeout=10, check=False
    )
    assert result.returncode == 0
    assert "cached but sha mismatch" in result.stdout
    assert target.read_bytes() == PAYLOAD
    _assert_no_transfer_debris(models)


def test_empty_response_is_rejected_and_cleaned(tmp_path: Path) -> None:
    result, models, _ = _run(tmp_path, _row(url="https://huggingface.co/example/models/resolve/0123456789abcdef0123456789abcdef01234567/empty/model.bin"))
    assert result.returncode != 0
    assert "empty response" in result.stdout
    assert not (models / "checkpoints/model.bin").exists()
    _assert_no_transfer_debris(models)


def test_valid_cache_is_reused_only_after_hash_verification(tmp_path: Path) -> None:
    manifest, models, counter, _, env = _setup(
        tmp_path, _row(url="https://huggingface.co/example/models/resolve/0123456789abcdef0123456789abcdef01234567/failure?token=must-not-leak")
    )
    target = models / "checkpoints/model.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(PAYLOAD)

    result = subprocess.run(
        ["sh", str(SCRIPT)], env=env, text=True, capture_output=True, timeout=10, check=False
    )
    assert result.returncode == 0
    assert "cached, sha verified" in result.stdout
    assert not counter.exists()
    assert target.read_bytes() == PAYLOAD
    assert "must-not-leak" not in result.stdout + result.stderr


def test_interrupted_transfer_cleans_private_partial_and_lock(tmp_path: Path) -> None:
    _, models, _, ready, env = _setup(
        tmp_path, _row(url="https://huggingface.co/example/models/resolve/0123456789abcdef0123456789abcdef01234567/hang/model.bin")
    )
    proc = subprocess.Popen(
        ["sh", str(SCRIPT)], env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    deadline = time.monotonic() + 5
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ready.exists(), "fake downloader never entered the transfer"
    assert _status_fields(models)[2] == "provisioning"
    proc.send_signal(signal.SIGTERM)
    proc.communicate(timeout=5)

    assert proc.returncode != 0
    assert not (models / "checkpoints/model.bin").exists()
    _assert_no_transfer_debris(models)


def test_invalid_retry_invalidates_prior_ready_status_before_external_effects(tmp_path: Path) -> None:
    manifest, models, counter, ready, env = _setup(tmp_path, _row(provisioning="required"))
    models.mkdir()
    plan_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
    (models / ".atlas-model-provisioning.tsv").write_text(
        f"v1\t{plan_sha}\tready\t0\t0\n", encoding="utf-8"
    )
    env["COMFYUI_DOWNLOAD_RETRIES"] = "0"

    result = subprocess.run(
        ["sh", str(SCRIPT)], env=env, text=True, capture_output=True, timeout=10, check=False
    )

    assert result.returncode != 0
    status = models / ".atlas-model-provisioning.tsv"
    assert not status.exists() or _status_fields(models)[2] != "ready"
    assert not counter.exists()


@pytest.mark.parametrize("failure_stage", ["snapshot", "configuration", "plan", "toolcheck"])
def test_every_early_failure_invalidates_prior_ready_status(
    tmp_path: Path, failure_stage: str
) -> None:
    manifest, models, counter, _, env = _setup(tmp_path, _row(provisioning="required"))
    models.mkdir()
    if failure_stage == "snapshot":
        env["TMPDIR"] = str(tmp_path / "missing-tmpdir")
    elif failure_stage == "configuration":
        env["COMFYUI_DOWNLOAD_RETRIES"] = "0"
    elif failure_stage == "plan":
        manifest.write_text("malformed\n", encoding="utf-8")
    else:
        env["TEST_TOOLCHECK_FAIL"] = "1"
    plan_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
    status = models / ".atlas-model-provisioning.tsv"
    status.write_text(f"v1\t{plan_sha}\tready\t0\t0\n", encoding="utf-8")

    result = subprocess.run(
        ["sh", str(SCRIPT)], env=env, text=True, capture_output=True, timeout=10, check=False
    )

    assert result.returncode != 0
    assert not status.exists() or _status_fields(models)[2] != "ready"
    assert not counter.exists()


def test_concurrent_same_target_downloads_once_and_both_finish_cleanly(tmp_path: Path) -> None:
    _, models, counter, _, env = _setup(
        tmp_path, _row(url="https://huggingface.co/example/models/resolve/0123456789abcdef0123456789abcdef01234567/slow/model.bin")
    )
    first = subprocess.Popen(
        ["sh", str(SCRIPT)], env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    second = subprocess.Popen(
        ["sh", str(SCRIPT)], env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    first.communicate(timeout=10)
    second.communicate(timeout=10)

    assert first.returncode == second.returncode == 0
    assert counter.read_text(encoding="utf-8").splitlines() == ["call"]
    assert (models / "checkpoints/model.bin").read_bytes() == PAYLOAD
    _assert_no_transfer_debris(models)


def _write_lock(
    path: Path,
    pid: int,
    fingerprint: str,
    token: str = "prior-owner",
    namespace: str = "pid:[100]",
) -> None:
    path.write_text(
        f"v2\t{pid}\t{namespace}\t{fingerprint}\t{token}\n", encoding="utf-8"
    )
    path.chmod(0o600)


def test_stale_same_pid_different_process_identity_is_recovered_promptly(tmp_path: Path) -> None:
    _, models, _, _, env = _setup(tmp_path, _row())
    target = models / "checkpoints/model.bin"
    target.parent.mkdir(parents=True)
    stale_lock = Path(str(target) + ".download.lock")
    _write_lock(stale_lock, os.getpid(), "different-process-start")
    stale_partial = Path(str(target) + ".part.abandoned")
    stale_partial.write_bytes(b"abandoned")
    env["COMFYUI_DOWNLOAD_LOCK_TIMEOUT_SECONDS"] = "2"

    result = subprocess.run(
        ["sh", str(SCRIPT)], env=env, text=True, capture_output=True, timeout=10, check=False
    )

    assert result.returncode == 0
    assert "recovered stale download lock" in result.stdout
    assert target.read_bytes() == PAYLOAD
    assert not stale_partial.exists()
    _assert_no_transfer_debris(models)


def test_true_live_lock_is_not_stolen_and_times_out_finitely(tmp_path: Path) -> None:
    _, models, counter, _, env = _setup(tmp_path, _row())
    target = models / "checkpoints/model.bin"
    target.parent.mkdir(parents=True)
    lock = Path(str(target) + ".download.lock")
    live = subprocess.Popen(["sleep", "10"])
    try:
        fingerprint, namespace = _live_process_identity(live.pid, env)
        _write_lock(lock, live.pid, fingerprint, namespace=namespace)
        env["COMFYUI_DOWNLOAD_LOCK_TIMEOUT_SECONDS"] = "2"
        result = subprocess.run(
            ["sh", str(SCRIPT)], env=env, text=True, capture_output=True, timeout=7, check=False
        )
        assert result.returncode != 0
        assert "timed out waiting" in result.stdout
        assert lock.exists()
        assert not counter.exists()
    finally:
        live.terminate()
        live.wait(timeout=3)


def test_unverifiable_lock_is_not_reclaimed_and_fails_finitely(tmp_path: Path) -> None:
    _, models, counter, _, env = _setup(tmp_path, _row())
    target = models / "checkpoints/model.bin"
    target.parent.mkdir(parents=True)
    lock = Path(str(target) + ".download.lock")
    lock.write_text("creator-has-not-published-verifiable-ownership\n", encoding="utf-8")
    lock.chmod(0o600)
    env["COMFYUI_DOWNLOAD_LOCK_TIMEOUT_SECONDS"] = "1"
    result = subprocess.run(
        ["sh", str(SCRIPT)], env=env, text=True, capture_output=True, timeout=5, check=False
    )
    assert result.returncode != 0
    assert "timed out waiting" in result.stdout
    assert lock.exists()
    assert not counter.exists()


def test_creator_paused_over_two_seconds_is_not_stolen_and_downloads_once(tmp_path: Path) -> None:
    _, models, counter, ready, env = _setup(
        tmp_path, _row(url="https://huggingface.co/example/models/resolve/0123456789abcdef0123456789abcdef01234567/slow/model.bin")
    )
    env["TEST_WGET_DELAY"] = "3"
    first = subprocess.Popen(
        ["sh", str(SCRIPT)], env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    deadline = time.monotonic() + 5
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ready.exists()
    second = subprocess.Popen(
        ["sh", str(SCRIPT)], env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    first.communicate(timeout=10)
    second.communicate(timeout=10)
    assert first.returncode == second.returncode == 0
    assert counter.read_text().splitlines() == ["call"]
    assert (models / "checkpoints/model.bin").read_bytes() == PAYLOAD
    _assert_no_transfer_debris(models)


def test_live_owner_in_another_pid_namespace_is_not_stolen(tmp_path: Path) -> None:
    _, models, counter, ready, first_env = _setup(
        tmp_path,
        _row(
            url="https://huggingface.co/example/models/resolve/"
            "0123456789abcdef0123456789abcdef01234567/slow/model.bin"
        ),
    )
    first_env["TEST_WGET_DELAY"] = "3"
    first_env["TEST_PID_NAMESPACE"] = "pid:[101]"
    second_env = first_env.copy()
    second_env["TEST_PID_NAMESPACE"] = "pid:[202]"
    first = subprocess.Popen(
        ["sh", str(SCRIPT)],
        env=first_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 5
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ready.exists()
    second = subprocess.Popen(
        ["sh", str(SCRIPT)],
        env=second_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    first.communicate(timeout=10)
    second.communicate(timeout=10)
    assert first.returncode == second.returncode == 0
    assert counter.read_text().splitlines() == ["call"]
    assert (models / "checkpoints/model.bin").read_bytes() == PAYLOAD
    _assert_no_transfer_debris(models)


def test_stale_owner_from_prior_pid_namespace_is_recovered(tmp_path: Path) -> None:
    _, models, _, _, env = _setup(tmp_path, _row())
    target = models / "checkpoints/model.bin"
    target.parent.mkdir(parents=True)
    lock = Path(str(target) + ".download.lock")
    _write_lock(
        lock,
        1,
        "foreign-process-start",
        namespace="pid:[999]",
    )
    old = time.time() - 30
    os.utime(lock, (old, old))
    result = subprocess.run(
        ["sh", str(SCRIPT)], env=env, text=True, capture_output=True, timeout=10, check=False
    )
    assert result.returncode == 0
    assert "recovered stale download lock" in result.stdout
    assert target.read_bytes() == PAYLOAD
    _assert_no_transfer_debris(models)


def test_sigkill_owner_is_recovered_and_abandoned_partial_cleaned(tmp_path: Path) -> None:
    manifest, models, counter, ready, env = _setup(
        tmp_path, _row(url="https://huggingface.co/example/models/resolve/0123456789abcdef0123456789abcdef01234567/hang/model.bin")
    )
    first = subprocess.Popen(
        ["sh", str(SCRIPT)], env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    deadline = time.monotonic() + 5
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ready.exists()
    first.kill()
    first.communicate(timeout=5)
    manifest.write_text(_row() + "\n", encoding="utf-8")
    result = subprocess.run(
        ["sh", str(SCRIPT)], env=env, text=True, capture_output=True, timeout=10, check=False
    )
    assert result.returncode == 0
    assert "recovered stale download lock" in result.stdout
    assert (models / "checkpoints/model.bin").read_bytes() == PAYLOAD
    assert counter.read_text().splitlines() == ["call", "call"]
    _assert_no_transfer_debris(models)


@pytest.mark.parametrize("symlink_part", ["root", "directory", "destination"])
def test_unsafe_symlink_state_never_writes_outside_models_root_or_downloads(
    tmp_path: Path, symlink_part: str
) -> None:
    manifest, models, counter, _, env = _setup(tmp_path, _row())
    outside = tmp_path / "outside"
    outside.mkdir()
    if symlink_part == "root":
        models.symlink_to(outside, target_is_directory=True)
    elif symlink_part == "directory":
        models.mkdir()
        (models / "checkpoints").symlink_to(outside, target_is_directory=True)
    else:
        (models / "checkpoints").mkdir(parents=True)
        (models / "checkpoints/model.bin").symlink_to(outside / "model.bin")
    result = subprocess.run(
        ["sh", str(SCRIPT)], env=env, text=True, capture_output=True, timeout=10, check=False
    )
    assert result.returncode != 0
    assert "unsafe model path" in result.stdout
    assert not counter.exists()
    assert list(outside.iterdir()) == []


def test_symlink_swap_during_toolcheck_is_rechecked_before_partial_or_network(
    tmp_path: Path,
) -> None:
    _, models, counter, _, env = _setup(tmp_path, _row())
    (models / "checkpoints").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    env["TEST_TOOLCHECK_SYMLINK_DIR"] = str(models / "checkpoints")
    env["TEST_TOOLCHECK_SYMLINK_OUTSIDE"] = str(outside)
    result = subprocess.run(
        ["sh", str(SCRIPT)], env=env, text=True, capture_output=True, timeout=10, check=False
    )
    assert result.returncode != 0
    assert "unsafe model path" in result.stdout
    assert not counter.exists()
    assert list(outside.iterdir()) == []


def test_unhashed_custom_entry_remains_explicitly_unverified(tmp_path: Path) -> None:
    result, models, _ = _run(
        tmp_path,
        _row(sha="", source="custom", url="https://models.example/success/custom.bin"),
    )
    assert result.returncode == 0
    assert "custom/unverified" in result.stdout
    assert (models / "checkpoints/model.bin").read_bytes() == PAYLOAD


def test_uppercase_civitai_sha_is_normalized_through_parser_manifest_and_downloader(
    tmp_path: Path,
) -> None:
    from utils.comfyui_library import _parse_civitai_response
    from utils.comfyui_manifest_generator import ComfyUIManifestGenerator

    response = {
        "items": [{
            "id": 42,
            "name": "Real shaped Civitai model",
            "stats": {"downloadCount": 7},
            "modelVersions": [{
                "files": [{
                    "primary": True,
                    "name": "model.bin",
                    "downloadUrl": "https://models.example/success/model.bin",
                    "sizeKB": 1,
                    "hashes": {"SHA256": PAYLOAD_SHA.upper()},
                }]
            }],
        }]
    }
    entries = _parse_civitai_response(response, category="checkpoint")
    assert entries[0].sha256 == PAYLOAD_SHA

    manifest, models, _, _, env = _setup(tmp_path, _row())
    ComfyUIManifestGenerator({})._write_tsv(entries, manifest)
    assert f"\t{PAYLOAD_SHA}\t" in manifest.read_text(encoding="utf-8")
    result = subprocess.run(
        ["sh", str(SCRIPT)], env=env, text=True, capture_output=True, timeout=10, check=False
    )
    assert result.returncode == 0
    assert (models / "checkpoints/model.bin").read_bytes() == PAYLOAD


def _status_fields(models: Path) -> list[str]:
    status = models / ".atlas-model-provisioning.tsv"
    assert status.is_file()
    return status.read_text(encoding="utf-8").strip().split("\t")


def test_required_download_failure_writes_failed_plan_status(tmp_path: Path) -> None:
    result, models, _ = _run(
        tmp_path,
        _row(
            url="https://huggingface.co/example/models/resolve/"
            "0123456789abcdef0123456789abcdef01234567/failure/model.bin",
            provisioning="required",
        ),
    )

    assert result.returncode != 0
    fields = _status_fields(models)
    assert fields[0] == "v1"
    assert fields[2:] == ["failed", "1", "0"]


def test_optional_download_failure_warns_but_plan_is_ready(tmp_path: Path) -> None:
    result, models, _ = _run(
        tmp_path,
        _row(
            url="https://models.example/failure/optional.bin",
            sha="",
            source="custom",
            provisioning="optional",
        ),
    )

    assert result.returncode == 0
    assert "optional" in result.stdout.lower()
    assert _status_fields(models)[2:] == ["ready", "0", "1"]


def test_required_download_retry_replaces_failed_status_atomically(tmp_path: Path) -> None:
    manifest, models, _, _, env = _setup(
        tmp_path,
        _row(
            url="https://huggingface.co/example/models/resolve/"
            "0123456789abcdef0123456789abcdef01234567/failure/model.bin",
            provisioning="required",
        ),
    )
    first = subprocess.run(
        ["sh", str(SCRIPT)], env=env, text=True, capture_output=True, timeout=10, check=False
    )
    assert first.returncode != 0
    assert _status_fields(models)[2] == "failed"

    manifest.write_text(_row(provisioning="required") + "\n", encoding="utf-8")
    second = subprocess.run(
        ["sh", str(SCRIPT)], env=env, text=True, capture_output=True, timeout=10, check=False
    )
    assert second.returncode == 0
    assert _status_fields(models)[2:] == ["ready", "0", "0"]
    assert not list(models.glob(".atlas-model-provisioning.tsv.*"))


def test_verified_cache_reuse_refreshes_ready_plan_status(tmp_path: Path) -> None:
    _, models, counter, _, env = _setup(tmp_path, _row(provisioning="required"))
    target = models / "checkpoints/model.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(PAYLOAD)

    result = subprocess.run(
        ["sh", str(SCRIPT)], env=env, text=True, capture_output=True, timeout=10, check=False
    )

    assert result.returncode == 0
    assert not counter.exists()
    assert _status_fields(models)[2:] == ["ready", "0", "0"]


def test_empty_model_plan_publishes_digest_bound_ready_status(tmp_path: Path) -> None:
    manifest, models, counter, _, env = _setup(tmp_path, _row())
    manifest.write_text("", encoding="utf-8")

    result = subprocess.run(
        ["sh", str(SCRIPT)], env=env, text=True, capture_output=True, timeout=10, check=False
    )

    assert result.returncode == 0
    fields = _status_fields(models)
    assert fields[1] == hashlib.sha256(b"").hexdigest()
    assert fields[2:] == ["ready", "0", "0"]
    assert not counter.exists()
