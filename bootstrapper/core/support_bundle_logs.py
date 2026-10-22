"""Bounded immutable diagnostic log sources and source-boundary handling."""
from __future__ import annotations

from dataclasses import dataclass
import re

RAW_SNAPSHOT_BYTES = 1024 * 1024
CONTEXT_BYTES = 64 * 1024
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_KEY_BODY = re.compile(r"[A-Za-z0-9+/=]{16,}\r?\Z")
_KEY_CONTINUATION = re.compile(r"[A-Za-z0-9+/=]+\r?\Z")


@dataclass(frozen=True)
class LogSnapshot:
    """Raw logical payload captured while the source still owns its files."""

    data: bytes = b""
    original_bytes: int = 0
    segment_indexes: tuple[int, ...] = ()
    history_omitted: bool = False
    head_cut: bool = False
    error: str | None = None
    cutoff: str = "failure-export-request"


def read_source(source, limit):
    """Bound every path read even when a file grows after stat."""
    if source.snapshot is not None:
        return source.snapshot
    budget = min(RAW_SNAPSHOT_BYTES, max(0, limit) + CONTEXT_BYTES)
    if source.path is None:
        data, total = _text_tail(source.text or "", budget)
    else:
        total = source.path.stat().st_size
        with source.path.open("rb") as handle:
            handle.seek(max(0, total - budget))
            data = handle.read(min(budget, total))
    return LogSnapshot(data=data, original_bytes=total, head_cut=len(data) < total,
                       cutoff="source-read")


def _text_tail(text, budget):
    # Avoid duplicating an entire in-memory transcript into bytes.
    total = sum(len(text[i:i + 16384].encode("utf-8", errors="replace"))
                for i in range(0, len(text), 16384))
    chunks, position, remaining = [], len(text), budget
    while position and remaining:
        start = max(0, position - min(remaining, 16384))
        chunk = text[start:position].encode("utf-8", errors="replace")[-remaining:]
        chunks.append(chunk)
        remaining -= len(chunk)
        position = start
    return b"".join(reversed(chunks)), total


def replace_at_boundary(pattern, replacement, text, start):
    """Carry an excluded source head through a shrinking redaction pass."""
    parts, previous, adjusted = [], 0, start
    for match in pattern.finditer(text):
        value = match.expand(replacement)
        parts.extend((text[previous:match.start()], value))
        if match.end() <= start:
            adjusted += len(value) - (match.end() - match.start())
        elif match.start() < start:
            adjusted += len(value) - (start - match.start())
        previous = match.end()
    parts.append(text[previous:])
    return "".join(parts), adjusted


def _safe_start(text, values, limit):
    if any(len(value.encode("utf-8")) > CONTEXT_BYTES for value in values):
        return 0, "known-value safety context exceeds the bounded context limit"
    known = max((len(value.encode("utf-8")) for value in values), default=0)
    data = text.encode("utf-8")
    context = min(CONTEXT_BYTES, max(known, len(data) - max(0, limit)))
    start = len(data[:context].decode("utf-8", errors="ignore"))
    newline = text.find("\n", start)
    if newline < 0:
        return 0, "no complete safe line after the bounded source cutoff"
    # A cut can land inside a PEM body with neither delimiter in the window.
    # Include the discarded context line when recognizing a body run, then
    # suppress its short final continuation before permitting diagnostics.
    line_start = text.rfind("\n", 0, start) + 1
    in_key_body = bool(_KEY_BODY.fullmatch(text[line_start:newline]))
    start = newline + 1
    for line in text[start:].splitlines(keepends=True):
        body = line.rstrip("\n")
        if not (_KEY_BODY.fullmatch(body) or in_key_body and _KEY_CONTINUATION.fullmatch(body)):
            break
        in_key_body = True
        start += len(line)
    return start, None


def tail_text(text, limit):
    data = text.encode("utf-8")
    if len(data) <= max(0, limit):
        return text
    data = data[-limit:] if limit > 0 else b""
    if b"\n" in data:
        data = data[data.find(b"\n") + 1:]
    return data.decode("utf-8", errors="ignore")


def log_entry(source, limits, redactor):
    """Redact the logical window before its one final exported tail cap."""
    name = redactor.text(source.name)
    try:
        snapshot = read_source(source, limits.max_log_bytes)
    except OSError as exc:
        return {"name": name, "available": False, "error": redactor.text(str(exc))}
    metadata = {"name": name, "original_bytes": snapshot.original_bytes,
                "history_omitted": snapshot.history_omitted,
                "source_head_cut": snapshot.head_cut, "raw_read_bytes": len(snapshot.data),
                "segments": list(snapshot.segment_indexes), "cutoff": snapshot.cutoff}
    if snapshot.error:
        return {**metadata, "available": False, "truncated": True,
                "error": redactor.text(snapshot.error)}
    text = ANSI.sub("", snapshot.data.decode("utf-8", errors="replace"))
    start, reason = _safe_start(text, redactor._values, limits.max_log_bytes) if snapshot.head_cut else (0, None)
    if reason:
        return {**metadata, "available": False, "truncated": True, "error": reason}
    text, start = redactor.window(text, start)
    safe_text = text[start:]
    text = tail_text(safe_text, limits.max_log_bytes)
    return {**metadata, "available": True, "kept_bytes": len(text.encode("utf-8")),
            "truncated": snapshot.head_cut or snapshot.history_omitted or text != safe_text,
            "safety_head_omitted": bool(start), "text": text}
