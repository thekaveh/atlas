"""Regression test for the asymmetric-override class documented in
`feedback_localhost_url_override_symmetry.md`.

The wizard writes each localhost-source port override to a
`<SERVICE>_LOCALHOST_PORT` variable in `.env`
(`test_localhost_port_override.py` already covers that direction).
But the consumer side — `bootstrapper/services/service_config.py`,
which assembles the in-container `<SERVICE>_ENDPOINT` values used by
LiteLLM, OpenClaw, Open-WebUI, etc. — must read THE SAME `_LOCALHOST_PORT`
var when the source is `localhost`. Reading the container's
host-bound port var (e.g. `HERMES_API_PORT`, `OPENCLAW_GATEWAY_PORT`,
`DOC_PROCESSOR_PORT`) silently strands every wizard-supplied port
override — the user's stack works but every consumer points at the
default container port.

This bug shipped to main three times (docling, hermes, openclaw) before
the Pass 2 audit caught it. This test prevents the next instance by
asserting that EVERY `<X>_LOCALHOST_PORT` declared in `.env.example`
appears as a literal string somewhere in `service_config.py`. The
literal-string check is intentionally crude — it doesn't validate that
the reference is structurally correct (e.g. consumed in a `localhost`
branch), only that the consumer knows the name exists. Combined with
`test_localhost_port_override.py` (wizard side) it forces both sides of
the symmetry to stay in sync.

Exclusions are explicit: `BASE_PORT` and any LOCALHOST_PORT for which the
consumer pipeline genuinely doesn't exist yet (e.g. provider-only
variants that don't have a downstream `_ENDPOINT` emit step) are listed
in `_KNOWN_NO_CONSUMER`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_EXAMPLE = REPO_ROOT / ".env.example"
SERVICE_CONFIG = REPO_ROOT / "bootstrapper" / "services" / "service_config.py"
LOCALHOST_VALIDATOR = REPO_ROOT / "bootstrapper" / "utils" / "localhost_validator.py"
SERVICES_ROOT = REPO_ROOT / "services"


_LOCALHOST_PORT_PATTERN = re.compile(r"^([A-Z][A-Z0-9_]*_LOCALHOST_PORT)=", re.MULTILINE)


def _consumer_source_text() -> str:
    """All the places a `<X>_LOCALHOST_PORT` var can legitimately be read
    to build an in-container endpoint or probe URL:

    - `bootstrapper/services/service_config.py` — where Python code
      assembles `<X>_ENDPOINT` for downstream consumers (used when the
      localhost source has `scale: 0` and there's no container env block
      to interpolate into).
    - `bootstrapper/utils/localhost_validator.py` — where the wizard's
      pre-flight check probes the user's host-side process via
      `http://localhost:<port>/...`. Must use the LOCALHOST_PORT var or
      it probes the wrong port (Pass 3 caught this).
    - `services/<X>/service.yml` runtime_sc — where Compose-level
      `${...}` interpolation injects the var into the producer
      container's runtime env block.

    Any of these paths is a valid consumer. The asymmetric-override bug
    only fires when NONE of them references the localhost port var.
    """
    parts = [
        SERVICE_CONFIG.read_text(encoding="utf-8"),
        LOCALHOST_VALIDATOR.read_text(encoding="utf-8"),
    ]
    for sy in sorted(SERVICES_ROOT.glob("*/service.yml")):
        parts.append(sy.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


# LOCALHOST_PORTs that intentionally have no in-container consumer yet.
# The wizard still writes them (so users can override the host port), but
# no `<X>_ENDPOINT` is emitted downstream because nothing in the stack
# calls them in-container with that hostname. When a consumer lands, the
# matching name moves OUT of this set and the test below enforces the
# symmetry.
_KNOWN_NO_CONSUMER: set[str] = {
    # Direct Bolt-protocol connection from a few clients; no
    # auto-generated NEO4J_BOLT_ENDPOINT today.
    # (PARAKEET/WHISPER_CPP/CHATTERBOX_LOCALHOST_PORT moved out of this
    # set once STT_ENDPOINT/TTS_ENDPOINT runtime_sc consumers landed.)
    "NEO4J_LOCALHOST_BOLT_PORT",
}


def _localhost_port_vars() -> list[str]:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    names = sorted(set(_LOCALHOST_PORT_PATTERN.findall(text)))
    return [n for n in names if n not in _KNOWN_NO_CONSUMER]


@pytest.mark.parametrize("var_name", _localhost_port_vars())
def test_localhost_port_var_referenced_by_consumer(var_name: str) -> None:
    """`var_name` (a `<X>_LOCALHOST_PORT` env var) must be CONSUMED
    either by `service_config.py` (Python-built endpoint) OR by a
    `services/*/service.yml` runtime_sc block (Compose `${...}`
    interpolation). Either path correctly plumbs the wizard's port
    override into the in-container endpoint.

    "Consumed" means a real READ pattern — `${VAR...}` interpolation
    or a Python string literal like `'VAR'`. Bare declarations on env:
    or row metadata lines don't count, because those are how the var is
    SURFACED but not how it's READ. The bug we're guarding against is
    exactly the case where the var is declared everywhere but read
    nowhere on the consumer side.

    See module docstring for the full asymmetric-override failure mode.
    """
    text = _consumer_source_text()
    compose_interp = f"${{{var_name}"           # ${VAR or ${VAR:-default}
    python_literal_single = f"'{var_name}'"     # 'VAR' as a string literal
    python_literal_double = f'"{var_name}"'     # "VAR" as a string literal

    consumed = (
        compose_interp in text
        or python_literal_single in text
        or python_literal_double in text
    )

    assert consumed, (
        f"{var_name} is declared in .env.example but is NOT consumed by "
        f"any of:\n"
        f"  - `${{{var_name}:-...}}` interpolation in a "
        f"services/*/service.yml runtime_sc block\n"
        f"  - `current_env.get('{var_name}', ...)` in "
        f"bootstrapper/services/service_config.py\n\n"
        f"The wizard writes the user's host port to this var, but no "
        f"in-container ENDPOINT generator reads it — so the override "
        f"silently dies. This is the asymmetric-override class "
        f"documented in feedback_localhost_url_override_symmetry.md.\n\n"
        f"Either:\n"
        f"  1. Add a `${{{var_name}:-...}}` interpolation in the "
        f"     service's runtime_sc.environment block (preferred when "
        f"     the localhost variant runs a container), OR\n"
        f"  2. Add a `_LOCALHOST` branch in service_config.py that "
        f"     reads `{var_name}` to build the corresponding ENDPOINT "
        f"     (when the container is disabled), OR\n"
        f"  3. If this service genuinely has no in-container consumer, "
        f"     add `{var_name}` to _KNOWN_NO_CONSUMER in this test file "
        f"     with a comment explaining why."
    )


def test_known_no_consumer_set_doesnt_grow_silently() -> None:
    """Sanity check: _KNOWN_NO_CONSUMER should be deliberate. If a future
    contributor moves something INTO this set, they must justify it in
    the docstring comment above. If they REMOVE one (because a consumer
    landed), the parametrised test above starts enforcing the read.
    """
    assert len(_KNOWN_NO_CONSUMER) <= 6, (
        "_KNOWN_NO_CONSUMER has grown past the expected size. Each entry "
        "should be justified inline — silent growth means the symmetry "
        "test is losing coverage."
    )
# ─── default-literal agreement across seams (pass 49) ────────────────

def test_localhost_port_default_literals_agree_across_seams():
    """Every hardcoded *LOCALHOST*PORT fallback (wizard wiring table,
    Kong generator, LocalhostValidator.SERVICE_CHECKS) must equal the
    var's value in .env.example. These literals agree today only by
    manual discipline — this pins them together (the df8f627 re-default
    touched all four seams at once; a future edit must too)."""
    import re

    env_text = ENV_EXAMPLE.read_text(encoding="utf-8")
    env_values = dict(
        re.findall(r"^([A-Z_]*LOCALHOST[A-Z_]*PORT)=(\d+)$", env_text, re.M)
    )
    assert env_values, "no localhost port vars found in .env.example"

    repo = ENV_EXAMPLE.parent
    mismatches: list[str] = []

    # Seam 1: wizard wiring table (function-local dict — read source).
    integration_src = (
        repo / "bootstrapper" / "ui" / "textual" / "integration.py"
    ).read_text(encoding="utf-8")
    wiring_pairs = re.findall(
        r'\("([A-Z_]*LOCALHOST[A-Z_]*PORT)",\s*(\d+)\)', integration_src
    )
    assert len(wiring_pairs) >= 5, "wiring-table regex matched nothing — refactor?"
    for var, default in wiring_pairs:
        if env_values.get(var) != default:
            mismatches.append(
                f"integration.py wiring: {var} fallback {default} != "
                f".env.example {env_values.get(var)}"
            )

    # Seam 2: Kong generator fallbacks.
    kong_src = (
        repo / "bootstrapper" / "utils" / "kong_config_generator.py"
    ).read_text(encoding="utf-8")
    kong_pairs = re.findall(
        r"_localhost_url\(\s*['\"]([A-Z_]+)['\"],\s*['\"](\d+)['\"]", kong_src
    )
    assert len(kong_pairs) >= 5, "kong fallback regex matched nothing — refactor?"
    for var, default in kong_pairs:
        if env_values.get(var) != default:
            mismatches.append(
                f"kong_config_generator: {var} fallback {default} != "
                f".env.example {env_values.get(var)}"
            )

    # Seam 3: LocalhostValidator.SERVICE_CHECKS (class attr — walk it).
    from utils.localhost_validator import LocalhostValidator

    def _walk(node):
        if isinstance(node, dict):
            if "port_env_var" in node and "default_port" in node:
                yield node["port_env_var"], str(node["default_port"])
            for v in node.values():
                yield from _walk(v)

    validator_pairs = list(_walk(LocalhostValidator.SERVICE_CHECKS))
    assert len(validator_pairs) >= 5, "SERVICE_CHECKS walk found nothing — refactor?"
    for var, default in validator_pairs:
        if var in env_values and env_values[var] != default:
            mismatches.append(
                f"localhost_validator: {var} default {default} != "
                f".env.example {env_values[var]}"
            )

    # Seam 4: imperative endpoint assembly in service_config.py.
    service_config_src = SERVICE_CONFIG.read_text(encoding="utf-8")
    config_pairs = re.findall(
        r"current_env\.get\(['\"]([A-Z_]*LOCALHOST[A-Z_]*PORT)['\"],\s*['\"](\d+)['\"]\)",
        service_config_src,
    )
    assert config_pairs, "service_config.py localhost fallbacks not found — refactor?"
    for var, default in config_pairs:
        if var in env_values and env_values[var] != default:
            mismatches.append(
                f"service_config.py: {var} fallback {default} != "
                f".env.example {env_values[var]}"
            )

    assert not mismatches, "\n".join(mismatches)


def test_localhost_port_defaults_do_not_collide_with_container_ports():
    """Host-source defaults should not point at an Atlas container host port.

    A localhost-source default targets a process already running on the
    operator's host. If it equals a generated container host port, the wizard
    can appear to validate against an unrelated Atlas container and then route
    traffic to the wrong service.
    """
    import re

    from services.topology import build_topology

    repo = ENV_EXAMPLE.parent
    env_text = ENV_EXAMPLE.read_text(encoding="utf-8")
    localhost_defaults = {
        var: int(value)
        for var, value in re.findall(r"^([A-Z0-9_]*LOCALHOST[A-Z0-9_]*PORT)=(\d+)$", env_text, re.M)
    }
    container_defaults = build_topology(repo / "services").port_defaults
    ports_to_container_vars: dict[int, list[str]] = {}
    for var, port in container_defaults.items():
        ports_to_container_vars.setdefault(port, []).append(var)

    collisions = [
        f"{localhost_var}={localhost_port} collides with {', '.join(sorted(container_vars))}"
        for localhost_var, localhost_port in sorted(localhost_defaults.items())
        if (container_vars := ports_to_container_vars.get(localhost_port))
    ]

    assert not collisions, "\n".join(collisions)
