"""Redacted, versioned doctor support bundle (#1057).

``./start.sh doctor --bundle PATH`` and ``./start.sh --support-bundle PATH``
(on a failed start, in the Textual app and under ``--no-tui``) package the
doctor checks, the effective configuration with the file that set each key,
and a log excerpt into one local ``.tar.gz`` a user can attach to an issue.

What goes in is an allowlist (``ALLOWLIST`` below, documented in
docs/operations/index.md §4.1): check id/status/message, findings with their
corrective action, configuration keys matching ``CONFIG_KEY_PATTERNS`` and
the redacted log tails. Check ``details`` payloads and every other
configuration key are omitted, and counted, unless the caller opts in.

Redaction is best-effort, applied to every string before it is written:
known secret values from the environment, URL credentials, auth headers,
``KEY=value`` assignments whose key names a secret, PEM private keys and
common token shapes. Nothing is sent anywhere: collection blocks any socket
connection that would leave the machine, and the user sees a preview before
the file is written.
"""

from __future__ import annotations

import datetime
import gzip
import io
import json
import os
import platform
import re
import socket
import tarfile
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator
from urllib.parse import quote

SCHEMA_VERSION = "atlas-support-bundle/1"
REDACTED = "[REDACTED]"
ARCHIVE_ROOT = "atlas-support-bundle"

#: Configuration keys the default bundle includes. Everything else is omitted
#: (and counted) unless the caller opts in with ``include_unlisted``.
CONFIG_KEY_PATTERNS = (
    re.compile(r"^[A-Z0-9_]+_SOURCE$"),
    re.compile(r"^(?:BASE_PORT|[A-Z0-9_]+_PORT)$"),
    re.compile(r"^[A-Z0-9_]*MODELS?$"),
    re.compile(r"^(?:PROJECT_NAME|ATLAS_PROFILE_APPLIED|COMPOSE_PROFILES|HOST_BIND_IP)$"),
)
#: Fields of a check result the default bundle keeps; ``details`` is opt-in.
CHECK_FIELDS = ("id", "status", "message")
ALLOWLIST = {
    "checks": list(CHECK_FIELDS),
    "config": [pattern.pattern for pattern in CONFIG_KEY_PATTERNS],
    "logs": "redacted tail of each log source, capped per file",
}

_SECRET_NAME = re.compile(
    r"(PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|ACCESS_KEY|PRIVATE_KEY|"
    r"CREDENTIAL|SALT|JWT|COOKIE|_KEY$|^KEY$|_AUTH$)",
    re.IGNORECASE,
)
_MIN_SECRET_LEN = 6
_SECRET_WORD = (
    r"[A-Za-z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key"
    r"|private[_-]?key|credential|salt)[A-Za-z0-9_.-]*"
)
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.DOTALL,
    ), REDACTED),
    (re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s@]+@"), rf"\1{REDACTED}@"),
    (re.compile(
        r"(?im)^(\s*(?:proxy-)?(?:authorization|x-api-key|api-key|apikey|x-auth-token|"
        r"cookie|set-cookie)\s*:\s*).+$"
    ), rf"\1{REDACTED}"),
    (re.compile(r"(?i)\b(bearer|basic)(\s+)[A-Za-z0-9._~+/=-]{8,}"), rf"\1\2{REDACTED}"),
    (re.compile(rf"(?i)([?&]{_SECRET_WORD}=)[^&\s#\"']+"), rf"\1{REDACTED}"),
    (re.compile(rf"(?i)(\"{_SECRET_WORD}\"\s*:\s*)\"[^\"]*\""), rf'\1"{REDACTED}"'),
    (re.compile(rf"(?i)\b({_SECRET_WORD}\s*[=:]\s*)(['\"]?)[^\s'\",;]+"), rf"\1\2{REDACTED}"),
    (re.compile(
        r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
        r"|hf_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}"
        r"|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})"
    ), REDACTED),
)


def is_secret_name(key: str) -> bool:
    return bool(_SECRET_NAME.search(key))


class Redactor:
    """Best-effort secret scrubbing for text and nested values."""

    def __init__(self, env: dict[str, str] | None = None) -> None:
        values: set[str] = set()
        for key, value in (env or {}).items():
            text = str(value or "").strip()
            if is_secret_name(key) and len(text) >= _MIN_SECRET_LEN:
                values.update({text, quote(text, safe="")})
        self._values = sorted(values, key=len, reverse=True)

    def text(self, value: str) -> str:
        for secret in self._values:
            value = value.replace(secret, REDACTED)
        for pattern, replacement in _PATTERNS:
            value = pattern.sub(replacement, value)
        return value

    def value(self, value: Any, key: str = "") -> Any:
        if key and is_secret_name(key) and value not in (None, "", [], {}):
            return REDACTED
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {str(k): self.value(v, str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.value(item) for item in value]
        return value


@dataclass(frozen=True)
class BundleLimits:
    """Caps that keep collection bounded in size and wall time."""

    max_log_bytes: int = 256 * 1024
    max_field_chars: int = 8 * 1024
    max_seconds: float = 60.0


@dataclass(frozen=True)
class LogSource:
    name: str
    text: str | None = None
    path: Path | None = None


@dataclass(frozen=True)
class SupportBundle:
    """``body`` becomes bundle.json; each ``logs`` entry a logs/<name> file."""

    body: dict[str, Any]
    logs: dict[str, str]


@dataclass(frozen=True)
class BundleOptions:
    """What a caller adds to the doctor checks: context, logs and opt-ins."""

    context: dict[str, Any] = field(default_factory=dict)
    logs: tuple[LogSource, ...] = ()
    include_unlisted: bool = False
    limits: BundleLimits = field(default_factory=BundleLimits)


@dataclass
class BundleRequest:
    """Everything a bundle is built from, gathered by the caller."""

    env: dict[str, str]
    env_origins: dict[str, str] = field(default_factory=dict)
    env_file: Path | None = None
    checks: list[dict] = field(default_factory=list)
    logs: list[LogSource] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    include_unlisted: bool = False
    limits: BundleLimits = field(default_factory=BundleLimits)
    notes: list[str] = field(default_factory=list)


# ─── collection ──────────────────────────────────────────────────────────


def _is_local_address(address: Any) -> bool:
    if not isinstance(address, tuple):  # AF_UNIX path (e.g. the Docker socket)
        return True
    host = str(address[0])
    return host in ("localhost", "::1") or host.startswith("127.")


@contextmanager
def offline() -> Iterator[None]:
    """Refuse every socket connection that would leave this machine.

    Loopback and Unix sockets stay usable, so checks that read a local
    daemon still run; anything else raises and the check reports it.
    """
    original = socket.socket.connect, socket.socket.connect_ex

    def guard(method: Callable) -> Callable:
        def connect(sock: socket.socket, address: Any) -> Any:
            if not _is_local_address(address):
                raise OSError(
                    f"support-bundle collection is offline; refused {address!r}"
                )
            return method(sock, address)
        return connect

    socket.socket.connect, socket.socket.connect_ex = map(guard, original)
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex = original


def check_id(check: Callable) -> str:
    name = getattr(check, "__name__", "check")
    return name.removeprefix("_doctor_check_").replace("_", "-")


def _unavailable(check: Callable, reason: str) -> dict:
    return {"id": check_id(check), "status": "unavailable", "message": reason, "details": {}}


def _run_one(check: Callable, argument: Any, timeout: float) -> dict:
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["result"] = check(argument)
        except BaseException as exc:  # noqa: BLE001 - recorded, never raised
            box["error"] = exc

    worker = threading.Thread(target=target, name=f"bundle-{check_id(check)}", daemon=True)
    worker.start()
    worker.join(max(timeout, 0.0))
    if worker.is_alive():
        return _unavailable(check, f"did not finish within the {timeout:.0f}s left of the budget")
    if "error" in box:
        error = box["error"]
        return _unavailable(check, f"could not run: {type(error).__name__}: {error}")
    return box["result"]


def run_checks(
    checks: Iterable[Callable], argument: Any, limits: BundleLimits,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[list[dict], list[str]]:
    """Run each check offline inside one wall-time budget.

    A check that raises, hangs past the budget, or never starts because the
    budget is spent is recorded as ``unavailable``, never dropped.
    """
    deadline = clock() + limits.max_seconds
    results: list[dict] = []
    notes: list[str] = []
    with offline():
        for check in checks:
            left = deadline - clock()
            if left <= 0:
                results.append(_unavailable(check, "not run: the collection time budget was spent"))
                continue
            results.append(_run_one(check, argument, left))
    skipped = [r["id"] for r in results if r["message"].startswith("not run:")]
    if skipped:
        notes.append(
            f"time budget of {limits.max_seconds:.0f}s reached; "
            f"{len(skipped)} check(s) not run: {', '.join(skipped)}"
        )
    return results, notes


# ─── building ────────────────────────────────────────────────────────────


def _listed(key: str) -> bool:
    return any(pattern.match(key) for pattern in CONFIG_KEY_PATTERNS)


def _action(key: str, origin: str, env_file: Path | None) -> str:
    if origin.endswith((".yml", ".yaml")):
        field_name = "project_name" if key == "PROJECT_NAME" else f"env.values.{key}"
        return (f"Change `{field_name}` in {origin}. Every start writes the manifest's "
                "values into .env, so an edit to .env alone does not stick.")
    if origin == "default":
        return f"Set {key} in .env; it currently takes the .env.example default."
    if env_file is not None and origin == str(env_file):
        return f"Change {key} in {origin} (or pass the matching ./start.sh flag), then restart."
    return f"Change {key} in {origin}, the consumer env file that sets it, then restart."


def _origin(key: str, request: BundleRequest, file_keys: set[str]) -> str:
    if key in request.env_origins:
        return request.env_origins[key]
    if request.env_file is not None and key in file_keys:
        return str(request.env_file)
    return "default"


def _file_keys(env_file: Path | None) -> set[str]:
    if env_file is None or not env_file.is_file():
        return set()
    keys = set()
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        name, sep, _ = line.strip().partition("=")
        if sep and not name.startswith("#"):
            keys.add(name.strip())
    return keys


def config_entries(request: BundleRequest, redactor: Redactor) -> tuple[list[dict], int]:
    file_keys = _file_keys(request.env_file)
    entries, omitted = [], 0
    for key in sorted(request.env):
        if not (_listed(key) or request.include_unlisted):
            omitted += 1
            continue
        origin = _origin(key, request, file_keys)
        entries.append({
            "key": key,
            "value": redactor.value(str(request.env[key]), key),
            "origin": origin,
            "action": _action(key, origin, request.env_file),
        })
    return entries, omitted


def _related(check: dict, entries: list[dict]) -> list[dict]:
    text = f"{check.get('message', '')} {json.dumps(check.get('details') or {}, default=str)}"
    return [entry for entry in entries if re.search(rf"\b{re.escape(entry['key'])}\b", text)]


def _check_entry(check: dict, request: BundleRequest, redactor: Redactor) -> dict:
    status = str(check.get("status", "unavailable"))
    entry = {name: redactor.value(check.get(name, "")) for name in CHECK_FIELDS}
    entry["available"] = status not in ("skipped", "unavailable")
    if request.include_unlisted:
        entry["details"] = redactor.value(check.get("details") or {})
    return entry


def _clip(value: Any, limit: int, notes: list[str], where: str) -> Any:
    if isinstance(value, str) and len(value) > limit:
        notes.append(f"{where}: cut to {limit} of {len(value)} characters")
        return value[:limit] + " … [truncated]"
    if isinstance(value, dict):
        return {k: _clip(v, limit, notes, f"{where}.{k}") for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v, limit, notes, f"{where}[{i}]") for i, v in enumerate(value)]
    return value


def _read_tail(source: LogSource, limit: int) -> tuple[str, int]:
    """Up to ``limit`` + a margin of trailing bytes, cut at a line boundary."""
    if source.path is None:
        data = (source.text or "").encode("utf-8", errors="replace")
        total = len(data)
        data = data[-(limit + 4096):]
    else:
        total = source.path.stat().st_size
        with source.path.open("rb") as handle:
            handle.seek(max(0, total - limit - 4096))
            data = handle.read()
    if len(data) < total and b"\n" in data:
        data = data[data.index(b"\n") + 1:]
    return data.decode("utf-8", errors="replace"), total


def log_entry(source: LogSource, limits: BundleLimits, redactor: Redactor) -> dict:
    """A redacted log tail. Redaction runs before the cut, so a secret is
    never split into an unrecognisable fragment by the size cap."""
    try:
        text, total = _read_tail(source, limits.max_log_bytes)
    except OSError as exc:
        return {"name": source.name, "available": False, "error": redactor.text(str(exc))}
    text = redactor.text(text)
    data = text.encode("utf-8")
    truncated = total > len(data) or len(data) > limits.max_log_bytes
    if len(data) > limits.max_log_bytes:
        data = data[-limits.max_log_bytes:]
        data = data[data.find(b"\n") + 1:] if b"\n" in data else data
        text = data.decode("utf-8", errors="ignore")
    return {
        "name": source.name, "available": True, "original_bytes": total,
        "kept_bytes": len(text.encode("utf-8")), "truncated": truncated, "text": text,
    }


def _findings(raw_checks: list[dict], checks: list[dict], config: list[dict]) -> list[dict]:
    """Failing and warning checks, each with the configuration it names."""
    return [
        {**entry, "keys": _related(raw, config)}
        for raw, entry in zip(raw_checks, checks)
        if entry["status"] in ("fail", "warn")
    ]


def _omitted(request: BundleRequest, config_keys: int, checks: int) -> dict[str, Any]:
    if request.include_unlisted:
        return {"config_keys": 0, "check_details": 0, "opt_in": None}
    return {"config_keys": config_keys, "check_details": checks, "opt_in": "--include-unlisted"}


def _log_section(request: BundleRequest, redactor: Redactor) -> tuple[list[dict], list[str]]:
    logs = [log_entry(source, request.limits, redactor) for source in request.logs]
    notes = [
        f"log {log['name']}: kept the last {log['kept_bytes']} of {log['original_bytes']} bytes"
        for log in logs if log.get("truncated")
    ]
    return logs, notes


def _timestamp(now: datetime.datetime | None) -> str:
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    return moment.isoformat(timespec="seconds")


def build_bundle(request: BundleRequest, *, now: datetime.datetime | None = None) -> SupportBundle:
    """Assemble the redacted bundle. Secrets known to the process environment
    are scrubbed as well as those in ``request.env``."""
    redactor = Redactor({**os.environ, **request.env})
    notes = list(request.notes)
    config, omitted_keys = config_entries(request, redactor)
    checks = [_check_entry(check, request, redactor) for check in request.checks]
    logs, log_notes = _log_section(request, redactor)
    notes += log_notes
    bundle = {
        "schema": SCHEMA_VERSION,
        "generated_at": _timestamp(now),
        "redaction": "best-effort; review the preview before sharing",
        "context": redactor.value(request.context),
        "host": {"system": platform.system(), "machine": platform.machine(),
                 "python": platform.python_version()},
        "ok": not any(check["status"] == "fail" for check in checks),
        "checks": checks,
        "findings": _findings(request.checks, checks, config),
        "config": config,
        "omitted": _omitted(request, omitted_keys, len(checks)),
        "allowlist": ALLOWLIST,
        "logs": [{k: v for k, v in log.items() if k != "text"} for log in logs],
    }
    bundle = _clip(bundle, request.limits.max_field_chars, notes, "bundle")
    bundle["truncation"] = notes
    texts = {log["name"]: log["text"] for log in logs if log.get("available")}
    return SupportBundle(body=bundle, logs=texts)


# ─── preview + archive ───────────────────────────────────────────────────


def _members(bundle: SupportBundle) -> list[tuple[str, bytes]]:
    members = [(f"{ARCHIVE_ROOT}/bundle.json",
                (json.dumps(bundle.body, indent=2, sort_keys=True) + "\n").encode("utf-8"))]
    for name, text in sorted(bundle.logs.items()):
        members.append((f"{ARCHIVE_ROOT}/logs/{_safe_name(name)}", text.encode("utf-8")))
    return members


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or "log"


def preview_lines(bundle: SupportBundle) -> list[str]:
    """Every file the archive will hold, in full, shown before it is written."""
    lines = [f"Support bundle preview ({bundle.body['schema']}): the archive will "
             "contain exactly these files."]
    for name, data in _members(bundle):
        lines.append(f"── {name} ({len(data)} bytes) ──")
        lines.extend(data.decode("utf-8").splitlines())
    lines.append("── end of preview ── Redaction is best-effort: read it before you share the file.")
    return lines


def archive_bytes(bundle: SupportBundle) -> bytes:
    """Deterministic ``.tar.gz``: no user, host, or original file names."""
    stamp = int(datetime.datetime.fromisoformat(bundle.body["generated_at"]).timestamp())
    raw = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=stamp) as zipped:
        with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for name, data in _members(bundle):
                info = tarfile.TarInfo(name)
                info.size, info.mtime, info.mode = len(data), stamp, 0o600
                info.uid = info.gid = 0
                info.uname = info.gname = "atlas"
                tar.addfile(info, io.BytesIO(data))
    return raw.getvalue()


def export(bundle: SupportBundle, destination: Path, echo: Callable[[str], None]) -> Path:
    """Show the full preview through ``echo``, then write the archive."""
    for line in preview_lines(bundle):
        echo(line)
    path = write_bundle(bundle, destination)
    echo(
        f"📦 Wrote support bundle {path} ({path.stat().st_size} bytes, owner-only). "
        "Nothing was sent anywhere; attach it yourself after reading the preview."
    )
    return path


def write_bundle(bundle: SupportBundle, destination: Path) -> Path:
    """Write the archive owner-only, replacing ``destination`` atomically."""
    path = Path(destination).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(archive_bytes(bundle))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path


# ─── --no-tui transcript ─────────────────────────────────────────────────


class Transcript:
    """Bounded tail of what the linear flow printed, for its bundle log.

    Output still reaches the terminal unchanged; this only keeps the last
    ``max_bytes`` of it. Subprocess output written straight to the terminal
    file descriptors (for example Docker Compose progress) is not seen.
    """

    def __init__(self, max_bytes: int = 1024 * 1024) -> None:
        self.max_bytes = max_bytes
        self._chunks: list[str] = []
        self._size = 0
        self._lock = threading.Lock()

    def write(self, text: str) -> None:
        with self._lock:
            self._chunks.append(text)
            self._size += len(text)
            while self._size > self.max_bytes and len(self._chunks) > 1:
                self._size -= len(self._chunks.pop(0))

    def text(self) -> str:
        with self._lock:
            return "".join(self._chunks)[-self.max_bytes:]

    @contextmanager
    def capture(self) -> Iterator["Transcript"]:
        import sys

        streams = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = (_Tee(stream, self) for stream in streams)
        try:
            yield self
        finally:
            sys.stdout, sys.stderr = streams


class _Tee:
    def __init__(self, stream: Any, sink: Transcript) -> None:
        self._stream = stream
        self._sink = sink

    def write(self, text: str) -> int:
        self._sink.write(text)
        return self._stream.write(text)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)
