import re
from pathlib import Path

from bootstrapper.docs.sitegen.pages import ARCHITECTURE_PERSPECTIVES
from scripts.docs.canonical_references import (
    render_canonical_references,
    sync_canonical_references,
)
from scripts.docs.license_inventory import (
    MODES,
    Pins,
    effective_modes,
    inventory_findings,
    load_inventory,
    load_pins,
    render_license_inventory,
)
from scripts.docs.manifest import load_manifest


ROOT = Path(__file__).resolve().parents[2]


def test_canonical_reference_projection_covers_dynamic_public_pages() -> None:
    rendered = render_canonical_references(ROOT)
    architecture = {
        f"docs/architecture/{slug}.{suffix}"
        for slug in ARCHITECTURE_PERSPECTIVES
        for suffix in ("md", "html")
    } | {"docs/architecture/README.md", "docs/architecture/index.md"}

    assert {path.relative_to(ROOT).as_posix() for path in rendered} == {
        "docs/CONTRIBUTING-services.md",
        "docs/superpowers/README.md",
        "docs/tracks.md",
        "docs/services.md",
        "docs/reference/index.md",
        "docs/reference/source-values.md",
        "docs/reference/env-vars.md",
        "docs/reference/ports-routes.md",
        "docs/reference/service-dependencies.md",
        "docs/reference/manifest-fields.md",
        "docs/reference/license-inventory.md",
    } | architecture
    assert "../services/comfyui/README.md" in rendered[ROOT / "docs/services.md"]
    assert "../../operations/" not in rendered[ROOT / "docs/reference/ports-routes.md"]


def test_committed_canonical_references_match_the_live_service_model() -> None:
    assert sync_canonical_references(ROOT, check=True) == []


def test_the_track_matrix_has_exactly_one_generated_home() -> None:
    """#838: the matrix used to be rendered byte-identically at nav §4
    (``docs/tracks.md``) and §10.5 (``docs/reference/tracks.md``).

    Both were generated from the same ``model.tracks``, so they could never
    drift — which is precisely why the duplication survived unnoticed. The
    reference copy was collapsed into the nav page, which is the one users
    actually browse. This guards the collapse rather than the symptom: a
    second generated home would reintroduce it silently.
    """
    from bootstrapper.docs.sitegen.model import load_docs_model
    from bootstrapper.docs.sitegen.pages import reference_pages, static_pages

    model = load_docs_model(ROOT)
    rendered = {**static_pages(model), **reference_pages(model)}
    homes = [
        path.relative_to(ROOT).as_posix()
        for path, text in rendered.items()
        if "| Track | Description | Services |" in text
    ]
    # static_pages() emits into the docs/site/ staging tree, which the build
    # publishes as docs/tracks.md — the path differs, the count is the point.
    assert homes == ["docs/site/tracks.md"], (
        f"the track matrix should have exactly one generated home, found: {homes}"
    )


def test_documentation_map_delegates_service_inventory_to_generated_catalog() -> None:
    manifest = load_manifest(ROOT / "docs" / "manifest.yaml", ROOT)
    documentation_map = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    service_catalog = (ROOT / "docs" / "services.md").read_text(encoding="utf-8")
    service_sources = [
        page.source
        for page in manifest.pages
        if len(Path(page.source).parts) == 3
        and Path(page.source).parts[0] == "services"
        and Path(page.source).name == "README.md"
    ]

    assert service_sources
    for source in service_sources:
        link = f"../{source}"
        assert link in service_catalog
        assert link not in documentation_map
    assert "[Service catalog](services.md)" in documentation_map


# ── supply-chain license inventory (#1064) ──────────────────────────────


def _inventory_and_pins():
    return load_inventory(ROOT), load_pins(ROOT)


def test_license_inventory_matches_every_image_and_model_pin() -> None:
    """AC1/AC3: one row per manifest image and catalogue model, each at its pin."""
    inventory, pins = _inventory_and_pins()

    assert inventory_findings(inventory, pins) == []
    assert (len(inventory["images"]), len(inventory["build_images"]), len(inventory["models"])) == (
        len(pins.images), len(pins.build_images), len(pins.models))


def test_a_moved_pin_or_unpinned_license_url_is_a_finding() -> None:
    """AC3: a pin that moves while its row stays put fails the check, as does
    a license URL that names a branch rather than a revision."""
    inventory, pins = _inventory_and_pins()
    krea = pins.models[("comfyui", "krea2-turbo-bf16")]
    jupyter = "services/jupyterhub/build/Dockerfile"
    node = next(image for dockerfile, image in pins.build_images if dockerfile == jupyter)
    build_images = {key: value for key, value in pins.build_images.items() if key != (jupyter, node)}
    moved = Pins(
        {**pins.images, ("airflow", "AIRFLOW_IMAGE"): "apache/airflow:3.3.3"},
        {**build_images, (jupyter, "node:24"): "node:24"},
        {**pins.models, ("comfyui", "krea2-turbo-bf16"): {
            **krea, "sources": [*krea["sources"][:-1], "https://huggingface.co/x/y/resolve/abcdef1/vae.safetensors"]},
         ("comfyui", "hunyuan3d-2"): {**pins.models[("comfyui", "hunyuan3d-2")],
                                      "license_url": "https://huggingface.co/x/y/blob/abcdef1/LICENSE"}},
    )
    unpinned = {**inventory, "images": [
        {**row, "url": re.sub(r"/blob/[0-9a-f]{7,40}/", "/blob/main/", row["url"])}
        if row["var"] == "AIRFLOW_IMAGE" else row
        for row in inventory["images"]
    ]}

    # A bundle's extra file and a Dockerfile-only runtime stage are pins too.
    assert [f.split(" pin moved")[0].split(" license URL changed")[0]
            for f in inventory_findings(inventory, moved)] == [
        "image airflow/AIRFLOW_IMAGE",
        f"Dockerfile image {jupyter}/node:24 has 0 inventory rows, expected 1",
        f"inventory row {jupyter}/{node} matches no Dockerfile image",
        "model comfyui/krea2-turbo-bf16",
        "model comfyui/hunyuan3d-2",
    ]
    assert inventory_findings(unpinned, pins) == [
        "image airflow/AIRFLOW_IMAGE: https://github.com/apache/airflow/blob/main/LICENSE "
        "is not pinned to a revision"
    ]


def test_model_exceptions_must_be_explicit_and_carry_their_question() -> None:
    """Review finding: an unrelated open item no longer excuses an unpinned
    model license URL or a floating model source; each exception must be
    declared and must name one of the row's own open items."""
    inventory, pins = _inventory_and_pins()

    def without(field: str, row_id: str) -> dict:
        return {**inventory, "models": [
            {k: v for k, v in row.items() if not (row["id"] == row_id and k == field)}
            for row in inventory["models"]
        ]}

    misnamed = {**inventory, "models": [
        {**row, "unpinned_source": "R-MODELS"} if row["id"] == "bge-m3" else row
        for row in inventory["models"]
    ]}
    assert inventory_findings(without("unpinned_license_url", "krea2-identity-edit-v1-2"), pins) == [
        "model comfyui/krea2-identity-edit-v1-2: license URL "
        "https://huggingface.co/krea/Krea-2-Raw/blob/main/LICENSE.pdf is not pinned to a revision"
    ]
    assert inventory_findings(without("unpinned_source", "bge-m3"), pins) == [
        "model ollama/bge-m3: source bge-m3 is not pinned to a revision"
    ]
    assert inventory_findings(without("hosted", "hunyuan3d-2"), pins) == [
        "model comfyui/hunyuan3d-2: hosted is not recorded"
    ]
    stale = {**inventory, "models": [
        {**row, "unpinned_source": "R-HUNYUAN"} if row["id"] == "hunyuan3d-2" else row
        for row in inventory["models"]
    ]}
    assert inventory_findings(stale, pins) == [
        "model comfyui/hunyuan3d-2: unpinned_source is declared but nothing is unpinned any more"
    ]
    assert inventory_findings(misnamed, pins) == [
        "model ollama/bge-m3: unpinned_source must name one of the row's open items"
    ]


def test_a_malformed_row_renders_and_is_reported_instead_of_crashing() -> None:
    """Review finding: the page renders before the gate validates, so a row
    missing fields must still render; the validator names what is wrong."""
    inventory, pins = _inventory_and_pins()
    broken = {**inventory, "images": [
        {k: v for k, v in row.items() if k != "upstream"} | {"redistribution": "maybe"}
        if row["var"] == "AIRFLOW_IMAGE" else row
        for row in inventory["images"]
    ] + [{"service": "typo", "variable": "X_IMAGE"}], "build_images": [
        {**row, "url": None} for row in inventory["build_images"][:1]
    ] + inventory["build_images"][1:]}

    assert "`airflow` · `AIRFLOW_IMAGE`" in render_license_inventory(broken, pins)
    first_build = inventory["build_images"][0]
    assert inventory_findings(broken, pins) == [
        "images row {'service': 'typo', 'variable': 'X_IMAGE'} lacks var",
        "image airflow/AIRFLOW_IMAGE: redistribution value 'maybe' is not one of "
        "permitted, with notices, with source, conditional, unresolved",
        f"image {first_build['dockerfile']}/{first_build['image']}: no license URL, "
        "so every mode must be unresolved",
        f"image {first_build['dockerfile']}/{first_build['image']}: None is not pinned to a revision",
    ]


def test_every_row_has_a_license_or_a_named_open_question() -> None:
    """AC1/AC4: no blank license cell, and nothing unadjudicated is passed off
    as compatible: every unresolved mode names its question."""
    inventory, pins = _inventory_and_pins()
    licenses, open_items = inventory["licenses"], inventory["open_items"]
    for row in [*inventory["images"], *inventory["build_images"]]:
        modes = effective_modes(row, licenses)
        assert licenses[row["license"]]["name"] and (row.get("url") or row.get("open")), row
        assert "unresolved" not in modes.values() or row.get("open"), row
    for row in inventory["models"]:
        recorded = pins.models[(row["catalogue"], row["id"])].get("license_name")
        assert recorded or {row[mode] for mode in MODES} == {"unresolved"}, row
        assert all(open_items[item]["question"].endswith(("?", ".")) for item in row.get("open", []))


def test_an_unresolved_mode_needs_an_open_item_about_that_mode() -> None:
    """Review finding: an unrelated open item (R-FLOAT is context only) no
    longer names the question for an unresolved redistribution."""
    inventory, pins = _inventory_and_pins()
    swapped = {**inventory, "images": [
        {**row, "open": ["R-FLOAT"]} if row["var"] == "RAY_GPU_IMAGE" else row
        for row in inventory["images"]
    ]}

    assert inventory_findings(swapped, pins) == [
        "image ray/RAY_GPU_IMAGE: redistribution is unresolved but none of its open items "
        "concerns redistribution"
    ]

def test_mode_dependent_terms_are_recorded_separately() -> None:
    """AC2: where hosted use, integration and redistribution differ upstream,
    the inventory's values differ too."""
    inventory, _ = _inventory_and_pins()
    licenses = inventory["licenses"]
    by_model = {row["id"]: row for row in inventory["models"]}
    by_image = {row["var"]: effective_modes(row, licenses) for row in inventory["images"]}

    assert {by_model["krea2-turbo-bf16"][m] for m in MODES} == {"conditional", "unresolved"}
    assert (by_image["N8N_IMAGE"]["hosted"], by_image["N8N_IMAGE"]["redistribution"]) == (
        "conditional", "unresolved")
    assert (by_image["GRAFANA_IMAGE"]["hosted"], by_image["GRAFANA_IMAGE"]["redistribution"]) == (
        "permitted", "with source")


def test_release_review_runs_the_inventory_check_before_tagging() -> None:
    """AC5: the inventory is a pre-tag release step, not a standalone page."""
    releasing = (ROOT / "docs/operations/releasing.md").read_text(encoding="utf-8")
    step = releasing.split("2. **Review the license inventory**", 1)[1].split("3. **", 1)[0]

    assert releasing.index("Review the license inventory") < releasing.index("Create the tag")
    assert "python -m scripts.docs.license_inventory --check" in " ".join(step.split())
    assert "(../reference/license-inventory.md)" in step


def test_docs_gate_reports_license_inventory_findings(monkeypatch) -> None:
    """AC3/AC5: `make docs-check` fails on an inventory finding, attributed to
    the record a contributor has to edit."""
    from scripts.docs import check_docs

    assert check_docs.check_license_inventory(ROOT) == []
    monkeypatch.setattr(check_docs, "inventory_findings", lambda inventory, pins: ["pin moved"])
    assert [(f.path, f.message) for f in check_docs.check_license_inventory(ROOT)] == [
        ("docs/reference/license-inventory.yaml", "pin moved")
    ]
