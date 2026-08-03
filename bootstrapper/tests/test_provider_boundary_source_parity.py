"""Keep service-local provider boundary copies from drifting."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOCLING = ROOT / "services" / "docling" / "provider"
PARAKEET = ROOT / "services" / "parakeet" / "provider"


def test_provider_boundary_modules_are_byte_identical():
    assert (DOCLING / "provider_boundary.py").read_bytes() == (
        PARAKEET / "provider_boundary.py"
    ).read_bytes()


def test_gpu_dockerfiles_copy_provider_boundary_module():
    docling_dockerfile = (DOCLING / "gpu" / "Dockerfile").read_text(encoding="utf-8")
    parakeet_dockerfile = (PARAKEET / "gpu" / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY provider_boundary.py /app/" in docling_dockerfile
    assert "COPY provider_boundary.py /app/" in parakeet_dockerfile
