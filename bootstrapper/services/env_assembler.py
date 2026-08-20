"""
Assemble a `.env.example`-shaped text document from a list of service manifests.

The output is a pure function of the manifests (and an optional ordering). It
is deterministic — re-running with the same inputs produces byte-identical
output — so CI can diff against the committed `.env.example`.

Used by `bootstrapper/tools/validate_fragments.py --check-env-example` to
assert that the committed file matches the manifest-driven assembly. Also
consumed by start.py for the pre-launch env-example sync.

Output shape (per service):

    # ──────────────────────────────────────────────────────────────────────
    # llm: Ollama (services/ollama/service.yml)
    # ──────────────────────────────────────────────────────────────────────
    # Source: ollama-container-cpu | ollama-container-gpu | ollama-localhost | none
    LLM_PROVIDER_SOURCE=ollama-container-cpu

    # Image references
    LLM_PROVIDER_IMAGE=ollama/ollama:0.30.11
    OLLAMA_PULL_IMAGE=alpine:3.24.1

    # auto-managed (computed from LLM_PROVIDER_SOURCE)
    OLLAMA_SCALE=
    OLLAMA_ENDPOINT=
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path
from typing import Iterable

from services.manifests import EnvVarDecl, Manifest
from services.topology import build_topology


_HEADER = """\
# ============================================================================
# GENERATED FILE — DO NOT EDIT BY HAND
#
# This .env.example is produced by bootstrapper/services/env_assembler.py
# from services/<name>/service.yml manifests. To change a default, edit the
# relevant manifest and re-run `./start.sh` (which regenerates this file)
# or `python -m services.env_assembler` for the regen-only path.
#
# Variables flagged `auto-managed` are computed at runtime by the bootstrapper
# from SOURCE values and are intentionally emitted without a default here.
# ============================================================================
"""


def assemble_env_example(
    manifests: Iterable[Manifest],
    order: list[str] | None = None,
    *,
    services_root: Path | None = None,
) -> str:
    """Render the manifests into .env.example-shaped text.

    Args:
        manifests: Loaded service manifests.
        order: Optional list of manifest names declaring display order.
            Manifests not mentioned are appended alphabetically after the
            ordered ones. If None, all manifests are sorted alphabetically.
        services_root: Path to the services/ directory used to build the
            Topology (for port_defaults). If None, resolved from this file's
            location (i.e. <repo_root>/services).
    """
    manifests = list(manifests)

    _services_root = services_root or (
        Path(__file__).resolve().parent.parent.parent / "services"
    )
    port_defaults: dict[str, int]
    try:
        _topology = build_topology(_services_root)
        port_defaults = dict(_topology.port_defaults)
    except (FileNotFoundError, NotADirectoryError):
        # Synthetic test trees with no services/ dir on disk — fall back
        # to manifest-declared defaults silently. This is the supported
        # "stand-alone library" path used by env_assembler unit tests.
        port_defaults = {}
    except Exception as exc:
        # Real services/ tree present but topology failed (e.g. cycle,
        # unknown category, slot overflow). Warn so the failure surfaces
        # in the test log and CI, then degrade gracefully to manifest
        # defaults. Re-raising would block the assembler entirely; we
        # prefer a noisy fallback over silent breakage.
        warnings.warn(
            f"build_topology failed; port defaults will be empty: {exc}",
            stacklevel=2,
        )
        port_defaults = {}

    ordered = _apply_order(manifests, order)

    parts: list[str] = [_HEADER]
    for m in ordered:
        # Internal logical graph nodes can own dependency documentation without
        # declaring any operator-facing configuration. Do not emit an empty
        # banner for them in the generated environment template.
        if m.virtual and m.sources is None and not m.images and not m.env:
            continue
        parts.append(_render_manifest(m, port_defaults))
    text = "\n".join(parts).rstrip() + "\n"

    # ── Model-resolver override ────────────────────────────────────────────
    # Override LITELLM_DEFAULT_MODEL and LITELLM_VISION_MODEL with the values
    # computed by model_resolver.resolved_defaults({}).  We pass an empty env
    # dict (= "no user overrides") so the output reflects the default-config
    # winner — the YAML default_active=True ollama model, which under the
    # default stack is qwen3.8:latest for both content and vision.
    #
    # NOTE: LITELLM_EMBEDDING_MODEL is deliberately NOT overridden here.
    # Its static default (ollama/nomic-embed-text, 768-dim) must stay fixed
    # because the public.memory_facts.embedding vector(768) pgvector column
    # is dimension-locked to nomic-embed-text.  Switching dimension would
    # silently corrupt all stored embeddings.  See model_resolver.py for the
    # full rationale.
    text = _apply_model_resolver_defaults(text)

    # ── Ollama user-models pre-fill ───────────────────────────────────────
    # Pre-fill OLLAMA_USER_MODELS with the catalog's default-active Ollama
    # model names (comma-joined, catalog order).  This ensures a fresh
    # install / CLI-flag run pulls the right default trio without requiring
    # the wizard to have set the var first.  The wizard overwrites it with
    # the user's selection on each wizard run; OLLAMA_CUSTOM_MODELS is left
    # empty (free-text additions by the user).
    text = _apply_ollama_user_models_default(text)

    return text


# ────────────────────────────────────────────────────────────────────────────
# Model-resolver post-step
# ────────────────────────────────────────────────────────────────────────────

# The two vars we override with computed defaults.  LITELLM_EMBEDDING_MODEL is
# intentionally absent — see the comment in assemble_env_example().
_MODEL_RESOLVER_VARS = frozenset({"LITELLM_DEFAULT_MODEL", "LITELLM_VISION_MODEL"})


def _apply_model_resolver_defaults(text: str) -> str:
    """Replace the values of LITELLM_DEFAULT_MODEL and LITELLM_VISION_MODEL
    with the model-resolver's computed defaults (default-config / empty-env
    scenario).

    We import model_resolver lazily here to keep env_assembler importable even
    in test trees that don't have the full services/ YAML structure (model
    resolver triggers catalog YAML loading at import time).  The lazy import
    means test suites that use synthetic manifests don't need models.yaml.
    When the import fails (e.g. during isolated unit tests), we leave the
    manifest-declared defaults (empty strings) untouched.
    """
    try:
        from utils.model_resolver import resolved_defaults  # noqa: PLC0415
    except Exception:  # pragma: no cover
        # Graceful degradation: if the resolver can't load (e.g. YAML missing
        # in a synthetic test tree), leave the text unchanged.
        return text

    try:
        overrides = resolved_defaults({})
    except Exception:  # pragma: no cover
        return text

    for var_name, computed_value in overrides.items():
        if var_name not in _MODEL_RESOLVER_VARS:
            continue  # safety guard — should never happen
        if not computed_value:
            continue  # nothing to override with; leave manifest default
        # Replace the line `VAR_NAME=<anything>` with `VAR_NAME=<computed>`.
        # The line immediately follows the description comment lines and is
        # anchored at the start of a line.
        # Function replacement so computed_value is spliced LITERALLY — a
        # template like rf"\g<1>={computed_value}" would interpret backslashes /
        # \g / \1 in the value (model ids are backslash-free today, but this
        # keeps the .env-writing seam corruption-proof; same fix as docs/regen).
        text = re.sub(
            rf"^({re.escape(var_name)})=.*$",
            lambda m, v=computed_value: f"{m.group(1)}={v}",
            text,
            flags=re.MULTILINE,
        )

    return text


def _apply_ollama_user_models_default(text: str) -> str:
    """Pre-fill OLLAMA_USER_MODELS in .env.example with the catalog's
    default-active Ollama model names (comma-joined, catalog order).

    This keeps OLLAMA_USER_MODELS in sync with the catalog YAML — when the
    catalog's default_active set changes, the next env_assembler regen picks
    it up automatically.  The wizard overwrites this value with the user's
    selection; OLLAMA_CUSTOM_MODELS is intentionally left empty.

    Imported lazily for the same reason as _apply_model_resolver_defaults:
    llm_catalog triggers YAML loading at import time, which synthetic test
    trees (no models.yaml) cannot satisfy.  On import failure we leave the
    manifest-declared empty default untouched.
    """
    try:
        from utils.llm_catalog import default_active_names  # noqa: PLC0415
    except Exception:  # pragma: no cover
        return text

    try:
        names = default_active_names("ollama")
    except Exception:  # pragma: no cover
        return text

    if not names:
        return text  # nothing to override with

    csv_value = ",".join(names)
    # Function replacement (literal splice) — see _apply_model_resolver_defaults.
    text = re.sub(
        r"^(OLLAMA_USER_MODELS)=.*$",
        lambda m: f"{m.group(1)}={csv_value}",
        text,
        flags=re.MULTILINE,
    )
    return text


# ────────────────────────────────────────────────────────────────────────────
# Ordering
# ────────────────────────────────────────────────────────────────────────────


def _apply_order(manifests: list[Manifest], order: list[str] | None) -> list[Manifest]:
    by_name = {m.name: m for m in manifests}
    if order is None:
        return sorted(manifests, key=lambda m: m.name)

    ordered: list[Manifest] = []
    seen: set[str] = set()
    for name in order:
        if name in by_name and name not in seen:
            ordered.append(by_name[name])
            seen.add(name)
    # Append any manifests not mentioned in `order`, alphabetically.
    for m in sorted(manifests, key=lambda m: m.name):
        if m.name not in seen:
            ordered.append(m)
    return ordered


# ────────────────────────────────────────────────────────────────────────────
# Rendering
# ────────────────────────────────────────────────────────────────────────────


def _render_manifest(m: Manifest, port_defaults: dict[str, int]) -> str:
    lines: list[str] = []
    lines.append("# " + "─" * 74)
    lines.append(f"# {m.category}: {m.label}  (services/{m.name}/service.yml)")
    lines.append("# " + "─" * 74)

    # Source var, if any, goes first — it's the most prominent dial.
    if m.sources is not None:
        option_ids = " | ".join(opt.id for opt in m.sources.options)
        lines.append(f"# Source variant. Options: {option_ids}")
        lines.append(f"{m.sources.var}={m.sources.default}")
        lines.append("")

    # Image references, if any.
    if m.images:
        lines.append("# Image references")
        for img in m.images:
            if img.notes:
                lines.append(f"# {img.notes}")
            lines.append(f"{img.var}={img.default}")
        lines.append("")

    # env[] entries. Order preserved as written in the manifest, except the
    # SOURCE var (already emitted above) is skipped here to avoid duplication.
    source_var = m.sources.var if m.sources else None
    for entry in m.env:
        if entry.name == source_var:
            continue  # already emitted at the top of this block
        if entry.bootstrapper_only:
            continue  # consumed only by the host-side bootstrapper; not compose-interpolated
        lines.append(_render_env_entry(entry, port_defaults))

    lines.append("")  # blank line between services
    return "\n".join(lines)


def _render_env_entry(entry: EnvVarDecl, port_defaults: dict[str, int]) -> str:
    """Render one env line plus any preceding comment line(s)."""
    out: list[str] = []
    if entry.description:
        for line in entry.description.rstrip().splitlines():
            out.append(f"# {line}")
    flags: list[str] = []
    if entry.auto_managed:
        flags.append("auto-managed")
    if entry.secret:
        flags.append("secret")
    if flags:
        out.append(f"# ({', '.join(flags)})")

    if entry.auto_managed:
        out.append(f"{entry.name}=")
    else:
        # Port vars: topology slot-allocator is the single source of truth.
        if entry.name in port_defaults:
            value: object = port_defaults[entry.name]
        else:
            value = entry.default
        # Secret-marked vars still emit their manifest default when one
        # exists. The ``secret`` flag governs LOGGING behavior (never
        # echo plain-text to wizard output / docker compose logs); the
        # manifest's `default` is treated as a development placeholder
        # that lets a fresh clone bring the stack up without manual
        # `.env` editing. When a manifest wants an empty value for a
        # secret (the LITELLM_MASTER_KEY auto-generation case), it
        # leaves `default: ""` — that empty string flows through here
        # untouched.
        out.append(f"{entry.name}={_format_default(value)}")
    return "\n".join(out)


def _format_default(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


if __name__ == "__main__":
    import sys

    from services.manifests import load_manifests

    project_root = Path(__file__).resolve().parent.parent.parent
    services_dir = project_root / "services"
    env_example_path = project_root / ".env.example"

    manifests = load_manifests(services_dir)
    output = assemble_env_example(manifests)
    env_example_path.write_text(output, encoding="utf-8")
    print(f"Wrote {env_example_path} ({output.count(chr(10))} lines)", file=sys.stderr)
