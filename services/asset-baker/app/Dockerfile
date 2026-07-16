ARG BASE_IMAGE=python:3.12.9-slim
FROM ${BASE_IMAGE}

# Pinned headless Blender (>= 4.3). Blender bundles its own Python + numpy, which
# the bake script (bake.py) runs under; the FastAPI layer uses the image's Python.
ARG BLENDER_VERSION=4.3.2
ARG BLENDER_SHA256=4da1c956673c0485e63054e563ee69198cc8f80d8157dd7592dffc8a6a5592e6

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ASSET_BAKER_BLENDER_BIN=/opt/blender/blender

# Runtime libs a headless Blender still loads even under -b, plus download tools.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl xz-utils \
        libx11-6 libxi6 libxxf86vm1 libxfixes3 libxrender1 libxkbcommon0 \
        libgl1 libglu1-mesa libsm6 libice6 libxext6 libegl1 \
    && rm -rf /var/lib/apt/lists/*

# Download + extract the pinned Blender release (series is derived from the version).
RUN set -eux; \
    series="$(echo "${BLENDER_VERSION}" | cut -d. -f1,2)"; \
    url="https://download.blender.org/release/Blender${series}/blender-${BLENDER_VERSION}-linux-x64.tar.xz"; \
    curl -fsSL "$url" -o /tmp/blender.tar.xz; \
    echo "${BLENDER_SHA256}  /tmp/blender.tar.xz" | sha256sum -c -; \
    mkdir -p /opt/blender; \
    tar -xJf /tmp/blender.tar.xz -C /opt/blender --strip-components=1; \
    rm /tmp/blender.tar.xz; \
    ln -s /opt/blender/blender /usr/local/bin/blender; \
    /opt/blender/blender --version

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY asset_baker /app/asset_baker
COPY main.py /app/main.py

EXPOSE 8096
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8096"]
