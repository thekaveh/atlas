from __future__ import annotations

import json
from pathlib import Path

from tests.three_surface_test_utils import surface_text


ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_chonkie_dependency_is_limited_to_backend_and_jupyterhub_surfaces() -> None:
    backend_requirements = _read("services/backend/app/app/requirements.txt")
    jupyter_requirements = _read("services/jupyterhub/build/requirements.txt")
    workflow = _read(".github/workflows/services-lint.yml")

    assert "chonkie>=1.7.0,<2" in backend_requirements
    assert "chonkie>=1.7.0,<2" in jupyter_requirements
    assert "-r requirements.txt -r requirements-dev.txt" in workflow

    for unexpected_path in (
        "services/docling/app/requirements.txt",
        "services/lightrag/build/requirements.txt",
        "services/n8n/requirements.txt",
    ):
        path = ROOT / unexpected_path
        if path.exists():
            assert "chonkie" not in path.read_text(encoding="utf-8").lower()


def test_chonkie_notebook_and_docs_register_backend_runtime_contract() -> None:
    notebook = json.loads(
        (ROOT / "services/jupyterhub/build/notebooks/13_chonkie_chunking.ipynb").read_text(
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
            surface_text("docs/core-concepts.md", "site"),
            surface_text("docs/core-concepts.md", "wiki"),
        ]
    )

    assert "TokenChunker" in notebook_text
    assert "RecursiveChunker" in notebook_text
    assert "SemanticChunker" in notebook_text
    assert "/api/chunk" in notebook_text
    assert "13_chonkie_chunking.ipynb" in docs_text
    assert "POST /api/chunk" in docs_text
    assert "n8n workflows" in docs_text
