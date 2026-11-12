"""The OpenCV notebook runs its own shape and dtype assertions offline (#601).

The notebook's cells run here when `cv2` is importable. The JupyterHub image has
OpenCV, so the image build and a local venv with the pinned wheel both run this.
"""

import json
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK = ROOT / "services" / "jupyterhub" / "build" / "notebooks" / "16_opencv_vision.ipynb"
DOCKERFILE = ROOT / "services" / "jupyterhub" / "build" / "Dockerfile"
BACKEND_REQUIREMENTS = ROOT / "services" / "backend" / "app" / "app" / "requirements.txt"


def _code_cells(path: Path) -> list[str]:
    notebook = json.loads(path.read_text(encoding="utf-8"))
    return ["".join(cell["source"]) for cell in notebook["cells"] if cell["cell_type"] == "code"]


def test_no_window_or_gui_call_in_the_notebook_code_or_the_image():
    code = "\n".join(_code_cells(NOTEBOOK)) + DOCKERFILE.read_text(encoding="utf-8")
    assert not re.search(r"cv2\.(imshow|namedWindow|waitKey)", code)


def test_backend_stays_free_of_opencv():
    assert not re.search(r"(?im)^opencv", BACKEND_REQUIREMENTS.read_text(encoding="utf-8"))


def test_image_build_asserts_one_headless_contrib_distribution():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert 'assert dists == ["opencv-contrib-python-headless"], dists' in text
    assert 'assert gui == "NONE", gui' in text
    assert 'assert hasattr(cv2, "xfeatures2d")' in text


@pytest.mark.skipif(os.environ.get("ATLAS_SKIP_OPENCV_NOTEBOOK") == "1", reason="explicitly skipped")
def test_notebook_cells_run_and_their_shape_and_dtype_assertions_hold():
    cv2 = pytest.importorskip("cv2")
    pytest.importorskip("matplotlib")
    assert cv2.__version__.startswith("5.0")
    namespace: dict = {"__name__": "__opencv_notebook__"}
    for source in _code_cells(NOTEBOOK):
        exec(compile(source.replace("plt.show()", ""), str(NOTEBOOK), "exec"), namespace)
