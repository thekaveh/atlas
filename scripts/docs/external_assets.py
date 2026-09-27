"""Build-time external asset contract for the documentation site (#1053).

The Material ``privacy`` plugin self-hosts the theme fonts and the Mermaid
bundle by downloading them during ``mkdocs build``. ``docs/external-assets.yaml``
records every asset a clean-cache build fetches. This module turns that record
into two checks:

``--preflight``
    Runs before the strict build. An asset already in the plugin cache needs
    no network. A missing one is probed; if it cannot be fetched the build
    stops here with the asset's name and URL, instead of reaching a bare
    "Aborted with 1 warnings in strict mode" several minutes later.

``--verify-cache``
    Runs after a clean-cache build and diffs the cache against the record:
    missing, unexpected and checksum-mismatched files.

``--print-inventory`` rebuilds the record from a warm cache, for review when
the theme fonts or mkdocs-material change.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

INVENTORY = Path("docs/external-assets.yaml")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_KINDS = ("stylesheet", "font", "script")
# The plugin sends a desktop browser user agent so Google Fonts serves woff2;
# probe with the same one so a preflight success means the build's request
# would succeed too.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36"
)
_PROBE_TIMEOUT_SECONDS = 15
_POLICY = "docs/development.md §3.1"


class InventoryError(ValueError):
    """Raised when docs/external-assets.yaml is malformed."""


@dataclass(frozen=True)
class Asset:
    name: str
    kind: str
    version: str
    url: str
    path: str
    sha256: str | None


@dataclass(frozen=True)
class Inventory:
    cache_root: str
    assets: tuple[Asset, ...]


def _entry_problem(raw: dict[str, Any]) -> str | None:
    missing = [key for key in ("name", "kind", "version", "url", "path") if not raw.get(key)]
    if missing:
        return f"is missing {', '.join(missing)}"
    digest = raw.get("sha256")
    checks = (
        (raw["kind"] in _KINDS, f"kind must be one of {', '.join(_KINDS)}"),
        (digest is None or bool(_SHA256_RE.match(str(digest))),
         "sha256 must be 64 lowercase hex digits or null"),
        (str(raw["url"]).startswith("https://"), "url must be https"),
        (str(raw["path"]).startswith("assets/external/"),
         "path must live under assets/external/"),
    )
    return next((message for ok, message in checks if not ok), None)


def _parse_asset(raw: Any, index: int) -> Asset:
    if not isinstance(raw, dict):
        raise InventoryError(f"assets[{index}] must be a mapping")
    problem = _entry_problem(raw)
    if problem:
        raise InventoryError(f"assets[{index}] {problem}")
    digest = raw.get("sha256")
    return Asset(
        name=str(raw["name"]), kind=str(raw["kind"]), version=str(raw["version"]),
        url=str(raw["url"]), path=str(raw["path"]),
        sha256=None if digest is None else str(digest),
    )


def parse_inventory(text: str) -> Inventory:
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or not isinstance(data.get("assets"), list):
        raise InventoryError("inventory must be a mapping with an assets list")
    assets = tuple(_parse_asset(raw, i) for i, raw in enumerate(data["assets"]))
    paths = [asset.path for asset in assets]
    duplicates = sorted({path for path in paths if paths.count(path) > 1})
    if duplicates:
        raise InventoryError(f"duplicate asset paths: {', '.join(duplicates)}")
    return Inventory(cache_root=str(data.get("cache_root") or ""), assets=assets)


def load_inventory(path: Path) -> Inventory:
    return parse_inventory(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def probe(url: str) -> None:
    """Raise when ``url`` cannot be fetched the way the build will fetch it.

    Streams so only the headers are read; the build downloads the body.
    """
    import requests

    with requests.get(
        url, headers={"User-Agent": _USER_AGENT},
        timeout=_PROBE_TIMEOUT_SECONDS, stream=True,
    ) as response:
        response.raise_for_status()


def _unfetchable(asset: Asset, cache: Path, fetch: Callable[[str], None]) -> str | None:
    try:
        fetch(asset.url)
    except Exception as error:  # noqa: BLE001 - every failure is reported, none raised
        return f"{asset.name} ({asset.url}) is not in {cache} and cannot be fetched: {error}"
    return None


def preflight(
    inventory: Inventory, cache: Path, fetch: Callable[[str], None] = probe,
) -> list[str]:
    """Problems that would make the strict build fail, one line per asset.

    Missing assets are probed concurrently, so a host that drops packets
    costs one probe timeout rather than one per asset.
    """
    problems: list[str] = []
    missing: list[Asset] = []
    for asset in inventory.assets:
        cached = cache / asset.path
        if not cached.is_file():
            missing.append(asset)
        elif asset.sha256 and _sha256(cached) != asset.sha256:
            problems.append(
                f"{asset.name} ({asset.url}): the cached copy at {cached} does "
                "not match the recorded sha256; delete it so the build refetches it"
            )
    if missing:
        with ThreadPoolExecutor(max_workers=min(8, len(missing))) as pool:
            results = pool.map(lambda asset: _unfetchable(asset, cache, fetch), missing)
            problems += [problem for problem in results if problem]
    return problems


def _cached_files(cache: Path) -> set[str]:
    """Cache-relative paths of the downloaded assets.

    Symlinks are the plugin's own aliases for extensionless URLs (the
    stylesheet is stored as ``css.<hash>.css`` behind ``css.<hash>``), not
    assets in their own right.
    """
    external = cache / "assets" / "external"
    if not external.is_dir():
        return set()
    return {
        path.relative_to(cache).as_posix()
        for path in external.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def verify_cache(inventory: Inventory, cache: Path) -> list[str]:
    """Differences between the plugin cache and the recorded inventory."""
    present = _cached_files(cache)
    recorded = {asset.path: asset for asset in inventory.assets}
    differences = [
        f"missing: {path} ({recorded[path].url})" for path in sorted(set(recorded) - present)
    ]
    differences += [f"unexpected: {path}" for path in sorted(present - set(recorded))]
    for path in sorted(present & set(recorded)):
        expected = recorded[path].sha256
        if expected and _sha256(cache / path) != expected:
            differences.append(f"changed: {path} no longer matches its recorded sha256")
    return differences


def _font_faces(css: str) -> dict[str, dict[str, Any]]:
    faces: dict[str, dict[str, Any]] = {}
    for match in re.finditer(r"/\* ([\w-]+) \*/\s*@font-face \{(.*?)\}", css, re.S):
        subset, body = match.groups()
        url = re.search(r"url\((https://[^)]+)\)", body)
        family = re.search(r"font-family: '([^']+)'", body)
        style = re.search(r"font-style: (\w+)", body)
        weight = re.search(r"font-weight: (\d+)", body)
        if not (url and family and style and weight):
            continue
        face = faces.setdefault(url.group(1), {
            "family": family.group(1), "style": style.group(1),
            "subset": subset, "weights": set(),
        })
        face["weights"].add(int(weight.group(1)))
    return faces


def print_inventory(inventory: Inventory, cache: Path) -> str:
    """The recorded inventory refreshed from a warm cache, as YAML entries.

    Font entries are rebuilt from the cached stylesheet; the stylesheet and
    script entries keep their recorded URL and metadata. Review the output
    against the committed file rather than pasting it blind.
    """
    stylesheets = [asset for asset in inventory.assets if asset.kind == "stylesheet"]
    scripts = [asset for asset in inventory.assets if asset.kind == "script"]
    faces = _cached_faces(stylesheets, cache)
    fonts = [_font_entry(url, faces[url], cache) for url in sorted(faces, key=lambda u: (
        faces[u]["family"], faces[u]["style"], faces[u]["subset"],
    ))]
    ordered = [a.__dict__ for a in stylesheets] + fonts + [a.__dict__ for a in scripts]
    return yaml.safe_dump({"assets": ordered}, sort_keys=False, width=200)


def _cached_faces(stylesheets: list[Asset], cache: Path) -> dict[str, dict[str, Any]]:
    faces: dict[str, dict[str, Any]] = {}
    for sheet in stylesheets:
        if (cache / sheet.path).is_file():
            faces.update(_font_faces((cache / sheet.path).read_text(encoding="utf-8")))
    return faces


def _font_entry(url: str, face: dict[str, Any], cache: Path) -> dict[str, Any]:
    path = "assets/external/" + url.split("://", 1)[1]
    weights = sorted(face["weights"])
    span = f"{weights[0]}-{weights[-1]}" if len(weights) > 1 else str(weights[0])
    version = re.search(r"/(v\d+)/", url)
    return {
        "name": f"{face['family']} {face['style']} {span}, {face['subset']}",
        "kind": "font",
        "version": f"{face['family']} {version.group(1) if version else 'unversioned'}",
        "url": url,
        "path": path,
        "sha256": _sha256(cache / path) if (cache / path).is_file() else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--verify-cache", action="store_true")
    mode.add_argument("--print-inventory", action="store_true")
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    args = parser.parse_args(argv)
    inventory = load_inventory(args.inventory)
    cache = Path(inventory.cache_root)
    if args.print_inventory:
        print(print_inventory(inventory, cache), end="")
        return 0
    if args.preflight:
        problems = preflight(inventory, cache)
        if problems:
            print(
                f"FAIL documentation build assets: {len(problems)} of "
                f"{len(inventory.assets)} cannot be supplied. The build is online-only "
                f"on a cold cache ({_POLICY}); restore a warm {cache} or allow "
                "access to these hosts:",
                file=sys.stderr,
            )
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1
        print(f"PASS documentation build assets ({len(inventory.assets)} cached or reachable)")
        return 0
    differences = verify_cache(inventory, cache)
    if differences:
        print(f"FAIL {cache} differs from {args.inventory}:", file=sys.stderr)
        for difference in differences:
            print(f"  - {difference}", file=sys.stderr)
        return 1
    print(f"PASS {cache} matches {args.inventory} ({len(inventory.assets)} assets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
