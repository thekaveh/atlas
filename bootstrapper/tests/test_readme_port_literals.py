from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def _env_port_defaults() -> dict[str, str]:
    defaults: dict[str, str] = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.endswith("_PORT") and value.isdigit():
            defaults[key] = value
    return defaults


def test_active_readme_port_assignments_match_env_example() -> None:
    env_defaults = _env_port_defaults()
    paths = [
        ROOT / "README.md",
        *sorted((ROOT / "services").glob("*/README.md")),
        *sorted((ROOT / "services").glob("*/*/README.md")),
    ]
    allowed_overrides = {
        ("services/openclaw/README.md", "OPENCLAW_LOCALHOST_PORT", "18789"),
        ("services/jupyterhub/README.md", "JUPYTERHUB_PORT", "64094"),
    }
    mismatches: list[str] = []

    for path in paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        relative = path.relative_to(ROOT).as_posix()
        for match in re.finditer(r"\b([A-Z][A-Z0-9_]+_PORT)\s*=\s*([0-9]{4,5})\b", text):
            var_name, value = match.groups()
            if var_name not in env_defaults:
                continue
            if (relative, var_name, value) in allowed_overrides:
                continue
            if env_defaults[var_name] != value:
                line = text[: match.start()].count("\n") + 1
                mismatches.append(
                    f"{relative}:{line}: {var_name}={value}, .env.example has {env_defaults[var_name]}"
                )

    assert not mismatches, "\n".join(mismatches)
