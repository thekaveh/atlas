from __future__ import annotations

import ast
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
BACKEND_MANIFEST = ROOT / "services/backend/service.yml"
BACKEND_COMPOSE = ROOT / "services/backend/compose.yml"
OPEN_WEBUI_COMPOSE = ROOT / "services/open-webui/compose.yml"
N8N_COMPOSE = ROOT / "services/n8n/compose.yml"
JUPYTER_COMPOSE = ROOT / "services/jupyterhub/compose.yml"
N8N_WORKFLOWS = ROOT / "services/n8n/workflows-stage/workflows"
JUPYTER_NOTEBOOKS = (
    ROOT / "services/jupyterhub/build/notebooks/13_chonkie_chunking.ipynb",
    ROOT / "services/jupyterhub/build/notebooks/14_ragas_evaluation.ipynb",
)
OPEN_WEBUI_BACKEND_CALLERS = (
    ROOT / "services/open-webui/extras/functions/memory_filter.py",
    ROOT / "services/open-webui/extras/tools/memory_tool.py",
    ROOT / "services/open-webui/extras/tools/comfyui_image_generation_tool.py",
)


def test_backend_identity_secrets_are_manifest_owned_and_wired() -> None:
    manifest = yaml.safe_load(BACKEND_MANIFEST.read_text(encoding="utf-8"))
    env = {entry["name"]: entry for entry in manifest["env"]}
    assert env["BACKEND_IDENTITY_AUTH"]["default"] == "required"
    assert env["BACKEND_INTERNAL_API_TOKEN"]["default"] == ""
    assert env["BACKEND_INTERNAL_API_TOKEN"]["secret"] is True
    assert env["BACKEND_N8N_API_TOKEN"]["secret"] is True
    assert env["BACKEND_NOTEBOOK_API_TOKEN"]["default"] == ""
    assert env["BACKEND_NOTEBOOK_API_TOKEN"]["secret"] is True
    assert env["BACKEND_OPEN_WEBUI_API_TOKEN"]["secret"] is True

    backend = yaml.safe_load(BACKEND_COMPOSE.read_text(encoding="utf-8"))["services"][
        "backend"
    ]["environment"]
    assert backend["BACKEND_IDENTITY_AUTH"] == "${BACKEND_IDENTITY_AUTH:-required}"
    assert backend["BACKEND_INTERNAL_API_TOKEN"] == "${BACKEND_INTERNAL_API_TOKEN}"
    assert backend["BACKEND_N8N_API_TOKEN"] == "${BACKEND_N8N_API_TOKEN}"
    assert backend["BACKEND_NOTEBOOK_API_TOKEN"] == "${BACKEND_NOTEBOOK_API_TOKEN}"
    assert backend["BACKEND_OPEN_WEBUI_API_TOKEN"] == "${BACKEND_OPEN_WEBUI_API_TOKEN}"
    assert backend["SUPABASE_JWT_SECRET"] == "${SUPABASE_JWT_SECRET}"

    open_webui = yaml.safe_load(OPEN_WEBUI_COMPOSE.read_text(encoding="utf-8"))[
        "services"
    ]["open-web-ui"]["environment"]
    assert open_webui["BACKEND_OPEN_WEBUI_API_TOKEN"] == "${BACKEND_OPEN_WEBUI_API_TOKEN}"

    n8n_services = yaml.safe_load(N8N_COMPOSE.read_text(encoding="utf-8"))["services"]
    for service_name in ("n8n", "n8n-worker"):
        assert n8n_services[service_name]["environment"][
            "BACKEND_N8N_API_TOKEN"
        ] == "${BACKEND_N8N_API_TOKEN}"

    jupyterhub = yaml.safe_load(JUPYTER_COMPOSE.read_text(encoding="utf-8"))[
        "services"
    ]["jupyterhub"]["environment"]
    assert jupyterhub["BACKEND_NOTEBOOK_API_TOKEN"] == "${BACKEND_NOTEBOOK_API_TOKEN}"
    assert "SUPABASE_SERVICE_KEY" not in jupyterhub


def test_backend_notebooks_send_the_scoped_bearer_token() -> None:
    for path in JUPYTER_NOTEBOOKS:
        notebook = json.loads(path.read_text(encoding="utf-8"))
        source = "\n".join(
            "".join(cell.get("source", [])) for cell in notebook.get("cells", [])
        )
        assert 'os.getenv("BACKEND_NOTEBOOK_API_TOKEN")' in source, path.name
        assert "headers=backend_headers" in source, path.name


def test_every_seeded_n8n_backend_request_sends_internal_bearer() -> None:
    found = 0
    for workflow_path in sorted(N8N_WORKFLOWS.glob("*.json")):
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        for node in workflow.get("nodes", []):
            params = node.get("parameters", {})
            if "http://backend:8000" not in str(params.get("url", "")):
                continue
            found += 1
            assert params.get("sendHeaders") is True, workflow_path.name
            headers = params.get("headerParameters", {}).get("parameters", [])
            assert {
                "name": "Authorization",
                "value": "={{ 'Bearer ' + $env.BACKEND_N8N_API_TOKEN }}",
            } in headers, workflow_path.name
    assert found, "expected at least one seeded n8n request to the backend"


def test_every_open_webui_backend_request_sends_internal_bearer() -> None:
    found = 0
    for path in OPEN_WEBUI_BACKEND_CALLERS:
        source = path.read_text(encoding="utf-8")
        assert "BACKEND_OPEN_WEBUI_API_TOKEN" in source
        assert "BACKEND_INTERNAL_API_TOKEN" not in source
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if not isinstance(node.func.value, ast.Name) or node.func.value.id != "requests":
                continue
            if node.func.attr not in {"get", "post", "put", "patch", "delete"}:
                continue
            found += 1
            assert "headers" in {kw.arg for kw in node.keywords}, (
                f"{path.name}:{node.lineno} lacks backend auth headers"
            )
    assert found == 11
