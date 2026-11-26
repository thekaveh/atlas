ARG BASE_IMAGE=python:3.12.14-slim
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Take Debian's published fixes on build, as services/docling/provider/adapter
# does (#1002). The final-image scan gates every refresh.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /usr/sbin/nologin speech-gate

COPY requirements.txt /app/requirements.txt
COPY requirements-locked.txt /app/requirements-locked.txt
# pip 26.3 vendors certifi 2026.10.23, which excludes the distrusted emSign
# ECC Root CA - G3. The base pip still vendors it, so upgrade before first use (#1905).
RUN pip install --no-cache-dir pip==26.3
RUN pip install --no-cache-dir -r /app/requirements.txt -c /app/requirements-locked.txt \
    && pip check

COPY model/silero_vad.onnx /app/model/silero_vad.onnx
COPY speech_gate /app/speech_gate

USER speech-gate
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
CMD ["python", "-m", "uvicorn", "speech_gate.main:app", "--host", "0.0.0.0", "--port", "8000"]
