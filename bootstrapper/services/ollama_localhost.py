"""Host-side Ollama model provisioning for ``ollama-localhost`` (#757).

The container sources pull declared models via the ``ollama-pull`` init
container (``services/ollama/pull/scripts/pull.sh``); the ``ollama-localhost``
source pointed consumers at a host daemon but pulled nothing — the operator
had to ``ollama pull`` every declared tag by hand. This module is the host
analog of ``pull.sh``: the same declared set (``OLLAMA_USER_MODELS`` ∪
``OLLAMA_CUSTOM_MODELS``, so ``model_sidecars.ollama`` declarations provision
identically across sources), the same ``POST /api/pull`` mechanism (Ollama
verifies layers natively, so re-pulls of present tags are cheap no-ops and
interrupted pulls resume on layer boundaries), and the same non-fatal
per-model philosophy (a typo'd tag never aborts a stack launch).

Note on reproducibility: Ollama tags (``:latest``) are not checksum-pinned —
provisioning converges every machine on the same *tags*, not byte-identical
blobs. Pin exact tags (``qwen3.6:q4_K_M``-style) for tighter parity.

Everything network goes through thin stdlib urllib calls so the module is
fully unit-testable with mocks on CI (no host daemon required).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Mapping

_DEFAULT_PORT = "11434"
_TAGS_TIMEOUT = 5.0
_PULL_TIMEOUT = 3600.0  # one tag can be tens of GB; per-request ceiling


@dataclass
class OllamaPullResult:
    """Outcome of a host pull run. ``reachable=False`` means the daemon
    could not be queried at all (nothing was attempted)."""

    reachable: bool = True
    pulled: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.reachable and not self.failed

    def to_dict(self) -> dict:
        return {
            "reachable": self.reachable,
            "pulled": list(self.pulled),
            "skipped": list(self.skipped),
            "failed": list(self.failed),
            "ok": self.ok,
        }


def declared_models(env: Mapping[str, str]) -> list[str]:
    """The declared tag set — ordered union of OLLAMA_USER_MODELS and
    OLLAMA_CUSTOM_MODELS, exactly the list the container ``pull.sh`` builds
    (consumer ``model_sidecars.ollama`` lands in OLLAMA_CUSTOM_MODELS)."""
    seen: set[str] = set()
    ordered: list[str] = []
    for var in ("OLLAMA_USER_MODELS", "OLLAMA_CUSTOM_MODELS"):
        for raw in (env.get(var, "") or "").split(","):
            name = raw.strip()
            if name and name not in seen:
                seen.add(name)
                ordered.append(name)
    return ordered


def host_base_url(env: Mapping[str, str]) -> str:
    """The host daemon as seen FROM the host (the bootstrapper runs there) —
    ``localhost:$OLLAMA_LOCALHOST_PORT``, not the in-network
    ``host.docker.internal`` form containers use."""
    port = (env.get("OLLAMA_LOCALHOST_PORT", "") or "").strip() or _DEFAULT_PORT
    if not port.isdigit():  # a malformed port must degrade, never crash (#757)
        port = _DEFAULT_PORT
    return f"http://localhost:{port}"


def _normalize(tag: str) -> str:
    """Ollama treats ``name`` and ``name:latest`` as the same tag."""
    return tag if ":" in tag else f"{tag}:latest"


def list_host_tags(base_url: str, *, timeout: float = _TAGS_TIMEOUT) -> set[str] | None:
    """Tags present on the host daemon (normalized), or None when unreachable."""
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 — URLError/OSError/ValueError/InvalidURL:
        # any query failure means "cannot see the daemon"; callers treat None
        # as unreachable and warn. Provisioning is strictly non-fatal.
        return None
    tags: set[str] = set()
    for model in payload.get("models") or []:
        name = str(model.get("name") or model.get("model") or "").strip()
        if name:
            tags.add(_normalize(name))
    return tags


def _pull_one(base_url: str, tag: str, *, log, timeout: float = _PULL_TIMEOUT) -> None:
    """POST /api/pull, streaming NDJSON status lines. Raises on failure.

    Progress is coarsened to status transitions (Ollama emits a line per
    chunk; re-logging each would flood the launch log)."""
    request = urllib.request.Request(
        f"{base_url}/api/pull",
        data=json.dumps({"model": tag}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    last_status = ""
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if "error" in event:
                raise RuntimeError(str(event["error"]))
            status = str(event.get("status") or "")
            if status and status != last_status:
                last_status = status
                log(f"  {tag}: {status}")


def pull_declared_models(env: Mapping[str, str], *, log=None) -> OllamaPullResult:
    """Provision the declared tag set onto the host daemon.

    Present tags (via /api/tags) are skipped; missing ones are pulled with
    streamed progress; per-tag failures are collected, never raised. An
    unreachable daemon short-circuits with ``reachable=False`` — the caller
    decides how loudly to surface that (the daemon is user-run)."""
    emit = log or (lambda message: None)
    result = OllamaPullResult()
    declared = declared_models(env)
    if not declared:
        return result

    base_url = host_base_url(env)
    present = list_host_tags(base_url)
    if present is None:
        result.reachable = False
        return result

    for tag in declared:
        if _normalize(tag) in present:
            result.skipped.append(tag)
            emit(f"✔ {tag} (already present on host, skipped)")
            continue
        try:
            _pull_one(base_url, tag, log=emit)
        except Exception as exc:  # noqa: BLE001 — per-tag isolation
            result.failed.append(f"{tag}: {exc}")
            emit(f"✗ {tag} failed: {exc}")
            continue
        result.pulled.append(tag)
        emit(f"✓ {tag} pulled")
    return result


# ─── host parallel-serving configuration (#849 part 2) ───────────────
#
# For ``ollama-localhost`` Atlas does not own the daemon (host-prereq
# doctrine), so it cannot SET these — but it can read them back and say
# when the host is provisioned below what a consumer declared it needs.
# Ollama's default is ONE parallel slot, which silently serializes a
# multi-agent consumer instead of failing, so an unnoticed default is the
# expensive case this probe exists to catch.

_PARALLEL_VARS = ("OLLAMA_NUM_PARALLEL", "OLLAMA_MAX_LOADED_MODELS")


def host_parallel_config(
    *, runner=None, platform_name: str | None = None
) -> dict[str, int | None]:
    """Best-effort read of the host daemon's parallel-serving settings.

    Returns a dict keyed by env-var name; a value of ``None`` means "could
    not determine", which is deliberately distinct from a real 0/1 — the
    caller must not warn on an unknown.

    macOS is the verified path: the daemon inherits ``launchctl setenv``,
    and the reporter on #849 confirmed the round trip (server.log logs
    ``OLLAMA_NUM_PARALLEL:8`` after a restart). Elsewhere the daemon's
    environment depends on how it was started — systemd drop-in, shell
    export, container — with no single readable source, so this returns
    unknown rather than guessing wrong and emitting a false warning.

    ``runner``/``platform_name`` are injected by tests; nothing here shells
    out when a runner is supplied.
    """
    import platform as _platform
    import subprocess

    system = platform_name if platform_name is not None else _platform.system()
    result: dict[str, int | None] = {name: None for name in _PARALLEL_VARS}
    if system != "Darwin":
        return result

    def _default_runner(args: list[str]) -> str | None:
        try:
            proc = subprocess.run(
                args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5, check=False
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return proc.stdout if proc.returncode == 0 else None

    run = runner or _default_runner
    for name in _PARALLEL_VARS:
        raw = run(["launchctl", "getenv", name])
        if raw is None:
            continue
        raw = raw.strip()
        if not raw:
            continue
        try:
            result[name] = int(raw)
        except ValueError:
            # A non-numeric value is as good as unset for our purposes;
            # the daemon would ignore it too.
            continue
    return result


def parallel_shortfall(
    declared_min: int, observed: dict[str, int | None]
) -> tuple[bool, str]:
    """Compare a declared minimum against the observed host config.

    Returns ``(is_shortfall, explanation)``. Unknown observations never
    report a shortfall — warning about a value we failed to read would
    train people to ignore the check.
    """
    current = observed.get("OLLAMA_NUM_PARALLEL")
    if current is None:
        return False, (
            "could not read the host daemon's OLLAMA_NUM_PARALLEL "
            "(only macOS launchctl is supported); skipping the comparison"
        )
    if current >= declared_min:
        return False, (
            f"host OLLAMA_NUM_PARALLEL={current} meets the declared "
            f"minimum of {declared_min}"
        )
    return True, (
        f"host OLLAMA_NUM_PARALLEL={current} is below the declared minimum "
        f"of {declared_min}; {declared_min} concurrent requests will be "
        f"serialized. Fix with: launchctl setenv OLLAMA_NUM_PARALLEL "
        f"{declared_min} && osascript -e 'quit app \"Ollama\"' "
        "then relaunch Ollama."
    )


# ─── host residency configuration (#798) ─────────────────────────────
#
# Separate from the parallel probe above because ``OLLAMA_KEEP_ALIVE`` is a
# DURATION STRING ("5m", "1h", "-1" for forever), not an int — feeding it
# through the int-coercing reader would silently report it as unknown.

_KEEP_ALIVE_VAR = "OLLAMA_KEEP_ALIVE"


def host_keep_alive(*, runner=None, platform_name: str | None = None) -> str | None:
    """Best-effort read of the host daemon's ``OLLAMA_KEEP_ALIVE``.

    Returns the raw string (e.g. ``"5m"``, ``"-1"``) or None when it cannot
    be determined. Same macOS-only scoping and same unknown-never-warns rule
    as :func:`host_parallel_config`; see its docstring for why.
    """
    import platform as _platform
    import subprocess

    system = platform_name if platform_name is not None else _platform.system()
    if system != "Darwin":
        return None

    def _default_runner(args: list[str]) -> str | None:
        try:
            proc = subprocess.run(
                args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5, check=False
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return proc.stdout if proc.returncode == 0 else None

    raw = (runner or _default_runner)(["launchctl", "getenv", _KEEP_ALIVE_VAR])
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def residency_shortfall(
    declared_min: int, observed: dict[str, int | None], keep_alive: str | None
) -> tuple[bool, str]:
    """Compare a declared resident-model floor against the host's config.

    A multi-model ingest (extract + embed + keyword, often different models)
    evicts its own working set when ``OLLAMA_MAX_LOADED_MODELS`` is below the
    number of models the run touches: Ollama unloads one to load the next and
    reloads it moments later. The symptom is reload thrash, not an error —
    the run just crawls, which is why it needs a check rather than a log line.

    Unknown observations never report a shortfall, for the same reason as
    :func:`parallel_shortfall`: warning about a value we failed to read
    trains people to ignore the check.
    """
    max_loaded = observed.get("OLLAMA_MAX_LOADED_MODELS")
    if max_loaded is None:
        return False, (
            "could not read the host daemon's OLLAMA_MAX_LOADED_MODELS "
            "(only macOS launchctl is supported); skipping the comparison"
        )
    if max_loaded >= declared_min:
        detail = (
            f"host OLLAMA_MAX_LOADED_MODELS={max_loaded} meets the declared "
            f"minimum of {declared_min}"
        )
        # keep_alive only matters once residency is otherwise sufficient:
        # enough slots but a short TTL still evicts between calls.
        if keep_alive is None:
            return False, detail + "; OLLAMA_KEEP_ALIVE not set (Ollama default is 5m)"
        return False, detail + f"; OLLAMA_KEEP_ALIVE={keep_alive}"
    return True, (
        f"host OLLAMA_MAX_LOADED_MODELS={max_loaded} is below the declared "
        f"minimum of {declared_min}; a run touching {declared_min} models will "
        "evict and reload between calls. Fix with: launchctl setenv "
        f"OLLAMA_MAX_LOADED_MODELS {declared_min} (and consider "
        "OLLAMA_KEEP_ALIVE=-1 for the duration of the run), then restart "
        "Ollama. Note -1 pins every loaded model in RAM until reverted."
    )
