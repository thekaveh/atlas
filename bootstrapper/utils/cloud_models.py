"""
Live cloud-provider model discovery for the wizard's multi-select.

One discovery function per provider:

* ``discover_openai_models(api_key)``
* ``discover_anthropic_models(api_key)``
* ``discover_openrouter_models()``    (no auth required)

Each returns a ``DiscoveryResult`` — the filtered ``list[ModelInfo]``
plus a ``state`` saying whether the provider actually answered, and a
short ``detail`` phrase with the API key scrubbed out. The wizard's
options_provider calls these synchronously when the user advances to
the cloud multi-select step (timeout 5s per call). On any failure
(no key, auth, timeout, transport, malformed reply, empty result
post-filter) the caller falls back to the curated ``CLOUD_CATALOG``
from ``llm_catalog.py`` **and says so on screen** — see #1180; the
older shape returned a bare ``[]`` for every failure, so the picker
showed the catalog under a caption claiming a live listing.

``list_openai_models`` / ``list_anthropic_models`` /
``list_openrouter_models`` remain as thin wrappers returning just the
models, for callers that do not need the provenance.

Filtering rationale per provider:

* **OpenAI** — ``/v1/models`` returns 50–150 entries including
  DALL-E, Whisper, TTS, snapshots, fine-tunes, deprecated bases.
  Without filtering the picker is unusable. We use an allow-list of
  prefix patterns + a deny-list to drop noise.
* **Anthropic** — already clean (~5–15 ``claude-*`` entries). We use
  ``display_name`` for the picker label and dedup snapshots that share
  a display name.
* **OpenRouter** — already clean and rich (``id``, ``name``,
  ``description``, ``context_length``). We cap at 50 to keep the
  picker usable; sort alphabetically.

Maintenance cadence
-------------------
The OpenAI allow/deny patterns are the most likely to drift — every
new family from OpenAI (gpt-6, o4, o5, etc.) needs a prefix added to
``_OPENAI_ALLOW_PREFIXES``. Suggested cadence:

  • **At each major OpenAI release**: add new family prefixes to the
    allow-list. Audit ``_OPENAI_DENY_SUBSTRINGS`` for new noise patterns
    (audio, image, deprecated families).
  • **Anthropic / OpenRouter**: rarely need maintenance — Anthropic's
    response shape is stable, OpenRouter's curated catalog handles its
    own filtering. Touch only if the response format changes.

When the upstream catalog drifts faster than we update these filters,
the wizard's worst-case behavior is silently dropping new models from
the picker — the user can still set the model name explicitly via
``OPENAI_USER_MODELS`` (or the relevant ``*_USER_MODELS`` env var).
"""

from __future__ import annotations

import json
import re
import socket
import http.client
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional


# Type alias for the failure-reason logger. The wizard passes its
# ``_safe_log`` adapter so transport / parse / empty-result failures
# end up in the launch log alongside the rest of the wizard's events.
WarnFn = Callable[[str], None]


def _format_url_error(exc: BaseException) -> str:
    """Compress a urllib/socket exception into one human-readable line."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code} {exc.reason}"
    if isinstance(exc, urllib.error.URLError):
        return f"URLError {exc.reason}"
    if isinstance(exc, socket.timeout):
        return "timeout"
    return f"{type(exc).__name__}: {exc}"


def _emit(on_warn: Optional[WarnFn], msg: str) -> None:
    if on_warn is None:
        return
    try:
        on_warn(msg)
    except Exception:
        pass


@dataclass
class ModelInfo:
    """One row in the cloud multi-select picker.

    ``id`` is the canonical model name in the YAML catalog and what
    ``litellm-init`` puts in ``model_list[].model_name`` — stable.
    ``label`` is what the user sees in the wizard. ``description``
    is shown as the row hint.
    """
    id: str
    label: str
    description: str = ""


# ─── Discovery outcome ───────────────────────────────────────────────

# Why a provenance state and not just an empty list (#1180): every
# failure path used to return ``[]``, so the wizard could not tell a
# rejected key from a dropped connection from a provider that simply
# listed nothing usable. It showed the curated catalog captioned "Live
# from /v1/models" either way, and a row appearing in the picker looked
# like proof the key worked. These states are what the caller renders.
LIVE = "live"                   # provider answered; these rows are its own
NO_KEY = "no-key"               # nothing to authenticate with
UNAUTHORIZED = "unauthorized"   # HTTP 401 / 403 — the key was rejected
RATE_LIMITED = "rate-limited"   # HTTP 429
HTTP_ERROR = "http-error"       # any other HTTP status
TIMEOUT = "timeout"             # no answer within the deadline
UNREACHABLE = "unreachable"     # DNS / TLS / connection failure
MALFORMED = "malformed"         # answered, but not the documented shape
EMPTY = "empty"                 # answered, but no model survived filtering

#: Every state other than :data:`LIVE`. Kept as a set so callers can ask
#: "is this a fallback?" without re-listing the names.
FALLBACK_STATES = frozenset({
    NO_KEY, UNAUTHORIZED, RATE_LIMITED, HTTP_ERROR,
    TIMEOUT, UNREACHABLE, MALFORMED, EMPTY,
})


@dataclass
class DiscoveryResult:
    """What one discovery attempt produced.

    ``models`` is non-empty only when ``state`` is :data:`LIVE`; a
    fallback result carries the reason instead of a substitute list, so
    the caller decides what to show and can label it honestly.
    ``detail`` is a short human phrase, already scrubbed of the API key.
    """
    models: list[ModelInfo]
    state: str
    detail: str = ""

    @property
    def is_live(self) -> bool:
        return self.state == LIVE


def _scrub(text: str, secret: str) -> str:
    """Remove an API key from a message before it is shown or logged.

    Nothing in this module interpolates a key into a message on purpose,
    but ``HTTPError.reason`` and ``URLError.reason`` are provider- and
    OS-supplied strings. This is the backstop that keeps AC "errors never
    expose provider keys" true regardless of what upstream puts there.
    """
    if secret and secret in text:
        return text.replace(secret, "***")
    return text


def _is_timeout(exc: BaseException) -> bool:
    """True for a deadline miss, whether raised bare or wrapped.

    ``urlopen`` raises ``socket.timeout`` directly when the read stalls
    and ``URLError(reason=socket.timeout())`` when the connect does.
    """
    if isinstance(exc, socket.timeout):
        return True
    return isinstance(getattr(exc, "reason", None), socket.timeout)


def _classify_transport(exc: BaseException) -> tuple[str, str]:
    """Map a transport exception onto ``(state, detail)``.

    ``urllib.error.HTTPError`` subclasses ``URLError``, so it has to be
    tested first — catching them together is exactly what made a 401
    indistinguishable from a timeout.

    ``detail`` is empty wherever the state name already says everything;
    it exists to add the fact the state cannot carry, such as which
    status code came back.
    """
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return UNAUTHORIZED, f"HTTP {exc.code}"
        if exc.code == 429:
            return RATE_LIMITED, "HTTP 429"
        return HTTP_ERROR, f"HTTP {exc.code}"
    if _is_timeout(exc):
        return TIMEOUT, ""
    return UNREACHABLE, _format_url_error(exc)


def _fetch_rows(url: str, headers: dict, timeout: float,
                secret: str) -> tuple[list, str, str]:
    """GET ``url`` and return ``(data[] rows, state, detail)``.

    ``state`` is :data:`LIVE` only when the provider answered with a JSON
    object carrying a ``data`` list. ``secret`` is scrubbed out of every
    detail string.
    """
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError,
            http.client.HTTPException) as exc:
        state, detail = _classify_transport(exc)
        return [], state, _scrub(detail, secret)
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return [], MALFORMED, "not JSON"
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return [], MALFORMED, "no data[] array"
    return rows, LIVE, ""


def _fallback(on_warn: Optional[WarnFn], provider_key: str,
              state: str, detail: str) -> DiscoveryResult:
    """Log why discovery fell back, and return the empty result saying so.

    ``detail`` carries only what ``state`` does not already say, so it is
    often empty — the state name is the reason.
    """
    cause = f"{state} ({detail})" if detail else state
    _emit(on_warn, f"[warn/{provider_key}-fetch] {cause} — falling back to catalog")
    return DiscoveryResult(models=[], state=state, detail=detail)


# ─── OpenAI ───────────────────────────────────────────────────────────

# Allow-list: model id starts with one of these prefixes.
_OPENAI_ALLOW_PREFIXES = (
    "gpt-5",
    "gpt-4o",
    "gpt-4.1",
    "gpt-4-turbo",
    "o1",
    "o3",
    "o4",
    "chatgpt-",
    "text-embedding-3",
)

# Deny-list: deny-substring patterns and regexes. If any matches, the
# model is dropped EVEN IF the allow-list said yes.
_OPENAI_DENY_SUBSTRINGS = (
    "dall-e",
    "whisper",
    "tts-",
    "gpt-image",
    "realtime-preview",
    "audio-preview",
    "search-preview",
    "-instruct",
    "babbage",
    "davinci",
    "text-embedding-ada",
    "text-similarity",
    "ft-",  # fine-tune marker
)

# Snapshot suffixes — drop ``foo-2024-01-15`` and ``foo-1106`` style
# variants when the unsuffixed alias is also in the response.
_OPENAI_SNAPSHOT_RE = re.compile(r"-(?:\d{4}-\d{2}-\d{2}|\d{4})$")


def _openai_pass_filter(model_id: str) -> bool:
    mid = model_id.lower()
    if not any(mid.startswith(p) for p in _OPENAI_ALLOW_PREFIXES):
        return False
    if any(s in mid for s in _OPENAI_DENY_SUBSTRINGS):
        return False
    return True


def _dedup_openai_snapshots(ids: list[str]) -> list[str]:
    """Drop dated snapshots when the unsuffixed alias is present.

    e.g. if the response has both ``gpt-4o`` and ``gpt-4o-2024-08-06``,
    keep only ``gpt-4o``.
    """
    bare = {mid for mid in ids if not _OPENAI_SNAPSHOT_RE.search(mid)}
    out: list[str] = []
    for mid in ids:
        m = _OPENAI_SNAPSHOT_RE.search(mid)
        if m:
            alias = mid[: m.start()]
            if alias in bare:
                continue
        out.append(mid)
    return out


def discover_openai_models(api_key: str, timeout: float = 5.0,
                           on_warn: Optional[WarnFn] = None) -> DiscoveryResult:
    """GET https://api.openai.com/v1/models with the user's key.

    Returns a :class:`DiscoveryResult` whose ``state`` says whether the
    rows are the provider's own list or why they are not.
    """
    if not api_key:
        return _fallback(on_warn, "openai", NO_KEY, "")
    rows, state, detail = _fetch_rows(
        "https://api.openai.com/v1/models",
        {"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        timeout, api_key,
    )
    if state != LIVE:
        return _fallback(on_warn, "openai", state, detail)
    raw_ids = [r["id"] for r in rows
               if isinstance(r, dict) and isinstance(r.get("id"), str)]
    deduped = _dedup_openai_snapshots(
        [mid for mid in raw_ids if _openai_pass_filter(mid)]
    )
    deduped.sort()
    if not deduped:
        return _fallback(
            on_warn, "openai", EMPTY, f"{len(raw_ids)} listed",
        )
    return DiscoveryResult(
        models=[ModelInfo(id=mid, label=mid, description="") for mid in deduped],
        state=LIVE,
    )


def list_openai_models(api_key: str, timeout: float = 5.0,
                       on_warn: Optional[WarnFn] = None) -> list[ModelInfo]:
    """Models only. Empty on any failure — see :func:`discover_openai_models`
    when the caller needs to know which failure."""
    return discover_openai_models(api_key, timeout, on_warn).models


# ─── Anthropic ───────────────────────────────────────────────────────

def discover_anthropic_models(api_key: str, timeout: float = 5.0,
                              on_warn: Optional[WarnFn] = None) -> DiscoveryResult:
    """GET https://api.anthropic.com/v1/models with the user's key.
    Anthropic's response is already clean — only minor dedup needed.
    """
    if not api_key:
        return _fallback(on_warn, "anthropic", NO_KEY, "")
    rows, state, detail = _fetch_rows(
        "https://api.anthropic.com/v1/models",
        {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Accept": "application/json",
        },
        timeout, api_key,
    )
    if state != LIVE:
        return _fallback(on_warn, "anthropic", state, detail)
    models = _anthropic_models(rows)
    if not models:
        return _fallback(
            on_warn, "anthropic", EMPTY, f"{len(rows)} listed",
        )
    return DiscoveryResult(models=models, state=LIVE)


def _anthropic_models(rows: list) -> list[ModelInfo]:
    """Each row: ``{id, display_name, type, created_at}``. Dedup snapshots
    that share a ``display_name`` — keep the entry with the most recent
    ``created_at`` (string-sortable ISO)."""
    by_label: dict[str, dict] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        rid = r.get("id")
        label = r.get("display_name") or rid
        created = r.get("created_at") or ""
        if not isinstance(rid, str) or not isinstance(label, str):
            continue
        prev = by_label.get(label)
        if prev is None or str(created) > str(prev.get("created_at", "")):
            by_label[label] = {"id": rid, "label": label, "created_at": created}
    out = [ModelInfo(id=v["id"], label=v["label"]) for v in by_label.values()]
    out.sort(key=lambda m: m.label)
    return out


def list_anthropic_models(api_key: str, timeout: float = 5.0,
                          on_warn: Optional[WarnFn] = None) -> list[ModelInfo]:
    """Models only. Empty on any failure — see
    :func:`discover_anthropic_models` when the caller needs to know which."""
    return discover_anthropic_models(api_key, timeout, on_warn).models


# ─── OpenRouter ──────────────────────────────────────────────────────

def discover_openrouter_models(timeout: float = 5.0, cap: int = 50,
                               on_warn: Optional[WarnFn] = None) -> DiscoveryResult:
    """GET https://openrouter.ai/api/v1/models (no auth).
    OpenRouter returns 200+ models with rich metadata. Cap at ``cap``
    so the wizard multi-select stays usable; the cap is reported in the
    result detail rather than applied silently.
    """
    rows, state, detail = _fetch_rows(
        "https://openrouter.ai/api/v1/models",
        {"Accept": "application/json"},
        timeout, "",
    )
    if state != LIVE:
        return _fallback(on_warn, "openrouter", state, detail)
    out = _openrouter_models(rows)
    if not out:
        return _fallback(
            on_warn, "openrouter", EMPTY, f"{len(rows)} listed",
        )
    if len(out) > cap:
        return DiscoveryResult(
            models=out[:cap], state=LIVE,
            detail=f"first {cap} of {len(out)}",
        )
    return DiscoveryResult(models=out, state=LIVE)


def _openrouter_models(rows: list) -> list[ModelInfo]:
    """Normalise OpenRouter rows into catalog-shaped ids, sorted by label."""
    out: list[ModelInfo] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        rid = r.get("id")
        if not isinstance(rid, str):
            continue
        # The catalog model_names are prefixed ``openrouter/...`` so
        # LiteLLM routes via the OpenRouter API. The OpenRouter API's
        # ``id`` field is normally the bare slug (e.g.
        # ``anthropic/claude-sonnet-4-6``); we add the prefix so it
        # matches the per-provider routing rules in services/litellm/init/scripts/init.py.
        # Defensive check against double-prefixing in case OpenRouter's
        # response format ever includes the prefix already.
        oid = rid if rid.startswith("openrouter/") else f"openrouter/{rid}"
        label = r.get("name") or oid
        desc = r.get("description") or ""
        if isinstance(desc, str) and len(desc) > 120:
            desc = desc[:117] + "\u2026"
        out.append(ModelInfo(id=oid, label=str(label), description=desc))
    out.sort(key=lambda m: m.label.lower())
    return out


def list_openrouter_models(timeout: float = 5.0, cap: int = 50,
                           on_warn: Optional[WarnFn] = None) -> list[ModelInfo]:
    """Models only. Empty on any failure — see
    :func:`discover_openrouter_models` when the caller needs to know which."""
    return discover_openrouter_models(timeout, cap, on_warn).models
