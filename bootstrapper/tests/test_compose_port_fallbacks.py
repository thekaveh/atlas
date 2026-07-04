from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


PORT_FALLBACK_RE = re.compile(r"\$\{(?P<var>[A-Z0-9_]+):-?(?P<fallback>\d{4,5})\}")


def _env_example_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def test_compose_port_fallbacks_match_env_example_defaults() -> None:
    env = _env_example_values()
    mismatches: list[str] = []

    for compose in sorted((ROOT / "services").glob("*/compose.yml")):
        text = compose.read_text(encoding="utf-8")
        for match in PORT_FALLBACK_RE.finditer(text):
            var = match.group("var")
            fallback = match.group("fallback")
            if not var.endswith("_PORT") or var not in env:
                continue
            if env[var] and env[var] != fallback:
                mismatches.append(
                    f"{compose.relative_to(ROOT)} uses ${{{var}:-{fallback}}}; "
                    f".env.example has {var}={env[var]}"
                )

    assert not mismatches, "\n".join(mismatches)
