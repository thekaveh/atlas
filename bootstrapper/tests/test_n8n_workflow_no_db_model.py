"""
Test: searxng-research-workflow no longer queries public.llms for the
      summarisation model (Task B6c).

Asserts:
1. No "public.llms" substring anywhere in the file.
2. No n8n-nodes-base.postgres node named "Get AI Model for Summary".
3. The "Generate AI Summary" node's model body-param value is
   ={{ $env.LITELLM_DEFAULT_MODEL }}.
4. Connection integrity: every node name referenced as a source key or
   a target in connections[] exists in nodes[].
5. The file parses as valid JSON.
"""

import json
from pathlib import Path

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2]
    / "services"
    / "n8n"
    / "init"
    / "config"
    / "searxng-research-workflow.json"
)


def _load() -> dict:
    return json.loads(WORKFLOW_PATH.read_text())


def test_no_public_llms_reference():
    raw = WORKFLOW_PATH.read_text()
    assert "public.llms" not in raw, (
        "Workflow still references public.llms — run the B6c migration"
    )


def test_postgres_node_removed():
    wf = _load()
    postgres_nodes = [
        n for n in wf["nodes"]
        if n.get("type") == "n8n-nodes-base.postgres"
        and n.get("name") == "Get AI Model for Summary"
    ]
    assert postgres_nodes == [], (
        "The 'Get AI Model for Summary' postgres node must be removed"
    )


def test_generate_ai_summary_uses_env_model():
    wf = _load()
    summary_node = next(
        (n for n in wf["nodes"] if n["name"] == "Generate AI Summary"),
        None,
    )
    assert summary_node is not None, "'Generate AI Summary' node not found"
    params = summary_node["parameters"]
    # HTTP Request v4 ignores the v1 bodyContentType/jsonParameters keys and
    # sends key-pair values as-is, which turned `messages` into a string that
    # LiteLLM rejects; the body must be one JSON expression.
    assert params["specifyBody"] == "json"
    assert not {"bodyContentType", "jsonParameters", "bodyParameters"} & set(params)
    body = params["jsonBody"]
    assert body.startswith("={{ JSON.stringify({") and "model: $env.LITELLM_DEFAULT_MODEL" in body
    assert "messages: [{ role: 'user', content: $json.formatted_prompt }]" in body


def test_connection_integrity():
    """Every connection source key and target node must exist in nodes[]."""
    wf = _load()
    node_names = {n["name"] for n in wf["nodes"]}
    connections = wf.get("connections", {})

    for src in connections:
        assert src in node_names, (
            f"Connection source '{src}' is not in nodes[] — dangling reference"
        )
        for main_list in connections[src].get("main", []):
            for target in main_list:
                assert target["node"] in node_names, (
                    f"Connection target '{target['node']}' (from '{src}') "
                    f"is not in nodes[] — dangling reference"
                )

    # Specifically confirm the removed node is not referenced
    assert "Get AI Model for Summary" not in connections, (
        "'Get AI Model for Summary' still appears as a connection source"
    )
    for src, conns in connections.items():
        for main_list in conns.get("main", []):
            for target in main_list:
                assert target["node"] != "Get AI Model for Summary", (
                    f"'Get AI Model for Summary' still targeted from '{src}'"
                )


def test_valid_json():
    """Smoke-check: the file round-trips as valid JSON."""
    raw = WORKFLOW_PATH.read_text()
    wf = json.loads(raw)
    assert isinstance(wf, dict)
    assert "nodes" in wf
    assert "connections" in wf
