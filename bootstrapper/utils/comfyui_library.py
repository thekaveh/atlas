"""ComfyUI model catalog — scrapers, parsers, and types.

Mirrors ``bootstrapper/utils/llm_catalog.py``'s role for the ComfyUI side:
a pure-functional, side-effect-free catalog module imported by the wizard
(host-side) and by ``comfyui_resolver`` / ``comfyui_manifest_generator`` to
compute the active model set at bootstrapper start.

**Curated catalog SoT** (C1)
The hand-curated model list formerly embedded as ``_CURATED_ENTRIES`` in this
file now lives in ``services/comfyui/models.yaml``.  ``list_curated()`` reads
that YAML at call time (no import-time side effects).  Path resolution mirrors
``llm_catalog._find_models_dir()``:

  1. ``ATLAS_MODELS_DIR`` env var, if set.
  2. ``<repo_root>/services``  (repo_root = 3 parents above this file:
     ``bootstrapper/utils/comfyui_library.py`` → ``utils/`` → ``bootstrapper/``
     → ``repo_root``; then ``repo_root/services``).
  3. ``/catalog``  (defensive remnant of the former comfyui-catalog-init
     bind-mount; that container was deleted and no compose fragment mounts
     ``/catalog`` today — this path is unreachable in normal operation but
     kept harmlessly as a last-resort fallback).

The wizard **also** live-scrapes HuggingFace and civitai on each invocation;
those results are merged with the curated entries (curated wins on name
collision, last in the dedupe pass).  If both scrapers are down, the bundled
fallback snapshot at ``bootstrapper/utils/data/comfyui_catalog_fallback.json``
is used instead.  User additions go in ``services/comfyui/custom-models.yaml``.

There is no host-side file cache — the wizard runs once per invocation and
assembles the catalog live (``assemble_wizard_catalog``).  The bootstrapper
then writes the active set into ``volumes/comfyui/{selected-models.yaml,
active-models.tsv}`` via ``comfyui_manifest_generator``.  ``comfyui-init``
downloads from the TSV; the backend's ``GET /comfyui/db/models`` reads the
manifest YAML.  No DB; the former ``public.comfyui_models`` table was removed.
"""
from __future__ import annotations

import json as _json
import os as _os
import sys as _sys
from dataclasses import dataclass
from pathlib import Path as _Path

import requests as _requests
import yaml as _yaml


# ── Category enum ──────────────────────────────────────────────────────
# Closed enum mapping to in-container target directories.
# Adding a category here means:
#   1. Update CATEGORY_TARGET_DIR (this file).
#   2. Add a `mkdir -p` line in services/comfyui/init/scripts/download_models.sh.
#   3. Place it in a display group in CATEGORY_DISPLAY_GROUPS below.
CATEGORY_TARGET_DIR: dict[str, str] = {
    "checkpoint":       "checkpoints",
    "vae":              "vae",
    "lora":             "loras",
    "controlnet":       "controlnet",
    "ipadapter":        "ipadapter",
    "instantid":        "instantid",
    "upscaler":         "upscale_models",
    "embedding":        "embeddings",
    "clip":             "clip",
    "animatediff":      "animatediff_models",
    "motion_lora":      "animatediff_motion_lora",
    "video_model":      "checkpoints",  # HunyuanVideo / CogVideoX per upstream
    "voice_model":      "voice",
    "audio_model":      "audio",
    "mesh_model":       "mesh_models",
    "diffusion_models": "diffusion_models",  # FLUX / DiT / Krea 2 / SD3 transformer weights
    "text_encoders":    "text_encoders",      # Large text encoders (t5xxl, Qwen3-VL, etc.)
}

VALID_CATEGORIES = frozenset(CATEGORY_TARGET_DIR.keys())

# Display groups drive the wizard's filter chips.
CATEGORY_DISPLAY_GROUPS: dict[str, frozenset[str]] = {
    "All":         VALID_CATEGORIES,
    "Image":       frozenset({"checkpoint", "vae", "lora", "upscaler",
                              "embedding", "clip",
                              "diffusion_models", "text_encoders"}),
    "Image-edit":  frozenset({"controlnet", "ipadapter", "instantid"}),
    "Video":       frozenset({"animatediff", "motion_lora", "video_model"}),
    "Audio":       frozenset({"voice_model", "audio_model"}),
    "3D":          frozenset({"mesh_model"}),
}


@dataclass(frozen=True)
class ComfyUIModelFile:
    """One downloadable file inside a logical ComfyUI model entry.

    Most catalog entries are still one logical row -> one file and leave
    ``ComfyUILibraryEntry.files`` empty. Bundles use this type to declare each
    concrete artifact with its own category, target directory, filename, URL,
    and optional SHA.
    """
    role: str
    category: str
    url: str
    filename: str | None = None
    sha256: str | None = None
    target_dir: str | None = None
    size_gb: float | None = None
    size_bytes: int | None = None
    precision: str | None = None
    variant: str | None = None

    def __post_init__(self) -> None:
        if self.category not in VALID_CATEGORIES:
            raise ValueError(f"unknown ComfyUI model file category: {self.category!r}")
        if self.target_dir is None:
            object.__setattr__(self, "target_dir", CATEGORY_TARGET_DIR[self.category])


@dataclass(frozen=True)
class ComfyUILibraryEntry:
    """One catalog row.

    `pulled` is computed at wizard build time from a filesystem scan;
    every other field is static catalog metadata.
    """
    name: str
    family: str
    category: str
    size_gb: float
    url: str
    sha256: str | None
    target_dir: str
    min_vram_gb: float | None
    cpu_supported: bool
    requires_custom_node: tuple[str, ...]
    popularity: int
    source: str  # "huggingface" | "civitai" | "curated" | "fallback" | "custom"
    pulled: bool
    cloud_only: bool = False
    notes: str | None = None  # Optional one-line subtitle for wizard rendering (T15).
    # Explicit on-disk filename. Needed when the URL path carries none —
    # civitai download URLs are `/api/download/models/<id>?token=…`, so a
    # URL-derived name is a bare numeric id with no extension and ComfyUI's
    # extension-filtered scanner never lists the downloaded file.
    filename: str | None = None
    essential: bool = False  # True → activated by default even with empty COMFYUI_USER_MODELS.
    precision: str | None = None
    variant: str | None = None
    host_constraints: tuple[str, ...] = ()
    files: tuple[ComfyUIModelFile, ...] = ()
    size_bytes: int | None = None
    min_ram_gb: float | None = None
    license_name: str | None = None
    license_url: str | None = None
    license_restrictions: tuple[str, ...] = ()


# ── HF + civitai response parsers ──────────────────────────────────────
# These transform raw API JSON into ComfyUILibraryEntry instances.
# Live HTTP calls land in Task 5; parsers are pure-functional +
# fixture-tested here.

_HF_RESOLVE_BASE = "https://huggingface.co"

# HF tags → custom_nodes required. Conservative list — only add when verified
# against the upstream node's README.
_HF_TAG_TO_CUSTOM_NODE: dict[str, str] = {
    "gguf":           "ComfyUI-GGUF",
    "animatediff":    "ComfyUI-AnimateDiff-Evolved",
    "image-to-3d":    "ComfyUI-3D-Pack",
    "text-to-3d":     "ComfyUI-3D-Pack",
    "instantid":      "ComfyUI_InstantID",
    "ip-adapter":     "ComfyUI_IPAdapter_plus",
}

_VALID_MODEL_EXTENSIONS = (".safetensors", ".ckpt", ".bin", ".gguf", ".pt")


def _pick_primary_file(siblings: list[dict]) -> dict | None:
    """Pick the largest compatible model file from a HF repo's siblings array.

    Returns None if no compatible file. Compatible = ends with one of
    _VALID_MODEL_EXTENSIONS. When multiple, prefer largest by size; ties
    broken by shortest filename (proxy for "base" vs "variant").
    """
    candidates = [
        s for s in siblings
        if isinstance(s, dict)
        and isinstance(s.get("rfilename"), str)
        and s["rfilename"].lower().endswith(_VALID_MODEL_EXTENSIONS)
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda s: (s.get("size", 0), -len(s.get("rfilename", ""))),
        reverse=True,
    )
    return candidates[0]


def _family_from_id(model_id: str) -> str:
    """Heuristic family label: last segment of the HF model id, with
    common suffixes trimmed."""
    name = model_id.rsplit("/", 1)[-1]
    for suffix in ("-v1.0", "-base", "-dev", "-schnell", "-pruned"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
    return name.replace("_", "-").title()


def _family_root(entry_name: str, source: str) -> str:
    """Extract the family root for grouping the wizard's option list.

    Only HF entries participate in family grouping — civitai numeric
    IDs (``civitai-264290``), hand-picked curated entries, and
    user-supplied sidecar entries always stay flat (the family-root
    return is the empty string, which the wizard treats as "no
    family", keeping the entry as a singleton row).

    For HF entries (name shape ``owner--repo``) the root is the
    leading run of letters of the repo portion:

    | name                              | root        |
    | --------------------------------- | ----------- |
    | microsoft--TRELLIS-image-large    | TRELLIS     |
    | microsoft--TRELLIS.2-4B           | TRELLIS     |
    | gqk--TRELLIS-image-large-fork     | TRELLIS     |
    | tencent--Hunyuan3D-2              | Hunyuan     |
    | yyfz233--Pi3                      | Pi          |
    | yyfz233--Pi3X                     | Pi          |
    | stabilityai--stable-diffusion-xl  | stable      |

    Trade-off: ``stable`` over-groups SDXL / SD 1.5 / Stable-Cascade /
    Stable-Video; the wizard accepts this in exchange for catching
    the much more common case (TRELLIS, Hunyuan, Pi, etc.) without
    a per-model curated map.
    """
    if source != "huggingface":
        return ""
    if "--" not in entry_name:
        return ""
    _, repo = entry_name.split("--", 1)

    # Leading-letters extraction with a camel-case stop. A
    # capital-then-lowercase prefix (``Hunyuan``, ``Tripo``, ``Pi``)
    # terminates at the NEXT capital so that ``HunyuanImage`` groups
    # with ``Hunyuan3D`` under the same ``Hunyuan`` root instead of
    # falling into its own ``HunyuanImage`` family. Pure all-caps
    # tokens (``TRELLIS``, ``VGGT``) and pure-lowercase prefixes
    # (``stable``) are unaffected — both run until the first
    # non-letter character.
    import re as _re
    camel = _re.match(r"^([A-Z][a-z]+)", repo)
    if camel:
        return camel.group(1)
    leading = _re.match(r"^([A-Za-z]+)", repo)
    return leading.group(1) if leading else ""


def _custom_nodes_from_tags(tags: list[str]) -> tuple[str, ...]:
    nodes: list[str] = []
    for t in tags:
        node = _HF_TAG_TO_CUSTOM_NODE.get(str(t).lower())
        if node and node not in nodes:
            nodes.append(node)
    return tuple(nodes)


def _parse_hf_response(
    raw: list[dict],
    category: str,
) -> list[ComfyUILibraryEntry]:
    """Convert a single-category HF /api/models response into entries.

    Raises ValueError for unknown category.
    """
    if category not in VALID_CATEGORIES:
        raise ValueError(f"unknown category: {category}")
    target_dir = CATEGORY_TARGET_DIR[category]
    out: list[ComfyUILibraryEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        siblings = item.get("siblings") or []
        if not isinstance(siblings, list):
            continue
        primary = _pick_primary_file(siblings)
        if primary is None:
            continue
        model_id = item.get("id") or item.get("modelId")
        if not model_id:
            continue
        rfilename = primary["rfilename"]
        url = f"{_HF_RESOLVE_BASE}/{model_id}/resolve/main/{rfilename}"
        size_bytes = primary.get("size") or 0
        size_gb = round(size_bytes / (1024 ** 3), 2) if size_bytes else 0.0
        tags = item.get("tags") or []
        out.append(ComfyUILibraryEntry(
            name=model_id.replace("/", "--"),
            family=_family_from_id(model_id),
            category=category,
            size_gb=size_gb,
            url=url,
            sha256=None,
            target_dir=target_dir,
            min_vram_gb=None,
            cpu_supported=("cpu" in tags or category in ("vae", "embedding", "clip")),
            requires_custom_node=_custom_nodes_from_tags(tags),
            popularity=int(item.get("downloads") or 0),
            source="huggingface",
            pulled=False,
        ))
    return out


def _parse_civitai_response(
    raw: dict | list,
    category: str,
) -> list[ComfyUILibraryEntry]:
    """Convert civitai /api/v1/models response into entries.

    Civitai wraps results in {"items": [...]}. Each item has a
    modelVersions[].files[] array; pick the primary file (primary=True
    or first .safetensors).
    """
    if category not in VALID_CATEGORIES:
        raise ValueError(f"unknown category: {category}")
    items = raw.get("items", []) if isinstance(raw, dict) else raw
    target_dir = CATEGORY_TARGET_DIR[category]
    out: list[ComfyUILibraryEntry] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        versions = item.get("modelVersions") or []
        if not isinstance(versions, list):
            continue
        if not versions:
            continue
        primary_version = versions[0]
        files = primary_version.get("files") or []
        primary_file = next(
            (f for f in files if isinstance(f, dict) and f.get("primary") is True),
            files[0] if files else None,
        )
        if not isinstance(primary_file, dict):
            continue
        url = primary_file.get("downloadUrl")
        if not url:
            continue
        size_kb = primary_file.get("sizeKB") or 0
        size_gb = round(size_kb / (1024 * 1024), 2) if size_kb else 0.0
        stats = item.get("stats") or {}
        out.append(ComfyUILibraryEntry(
            name=f"civitai-{item.get('id', 'unknown')}",
            family=str(item.get("name", "Unknown"))[:40],
            category=category,
            size_gb=size_gb,
            url=url,
            sha256=(primary_file.get("hashes") or {}).get("SHA256"),
            target_dir=target_dir,
            min_vram_gb=None,
            cpu_supported=False,
            requires_custom_node=(),
            popularity=int(stats.get("downloadCount") or 0),
            source="civitai",
            pulled=False,
            filename=primary_file.get("name"),
        ))
    return out


# ── Curated catalog YAML path resolution ──────────────────────────────
# services/comfyui/models.yaml is the curated SoT (C1).  This loader runs
# HOST-SIDE only (the bootstrapper's comfyui_manifest_generator at start +
# the wizard) — no container imports it: comfyui-catalog-init was deleted
# and comfyui-init is a pure shell script (download_models.sh).
# Search order mirrors llm_catalog._find_models_dir():
#   1. ATLAS_MODELS_DIR env var
#   2. <repo_root>/services  (3 parents above this file)
#   3. /catalog              (defensive remnant of the former comfyui-catalog-init
#                             bind-mount; no compose fragment mounts /catalog today)

def _find_services_dir() -> _Path:
    """Resolve the directory that contains comfyui/models.yaml.

    This function runs HOST-SIDE only (bootstrapper venv).  No container
    imports comfyui_library; comfyui-init is a pure shell script.

    Search order:
      1. ``ATLAS_MODELS_DIR`` env var.
      2. ``<repo_root>/services``  (repo_root = 3 parents above this file).
      3. ``/catalog``  (defensive remnant of the former comfyui-catalog-init
         bind-mount; unreachable in normal operation — kept as a last-resort
         fallback only).
    """
    env_dir = _os.environ.get("ATLAS_MODELS_DIR")
    if env_dir:
        return _Path(env_dir)

    # bootstrapper/utils/comfyui_library.py → utils/ → bootstrapper/ → repo_root
    repo_root = _Path(__file__).resolve().parent.parent.parent
    candidates = [repo_root / "services", _Path("/catalog")]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate

    raise FileNotFoundError(
        "Cannot locate services directory for comfyui models.yaml. Tried: "
        + str([str(c) for c in candidates])
        + ". Set ATLAS_MODELS_DIR to override."
    )


def _find_comfyui_yaml() -> _Path:
    """Locate services/comfyui/models.yaml (host-side, normal case).

    Tries:
      1. ``<services_dir>/comfyui/models.yaml``  — host layout (expected path).
      2. ``<services_dir>/comfyui-models.yaml``  — flat layout under /catalog
         (defensive remnant of the former comfyui-catalog-init bind-mount;
         unreachable in normal operation).
    """
    base = _find_services_dir()
    primary = base / "comfyui" / "models.yaml"
    if primary.exists():
        return primary
    flat = base / "comfyui-models.yaml"
    if flat.exists():
        return flat
    raise FileNotFoundError(
        f"Cannot find ComfyUI curated catalog YAML. Tried: {primary}, {flat}. "
        "Ensure services/comfyui/models.yaml is present (host) or bind-mounted "
        "as /catalog/comfyui-models.yaml (container)."
    )


def _dict_to_model_file(d: dict) -> ComfyUIModelFile:
    """Translate one bundle file mapping into a ComfyUIModelFile."""
    url = d["url"]
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise ValueError(f"bundle file {d.get('role', '<unknown>')!r} has non-http(s) url")
    return ComfyUIModelFile(
        role=d["role"],
        category=d["category"],
        url=url,
        filename=d.get("filename"),
        sha256=d.get("sha256"),
        target_dir=d.get("target_dir"),
        size_gb=d.get("size_gb"),
        size_bytes=d.get("size_bytes"),
        precision=d.get("precision"),
        variant=d.get("variant"),
    )


def _dict_to_entry(d: dict, source: str) -> ComfyUILibraryEntry:
    """Translate a dict (curated, fallback, cache, or sidecar/custom) to a
    ComfyUILibraryEntry.

    Coerces `requires_custom_node` to tuple so callers can pass either
    JSON-list (fallback/cache/custom) or native tuple (curated).
    Defaults missing `size_gb` to 0.0 (sidecar makes this optional;
    curated/fallback always supply it). Falls back to CATEGORY_TARGET_DIR
    for `target_dir` when absent. Sets `source` and `pulled=False`
    (pulled is computed at wizard time, never from input).
    """
    cat = d["category"]
    files = tuple(_dict_to_model_file(f) for f in (d.get("files") or ()))
    url = d.get("url") or (files[0].url if files else None)
    if not url:
        raise ValueError(f"ComfyUI model entry {d.get('name', '<unknown>')!r} missing url")
    return ComfyUILibraryEntry(
        name=d["name"],
        family=d.get("family", d["name"]),
        category=cat,
        size_gb=d.get("size_gb") or 0.0,
        url=url,
        sha256=d.get("sha256"),
        target_dir=d.get("target_dir", CATEGORY_TARGET_DIR[cat]),
        min_vram_gb=d.get("min_vram_gb"),
        cpu_supported=d.get("cpu_supported", True),
        requires_custom_node=tuple(d.get("requires_custom_node") or ()),
        popularity=d.get("popularity", 0),
        source=source,
        pulled=False,
        cloud_only=bool(d.get("cloud_only", False)),
        notes=d.get("notes"),
        filename=d.get("filename"),
        essential=bool(d.get("essential", False)),
        precision=d.get("precision"),
        variant=d.get("variant"),
        host_constraints=tuple(d.get("host_constraints") or ()),
        files=files,
        size_bytes=d.get("size_bytes"),
        min_ram_gb=d.get("min_ram_gb"),
        license_name=d.get("license_name"),
        license_url=d.get("license_url"),
        license_restrictions=tuple(d.get("license_restrictions") or ()),
    )


def list_curated() -> list[ComfyUILibraryEntry]:
    """Return curated entries loaded from services/comfyui/models.yaml.

    Path resolved at call time (not import time) via ``_find_comfyui_yaml()``.
    Runs HOST-SIDE only — called by the wizard and by
    ``comfyui_manifest_generator`` at bootstrapper start.  No container
    imports this function; comfyui-init is a pure shell script.

    Raises RuntimeError if the curated YAML is missing or unparseable —
    services/comfyui/models.yaml is a REQUIRED file (unlike the optional
    fallback JSON).  A silent empty catalog would silently drop the curated
    SoT, which is always a misconfiguration.
    """
    try:
        yaml_path = _find_comfyui_yaml()
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"ComfyUI curated catalog YAML is required but could not be located: {exc}"
        ) from exc
    try:
        raw = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except _yaml.YAMLError as exc:
        raise RuntimeError(
            f"ComfyUI curated catalog YAML at {yaml_path} is unparseable: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise RuntimeError(
            f"ComfyUI curated catalog YAML at {yaml_path} must have a top-level "
            f"'models:' mapping; got {type(raw).__name__}."
        )
    return [_dict_to_entry(d, "curated") for d in (raw.get("models") or [])]


# ── Bundled fallback snapshot ──────────────────────────────────────────

_FALLBACK_FILE = _Path(__file__).parent / "data" / "comfyui_catalog_fallback.json"


def list_fallback() -> list[ComfyUILibraryEntry]:
    """Load the bundled fallback snapshot. Used only when both APIs are
    unreachable AND no cached catalog exists.
    """
    if not _FALLBACK_FILE.is_file():
        return []
    raw = _json.loads(_FALLBACK_FILE.read_text(encoding="utf-8"))
    return [_dict_to_entry(d, "fallback") for d in raw.get("entries", [])]


# ── HTTP scraping + cache ──────────────────────────────────────────────

_HF_API_BASE = "https://huggingface.co/api/models"
_CIVITAI_API_BASE = "https://civitai.com/api/v1/models"
_HTTP_TIMEOUT_S = 15

# Per-category HF filter sets. Each entry → one API call; results merged.
# Maintain this dict to evolve catalog coverage.
_HF_FILTERS: dict[str, list[dict]] = {
    "checkpoint": [
        {"pipeline_tag": "text-to-image", "library": "diffusers",
         "sort": "downloads", "limit": 30},
    ],
    "lora": [
        {"filter": "lora", "pipeline_tag": "text-to-image",
         "sort": "downloads", "limit": 20},
    ],
    # HF expects `library:` for ControlNet (not `filter:`); per HF API semantics.
    "controlnet": [
        {"library": "controlnet", "sort": "downloads", "limit": 20},
    ],
    "video_model": [
        {"pipeline_tag": "text-to-video", "sort": "downloads", "limit": 15},
    ],
    "voice_model": [
        {"pipeline_tag": "text-to-speech", "sort": "downloads", "limit": 15},
    ],
    "mesh_model": [
        {"filter": "image-to-3d", "sort": "downloads", "limit": 15},
        {"filter": "text-to-3d", "sort": "downloads", "limit": 10},
    ],
    "animatediff": [
        {"filter": "animatediff", "sort": "downloads", "limit": 10},
    ],
}


_HF_SIZE_FETCH_MAX_WORKERS = 10


def _enrich_siblings_with_sizes(items: list[dict]) -> None:
    """In-place: enrich each item's siblings[] with real file sizes.

    HF's list endpoint never returns sibling sizes — only filenames —
    even with ``full=true``. To get actual file sizes we have to call
    ``/api/models/{repo}?blobs=true`` for each repo individually.
    These per-repo calls fan out across a ThreadPoolExecutor so the
    wall-clock cost stays in the ~2-3s range (vs ~30s sequential)
    for the ~130 entries the catalog typically scrapes.

    Per-repo failures are silent — the corresponding entry keeps its
    sizeless siblings and ``_pick_primary_file`` falls back to picking
    by filename, ``_parse_hf_response`` records size_gb=0.0 (rendered
    as a `[?GB]` placeholder downstream). One slow / 5xx repo does
    not block the rest of the catalog.
    """
    from concurrent.futures import ThreadPoolExecutor

    def _fetch_one(item: dict) -> tuple[dict, dict[str, int]]:
        model_id = item.get("id") or item.get("modelId")
        if not model_id:
            return item, {}
        try:
            resp = _requests.get(
                f"{_HF_API_BASE}/{model_id}",
                params={"blobs": "true"},
                timeout=_HTTP_TIMEOUT_S,
            )
            resp.raise_for_status()
            data = resp.json()
        except (_requests.RequestException, ConnectionError):
            return item, {}
        sib_with_size: dict[str, int] = {}
        for s in data.get("siblings") or []:
            if not isinstance(s, dict):
                continue
            name = s.get("rfilename")
            size = s.get("size")
            if isinstance(name, str) and isinstance(size, int):
                sib_with_size[name] = size
        return item, sib_with_size

    if not items:
        return
    with ThreadPoolExecutor(max_workers=_HF_SIZE_FETCH_MAX_WORKERS) as ex:
        for item, size_map in ex.map(_fetch_one, items):
            if not size_map:
                continue
            for s in item.get("siblings") or []:
                if not isinstance(s, dict):
                    continue
                name = s.get("rfilename")
                if isinstance(name, str) and name in size_map:
                    s["size"] = size_map[name]


def list_huggingface_models() -> list[ComfyUILibraryEntry]:
    """Hit the HF API per _HF_FILTERS; parse + merge.

    Two-phase scrape:
      1. List endpoint with ``full=true`` returns repo metadata +
         siblings[].rfilename (no sizes — HF API limitation).
      2. _enrich_siblings_with_sizes fans out per-repo
         ``/api/models/{id}?blobs=true`` calls across a thread pool
         to populate siblings[].size in place.

    Then ``_pick_primary_file`` (size-aware) selects the largest
    model file and ``_parse_hf_response`` records the real size_gb.
    Without step 2 every catalog entry would show 0.00GB.

    Raises requests.RequestException / ConnectionError on transport
    failure during step 1 (the list call). Step-2 failures are
    silent per-repo — see ``_enrich_siblings_with_sizes``.
    """
    out: list[ComfyUILibraryEntry] = []
    for category, filters in _HF_FILTERS.items():
        for params in filters:
            full_params = {**params, "full": "true"}
            resp = _requests.get(_HF_API_BASE, params=full_params, timeout=_HTTP_TIMEOUT_S)
            resp.raise_for_status()
            items = resp.json()
            if isinstance(items, list):
                _enrich_siblings_with_sizes(items)
                out.extend(_parse_hf_response(items, category=category))
    return out


def list_civitai_loras() -> list[ComfyUILibraryEntry]:
    """Anonymous civitai API for LoRAs.

    We don't fetch checkpoints from civitai — too noisy; HF is the source
    of truth for those.
    """
    out: list[ComfyUILibraryEntry] = []
    for category, ctype in (("lora", "LORA"),):
        resp = _requests.get(
            _CIVITAI_API_BASE,
            params={"sort": "Most Downloaded", "types": ctype, "limit": 20},
            timeout=_HTTP_TIMEOUT_S,
        )
        resp.raise_for_status()
        out.extend(_parse_civitai_response(resp.json(), category=category))
    return out


# ── Merge ──────────────────────────────────────────────────────────────

def _dedupe_by_name(
    entries: list[ComfyUILibraryEntry],
) -> list[ComfyUILibraryEntry]:
    """Last-wins dedupe by name. Caller controls precedence via input order.

    Established precedence for assemble_wizard_catalog (lowest → highest):
        huggingface → civitai → fallback (if used) → curated
    So pass curated LAST when merging; the last write to the dict wins.
    """
    seen: dict[str, ComfyUILibraryEntry] = {}
    for e in entries:
        seen[e.name] = e
    return list(seen.values())


def assemble_wizard_catalog(
    force_refresh: bool = False,
) -> list[ComfyUILibraryEntry]:
    """Live-assembled, merged catalog: HF + civitai + curated
    (+ fallback if both scrapers are down).

    Called once per wizard invocation. NO caching — there is no host-side
    file cache (``comfyui_manifest_generator`` writes the active set to
    ``volumes/comfyui/`` at bootstrapper start; this function is the producer).
    The ``force_refresh`` parameter is accepted for API parity with
    upstream ``list_*`` helpers but is currently a no-op.

    Dedupe precedence (last wins): huggingface → civitai → fallback → curated.
    Partial failure (one scraper down) → uses whatever returned + curated.
    Total failure (both down) → loads the bundled fallback snapshot.
    """
    del force_refresh  # accepted for API parity; no cache to bypass

    hf_entries: list[ComfyUILibraryEntry] = []
    civ_entries: list[ComfyUILibraryEntry] = []
    hf_ok = True
    civ_ok = True

    try:
        hf_entries = list_huggingface_models()
    except (_requests.RequestException, ConnectionError) as exc:
        print(f"WARNING: HuggingFace catalog fetch failed: {exc}",
              file=_sys.stderr)
        hf_ok = False
    try:
        civ_entries = list_civitai_loras()
    except (_requests.RequestException, ConnectionError) as exc:
        print(f"WARNING: civitai catalog fetch failed: {exc}", file=_sys.stderr)
        civ_ok = False

    curated = list_curated()
    # Order matters — last wins.
    if not hf_ok and not civ_ok:
        # Both APIs down — supplement with bundled fallback before curated.
        print("WARNING: All catalog sources unreachable; using bundled fallback.",
              file=_sys.stderr)
        return _dedupe_by_name(
            hf_entries + civ_entries + list_fallback() + curated
        )
    return _dedupe_by_name(hf_entries + civ_entries + curated)


# ── Sidecar YAML loader ────────────────────────────────────────────────

def load_custom_models(path: str) -> list[ComfyUILibraryEntry]:
    """Parse a sidecar YAML file into entries.

    Invalid entries skipped with stderr warnings; never raises for
    malformed YAML (returns empty list). Required: name, category, url.
    Optional: family, size_gb, sha256, requires_custom_node, cpu_supported,
    min_vram_gb, notes, filename (REQUIRED in practice for signed-civitai
    URLs whose path has no real filename — without it the download lands
    extension-less and ComfyUI never lists it).

    The loader uses `_dict_to_entry` so future schema changes only need
    a single edit point (same path as curated/fallback).
    """
    p = _Path(path)
    if not p.is_file():
        return []
    try:
        raw = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except _yaml.YAMLError as exc:
        print(f"⚠️  custom-models YAML parse failed at {path}: {exc}",
              file=_sys.stderr)
        return []

    if not isinstance(raw, dict):
        # A list-rooted file (models written at top level) used to crash
        # with AttributeError, contradicting the never-raises contract.
        shape = "empty file" if raw is None else f"top-level {type(raw).__name__}"
        print(f"⚠️  custom-models YAML at {path} must have a top-level "
              f"'models:' mapping; got {shape}. Ignoring file.",
              file=_sys.stderr)
        return []
    raw_models = raw.get("models") or []
    out: list[ComfyUILibraryEntry] = []
    for idx, d in enumerate(raw_models):
        if not isinstance(d, dict):
            print(f"⚠️  custom-models[{idx}] is not a mapping; skipping.",
                  file=_sys.stderr)
            continue
        name = d.get("name")
        category = d.get("category")
        url = d.get("url")
        files = d.get("files") or []
        if not name:
            print(f"⚠️  custom-models[{idx}] missing 'name'; skipping.",
                  file=_sys.stderr)
            continue
        if not url and not files:
            print(f"⚠️  custom-models entry '{name}' missing 'url' or 'files'; skipping.",
                  file=_sys.stderr)
            continue
        if url and (not isinstance(url, str) or not url.startswith(("http://", "https://"))):
            print(f"⚠️  custom-models entry '{name}' has non-http(s) url; skipping.",
                  file=_sys.stderr)
            continue
        if category not in VALID_CATEGORIES:
            print(f"⚠️  custom-models entry '{name}' has unknown category "
                  f"'{category}'; skipping.", file=_sys.stderr)
            continue
        try:
            out.append(_dict_to_entry(d, source="custom"))
        except (KeyError, ValueError) as exc:
            print(f"⚠️  custom-models entry '{name}' construction failed: {exc}",
                  file=_sys.stderr)
            continue
    return out
