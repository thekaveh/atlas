"""#535 followups review, finding R1.

``wizard/model/track_rules.py`` was a second, independent implementation
of the off-track force-disable rule ``tracks.synthesize_track_source_args``
already implements — the function ``start.py``'s ``--no-tui`` path
actually uses. The two copies had already drifted: only
``synthesize_track_source_args`` honours ``consumer_declared`` (#783) —
the contract that a consumer manifest's ``env.values``-declared
``*_SOURCE`` survives an out-of-track selection instead of being
silently force-set to ``disabled``. ``track_rules.py`` implemented
neither the ``cloud_*`` skip nor ``consumer_declared``.

R1 deleted ``track_rules.py`` and routed ``_selections_to_args`` through
``tracks.synthesize_track_source_args`` directly, threading
``consumer_declared`` through exactly like ``start.py`` does. This file
proves the two load-bearing behaviors of that change:

1. A consumer-declared ``*_SOURCE`` now SURVIVES an out-of-track wizard
   selection (previously: silently force-disabled — the bug).
2. An out-of-track service with NO consumer declaration still gets
   force-disabled, same as before (the existing contract, unchanged —
   see also ``test_tracks_selections.py``, which this file does not
   duplicate wholesale).

Plus one degrade-safety check: a broken track registry must still
degrade to "no synthesis" rather than raise, now that the guard lives
as a local, function-scoped import inside ``_selections_to_args``
rather than at ``wizard/model/track_rules.py`` module scope.
"""

from __future__ import annotations

from types import SimpleNamespace

import tracks as tracks_module
from ui.textual.integration import _selections_to_args, PICKER_STEP_TITLE


def _svc(key: str, display_name: str):
    return SimpleNamespace(key=key, display_name=display_name)


def test_consumer_declared_source_survives_out_of_track_selection():
    """MINIO_SOURCE declared by a consumer manifest's env.values must
    NOT be force-set to 'disabled' when minio is off-track, even though
    the wizard never showed (and the user never answered) its step."""
    services_info = [_svc("minio", "MinIO")]
    selections = {PICKER_STEP_TITLE: "gen-ai-rag"}  # minio step was skipped

    source_args, _ = _selections_to_args(
        selections, services_info, current_base_port=63000, env_vars={},
        consumer_declared=frozenset({"minio_source"}),
    )

    assert "minio_source" not in source_args, (
        "a consumer-declared SOURCE must be left unwritten so the "
        f"manifest's own env.values write is not clobbered; got {source_args!r}"
    )


def test_undeclared_out_of_track_service_still_force_disabled():
    """Without a consumer declaration, the exact same off-track service
    still gets force-disabled — consumer_declared is an exemption, not
    a relaxation of the base rule."""
    services_info = [_svc("minio", "MinIO")]
    selections = {PICKER_STEP_TITLE: "gen-ai-rag"}

    source_args, _ = _selections_to_args(
        selections, services_info, current_base_port=63000, env_vars={},
        consumer_declared=frozenset(),
    )

    assert source_args.get("minio_source") == "disabled"


def test_consumer_declared_only_exempts_the_declared_key():
    """A declaration for one off-track service must not exempt a
    different off-track service that wasn't declared."""
    services_info = [_svc("minio", "MinIO"), _svc("comfyui", "ComfyUI")]
    selections = {PICKER_STEP_TITLE: "gen-ai-rag"}

    source_args, _ = _selections_to_args(
        selections, services_info, current_base_port=63000, env_vars={},
        consumer_declared=frozenset({"minio_source"}),
    )

    assert "minio_source" not in source_args
    assert source_args.get("comfyui_source") == "disabled"


def test_consumer_declared_defaults_to_empty_and_preserves_old_behavior():
    """Callers that don't pass consumer_declared (every pre-existing
    direct caller, including the other parity suites) get exactly the
    old force-disable-everything-off-track behavior."""
    services_info = [_svc("minio", "MinIO")]
    selections = {PICKER_STEP_TITLE: "gen-ai-rag"}

    source_args, _ = _selections_to_args(
        selections, services_info, current_base_port=63000, env_vars={},
    )

    assert source_args.get("minio_source") == "disabled"


def test_registry_load_failure_degrades_to_no_synthesis(monkeypatch):
    """A broken track registry (missing/broken yaml/jsonschema, a
    corrupt tracks.yml) must degrade the wizard's force-disable pass to
    a no-op, not raise — matching the contract the deleted
    track_rules.py used to document. The guard is now a local,
    function-scoped try/except inside _selections_to_args around
    `tracks.load_tracks`, so monkeypatching the real `tracks` module's
    `load_tracks` attribute (which the local `from tracks import
    load_tracks as ...` resolves at call time) exercises it directly."""
    def _boom():
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(tracks_module, "load_tracks", _boom)

    services_info = [_svc("comfyui", "ComfyUI")]
    selections = {PICKER_STEP_TITLE: "gen-ai-rag"}

    source_args, _ = _selections_to_args(
        selections, services_info, current_base_port=63000, env_vars={},
    )

    assert "comfyui_source" not in source_args
