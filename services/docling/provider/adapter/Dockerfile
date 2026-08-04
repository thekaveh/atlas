ARG BASE_IMAGE=python:3.12.13-slim
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DOCLING_ADAPTER_SPOOL_ROOT=/tmp/docling-adapter

WORKDIR /app

RUN useradd --create-home --shell /usr/sbin/nologin atlas-adapter \
    && mkdir -p /tmp/docling-adapter \
    && chown atlas-adapter:atlas-adapter /tmp/docling-adapter

COPY adapter/requirements.txt /app/requirements.txt
COPY adapter/requirements-locked.txt /app/requirements-locked.txt
RUN pip install --no-cache-dir -r /app/requirements.txt -c /app/requirements-locked.txt \
    && pip check

COPY bounded_upload.py /app/bounded_upload.py
COPY adapter /app/adapter

USER atlas-adapter
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
CMD ["python", "-m", "uvicorn", "adapter.app:app", "--host", "0.0.0.0", "--port", "8000"]
