# Derived ComfyUI image (#1905). The ai-dock base's system store trusts the
# emSign ECC Root CA - G3 through its Ubuntu 22.04 ca-certificates package.
# Pinning ca-certificates to the jammy-updates build that drops it removes G3
# from the system store. That build removes 44 roots and adds 27 (ledger
# section 24). The build fails if G3 is still trusted.
ARG BASE_IMAGE=ghcr.io/ai-dock/comfyui:v2-cpu-22.04-v0.2.7@sha256:b47cd16007c309ebbb78b85f87bbc69ac9f7f3fc7596607e81940eeb7dca2421
FROM ${BASE_IMAGE}

# Stage 1 (#2108): pin the Ubuntu packages whose HIGH and CRITICAL findings cleared in
# the scan image, 1712 to 335 unique findings. Exact versions keep rebuilds reproducible.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      apt=2.4.15ubuntu0.1 apt-transport-https=2.4.15ubuntu0.1 apt-utils=2.4.15ubuntu0.1 dirmngr=2.2.27-3ubuntu2.5 \
      gir1.2-packagekitglib-1.0=1.2.5-2ubuntu3.1 git=1:2.34.1-1ubuntu1.18 git-man=1:2.34.1-1ubuntu1.18 gnupg=2.2.27-3ubuntu2.5 \
      gnupg-l10n=2.2.27-3ubuntu2.5 gnupg-utils=2.2.27-3ubuntu2.5 gpg=2.2.27-3ubuntu2.5 gpg-agent=2.2.27-3ubuntu2.5 \
      gpg-wks-client=2.2.27-3ubuntu2.5 gpg-wks-server=2.2.27-3ubuntu2.5 gpgconf=2.2.27-3ubuntu2.5 gpgsm=2.2.27-3ubuntu2.5 \
      gpgv=2.2.27-3ubuntu2.5 libapt-pkg6.0=2.4.15ubuntu0.1 libbrotli1=1.0.9-2ubuntu0.1 libpackagekit-glib2-18=1.2.5-2ubuntu3.1 \
      libssl3=3.0.2-0ubuntu1.30 libtiff5=4.3.0-6ubuntu0.13 linux-libc-dev=5.15.0-204.214 openssl=3.0.2-0ubuntu1.30 \
      packagekit=1.2.5-2ubuntu3.1 rar=2:6.23-1~22.04.1 rsync=3.2.7-0ubuntu0.22.04.7 sudo=1.9.9-1ubuntu2.7 \
      unrar=1:6.1.5-1ubuntu0.1 \
 && rm -rf /var/lib/apt/lists/*

# The ai-dock jupyter Python environment bundles certifi 2024.8.30, which trusts the
# distrusted emSign ECC Root CA - G3. Install the G3-free overlay pin used by the
# ComfyUI venv (#1905), and fail the build if G3 remains.
RUN echo 'certifi==2026.10.19 --hash=sha256:42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b' > /tmp/certifi-overlay.txt \
 && /opt/environments/python/jupyter/bin/python -m pip install --no-cache-dir --no-deps --require-hashes -r /tmp/certifi-overlay.txt \
 && rm -f /tmp/certifi-overlay.txt \
 && if grep -q 'emSign ECC Root CA - G3' "$(/opt/environments/python/jupyter/bin/python -c 'import certifi; print(certifi.where())')"; then \
      echo 'emSign ECC Root CA - G3 is still in the jupyter environment certifi' >&2; exit 1; \
    fi

# Restores the tracked custom_nodes files before the AI-Dock update checkout (#2107).
COPY atlas-preflight-custom-nodes.sh /opt/ai-dock/bin/preflight.d/05-atlas-custom-nodes.sh

ARG CA_CERTIFICATES_VERSION=20260816ubuntu0.22.04.2

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates=${CA_CERTIFICATES_VERSION} \
 && update-ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && if grep -q '^mozilla/emSign_ECC_Root_CA_-_G3.crt' /etc/ca-certificates.conf; then \
      echo "emSign ECC Root CA - G3 is still enabled" >&2; exit 1; \
    fi \
 && if ls /etc/ssl/certs | grep -qi 'emsign_ecc_root_ca_-_g3'; then \
      echo "emSign ECC Root CA - G3 is still linked in /etc/ssl/certs" >&2; exit 1; \
    fi

# Stage 2 (#2108): rebuild the ComfyUI venv on Python 3.11.15 from the audited lock. The
# ai-dock base ships only Python 3.10, and its pins left pillow, rembg, soupsieve, tornado
# and PyJWT with Trivy findings whose fixes need Python 3.11 or later. The lock
# (requirements-venv311-locked.txt, CPU torch index) drops torchaudio, transparent-background,
# flet, flet-runtime and matrix-client; the #2108 ticket records that trade-off.
# uv downloads Python from its standalone builds. The uv binary itself is pinned by sha256.
ARG UV_VERSION=0.11.19
ARG UV_SHA256=7035608168e106375b36d0c818d537a889c51a8625fe7f8f7cad5e62b947c368
ARG COMFYUI_PYTHON=3.11.15
ENV UV_PYTHON_INSTALL_DIR=/opt/atlas/python
COPY requirements-venv311-locked.txt /opt/atlas/comfyui-venv311-locked.txt
RUN curl -fsSL -o /tmp/uv.tar.gz "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz" \
 && echo "${UV_SHA256}  /tmp/uv.tar.gz" | sha256sum -c - \
 && tar -xzf /tmp/uv.tar.gz -C /usr/local/bin --strip-components=1 uv-x86_64-unknown-linux-gnu/uv \
 && rm -f /tmp/uv.tar.gz \
 && uv python install "${COMFYUI_PYTHON}" \
 && rm -rf /opt/environments/python/comfyui \
 && uv venv --seed --python "${COMFYUI_PYTHON}" /opt/environments/python/comfyui \
 && VIRTUAL_ENV=/opt/environments/python/comfyui UV_INDEX_STRATEGY=unsafe-best-match \
    uv pip install --no-cache --extra-index-url https://download.pytorch.org/whl/cpu \
      -r /opt/atlas/comfyui-venv311-locked.txt -c /opt/atlas/comfyui-venv311-locked.txt \
 && cp /opt/atlas/comfyui-venv311-locked.txt /opt/atlas/comfyui-venv311-constraints.txt \
 && printf 'torchaudio<0\n' >> /opt/atlas/comfyui-venv311-constraints.txt \
 && printf '[global]\nconstraint = /opt/atlas/comfyui-venv311-constraints.txt\n' > /opt/environments/python/comfyui/pip.conf \
 && if /opt/environments/python/comfyui/bin/python -c 'import certifi, sys; sys.exit(0 if b"emSign ECC Root CA - G3" in open(certifi.where(), "rb").read() else 1)'; then \
      echo "emSign ECC Root CA - G3 is still in the ComfyUI environment certifi" >&2; exit 1; \
    fi \
 && rm -rf /root/.cache/uv

# Stage 3 (#2108 residual): the AI-Dock api, jupyter, python_310 and serviceportal venvs stay on
# Python 3.10 and install from their audited locks. Trivy flagged tornado, starlette, aiohttp,
# anyio, h11, the Jupyter server and notebook stack, python-multipart, setuptools, urllib3,
# jupyter_core, idna, pygments and jinja2 in them. Each lock header lists the advisories it
# leaves open and why: tornado and soupsieve fixes need Python 3.11, and the portal's starlette
# stays below 1.0 because its templates break on 1.x. Each venv gets a pip.conf that constrains
# boot-time pip installs to its own lock, and every venv must pass pip check at build time.
COPY requirements-api-locked.txt /opt/atlas/api-venv-locked.txt
COPY requirements-jupyter-locked.txt /opt/atlas/jupyter-venv-locked.txt
COPY requirements-python_310-locked.txt /opt/atlas/python_310-venv-locked.txt
COPY requirements-serviceportal-locked.txt /opt/atlas/serviceportal-venv-locked.txt
RUN set -e; \
    VIRTUAL_ENV=/opt/environments/python/api uv pip install --no-cache -r /opt/atlas/api-venv-locked.txt -c /opt/atlas/api-venv-locked.txt; \
    VIRTUAL_ENV=/opt/environments/python/jupyter uv pip install --no-cache -r /opt/atlas/jupyter-venv-locked.txt -c /opt/atlas/jupyter-venv-locked.txt; \
    VIRTUAL_ENV=/opt/environments/python/python_310 uv pip install --no-cache -r /opt/atlas/python_310-venv-locked.txt -c /opt/atlas/python_310-venv-locked.txt; \
    VIRTUAL_ENV=/opt/environments/python/serviceportal uv pip install --no-cache -r /opt/atlas/serviceportal-venv-locked.txt -c /opt/atlas/serviceportal-venv-locked.txt; \
    for venv in api jupyter python_310 serviceportal; do \
      venv_dir="/opt/environments/python/${venv}"; \
      cp "/opt/atlas/${venv}-venv-locked.txt" "/opt/atlas/${venv}-venv-constraints.txt"; \
      printf '[global]\nconstraint = /opt/atlas/%s-venv-constraints.txt\n' "${venv}" > "${venv_dir}/pip.conf"; \
      "${venv_dir}/bin/python" -m pip check; \
    done \
 && rm -rf /root/.cache/uv

# ai-dock starts Cloudflare quick tunnels for ComfyUI, Jupyter, the service portal and
# syncthing unless CF_QUICK_TUNNELS=false. Atlas's ENABLE_QUICKTUNNEL flag is not read by
# this base, so the tunnels published those services on public trycloudflare.com URLs
# while WEB_ENABLE_AUTH is false. The image turns them off for every container.
ENV CF_QUICK_TUNNELS=false
