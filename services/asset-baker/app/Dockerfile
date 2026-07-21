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
#
# Reproducibility (#505): the canonical host download.blender.org returns HTTP
# 403 to automated clients, so the build fetches from the official Blender
# mirror network instead, trying each mirror in turn. The pinned SHA-256 makes
# the source host interchangeable — integrity is verified against BLENDER_SHA256
# regardless of which mirror serves the bytes — and a checksum mismatch fails
# the build with sha256sum's own error. Blender publishes an official Linux
# build for x86_64 ONLY (there is no upstream linux-arm64 tarball), so the build
# fails fast with an actionable message on any other architecture rather than
# silently installing an x86_64 binary that cannot run. To bump the pin, set
# ASSET_BAKER_BLENDER_VERSION + ASSET_BAKER_BLENDER_SHA256 from Blender's
# published checksum file (see services/asset-baker/README.md).
RUN set -eux; \
    arch="$(uname -m)"; \
    if [ "$arch" != "x86_64" ]; then \
        echo "ERROR: asset-baker supports linux/amd64 only — Blender publishes no official linux-${arch} build. Build on an x86_64 host or pass 'docker build --platform=linux/amd64'." >&2; \
        exit 1; \
    fi; \
    series="$(echo "${BLENDER_VERSION}" | cut -d. -f1,2)"; \
    file="blender-${BLENDER_VERSION}-linux-x64.tar.xz"; \
    ok=0; \
    for base in \
        "https://ftp.nluug.nl/pub/graphics/blender/release" \
        "https://mirror.clarkson.edu/blender/release" \
        "https://mirrors.ocf.berkeley.edu/blender/release" \
        "https://mirrors.dotsrc.org/blender/blender-release"; do \
        url="${base}/Blender${series}/${file}"; \
        echo "Fetching ${url}"; \
        if curl -fsSL --retry 3 --retry-delay 2 -m 600 "$url" -o /tmp/blender.tar.xz; then ok=1; break; fi; \
        echo "mirror unavailable: ${base}" >&2; \
    done; \
    if [ "$ok" != "1" ]; then \
        echo "ERROR: could not download ${file} from any known Blender mirror." >&2; \
        exit 1; \
    fi; \
    echo "${BLENDER_SHA256}  /tmp/blender.tar.xz" | sha256sum -c -; \
    mkdir -p /opt/blender; \
    tar -xJf /tmp/blender.tar.xz -C /opt/blender --strip-components=1; \
    rm /tmp/blender.tar.xz; \
    ln -s /opt/blender/blender /usr/local/bin/blender; \
    installed="$(/opt/blender/blender --version | head -n1)"; \
    echo "${installed}"; \
    case "${installed}" in \
        "Blender ${BLENDER_VERSION}"*) : ;; \
        *) echo "ERROR: expected Blender ${BLENDER_VERSION}, got '${installed}'" >&2; exit 1 ;; \
    esac

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY asset_baker /app/asset_baker
COPY main.py /app/main.py

EXPOSE 8096
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8096"]
