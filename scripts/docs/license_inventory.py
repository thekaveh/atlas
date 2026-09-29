"""Supply-chain license inventory (#1064): validate the record and render its page.

``docs/reference/license-inventory.yaml`` records, for every image Atlas pins
(in a service manifest's ``images:`` or directly in a Dockerfile) and every
model weight a catalogue downloads, its license, required notices and what
the terms allow for hosted use, source integration and redistribution. This
module checks the record against the live pins, so a pin that moves without
its row being re-reviewed fails ``make docs-check``, and renders
``docs/reference/license-inventory.md`` from it.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

INVENTORY = Path("docs/reference/license-inventory.yaml")
PAGE = Path("docs/reference/license-inventory.md")
MODES = ("hosted", "integration", "redistribution")
MODE_VALUES = ("permitted", "with notices", "with source", "conditional", "unresolved")
# A URL counts as pinned only when it names an immutable revision.
_PINNED_URL = re.compile(
    r"^https://(?:github\.com/[^/]+/[^/]+/blob"
    r"|huggingface\.co/[^/]+/[^/]+/(?:blob|resolve))/([0-9a-f]{7,40})/"
)
#: Row fields that accept an unpinned value, each only when it names one of
#: the row's own open items, so every exception carries its question.
EXCEPTIONS = ("unpinned_source", "unpinned_license_url")
_ROW_KEYS = {"images": ("service", "var"), "build_images": ("dockerfile", "image"),
             "models": ("catalogue", "id")}


@dataclass(frozen=True)
class Pins:
    """What the repository pins today, keyed like the inventory's rows:
    manifest images by (service, var), Dockerfile-only images by (Dockerfile,
    image), and catalogue models by (catalogue, id) with every download URL
    and the catalogue's license fields."""

    images: dict[tuple[str, str], str]
    build_images: dict[tuple[str, str], str]
    models: dict[tuple[str, str], dict]


def load_inventory(root: Path) -> dict:
    return yaml.safe_load((root / INVENTORY).read_text(encoding="utf-8"))


def _dockerfile_pins(root: Path, declared: set[str]) -> dict[tuple[str, str], str]:
    """External images a Dockerfile uses that no manifest declares.

    Parsed by the container-security scan's own reader, which resolves ARG
    defaults and fallbacks, stage aliases, ``COPY --from`` and
    ``RUN --mount=from``, so both gates agree on what a build pulls in.
    """
    from scripts.container_security import load_dockerfile_source_images

    pins: dict[tuple[str, str], str] = {}
    for dockerfile in sorted((root / "services").rglob("Dockerfile*")):
        owner = dockerfile.relative_to(root).as_posix()
        images = load_dockerfile_source_images(
            dockerfile.read_text(encoding="utf-8"), owner=owner, require_pinned=False)
        pins.update({(owner, image): image for image in images if image not in declared})
    return pins


def _comfyui_models(root: Path) -> dict[tuple[str, str], dict]:
    catalogue = yaml.safe_load((root / "services/comfyui/models.yaml").read_text(encoding="utf-8"))
    models = {}
    for entry in catalogue["models"]:
        urls = [entry.get("url"), *(item.get("url") for item in entry.get("files") or [])]
        models[("comfyui", entry["name"])] = {
            "sources": list(dict.fromkeys(url for url in urls if url)),
            "license_name": entry.get("license_name"),
            "license_url": entry.get("license_url"),
            "license_restrictions": entry.get("license_restrictions") or [],
        }
    return models


def load_pins(root: Path) -> Pins:
    from bootstrapper.services.manifests import load_manifests

    images = {
        (manifest.name, image.var): image.default
        for manifest in load_manifests(root / "services")
        for image in manifest.images
    }
    models = _comfyui_models(root)
    ollama = yaml.safe_load((root / "services/ollama/models.yaml").read_text(encoding="utf-8"))
    for section in ollama.values():
        for entry in section:
            models[("ollama", entry["name"])] = {"sources": [entry["name"]]}
    return Pins(images, _dockerfile_pins(root, set(images.values())), models)


def effective_modes(row: dict, licenses: dict) -> dict[str, str]:
    """A row's mode values: its own overrides over its license's defaults."""
    defaults = licenses.get(row.get("license"), {})
    return {mode: row.get(mode, defaults.get(mode)) for mode in MODES}


# ── validation ──────────────────────────────────────────────────────────


def _pin_findings(rows: dict[tuple[str, str], list[dict]], pins: dict, field: str,
                  kind: str) -> list[str]:
    """Every pin needs exactly one row carrying it verbatim in ``field``."""
    findings = []
    for pin, pinned in pins.items():
        found = rows.get(pin, [])
        if len(found) != 1:
            findings.append(f"{kind} {'/'.join(pin)} has {len(found)} inventory rows, expected 1")
        elif found[0].get(field) != pinned:
            findings.append(
                f"{kind} {'/'.join(pin)} pin moved to {pinned}; re-review its license and update the row"
            )
    findings.extend(f"inventory row {'/'.join(pin)} matches no {kind}" for pin in rows if pin not in pins)
    return findings


def _grouped(rows: list[dict], keys: tuple[str, str]) -> dict[tuple[str, str], list[dict]]:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        if all(key in row for key in keys):
            grouped.setdefault(tuple(row[key] for key in keys), []).append(row)
    return grouped


def _shape_findings(inventory: dict) -> list[str]:
    return [
        f"{section} row {row!r} lacks {', '.join(key for key in keys if key not in row)}"
        for section, keys in _ROW_KEYS.items() for row in inventory.get(section, [])
        if any(key not in row for key in keys)
    ]


def _coverage_findings(label: str, row: dict, modes: dict, open_items: dict) -> list[str]:
    """Each unresolved mode must be settled by one of the row's own open
    items that says it concerns that mode."""
    covered = {mode for item in row.get("open", []) for mode in open_items.get(item, {}).get("modes", [])}
    return [
        f"{label}: {mode} is unresolved but none of its open items concerns {mode}"
        for mode, value in modes.items() if value == "unresolved" and mode not in covered
    ]


def _row_findings(label: str, row: dict, modes: dict, open_items: dict) -> list[str]:
    """Checks every row shares: its own mode values (inherited defaults are
    checked once, on the license), its open items and its exceptions."""
    findings = [
        f"{label}: {mode} value {row[mode]!r} is not one of {', '.join(MODE_VALUES)}"
        for mode in MODES if mode in row and row[mode] not in MODE_VALUES
    ]
    findings.extend(
        f"{label}: open item {item} is not defined" for item in row.get("open", [])
        if item not in open_items
    )
    findings.extend(
        f"{label}: {field} must name one of the row's open items"
        for field in EXCEPTIONS if field in row and row[field] not in row.get("open", [])
    )
    return findings + _coverage_findings(label, row, modes, open_items)


def _image_findings(label: str, row: dict, inventory: dict) -> list[str]:
    licenses = inventory["licenses"]
    modes = effective_modes(row, licenses)
    findings = [] if row.get("license") in licenses else [
        f"{label}: license {row.get('license')!r} is not defined"]
    if not row.get("url") and set(modes.values()) != {"unresolved"}:
        findings.append(f"{label}: no license URL, so every mode must be unresolved")
    return findings + _url_findings(label, row) + _row_findings(label, row, modes, inventory["open_items"])


def _url_findings(label: str, row: dict) -> list[str]:
    unpinned = [field for field in ("url", "notice")
                if field in row and not _PINNED_URL.match(row[field] or "")]
    return [f"{label}: {row[field]} is not pinned to a revision" for field in unpinned
            if not (field == "url" and "unpinned_license_url" in row)]


def _model_pin_findings(label: str, row: dict, catalogue: dict) -> list[str]:
    """Unpinned license URLs and sources need a declared exception, and a
    declared exception must still be needed."""
    url = catalogue.get("license_url")
    url_unpinned = bool(catalogue.get("license_name")) and not _PINNED_URL.match(url or "")
    floating = [source for source in row.get("sources") or [] if not _PINNED_URL.match(source or "")]
    return [
        *_exception_state(label, row, "unpinned_license_url",
                          f"license URL {url}" if url_unpinned else None),
        *_exception_state(label, row, "unpinned_source",
                          f"source {floating[0]}" if floating else None),
    ]


def _exception_state(label: str, row: dict, field: str, unpinned: str | None) -> list[str]:
    """``unpinned`` names what is unpinned, or is None when all is pinned."""
    if unpinned and field not in row:
        return [f"{label}: {unpinned} is not pinned to a revision"]
    if field in row and not unpinned:
        return [f"{label}: {field} is declared but nothing is unpinned any more"]
    return []


def _model_findings(label: str, row: dict, catalogue: dict, open_items: dict) -> list[str]:
    modes = {mode: row.get(mode) for mode in MODES}
    findings = [f"{label}: {mode} is not recorded" for mode in MODES if mode not in row]
    if not catalogue.get("license_name") and set(modes.values()) != {"unresolved"}:
        findings.append(f"{label}: no license is recorded, so every mode must be unresolved")
    return findings + _model_pin_findings(label, row, catalogue) + _row_findings(label, row, modes, open_items)


def inventory_findings(inventory: dict, pins: Pins) -> list[str]:
    """Every way the record disagrees with the pins or with its own rules."""
    rows = {section: inventory.get(section, []) for section in _ROW_KEYS}
    model_sources = {key: model["sources"] for key, model in pins.models.items()}
    model_licenses = {key: model.get("license_url") for key, model in pins.models.items()}
    findings = _shape_findings(inventory)
    findings += _pin_findings(_grouped(rows["images"], _ROW_KEYS["images"]), pins.images, "image", "image")
    findings += _pin_findings(_grouped(rows["build_images"], _ROW_KEYS["build_images"]),
                              pins.build_images, "image", "Dockerfile image")
    models = _grouped(rows["models"], _ROW_KEYS["models"])
    findings += _pin_findings(models, model_sources, "sources", "model")
    # A catalogue license change re-opens the row's mode values for review.
    findings += [
        finding.replace("pin moved to", "license URL changed to")
        for finding in _pin_findings(models, model_licenses, "license_url", "model")
        if "pin moved" in finding
    ]
    findings += [
        f"license {name}: {mode} value {terms.get(mode)!r} is not one of {', '.join(MODE_VALUES)}"
        for name, terms in inventory["licenses"].items() for mode in MODES
        if terms.get(mode) not in MODE_VALUES
    ]
    return findings + _rows_findings(inventory, rows, pins)


def _rows_findings(inventory: dict, rows: dict, pins: Pins) -> list[str]:
    """Each well-formed row against the inventory's own rules."""
    findings = []
    for section in ("images", "build_images"):
        keys = _ROW_KEYS[section]
        findings += [
            finding for row in rows[section] if all(key in row for key in keys)
            for finding in _image_findings(f"image {row[keys[0]]}/{row[keys[1]]}", row, inventory)
        ]
    findings += [
        finding for row in rows["models"] if all(key in row for key in _ROW_KEYS["models"])
        for finding in _model_findings(f"model {row['catalogue']}/{row['id']}", row,
                                       pins.models.get((row["catalogue"], row["id"]), {}),
                                       inventory["open_items"])
    ]
    return findings


# ── rendering ───────────────────────────────────────────────────────────


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _image_ref(image: str | None) -> str:
    name, _, digest = (image or "").partition("@")
    return f"`{name}@{digest[:19]}…`" if digest else f"`{name}`"


def _open(row: dict) -> str:
    return ", ".join(row.get("open", [])) or "—"


def _license_link(name: str, url: str | None, notice: str | None = None) -> str:
    text = f"[{name}]({url})" if url else name
    return text + (f" · [NOTICE]({notice})" if notice else "")


_IMAGE_HEADER = ("| Image | Upstream | License | Hosted use | Source integration "
                 "| Redistribution | Open items |")


def _image_rows(rows: list[dict], licenses: dict, keys: tuple[str, ...], first: str) -> list[str]:
    lines = [f"{first} {_IMAGE_HEADER}", "|---|---|---|---|---|---|---|---|"]
    for row in sorted(rows, key=lambda r: tuple(str(r.get(key, "")) for key in keys)):
        modes = effective_modes(row, licenses)
        lines.append("| " + " | ".join([
            " · ".join(f"`{row.get(key, '?')}`" for key in keys), _image_ref(row.get("image")),
            _cell(row.get("upstream", "—")),
            _license_link(row.get("license", "—"), row.get("url"), row.get("notice")),
            *(modes[mode] or "—" for mode in MODES), _open(row),
        ]) + " |")
    return lines


def _source_cell(row: dict) -> str:
    sources = row.get("sources") or ["?"]
    pinned = _PINNED_URL.match(sources[0] or "")
    first = f"revision `{pinned.group(1)[:12]}`" if pinned else f"floating `{sources[0]}`"
    return first + (f" (+{len(sources) - 1} files)" if len(sources) > 1 else "")


def _model_rows(inventory: dict, pins: Pins) -> list[str]:
    lines = ["| Catalogue | Model | Pinned source | License | Hosted use | Source integration "
             "| Redistribution | Open items |", "|---|---|---|---|---|---|---|---|"]
    for row in inventory["models"]:
        catalogue = pins.models.get((row.get("catalogue"), row.get("id")), {})
        name = catalogue.get("license_name")
        license_cell = _license_link(name, catalogue.get("license_url")) if name else "not recorded"
        if "unpinned_license_url" in row:
            license_cell += f" (not pinned: {row['unpinned_license_url']})"
        if catalogue.get("license_restrictions"):
            license_cell += ". Restrictions: " + _cell(" ".join(catalogue["license_restrictions"]))
        lines.append("| " + " | ".join([
            str(row.get("catalogue", "?")), f"`{row.get('id', '?')}`", _source_cell(row), license_cell,
            *(row.get(mode) or "—" for mode in MODES), _open(row),
        ]) + " |")
    return lines


def _intro_lines(inventory: dict) -> list[str]:
    rows = [*inventory["images"], *inventory.get("build_images", []), *inventory["models"]]
    images = len(inventory["images"]) + len(inventory.get("build_images", []))
    return [
        "# Supply-Chain License Inventory",
        "",
        "<!-- Generated from docs/reference/license-inventory.yaml by "
        "scripts/docs/canonical_references.py. Edit the YAML, not this page. -->",
        "",
        "Atlas is licensed under Apache 2.0, and that grant relicenses none of the container "
        "images, dependencies or model weights it pins. This inventory records, for every image "
        "Atlas pins and every model weight a catalogue downloads, its license, the notices it "
        "requires, and what its terms allow for each way it can be used. It is a review record, "
        "not a legal conclusion: an open item is a question nobody has adjudicated, and nothing "
        "here is marked compatible by assumption.",
        "",
        f"Reviewed {inventory['reviewed']}: {images} images and {len(inventory['models'])} "
        f"models, {sum(1 for row in rows if row.get('open'))} of them with at least one open item.",
        "",
        "## 1. Reading the inventory",
        "",
        "Each artifact is judged in three modes, because many licenses treat them differently:",
        "",
        "- **Hosted use**: operating the software for your own or other people's users.",
        "- **Source integration**: incorporating or modifying its source in another work.",
        "- **Redistribution**: shipping the artifact itself, for example in an appliance bundle.",
        "",
        "| Value | Meaning |",
        "|---|---|",
        "| permitted | No condition beyond keeping the license with the software. |",
        "| with notices | Ship the license text and the upstream NOTICE file, where there is one. |",
        "| with source | Copyleft: offer the complete corresponding source. |",
        "| conditional | The terms restrict this mode; the license's required notices say how. |",
        "| unresolved | Not adjudicated; one of the row's open items names the question. |",
        "",
        "License links point at the license file in the upstream repository at the exact "
        "revision the pinned version was built from, so a change of terms shows up as a "
        "different file rather than a silent edit. Redistribution values describe the upstream "
        "project's own terms. Shipping an image also ships its operating-system packages; that "
        "notice set is open item R-BASE and applies to every image.",
        "",
    ]


def _terms_lines(inventory: dict) -> list[str]:
    return [
        "## 2. Open review items",
        "",
        "| Item | Question | Applies to | Settles |",
        "|---|---|---|---|",
        *(f"| {key} | {_cell(item.get('question', '—'))} | {_cell(item.get('applies', '—'))} "
          f"| {', '.join(item.get('modes', [])) or 'context only'} |"
          for key, item in inventory["open_items"].items()),
        "",
        "## 3. Licenses and required notices",
        "",
        "| License | Hosted use | Source integration | Redistribution | Required notices |",
        "|---|---|---|---|---|",
        *(f"| {_cell(terms.get('name', key))} (`{key}`) | "
          + " | ".join(terms.get(mode) or "—" for mode in MODES)
          + f" | {_cell(terms.get('notice', '—'))} |"
          for key, terms in inventory["licenses"].items()),
        "",
    ]


def _artifact_lines(inventory: dict, pins: Pins) -> list[str]:
    licenses = inventory["licenses"]
    return [
        "## 4. Container images",
        "",
        "One row per image in a service manifest's `images:` list. Locally built images appear "
        "as the base image their Dockerfile builds on; what the build installs on top is open "
        "items R-PY and R-BUILD. A row overrides its license's mode values only where its own "
        "terms differ.",
        "",
        *_image_rows(inventory["images"], licenses, _ROW_KEYS["images"], "| Service · variable"),
        "",
        "### 4.1. Images only a Dockerfile pins",
        "",
        "An external image a Dockerfile builds from or copies out of (`FROM`, `COPY --from`, "
        "`RUN --mount=from`, resolved through ARG defaults) that no manifest declares, such as "
        "a multi-stage build's runtime stage. The container-security scan reads Dockerfiles "
        "the same way.",
        "",
        *_image_rows(inventory.get("build_images", []), licenses, ("dockerfile",), "| Dockerfile"),
        "",
        "## 5. Model weights",
        "",
        "One row per model in `services/comfyui/models.yaml` and `services/ollama/models.yaml`, "
        "listing every file the entry downloads. A ComfyUI model's license comes from its "
        "catalogue entry's `license_name`, `license_url` and `license_restrictions` fields, "
        "which the setup wizard also shows.",
        "",
        *_model_rows(inventory, pins),
        "",
    ]


_CLOSING = [
    "## 6. Not in this inventory",
    "",
    "- Cloud models in `services/litellm/models.yaml`: Atlas calls them as APIs under each "
    "provider's terms of service and downloads no weights.",
    "- Models an operator adds in `services/comfyui/custom-models.yaml`: the operator "
    "chooses them and owns their terms.",
    "- Weights a service downloads by its own configuration rather than from a catalogue, "
    "such as the TEI reranker model: open item R-SVC-MODELS.",
    "- Atlas's own code, which is licensed under Apache 2.0 (the repository's `LICENSE`).",
    "",
    "## 7. Keeping it current",
    "",
    "`make docs-check` runs the same check as `python -m scripts.docs.license_inventory "
    "--check`, which fails when a manifest image default, a Dockerfile image, or a catalogue "
    "model's download URLs or license URL differ from their row, when an artifact has no row, when "
    "a license URL is not pinned to a revision, and when an unresolved mode has no open item "
    "that concerns it. When a pin moves, read the license at the new revision, update the "
    "row's `image` or `sources`, `url`, `notice` and mode values in "
    "`docs/reference/license-inventory.yaml`, then regenerate this page with "
    "`uv run --project bootstrapper python -m scripts.docs.canonical_references`.",
    "",
    "Reviewing this inventory is a pre-tag step of every release; see "
    "[Releasing](../operations/releasing.md) §3.",
]


def render_license_inventory(inventory: dict, pins: Pins) -> str:
    return "\n".join([
        *_intro_lines(inventory), *_terms_lines(inventory),
        *_artifact_lines(inventory, pins), *_CLOSING,
    ]) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the supply-chain license inventory")
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero on any finding (without it, only report)")
    args = parser.parse_args()
    root = Path.cwd()
    inventory = load_inventory(root)
    findings = inventory_findings(inventory, load_pins(root))
    for finding in findings:
        print(f"ERROR {finding}")
    if findings:
        raise SystemExit(1 if args.check else 0)
    floating = sum(1 for row in inventory["models"] if "unpinned_source" in row)
    print(f"PASS license inventory matches every image and model pin; {floating} model "
          f"source(s) float and are carried as open items")


if __name__ == "__main__":
    main()
