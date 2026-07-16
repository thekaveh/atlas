ARG BASE_IMAGE=python:3.12.13-slim
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN useradd --create-home --shell /usr/sbin/nologin atlas-mcp

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt && pip check

COPY atlas_mcp_server.py /app/atlas_mcp_server.py

USER atlas-mcp
EXPOSE 8000
CMD ["python", "/app/atlas_mcp_server.py"]
