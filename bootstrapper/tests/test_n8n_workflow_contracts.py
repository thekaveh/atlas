from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


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


def _assert_contains(text, fragments):
    missing = tuple(fragment for fragment in fragments if fragment not in text)
    assert missing == ()


def _code_node(workflow: dict, name: str) -> str:
    node = next(node for node in workflow["nodes"] if node["name"] == name)
    return node["parameters"]["jsCode"]


def _run_code_node(
    code: str,
    *,
    input_payload: dict,
    references: dict[str, dict] | None = None,
) -> subprocess.CompletedProcess[str]:
    harness = f"""
const inputPayload = {json.dumps(input_payload)};
const references = {json.dumps(references or {})};
const $input = {{ first: () => ({{ json: inputPayload }}) }};
const $ = (name) => ({{ item: {{ json: references[name] }} }});
const $execution = {{ id: 'execution-1' }};
const $workflow = {{ name: 'contract-test' }};
const output = (() => {{
{code}
}})();
process.stdout.write(JSON.stringify(output));
"""
    if shutil.which("node") is None:
        pytest.skip("node CLI unavailable")
    return subprocess.run(
        ["node"],
        input=harness,
        text=True,
        capture_output=True,
        check=False,
    )


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


@pytest.mark.skipif(shutil.which("node") is None, reason="node CLI unavailable")
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


def test_comfyui_workflows_preserve_numeric_boundaries() -> None:
    advanced = _load(
        ROOT / "services/n8n/workflows-stage/workflows/comfyui-image-generation.json"
    )
    validate = _code_node(advanced, "Validate Request")
    accepted = _run_code_node(
        validate,
        input_payload={
            "prompt": "blue archive",
            "width": 512,
            "height": 512,
            "steps": 20,
            "cfg": 0,
        },
    )
    assert accepted.returncode == 0, accepted.stderr
    assert json.loads(accepted.stdout)["generationRequest"]["cfg"] == 0

    wrapped = _run_code_node(
        validate,
        input_payload={
            "body": {
                "prompt": "wrapped blue archive",
                "width": 768,
                "height": 512,
                "steps": 20,
                "cfg": 0,
            }
        },
    )
    assert wrapped.returncode == 0, wrapped.stderr
    assert json.loads(wrapped.stdout)["generationRequest"]["prompt"] == (
        "wrapped blue archive"
    )

    rejected = _run_code_node(
        validate,
        input_payload={
            "prompt": "blue archive",
            "width": 0,
            "height": 512,
            "steps": 20,
            "cfg": 7,
        },
    )
    assert rejected.returncode != 0
    assert "Width must be between" in rejected.stderr

    for field, value in (
        ("width", "abc"),
        ("height", {}),
        ("steps", 1.5),
        ("cfg", "abc"),
    ):
        malformed_payload = {
            "prompt": "blue archive",
            "width": 512,
            "height": 512,
            "steps": 20,
            "cfg": 7,
        }
        malformed_payload[field] = value
        malformed = _run_code_node(validate, input_payload=malformed_payload)
        assert malformed.returncode != 0, (field, malformed.stdout)
        assert "finite number" in malformed.stderr

    simple = _load(
        ROOT / "services/n8n/workflows-stage/workflows/comfyui-simple.json"
    )
    request = next(node for node in simple["nodes"] if node["name"] == "Generate Simple Image")
    values = {
        entry["name"]: entry["value"]
        for entry in request["parameters"]["bodyParameters"]["parameters"]
    }
    for name in ("width", "height", "steps", "cfg"):
        assert "??" in values[name]
        assert "||" not in values[name]
        assert "$json.body?." in values[name]
    assert "$json.body?.prompt" in values["prompt"]


def test_research_workflows_do_not_default_invalid_zero_loop_count() -> None:
    simple = _load(
        ROOT / "services/n8n/workflows-stage/workflows/research-simple.json"
    )
    request = next(node for node in simple["nodes"] if node["name"] == "Start Research")
    values = {
        entry["name"]: entry["value"]
        for entry in request["parameters"]["bodyParameters"]["parameters"]
    }
    assert values["max_loops"] == (
        "={{ $json.body?.max_loops ?? $json.max_loops ?? 3 }}"
    )

    batch = _load(
        ROOT / "services/n8n/workflows-stage/workflows/research-batch.json"
    )
    processed = _run_code_node(
        _code_node(batch, "Process Batch Request"),
        input_payload={"queries": [{"query": "atlas", "max_loops": 0}]},
    )
    assert processed.returncode != 0


def test_research_batch_rejects_unbounded_or_invalid_requests() -> None:
    workflow = _load(
        ROOT / "services/n8n/workflows-stage/workflows/research-batch.json"
    )
    process = _code_node(workflow, "Process Batch Request")

    accepted = _run_code_node(
        process,
        input_payload={
            "body": {
                "queries": [{"query": "atlas", "max_loops": 1}],
                "config": {"search_api": "searxng"},
            }
        },
    )
    assert accepted.returncode == 0, accepted.stderr

    invalid_payloads = (
        {"queries": ["atlas"] * 26},
        {"queries": ["   "]},
        {"queries": [{"query": "atlas", "max_loops": 0}]},
        {"queries": [{"query": "atlas", "search_api": "commercial"}]},
    )
    for payload in invalid_payloads:
        rejected = _run_code_node(process, input_payload={"body": payload})
        assert rejected.returncode != 0, payload


def test_all_bundled_workflow_webhooks_match_disclosed_auth_boundaries() -> None:
    staged_dir = ROOT / "services/n8n/workflows-stage/workflows"
    staged_workflows = sorted(staged_dir.glob("*.json"))
    expected = {
        "httpHeaderAuth": {
            "id": "atlas-webhook-header-auth",
            "name": "Atlas Webhook Header Auth",
        }
    }
    staged_webhook_contracts = [
        (path, webhook["parameters"]["authentication"], webhook["credentials"])
        for path in staged_workflows
        for webhook in (
            node
            for node in _load(path)["nodes"]
            if node["type"] == "n8n-nodes-base.webhook"
        )
    ]

    legacy_path = ROOT / "services/n8n/init/config/searxng-research-workflow.json"
    legacy = _load(legacy_path)
    legacy_webhook = next(
        node
        for node in legacy["nodes"]
        if node["type"] == "n8n-nodes-base.webhook"
    )
    assert (
        staged_workflows,
        staged_webhook_contracts,
        (
            legacy_webhook["parameters"]["path"],
            "authentication" in legacy_webhook["parameters"],
            "credentials" in legacy_webhook,
        ),
    ) == (
        list(WORKFLOWS[:-1]),
        [(path, "headerAuth", expected) for path, *_ in staged_webhook_contracts],
        ("research", False, False),
    )

    manifest = yaml.safe_load((ROOT / "services/n8n/service.yml").read_text())
    capability = next(
        row
        for row in manifest["capabilities"]
        if row["name"] == "Editor and webhook access control"
    )
    note = capability["note"]
    _assert_contains(note, (
        "staged privileged examples require operator-bound Header Auth",
        "legacy bundled /research fixture declares no authentication",
        "must be secured before activation",
    ))


def test_n8n_contract_distinguishes_scoped_and_stack_wide_workflow_credentials() -> None:
    compose = yaml.safe_load((ROOT / "services/n8n/compose.yml").read_text())
    legacy = _load(
        ROOT / "services/n8n/init/config/searxng-research-workflow.json"
    )
    legacy_text = json.dumps(legacy)

    service_credentials = (
        "DOCLING_API_TOKEN",
        "PARAKEET_API_TOKEN",
        "HERMES_API_KEY",
        "LIGHTRAG_API_KEY",
        "CRAWL4AI_API_TOKEN",
    )
    environment_contracts = {
        service_name: (
            environment["BACKEND_N8N_API_TOKEN"],
            environment["LITELLM_API_KEY"],
            tuple(name for name in service_credentials if name in environment),
        )
        for service_name in ("n8n", "n8n-worker")
        for environment in (compose["services"][service_name]["environment"],)
    }

    manifest = yaml.safe_load((ROOT / "services/n8n/service.yml").read_text())
    capability = next(
        row
        for row in manifest["capabilities"]
        if row["name"] == "Workflow credential propagation"
    )
    assert (
        environment_contracts,
        "$env.LITELLM_API_KEY" in legacy_text,
        (capability["status"], capability["verification"]),
    ) == (
        {
            service_name: (
                "${BACKEND_N8N_API_TOKEN}",
                "${LITELLM_MASTER_KEY}",
                service_credentials,
            )
            for service_name in ("n8n", "n8n-worker")
        },
        True,
        ("partial", "tested"),
    )
    _assert_contains(capability["note"], (
        "scoped Backend token",
        "stack-wide LiteLLM master key",
        "provider service credentials",
        "workflow expressions",
        "avoid returning them in outputs or webhooks",
    ))


def test_comfyui_workflows_preserve_fal_artifact_urls() -> None:
    fal_result = {
        "success": True,
        "prompt_id": "fal-1",
        "client_id": "fal",
        "data": {
            "provider": "fal",
            "outputs": {
                "images": [
                    {
                        "url": "https://cdn.example/fal.png",
                        "content_type": "image/png",
                        "width": 1024,
                        "height": 1024,
                    }
                ]
            },
        },
    }
    advanced = _load(
        ROOT / "services/n8n/workflows-stage/workflows/comfyui-image-generation.json"
    )
    processed = _run_code_node(
        _code_node(advanced, "Process Success"),
        input_payload=fal_result,
        references={
            "Validate Request": {
                "generationRequest": {
                    "prompt": "blue archive",
                    "negative_prompt": "",
                    "width": 1024,
                    "height": 1024,
                    "steps": 20,
                    "cfg": 0,
                    "checkpoint": "model.safetensors",
                },
                "timestamp": "2026-07-14T00:00:00.000Z",
            }
        },
    )
    assert processed.returncode == 0, processed.stderr
    advanced_result = json.loads(processed.stdout)
    assert advanced_result["image_count"] == 1
    assert advanced_result["generated_images"][0]["url"] == (
        "https://cdn.example/fal.png"
    )

    simple = _load(
        ROOT / "services/n8n/workflows-stage/workflows/comfyui-simple.json"
    )
    formatted = _run_code_node(
        _code_node(simple, "Format Response"),
        input_payload=fal_result,
        references={"Simple ComfyUI Webhook": {"body": {"prompt": "blue archive"}}},
    )
    assert formatted.returncode == 0, formatted.stderr
    simple_result = json.loads(formatted.stdout)
    assert simple_result["image_count"] == 1
    assert simple_result["prompt"] == "blue archive"
    assert simple_result["generated_images"][0]["url"] == (
        "https://cdn.example/fal.png"
    )
