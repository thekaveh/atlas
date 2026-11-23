"""#1905: JupyterHub's conda default CA bundle must not trust the emSign ECC G3 root.

The system store is fixed by the apt upgrade, but Python's default TLS context reads
/opt/conda/ssl/cacert.pem. The image replaces that file with certifi's bundle, and the
build fails if G3 survives. This test pins the step and the failure check.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "services" / "jupyterhub" / "build" / "Dockerfile"


def test_jupyterhub_replaces_the_conda_ca_bundle_with_certifi():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert 'cp "$(python -c \'import certifi; print(certifi.where())\')" /opt/conda/ssl/cacert.pem' in text
    assert 'grep -q "emSign ECC Root CA - G3" /opt/conda/ssl/cacert.pem' in text


def test_conda_ca_step_runs_after_the_locked_certifi_install():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "certifi==2026.10.19" in (ROOT / "services/jupyterhub/build/requirements-locked.txt").read_text(encoding="utf-8")
    assert text.index("requirements-locked.txt") < text.index("/opt/conda/ssl/cacert.pem")
