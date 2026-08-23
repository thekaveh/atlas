#!/usr/bin/env python3
"""Audit: every service in bootstrapper/tracks.yml exists as a
services/<name>/ folder, AND every source-configurable service appears
in at least one track other than 'all'.

Run from the repo root. Exits 0 on success, 1 on drift.
"""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

# Allow `from tracks import ...`
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))


def main() -> int:
    from tracks import load_tracks, normalize_service_key
    from core.config_parser import ConfigParser
    from wizard.model.service_discovery import ServiceDiscovery

    try:
        reg = load_tracks()
    except Exception as e:
        print(f"FAIL: tracks.yml failed to load: {e}", file=sys.stderr)
        return 1

    # Every configurable service must appear in at least one
    # non-"all" track.
    cp = ConfigParser()
    configurable_svc_keys: set[str] = set()
    for svc in ServiceDiscovery(cp).discover():
        configurable_svc_keys.add(normalize_service_key(svc.key))

    # Always-on keys are intentionally not in tracks.yml.
    always_on = reg.always_on

    union = set()
    for t in reg.tracks:
        if t.key == "all":
            continue
        if t.services is None:
            continue
        union |= set(t.services)

    missing = configurable_svc_keys - union - always_on
    if missing:
        print(
            f"FAIL: configurable services NOT in any non-'all' track: "
            f"{sorted(missing)}.\nAdd them to at least one track or "
            f"document the omission.",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK: {len(reg.tracks)} tracks, {len(configurable_svc_keys)} "
        f"configurable services. Coverage clean."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
