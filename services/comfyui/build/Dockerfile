# Derived ComfyUI image (#1905). The ai-dock base's system store trusts the
# emSign ECC Root CA - G3 through its Ubuntu 22.04 ca-certificates package.
# Pinning ca-certificates to the jammy-updates build that drops it removes G3
# from the system store. That build removes 44 roots and adds 27 (ledger
# section 24). The build fails if G3 is still trusted.
ARG BASE_IMAGE=ghcr.io/ai-dock/comfyui:v2-cpu-22.04-v0.2.7
FROM ${BASE_IMAGE}

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
