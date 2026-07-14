from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = (
    *sorted((ROOT / "services/n8n/workflows-stage/workflows").glob("*.json")),
    ROOT / "services/n8n/init/config/searxng-research-workflow.json",
)
TRIGGER_TYPES = {
    "n8n-nodes-base.cron",
    "n8n-nodes-base.manualTrigger",
    "n8n-nodes-base.scheduleTrigger",
    "n8n-nodes-base.webhook",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _reachable_nodes(workflow: dict) -> set[str]:
    connections = workflow.get("connections", {})
    roots = {
        node["name"]
        for node in workflow.get("nodes", [])
        if node.get("type") in TRIGGER_TYPES
    }
    reachable = set(roots)
    pending = list(roots)
    while pending:
        source = pending.pop()
        for output_type in connections.get(source, {}).values():
            for output in output_type:
                for target in output or []:
                    name = target["node"]
                    if name not in reachable:
                        reachable.add(name)
                        pending.append(name)
    return reachable


def test_http_request_and_webhook_method_fields_match_pinned_n8n_contract() -> None:
    for path in WORKFLOWS:
        for node in _load(path).get("nodes", []):
            parameters = node.get("parameters", {})
            if node.get("type") == "n8n-nodes-base.httpRequest":
                assert "httpMethod" not in parameters, (
                    f"{path.name}:{node['name']} uses the Webhook field "
                    "httpMethod instead of HTTP Request field method"
                )
            if node.get("type") == "n8n-nodes-base.webhook":
                assert "method" not in parameters
                assert parameters.get("httpMethod")


def test_body_bearing_http_requests_do_not_fall_back_to_get() -> None:
    for path in WORKFLOWS:
        for node in _load(path).get("nodes", []):
            if node.get("type") != "n8n-nodes-base.httpRequest":
                continue
            parameters = node.get("parameters", {})
            if parameters.get("sendBody"):
                assert parameters.get("method") in {"POST", "PUT", "PATCH"}, (
                    f"{path.name}:{node['name']} sends a body but defaults to GET"
                )


def test_scheduled_report_upload_matches_backend_multipart_contract() -> None:
    workflow = _load(
        ROOT / "services/n8n/workflows-stage/workflows/research-scheduled.json"
    )
    node = next(node for node in workflow["nodes"] if node["name"] == "Save Report")
    parameters = node["parameters"]

    assert parameters["method"] == "POST"
    assert parameters["url"].endswith("?bucket=research-reports")
    assert parameters["contentType"] == "multipart-form-data"
    assert parameters["bodyParameters"] == {
        "parameters": [
            {
                "parameterType": "formBinaryData",
                "name": "file",
                "inputDataFieldName": "report",
            }
        ]
    }
    assert "sendBinaryData" not in parameters
    assert "binaryPropertyName" not in parameters


def test_standalone_workflows_do_not_reference_instance_local_tags() -> None:
    for path in WORKFLOWS:
        assert "tags" not in _load(path), (
            f"{path.name} contains tag records that cannot be imported into a fresh n8n DB"
        )


def test_multi_session_research_flows_poll_and_report_all_failed_runs() -> None:
    expected_failure_targets = {
        "research-batch.json": "Format Batch Results",
        "research-scheduled.json": "Generate Report",
    }
    for filename, failure_target in expected_failure_targets.items():
        workflow = _load(
            ROOT / "services/n8n/workflows-stage/workflows" / filename
        )
        connections = workflow["connections"]
        assert connections["Prepare Status Checks"]["main"][0][0]["node"] == (
            "Check All Statuses"
        )
        assert connections["All Completed?"]["main"][1][0]["node"].startswith(
            "Wait and Retry"
        )
        assert connections["Has Successful Results?"]["main"][1][0]["node"] == (
            failure_target
        )
        prepare = next(
            node
            for node in workflow["nodes"]
            if node["name"] == "Prepare Result Requests"
        )
        assert "skipResultFetch: true" in prepare["parameters"]["jsCode"]


def test_shipped_workflow_nodes_are_reachable_from_a_trigger() -> None:
    for path in WORKFLOWS:
        if "research" not in path.name:
            continue
        workflow = _load(path)
        names = {node["name"] for node in workflow.get("nodes", [])}
        assert _reachable_nodes(workflow) == names, path.name


def test_research_workflows_carry_state_in_items_not_execution_custom_data() -> None:
    for path in WORKFLOWS:
        if "research" not in path.name:
            continue
        code = "\n".join(
            node.get("parameters", {}).get("jsCode", "")
            for node in _load(path).get("nodes", [])
        )
        assert "$execution.customData" not in code, path.name


def test_code_node_javascript_parses() -> None:
    for path in WORKFLOWS:
        for node in _load(path).get("nodes", []):
            code = node.get("parameters", {}).get("jsCode")
            if not code:
                continue
            result = subprocess.run(
                ["node", "--check"],
                input=code,
                text=True,
                capture_output=True,
                check=False,
            )
            assert result.returncode == 0, (
                f"{path.name}:{node['name']} has invalid JavaScript:\n{result.stderr}"
            )
