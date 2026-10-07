"""Static guards against fragment bind-mount path bug classes:

1. `services/<X>/services/<Y>/` doubled paths — Compose v2's `include:`
   resolves relative paths in an included fragment from the fragment's
   own directory, so a fragment at `services/<X>/compose.yml` that
   writes `./services/<X>/foo` (or `./services/<Y>/foo` from a typo /
   copy-paste between sibling fragments) silently produces a doubled
   path. PR #29 shipped exactly this for prometheus + grafana.

2. Bind sources that escape `REPO_ROOT` — a `../../../etc/...` source
   would silently bind-mount an arbitrary host path into a container,
   a security-relevant class of regression no current test catches.

Compose v2's `include:` directive resolves relative paths from the
fragment's own directory, not the parent compose file. PR #29's bug:
a fragment at `services/<X>/compose.yml` writing `./services/<X>/config/foo`
resolved to `services/<X>/services/<X>/config/foo`; Docker auto-created
the missing source as a *directory* on first launch, and the SECOND
launch failed with `not a directory` because the mount target expected
a file. `test_fragment_equivalence.py` couldn't catch it because the
committed baseline contained the same doubled paths.

The structural pattern `services/[^/]+/services/[^/]+/` is fundamentally
never correct, regardless of whether the inner and outer service names
match (a copy-paste between sibling fragments would not be a self-double
but would be just as broken).

The checks here are static (no Docker daemon) so they live in the
regular unit-test CI job and stay independent of any baseline — a buggy
fragment cannot pass by accidentally matching a buggy fixture.

Why not check that every resolved path *exists* on disk? Several
fragments legitimately mount runtime-generated paths that aren't on disk
in a fresh checkout (litellm's `volumes/litellm/config.yaml`, neo4j's
`build/snapshot`, supabase's `db/snapshot`, kong's
`volumes/api/kong-dynamic.yml`). An existence check produces false
positives for those without catching anything the structural checks
miss for either bug class above.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


_DOUBLED_SERVICES_PATTERN = re.compile(r"(?:^|/)services/[^/]+/services/[^/]+/")


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SERVICES_ROOT = REPO_ROOT / "services"


def _iter_fragment_files() -> list[Path]:
    return sorted(SERVICES_ROOT.glob("*/compose.yml"))


def _iter_bind_sources(fragment_path: Path):
    """Yield (service_name, raw_source, resolved_path) for every relative
    bind-mount source declared in the fragment.

    Skips named volumes, host-absolute paths, and `${VAR}` interpolations
    — none of those expose the doubling-bug surface.
    """
    doc = yaml.safe_load(fragment_path.read_text(encoding="utf-8")) or {}
    fragment_dir = fragment_path.parent

    for svc_name, svc_block in (doc.get("services") or {}).items():
        for entry in svc_block.get("volumes") or []:
            # Compose accepts short-form ("./a:/b:ro") or long-form
            # ({"type": "bind", "source": "./a", "target": "/b"}).
            if isinstance(entry, dict):
                if entry.get("type") not in (None, "bind"):
                    continue
                raw = entry.get("source")
            elif isinstance(entry, str):
                raw = entry.split(":", 1)[0]
            else:
                continue

            if not raw:
                continue
            if raw.startswith("/") or raw.startswith("${"):
                continue
            if not (raw.startswith("./") or raw.startswith("../")):
                # Bare names like "prometheus-data" — that's a named volume.
                continue

            yield svc_name, raw, (fragment_dir / raw).resolve()


@pytest.mark.parametrize(
    "fragment", _iter_fragment_files(), ids=lambda p: p.parent.name
)
def test_fragment_bind_sources_dont_self_double(fragment: Path) -> None:
    """No bind-mount source in `fragment` resolves to a path that contains
    a `services/<X>/services/<Y>/` segment. `<X>` and `<Y>` may be the
    same (the PR #29 self-double class) or different (a copy-paste
    between sibling fragments). Either form is fundamentally never
    correct under Compose v2's `include:` semantics — see module docstring.
    """
    offenders: list[str] = []
    for svc_name, raw, resolved in _iter_bind_sources(fragment):
        if _DOUBLED_SERVICES_PATTERN.search(str(resolved)):
            offenders.append(f"  - {svc_name}: '{raw}' → {resolved}")

    if offenders:
        rel = fragment.relative_to(REPO_ROOT)
        svc_dir_name = fragment.parent.name
        joined = "\n".join(offenders)
        pytest.fail(
            f"{rel}: bind-mount source(s) produce a doubled "
            f"`services/<X>/services/<Y>/` path.\n"
            f"Compose v2 resolves relative paths in included fragments "
            f"from the fragment's own directory "
            f"({fragment.parent.relative_to(REPO_ROOT)}/), NOT the repo "
            f"root. Strip the leading `services/<X>/` from each "
            f"offending source (typically `services/{svc_dir_name}/` if "
            f"this was a self-reference, or the sibling service name if "
            f"this was a copy-paste between fragments):\n{joined}"
        )


@pytest.mark.parametrize(
    "fragment", _iter_fragment_files(), ids=lambda p: p.parent.name
)
def test_fragment_bind_sources_stay_inside_repo_root(fragment: Path) -> None:
    """Every relative bind-mount source resolves to a path inside
    `REPO_ROOT`. A source like `../../../etc/passwd` would resolve outside
    the tree and silently bind-mount an arbitrary host file into the
    container — a security-relevant class no other test guards against.

    Legitimate `../../<repo-root-sibling>` patterns (kong, litellm,
    comfyui's `../../volumes/...` and `../../bootstrapper/utils`) stay
    inside `REPO_ROOT` and pass this check.
    """
    escapees: list[str] = []
    for svc_name, raw, resolved in _iter_bind_sources(fragment):
        if not resolved.is_relative_to(REPO_ROOT):
            escapees.append(f"  - {svc_name}: '{raw}' → {resolved}")

    if escapees:
        rel = fragment.relative_to(REPO_ROOT)
        joined = "\n".join(escapees)
        pytest.fail(
            f"{rel}: bind-mount source(s) escape the repo root "
            f"({REPO_ROOT}).\n"
            f"Compose v2 resolves relative paths in included fragments "
            f"from the fragment's own directory; a source that climbs "
            f"out of the repo tree would silently bind-mount an "
            f"arbitrary host path into the container. Either rewrite "
            f"the path to stay inside the repo or use an explicit "
            f"absolute path with a deliberate audit comment:\n{joined}"
        )


def test_at_least_one_fragment_has_relative_bind_sources() -> None:
    """Sanity check that iteration discovered some bind-mount sources —
    otherwise the parametrised test above would pass vacuously across
    every fragment if `_iter_bind_sources` regressed to yielding nothing.
    """
    total = sum(
        1
        for fragment in _iter_fragment_files()
        for _ in _iter_bind_sources(fragment)
    )
    assert total > 0, (
        "No relative bind-mount sources found across any fragment — "
        "the discovery logic in _iter_bind_sources is likely broken."
    )


def _sliced_host_vars(manifests) -> set[str]:
    return {
        var
        for manifest in manifests
        for options in manifest.runtime_sc.values()
        for option in options.values()
        for var, value in ((option or {}).get("environment") or {}).items()
        if "host.docker.internal" in str(value)
    }


def _generated_host_vars(env_with_overrides, overrides: dict) -> set[str]:
    from core.config_parser import ConfigParser
    from services.service_config import ServiceConfig

    parser = ConfigParser(str(REPO_ROOT))
    parser.env_file_path = env_with_overrides(overrides)
    env = ServiceConfig(config_parser=parser).generate_service_environment()
    return {var for var, value in env.items() if "host.docker.internal" in str(value)}


def _localhost_endpoint_vars(env_with_overrides) -> set[str]:
    """Variables that name host.docker.internal: from the manifests'
    runtime slices, and from the bootstrapper's generated environment with
    every source, and then each source alone, set to its localhost option
    (a consumer that is itself a source keeps its own variables only while
    it runs as a container)."""
    from services.manifests import load_manifests

    manifests = load_manifests(REPO_ROOT / "services")
    localhost = {
        m.sources.var: option.id
        for m in manifests if m.sources
        for option in m.sources.options if option.id.endswith("localhost") and "managed" not in option.id
    }
    runs = [localhost, *({var: option} for var, option in localhost.items())]
    return _sliced_host_vars(manifests).union(
        *(_generated_host_vars(env_with_overrides, overrides) for overrides in runs)
    )


def _reads_without_mapping(service: dict, endpoint_vars: set[str]) -> list[str]:
    if any(str(h).startswith("host.docker.internal:") for h in service.get("extra_hosts") or []):
        return []
    environment = yaml.safe_dump(service.get("environment") or {})
    return sorted(v for v in endpoint_vars if re.search(r"\$\{" + v + r"\b", environment))


def test_every_consumer_of_a_localhost_endpoint_maps_host_docker_internal(env_with_overrides):
    """Localhost-source endpoints always name host.docker.internal (#1361), so
    each container reading one must map it; Docker on Linux has no such name
    unless extra_hosts adds it."""
    endpoint_vars = _localhost_endpoint_vars(env_with_overrides)
    unmapped = [
        (name, reads)
        for fragment in _iter_fragment_files()
        for name, service in ((yaml.safe_load(fragment.read_text()) or {}).get("services") or {}).items()
        if (reads := _reads_without_mapping(service, endpoint_vars))
    ]
    assert endpoint_vars and unmapped == []
