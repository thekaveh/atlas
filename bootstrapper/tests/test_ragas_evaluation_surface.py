from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests.three_surface_test_utils import surface_text


ROOT = Path(__file__).resolve().parents[2]
_RAGAS_ALLOWED_DIRS = ("services/backend/app/app/", "services/jupyterhub/build/")


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _other_requirement_manifests() -> list[str]:
    """Tracked requirement manifests outside the two ragas-owning services.

    git's default pathspec lets `*` cross `/`, so this also reaches
    services/requirements-init-locked.txt.
    """
    listed = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "services/*requirements*.txt"], cwd=ROOT
    ).decode("utf-8")
    return [
        path for path in listed.split("\0")
        if path and not path.startswith(_RAGAS_ALLOWED_DIRS)
    ]


def test_ragas_dependency_is_limited_to_backend_and_jupyterhub_surfaces() -> None:
    backend_requirements = _read("services/backend/app/app/requirements.txt")
    jupyter_requirements = _read("services/jupyterhub/build/requirements.txt")
    workflow = _read(".github/workflows/services-lint.yml")

    assert "ragas==0.4.3" in backend_requirements
    assert "ragas==0.4.3" in jupyter_requirements
    assert "langchain-community>=0.3.0,<0.4" in backend_requirements
    assert "langchain-community>=0.3.0,<0.4" in jupyter_requirements
    assert "requirements-test-locked.txt" in workflow

    other_manifests = _other_requirement_manifests()
    assert other_manifests, "no other tracked requirement manifests were found"
    offenders = [path for path in other_manifests if "ragas" in _read(path).lower()]
    assert offenders == []


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


@pytest.mark.parametrize("opt_in", [None, "", "0", "1"])
def test_notebook_backend_evaluation_requires_its_own_opt_in(monkeypatch, opt_in) -> None:
    notebook = json.loads(
        (ROOT / "services/jupyterhub/build/notebooks/14_ragas_evaluation.ipynb").read_text()
    )
    cells = {cell["id"]: "".join(cell["source"]) for cell in notebook["cells"]}
    monkeypatch.delenv("RUN_BACKEND_RAGAS_LIVE", raising=False)
    if opt_in is not None:
        monkeypatch.setenv("RUN_BACKEND_RAGAS_LIVE", opt_in)
    # Opting into the local evaluator must not run the Backend evaluator too.
    monkeypatch.setenv("RUN_RAGAS_LIVE", "1")
    monkeypatch.setenv("BACKEND_API_URL", "http://fake-backend.invalid/")
    monkeypatch.setenv("BACKEND_NOTEBOOK_API_TOKEN", "fake-scoped-token")
    requests = []

    def post(url, **kwargs):
        requests.append((url, kwargs))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"scores": []})

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(post=post, HTTPError=RuntimeError))
    namespace = {}
    exec(compile(cells["atlas-ragas-imports"], "notebook-setup", "exec"), namespace)
    exec(compile(cells["atlas-ragas-backend-call"], "notebook-backend", "exec"), namespace)

    if opt_in != "1":
        assert requests == []
        return
    assert len(requests) == 1
    url, options = requests[0]
    assert url == "http://fake-backend.invalid/api/rag/evaluate"
    assert options["headers"] == {"Authorization": "Bearer fake-scoped-token"}
    assert options["json"] == {
        "records": [namespace["SAMPLE_RECORD"]], "metrics": namespace["METRICS"]
    }
    assert options["timeout"] == 120.0
