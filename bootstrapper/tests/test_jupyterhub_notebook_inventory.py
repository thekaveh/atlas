from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK_DIR = ROOT / "services" / "jupyterhub" / "build" / "notebooks"
COMPOSE_FILE = ROOT / "services" / "jupyterhub" / "compose.yml"
REQUIREMENTS_FILE = ROOT / "services" / "jupyterhub" / "build" / "requirements.txt"
STARTUP_FILE = ROOT / "services" / "jupyterhub" / "build" / "scripts" / "startup.sh"

MODULE_DISTRIBUTIONS = {
    "boto3": "boto3",
    "ccxt": "ccxt",
    "chonkie": "chonkie",
    "dotenv": "python-dotenv",
    "httpx": "httpx",
    "langchain_openai": "langchain-openai",
    "mlflow": "mlflow",
    "neo4j": "neo4j",
    "openai": "openai",
    "openbb": "openbb",
    "pandas": "pandas",
    "pyspark": "pyspark-client",
    "ragas": "ragas",
    "ray": "ray",
    "redis": "redis",
    "sqlalchemy": "sqlalchemy",
    "supabase": "supabase",
    "weaviate": "weaviate-client",
}
LOCAL_MODULES = {"atlas_finance"}


def _markdown_mentions(path: Path) -> set[str]:
    return set(re.findall(r"`([0-9][0-9]_[^`]+\.ipynb)`", path.read_text(encoding="utf-8")))


def test_jupyterhub_notebook_inventory_matches_docs_and_starter_notebook():
    notebooks = {p.name for p in NOTEBOOK_DIR.glob("*.ipynb")}
    readme_mentions = _markdown_mentions(ROOT / "services" / "jupyterhub" / "README.md")
    startup_mentions = _markdown_mentions(STARTUP_FILE)

    starter = json.loads((NOTEBOOK_DIR / "00_environment_check.ipynb").read_text())
    starter_text = "\n".join(
        "".join(cell.get("source", []))
        for cell in starter.get("cells", [])
        if cell.get("cell_type") == "markdown"
    )
    starter_mentions = set(re.findall(r"`([0-9][0-9]_[^`]+\.ipynb)`", starter_text))
    starter_mentions.add("00_environment_check.ipynb")

    assert readme_mentions == notebooks
    assert starter_mentions == notebooks
    assert startup_mentions == notebooks


def test_environment_check_is_bounded_redacted_and_truthfully_scoped():
    notebook = json.loads((NOTEBOOK_DIR / "00_environment_check.ipynb").read_text())
    markdown = "\n".join(
        "".join(cell.get("source", []))
        for cell in notebook.get("cells", [])
        if cell.get("cell_type") == "markdown"
    )
    code = "\n".join(
        "".join(cell.get("source", []))
        for cell in notebook.get("cells", [])
        if cell.get("cell_type") == "code"
    )

    headings = [line for line in markdown.splitlines() if line.startswith("## ")]
    assert headings
    assert all(re.match(r"^## \d+\. ", heading) for heading in headings)
    assert "all Atlas services" not in markdown
    assert "configured (value hidden)" in code
    assert 'os.getenv("DATABASE_URL", "not set")' not in code
    assert 'os.getenv("REDIS_URL", "not set")' not in code
    assert "timeout=5.0" in code
    assert 'connect_args={"connect_timeout": 5}' in code
    assert "socket_connect_timeout=5" in code
    assert "connection_timeout=5" in code
    for variable in (
        "LITELLM_BASE_URL",
        "WEAVIATE_URL",
        "COMFYUI_BASE_URL",
        "N8N_BASE_URL",
        "SEARXNG_URL",
        "BACKEND_API_URL",
    ):
        assert variable in code


def test_jupyterhub_notebook_cell_ids_are_nbformat_45_complete_and_unique():
    for path in sorted(NOTEBOOK_DIR.glob("*.ipynb")):
        notebook = json.loads(path.read_text(encoding="utf-8"))
        assert notebook.get("nbformat") == 4
        assert notebook.get("nbformat_minor", 0) >= 5, (
            f"{path} must declare nbformat 4.5+ so cell IDs are part of "
            "the file contract"
        )
        cells = notebook.get("cells", [])
        ids = [cell.get("id") for cell in cells]
        assert all(isinstance(cell_id, str) and cell_id for cell_id in ids), (
            f"{path} has cells without IDs"
        )
        assert len(ids) == len(set(ids)), f"{path} has duplicate cell IDs"


def test_python_notebook_cells_compile_and_direct_imports_are_declared():
    declared = set()
    for line in REQUIREMENTS_FILE.read_text(encoding="utf-8").splitlines():
        requirement = line.split("#", 1)[0].strip()
        if not requirement or requirement.startswith("-"):
            continue
        name = re.split(r"[<>=!~\[]", requirement, maxsplit=1)[0]
        declared.add(name.lower().replace("_", "-"))

    for path in sorted(NOTEBOOK_DIR.glob("*.ipynb")):
        notebook = json.loads(path.read_text(encoding="utf-8"))
        if notebook.get("metadata", {}).get("kernelspec", {}).get("language") == "scala":
            continue
        imports = set()
        for index, cell in enumerate(notebook.get("cells", [])):
            if cell.get("cell_type") != "code":
                continue
            source = "".join(cell.get("source", []))
            tree = ast.parse(source, filename=f"{path.name}:cell-{index}")
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split(".", 1)[0])

        third_party = imports - set(sys.stdlib_module_names) - LOCAL_MODULES
        unknown = third_party - MODULE_DISTRIBUTIONS.keys()
        assert not unknown, f"{path.name} has unmapped direct imports: {sorted(unknown)}"
        missing = {
            MODULE_DISTRIBUTIONS[module]
            for module in third_party
            if MODULE_DISTRIBUTIONS[module] not in declared
        }
        assert not missing, f"{path.name} imports undeclared packages: {sorted(missing)}"


def test_python_and_scala_spark_examples_share_runtime_contracts():
    def notebook_text(name: str, cell_type: str | None = None) -> str:
        notebook = json.loads((NOTEBOOK_DIR / name).read_text(encoding="utf-8"))
        return "\n".join(
            "".join(cell.get("source", []))
            for cell in notebook.get("cells", [])
            if cell_type is None or cell.get("cell_type") == cell_type
        )

    python_spark = notebook_text("09_spark_connect.ipynb")
    scala_spark = notebook_text("10_spark_scala.ipynb")
    scala_basics_code = notebook_text("08_scala_basics.ipynb", "code")

    for text in (python_spark, scala_spark):
        assert "SPARK_REMOTE" in text
        assert "s3a://spark-history/" in text
    assert "LITELLM_DEFAULT_MODEL" in scala_basics_code
    assert '"ollama/qwen3.6:latest"' not in scala_basics_code


def test_jupyterhub_allow_origin_flag_uses_env_knob():
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    command = compose["services"]["jupyterhub"]["command"]

    assert "--ServerApp.allow_origin=${JUPYTER_ALLOW_ORIGIN:-*}" in command
