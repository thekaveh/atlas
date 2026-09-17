ARG BASE_IMAGE=python:3.12.14-slim
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# The newest python:3.12 slim build still lags Debian's published updates
# (2026-09-17: 3.12.14-slim on Debian 13.6 reports 13 fixed HIGH findings in
# perl-base, libpcre2, sqlite and gzip), so take them the same way
# services/backend, airflow, spark, asset-baker, asset-worker and backup do
# (#1002). The final-image scan gates every refresh.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /usr/sbin/nologin atlas-mcp

COPY requirements.txt /app/requirements.txt
COPY requirements-locked.txt /app/requirements-locked.txt
RUN pip install --no-cache-dir -r /app/requirements.txt -c /app/requirements-locked.txt \
    && pip check

COPY atlas_mcp_server.py /app/atlas_mcp_server.py

USER atlas-mcp
EXPOSE 8000
CMD ["python", "/app/atlas_mcp_server.py"]
