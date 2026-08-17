from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
BUILD = REPO / "services/jupyterhub/build"
EXPECTED_MANIFEST = """schema_version = 1

[vader_lexicon]
url = "https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages/sentiment/vader_lexicon.zip"
sha256 = "8adba4294eef3964d820bf655e37e61bdc3a341994356af59b74fb3b4a36ce5c"
size = 90486
resource = "sentiment/vader_lexicon.zip"
member = "vader_lexicon/vader_lexicon.txt"
license = "MIT License"
"""
EXPECTED_MODEL = (
    "en-core-web-sm @ https://github.com/explosion/spacy-models/releases/download/"
    "en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl "
    "--hash=sha256:1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85\n"
)
EXPECTED_INSTALLER_SHA256 = (
    "812380895a1cf5cd1fc50aa9ef21ea818bfa35e6a18fa307f1755bde362a3ecd"
)
EXPECTED_DOCKER_BLOCK = """COPY --chown=${NB_UID}:${NB_GID} nlp-model-requirements.txt /tmp/nlp-model-requirements.txt
COPY --chown=${NB_UID}:${NB_GID} nlp-assets.toml /tmp/nlp-assets.toml
COPY --chown=${NB_UID}:${NB_GID} install_nlp_assets.py /tmp/install_nlp_assets.py
RUN python -m pip install --no-cache-dir --no-deps --require-hashes -r /tmp/nlp-model-requirements.txt \\
 && python /tmp/install_nlp_assets.py install --manifest /tmp/nlp-assets.toml --data-dir /home/jovyan/nltk_data \\
 && python /tmp/install_nlp_assets.py verify --manifest /tmp/nlp-assets.toml --data-dir /home/jovyan/nltk_data \\
 && rm -f /tmp/nlp-model-requirements.txt /tmp/nlp-assets.toml /tmp/install_nlp_assets.py

ENV NLTK_DATA=/home/jovyan/nltk_data"""


def _assert_docker_order(dockerfile: str) -> None:
    positions = (
        dockerfile.index(
            "COPY --chown=${NB_UID}:${NB_GID} nlp-model-requirements.txt"
        ),
        dockerfile.index(
            "python -m pip install --no-cache-dir --no-deps --require-hashes"
        ),
        dockerfile.index("install_nlp_assets.py install"),
        dockerfile.index("install_nlp_assets.py verify"),
        dockerfile.index("rm -f /tmp/nlp-model-requirements.txt"),
    )
    assert positions == tuple(sorted(positions))


def _assert_no_legacy_downloaders(dockerfile: str) -> None:
    forbidden = (
        "SPACY_MODEL_VERSION",
        "SPACY_MODEL_SHA256",
        "NLTK_DATA_COMMIT",
        "VADER_LEXICON_SHA256",
        "nltk.download",
        "nltk.downloader",
        "vader_lexicon.zip /home",
        "COPY vader_lexicon.zip",
    )
    for token in forbidden:
        assert token not in dockerfile


def _assert_contract(
    *,
    manifest: str,
    installer: bytes,
    model: str,
    dockerfile: str,
    requirements: str,
    changelog: str,
) -> None:
    assert manifest == EXPECTED_MANIFEST
    assert hashlib.sha256(installer).hexdigest() == EXPECTED_INSTALLER_SHA256
    assert model == EXPECTED_MODEL
    assert dockerfile.count(EXPECTED_DOCKER_BLOCK) == 1
    _assert_docker_order(dockerfile)
    _assert_no_legacy_downloaders(dockerfile)
    assert "integrity-locked by nlp-model-requirements.txt and nlp-assets.toml" in requirements
    assert "vader_lexicon downloaded in Dockerfile" not in requirements
    assert "Issue #64" in changelog
    assert "8adba4294eef3964d820bf655e37e61bdc3a341994356af59b74fb3b4a36ce5c" in changelog


def _current() -> dict[str, object]:
    return {
        "manifest": (BUILD / "nlp-assets.toml").read_text(encoding="utf-8"),
        "installer": (BUILD / "install_nlp_assets.py").read_bytes(),
        "model": (BUILD / "nlp-model-requirements.txt").read_text(encoding="utf-8"),
        "dockerfile": (BUILD / "Dockerfile").read_text(encoding="utf-8"),
        "requirements": (BUILD / "requirements.txt").read_text(encoding="utf-8"),
        "changelog": (REPO / "docs/CHANGELOG.md").read_text(encoding="utf-8").split(
            "## 2. [0.1.0]", 1
        )[0],
    }


def test_jupyterhub_nlp_asset_projection_and_build_contract_are_exact() -> None:
    _assert_contract(**_current())


@pytest.mark.parametrize(
    ("field", "old", "new"),
    (
        ("manifest", "raw.githubusercontent.com", "example.invalid"),
        ("manifest", "8adba4294eef3964", "0adba4294eef3964"),
        ("model", "en_core_web_sm-3.8.0", "en_core_web_sm-3.7.0"),
        ("dockerfile", " --require-hashes", ""),
        ("dockerfile", " --no-deps", ""),
        ("dockerfile", " --chown=${NB_UID}:${NB_GID}", ""),
        (
            "dockerfile",
            " && python /tmp/install_nlp_assets.py verify --manifest /tmp/nlp-assets.toml --data-dir /home/jovyan/nltk_data \\\n",
            "",
        ),
        (
            "dockerfile",
            "install_nlp_assets.py install",
            "install_nlp_assets.py verify-before-install",
        ),
        ("dockerfile", "ENV NLTK_DATA=", "RUN python -m nltk.downloader vader_lexicon\nENV NLTK_DATA="),
        (
            "dockerfile",
            "COPY --chown=${NB_UID}:${NB_GID} nlp-assets.toml",
            "COPY vader_lexicon.zip /tmp/vader_lexicon.zip\n"
            "COPY --chown=${NB_UID}:${NB_GID} nlp-assets.toml",
        ),
    ),
)
def test_jupyterhub_nlp_asset_contract_rejects_every_drift(
    field: str, old: str, new: str
) -> None:
    values = _current()
    source = values[field]
    assert isinstance(source, str)
    mutated = source.replace(old, new, 1)
    assert mutated != source
    values[field] = mutated

    with pytest.raises(AssertionError):
        _assert_contract(**values)
