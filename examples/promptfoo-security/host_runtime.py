"""Bounded process and immutable-tree helpers for the optional macOS recipe."""
from pathlib import Path
import hashlib
import json
import os
import selectors
import signal
import subprocess
import time


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def snapshot(root):
    files, links = {}, {}
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in dirs + names:
            path = Path(directory) / name
            if path.is_symlink():
                links[str(path)] = os.readlink(path)
            elif path.is_file():
                files[str(path)] = digest(path)
            elif not path.is_dir():
                raise ValueError("nonregular installed input: " + str(path))
    return {"root": str(root), "files": files, "symlinks": links}


def owned_bytes(root):
    count = 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                count += path.stat().st_size
        except FileNotFoundError:
            continue
    return count


def ready_bytes(selector, streams):
    for key, _ in selector.select(0.05):
        chunk = os.read(key.fd, 4096)
        if not chunk:
            selector.unregister(key.fileobj)
            continue
        target = streams[key.data]
        available = 16384 - len(target)
        target.extend(chunk[:max(0, available)])
        if len(chunk) > available:
            return "stream_limit"
    return None


def monitor(process, context):
    selector = selectors.DefaultSelector()
    streams = {"stdout": bytearray(), "stderr": bytearray()}
    for name, stream in [("stdout", process.stdout), ("stderr", process.stderr)]:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, name)
    started, reason = time.monotonic(), None
    try:
        while selector.get_map() or process.poll() is None:
            if time.monotonic() - started > context["timeout"]:
                reason = "deadline"
            elif owned_bytes(context["base"]) > 8 * 1024 * 1024:
                reason = "owned_output_limit"
            else:
                reason = ready_bytes(selector, streams)
            if reason:
                break
    except Exception as error:
        reason = repr(error)
    finally:
        selector.close()
    return reason, {name: data.decode(errors="replace") for name, data in streams.items()}


def execute(argv, context):
    started = time.monotonic()
    payload = context.get("input")
    process = subprocess.Popen(argv, cwd=context["base"] / "qualification", env=context["env"],
        stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, pass_fds=(), start_new_session=True)
    streams, reason = {}, None
    try:
        if payload is not None:
            assert len(payload) <= 4096
            process.stdin.write(payload)
            process.stdin.close()
        reason, streams = monitor(process, context)
    except Exception as error:
        reason = repr(error)
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        for stream in [process.stdin, process.stdout, process.stderr]:
            if stream is not None:
                stream.close()
    result = {"argv": argv, "pid": process.pid, "exit_code": process.returncode,
              "stop": reason, "elapsed_seconds": time.monotonic() - started, **streams}
    context["stages"].append(result)
    return result


def require_success(row):
    if row["exit_code"] or row["stop"] or row.get("stderr"):
        raise RuntimeError("qualification subprocess failed: " + json.dumps(row))
    return json.loads(row["stdout"])
