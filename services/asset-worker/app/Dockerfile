ARG BASE_IMAGE=python:3.12.9-slim
ARG NODE_IMAGE=node:22-bookworm-slim@sha256:f32b81066cde10a75dbac96646099533316d94bac4150c55da1636e1f0ffdc46
FROM ${NODE_IMAGE} AS node-runtime
WORKDIR /opt/gltf-transform
ARG GLTF_TRANSFORM_VERSION=4.4.1
COPY package.json package-lock.json ./
RUN locked_version="$(node -p 'require("./package.json").dependencies["@gltf-transform/cli"]')" \
    && if [ "$locked_version" != "$GLTF_TRANSFORM_VERSION" ]; then \
        echo >&2 "asset-worker glTF version mismatch: GLTF_TRANSFORM_VERSION=$GLTF_TRANSFORM_VERSION, package lock=$locked_version; update package.json and package-lock.json together"; \
        exit 1; \
    fi \
    && npm ci --omit=dev

FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ASSET_WORKER_GLTF_TRANSFORM_BIN=/opt/gltf-transform/node_modules/.bin/gltf-transform

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /opt/gltf-transform /opt/gltf-transform

# The pinned base lags Debian security updates, so the final-image gate sees
# fixable findings the pin alone cannot clear. Take the published updates the
# same way services/spark and services/airflow do; this trades byte-for-byte
# base reproducibility for a patched runtime.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt
COPY requirements-locked.txt /app/requirements-locked.txt
RUN pip install --no-cache-dir -r /app/requirements.txt -c /app/requirements-locked.txt \
    && pip check

COPY asset_worker /app/asset_worker
COPY main.py /app/main.py

EXPOSE 8095
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8095"]
