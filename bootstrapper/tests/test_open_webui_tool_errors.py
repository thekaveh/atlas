from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "services/open-webui/extras/tools"


def test_registered_tools_do_not_return_raw_error_details():
    forbidden = (
        "str(e)",
        "str(exc)",
        "str(parse_error)",
        "error_data.get('detail'",
        'error_data.get("detail"',
        "Raw response:",
        "result.get('error'",
        'result.get("error"',
        "health_data['error']",
        'health_data["error"]',
        "Backend URL:",
        "base64.b64encode",
    )
    findings = []
    for path in sorted(TOOLS.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in source:
                findings.append(f"{path.name}: {token}")

    assert not findings, findings
