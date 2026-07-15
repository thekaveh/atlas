from __future__ import annotations

import media_registry


def test_registry_covers_all_five_families():
    families = {m.family for m in media_registry.all_models()}
    assert families == {"hunyuan3d", "trellis", "tripo", "rodin", "pixal3d"}


def test_glb_response_keys_port_daydreams_extractor():
    # Must match DayDreams' extractGlbUrl probe order exactly.
    assert media_registry.GLB_RESPONSE_KEYS == (
        "model_glb",
        "model_mesh",
        "model",
        "mesh",
        "pbr_model",
        "base_model",
    )


def test_lookup_resolves_canonical_id_alias_and_case():
    canonical = media_registry.lookup("fal-ai/trellis")
    assert canonical is not None
    assert canonical.model_id == "fal-ai/trellis"
    # Alias + case-insensitivity resolve to the same entry.
    assert media_registry.lookup("TRELLIS") is canonical
    assert media_registry.lookup("trellis-2") is canonical
    assert media_registry.lookup("  fal-ai/trellis  ") is canonical


def test_lookup_unknown_returns_none():
    assert media_registry.lookup(None) is None
    assert media_registry.lookup("") is None
    assert media_registry.lookup("fal-ai/does-not-exist") is None


def test_prism_alias_maps_to_canonical_tripo_id():
    # "Prism" is 3D AI Studio's rebrand of Tripo H3.1 — registry records the
    # canonical vendor id only.
    entry = media_registry.lookup("prism")
    assert entry is not None
    assert entry.model_id == "tripo3d/tripo/v2.5/image-to-3d"
    assert entry.family == "tripo"


def test_tripo_requires_hosted_url_and_rejects_data_uri():
    tripo = media_registry.lookup("tripo3d/tripo/v2.5/image-to-3d")
    assert tripo is not None
    assert tripo.needs_hosted_url is True
    assert tripo.accepts_data_uri is False
    assert tripo.commercial_use == "gated"


def test_license_metadata_recorded_as_data():
    trellis = media_registry.lookup("fal-ai/trellis")
    hunyuan = media_registry.lookup("fal-ai/hunyuan3d/v2")
    assert trellis.license == "MIT"
    assert trellis.commercial_use == "yes"
    assert hunyuan.commercial_use == "yes"
    assert "Tencent" in hunyuan.license_notes


def test_pixal3d_endpoint_flagged_unverified():
    pixal = media_registry.lookup("pixal3d")
    assert pixal is not None
    assert pixal.endpoint_verified is False


def test_default_model_id_honors_env(monkeypatch):
    monkeypatch.delenv("FAL_IMAGE_TO_3D_MODEL", raising=False)
    assert media_registry.default_model_id() == "fal-ai/trellis"
    monkeypatch.setenv("FAL_IMAGE_TO_3D_MODEL", "fal-ai/hunyuan3d/v2")
    assert media_registry.default_model_id() == "fal-ai/hunyuan3d/v2"


def test_known_ids_are_canonical():
    ids = media_registry.known_ids()
    assert "fal-ai/trellis" in ids
    assert "tripo3d/tripo/v2.5/image-to-3d" in ids
    assert all(media_registry.lookup(model_id).endpoint_verified for model_id in ids)
