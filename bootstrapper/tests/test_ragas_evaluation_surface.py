from __future__ import annotations

import json
from pathlib import Path

from tests.three_surface_test_utils import surface_text


ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_ragas_dependency_is_limited_to_backend_and_jupyterhub_surfaces() -> None:
    backend_requirements = _read("services/backend/app/app/requirements.txt")
    jupyter_requirements = _read("services/jupyterhub/build/requirements.txt")
    workflow = _read(".github/workflows/services-lint.yml")

    assert "ragas==0.4.3" in backend_requirements
    assert "ragas==0.4.3" in jupyter_requirements
    assert "langchain-community>=0.3.0,<0.4" in backend_requirements
    assert "langchain-community>=0.3.0,<0.4" in jupyter_requirements
    assert "uv pip install -r requirements.txt" in workflow

    for unexpected_path in (
        "services/docling/app/requirements.txt",
        "services/lightrag/build/requirements.txt",
        "services/n8n/requirements.txt",
        "services/langfuse/requirements.txt",
    ):
        path = ROOT / unexpected_path
        if path.exists():
            text = path.read_text(encoding="utf-8").lower()
            assert "ragas" not in text


def test_ragas_notebook_and_docs_register_backend_runtime_contract() -> None:
    notebook = json.loads(
        (ROOT / "services/jupyterhub/build/notebooks/14_ragas_evaluation.ipynb").read_text(
            encoding="utf-8"
        )
    )
    notebook_text = "\n".join(
        "".join(cell.get("source", [])) for cell in notebook.get("cells", [])
    )
    docs_text = "\n".join(
        [
            _read("services/backend/README.md"),
            _read("services/jupyterhub/README.md"),
            _read("services/jupyterhub/build/README.md"),
            surface_text("docs/core-concepts.md", "site"),
            surface_text("docs/core-concepts.md", "wiki"),
        ]
    )

    assert "evaluate" in notebook_text
    assert "Ragas" in notebook_text
    assert "/api/rag/evaluate" in notebook_text
    assert "14_ragas_evaluation.ipynb" in docs_text
    assert "POST /api/rag/evaluate" in docs_text
    assert "LiteLLM" in docs_text
